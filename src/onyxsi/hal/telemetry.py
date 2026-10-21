"""
Tool-Call Telemetry over the HAL SPSC ring buffer.

Every meta-tool invocation is recorded as one compact entry in a lock-free
single-producer / single-consumer ring buffer (``hal.ring_buffer``). The buffer
is anonymous ``mmap`` memory, so recording costs no filesystem I/O and cannot
leak temp files.

Design constraints
------------------
* ``ENTRY_SIZE`` is 128 bytes, so records are a compact pipe-delimited line,
  not JSON: ``seq|tool|ok|duration_us|error``.
* The ring is bounded. When it is full the oldest entry is dropped and counted
  in ``dropped``; recording must never raise into a tool call.
* ``snapshot()`` drains the ring into a bounded in-process history so callers
  (``causal_flight stage=reflect``) can read a stable summary.
* ``flush_jsonl()`` is opt-in (CLI ``--telemetry-out``), so normal runs never
  write telemetry files.

This module is intentionally dependency-free and works on Windows and Linux.
"""

import json
import os
import threading
from collections import Counter, deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from onyxsi.hal.ring_buffer import (
    ENTRY_SIZE,
    RingBufferConfig,
    RingBufferOverflowError,
    RingBufferUnderflowError,
    SPSCRingBuffer,
)

DEFAULT_CAPACITY = 1024
DEFAULT_HISTORY = 2048
_MAX_TOOL_CHARS = 24
_MAX_ERROR_CHARS = 24


class ToolTelemetry:
    """Bounded, file-free tool-call telemetry backed by an SPSC ring buffer."""

    def __init__(
        self,
        capacity: int = DEFAULT_CAPACITY,
        history_limit: int = DEFAULT_HISTORY,
        buffer: Optional[SPSCRingBuffer] = None,
    ):
        self.capacity = max(1, int(capacity))
        self._buffer = buffer or SPSCRingBuffer(
            RingBufferConfig(size_mb=1), capacity=self.capacity
        )
        self._buffer.initialize()
        self._lock = threading.Lock()
        self._history: Deque[Dict[str, Any]] = deque(maxlen=max(1, int(history_limit)))
        self._dropped = 0
        self._closed = False

    # ------------------------------------------------------------------ writing
    def record(
        self,
        tool: str,
        ok: bool,
        duration_us: int,
        error: Optional[str] = None,
    ) -> None:
        """Records one invocation. Never raises: telemetry must not break a call."""
        if self._closed:
            return
        name = str(tool)[:_MAX_TOOL_CHARS]
        err = (error or "")[:_MAX_ERROR_CHARS]
        line = f"{name}|{1 if ok else 0}|{max(0, int(duration_us))}|{err}"
        payload = line.encode("utf-8")
        if len(payload) > ENTRY_SIZE:
            payload = payload[:ENTRY_SIZE]

        with self._lock:
            try:
                self._buffer.push(payload)
                return
            except RingBufferOverflowError:
                # Drop the oldest entry so the newest record always survives.
                self._dropped += 1
                try:
                    self._buffer.pop()
                except RingBufferUnderflowError:
                    pass
            except Exception:  # noqa: BLE001 - telemetry is best-effort
                self._dropped += 1
                return

            try:
                self._buffer.push(payload)
            except Exception:  # noqa: BLE001
                self._dropped += 1

    # ------------------------------------------------------------------ reading
    def _drain_locked(self) -> int:
        drained = 0
        while True:
            try:
                entry = self._buffer.pop()
            except RingBufferUnderflowError:
                break
            except Exception:  # noqa: BLE001
                break
            drained += 1
            try:
                line = entry.data.decode("utf-8", errors="replace")
                tool, ok_s, dur_s, err = (line.split("|", 3) + ["", "", "", ""])[:4]
                self._history.append(
                    {
                        "seq": entry.sequence,
                        "tool": tool,
                        "ok": ok_s == "1",
                        "duration_us": int(dur_s or 0),
                        "error": err or None,
                        "timestamp": entry.timestamp,
                    }
                )
            except Exception:  # noqa: BLE001 - a corrupt slot must not kill the drain
                continue
        return drained

    def snapshot(self, recent: int = 10) -> Dict[str, Any]:
        """Drains the ring and returns an aggregate view of the whole history."""
        with self._lock:
            self._drain_locked()
            total = len(self._history)
            by_tool: Counter = Counter(rec["tool"] for rec in self._history)
            by_status: Counter = Counter("ok" if rec["ok"] else "error" for rec in self._history)
            durations = sorted(rec["duration_us"] for rec in self._history)
            errors: Counter = Counter(
                rec["error"] for rec in self._history if rec.get("error")
            )

            def pct(p: float) -> Optional[int]:
                if not durations:
                    return None
                idx = min(len(durations) - 1, int(round((len(durations) - 1) * p)))
                return durations[idx]

            return {
                "total": total,
                "ok": by_status.get("ok", 0),
                "error": by_status.get("error", 0),
                "dropped": self._dropped,
                "buffer_capacity": self.capacity,
                "buffer_state": self._safe_state(),
                "by_tool": dict(by_tool),
                "errors": dict(errors),
                "duration_us": {
                    "p50": pct(0.50),
                    "p95": pct(0.95),
                    "max": durations[-1] if durations else None,
                },
                "recent": list(self._history)[-max(1, int(recent)):],
            }

    def _safe_state(self) -> str:
        try:
            return self._buffer.state()
        except Exception:  # noqa: BLE001
            return "UNKNOWN"

    # ------------------------------------------------------------------ exports
    def flush_jsonl(self, path: str) -> int:
        """
        Appends the drained history to ``path`` (one JSON object per line).

        Returns the number of records written. Creates parent directories.
        """
        with self._lock:
            self._drain_locked()
            records: List[Dict[str, Any]] = list(self._history)
        if not records:
            return 0

        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return len(records)

    def close(self) -> None:
        """Releases the mmap backing store. History remains readable."""
        with self._lock:
            if self._closed:
                return
            self._drain_locked()
            self._closed = True
        try:
            self._buffer.cleanup()
        except Exception:  # noqa: BLE001
            pass

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "ToolTelemetry":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def summarize(records: List[Tuple[str, bool, int]]) -> Dict[str, Any]:
    """Pure helper for tests/tools that already hold ``(tool, ok, duration_us)``."""
    total = len(records)
    ok = sum(1 for _, good, _ in records if good)
    durations = sorted(max(0, int(d)) for _, _, d in records)
    return {
        "total": total,
        "ok": ok,
        "error": total - ok,
        "max_duration_us": durations[-1] if durations else None,
    }
