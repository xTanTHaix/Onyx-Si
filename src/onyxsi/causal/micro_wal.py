"""
Micro-WAL Flight Recorder.
Maintains a bounded 64-event circular buffer of kernel actions and generates
a deterministic CRASH_REPRO.md report when an unexpected crash or abort occurs.

Also owns the Time-Travel Chronicle renderer: the ASCII decision tree plus the
frame-by-frame event table that ``TIME_TRAVEL_REPLAY.md`` is built from.
100% Python Standard Library only.
"""

import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional

MAX_WAL_EVENTS = 64


@dataclass(slots=True, frozen=True)
class WalEvent:
    """Single flight recorder telemetry event."""

    seq: int
    timestamp: float
    action: str
    target: str
    status: str
    details: str = ""


# ---------------------------------------------------------------- time-travel
# Keys accepted for a branch descriptor, in priority order. The renderer is
# deliberately forgiving: it is fed by MCP clients, not by the kernel itself.
_BRANCH_ID_KEYS = ("branch_id", "id", "name", "branch")
_BRANCH_PARENT_KEYS = ("parent", "parent_id", "from_branch", "parent_branch")
_BRANCH_SCORE_KEYS = ("score", "mcts_score", "value")
_BRANCH_HYPOTHESIS_KEYS = ("hypothesis", "reason", "note")
_BRANCH_STATUS_KEYS = ("status", "state")
_BRANCH_FILE_KEYS = ("modified_files", "files", "changed_files")

_FRAME_ACTION_KEYS = ("action", "step", "event", "name")
_FRAME_TARGET_KEYS = ("target", "path", "file", "subject")
_FRAME_OUTCOME_KEYS = ("outcome", "status", "result")
_FRAME_BRANCH_KEYS = ("branch", "branch_id")
_FRAME_REL_KEYS = ("rel_time", "relative_time", "elapsed", "dt")
_FRAME_TS_KEYS = ("timestamp", "ts", "time", "at")


def _first_key(mapping: Dict[str, Any], keys: tuple) -> Any:
    """Returns the first present, non-None value among ``keys``."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _format_rel_time(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+.3f}s"


def _normalise_frames(raw_frames: Any) -> List[Dict[str, Any]]:
    """Normalises loose frame descriptors into a stable rendering shape."""
    if not raw_frames:
        return []

    frames: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_frames):
        if not isinstance(raw, dict):
            frames.append(
                {
                    "index": index + 1,
                    "action": str(raw),
                    "target": "",
                    "outcome": "",
                    "branch": "",
                    "rel_time": None,
                }
            )
            continue

        rel = _first_key(raw, _FRAME_REL_KEYS)
        try:
            rel = float(rel) if rel is not None else None
        except (TypeError, ValueError):
            rel = None

        ts = _first_key(raw, _FRAME_TS_KEYS)
        try:
            ts = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts = None

        frames.append(
            {
                "index": index + 1,
                "action": str(_first_key(raw, _FRAME_ACTION_KEYS) or "unknown"),
                "target": str(_first_key(raw, _FRAME_TARGET_KEYS) or ""),
                "outcome": str(_first_key(raw, _FRAME_OUTCOME_KEYS) or ""),
                "branch": str(_first_key(raw, _FRAME_BRANCH_KEYS) or ""),
                "rel_time": rel,
                "timestamp": ts,
            }
        )

    # Relative time falls back to the delta between the first and each frame.
    if any(f["rel_time"] is None for f in frames):
        stamps = [f["timestamp"] for f in frames if f["timestamp"] is not None]
        if stamps:
            base = stamps[0]
            for frame in frames:
                if frame["rel_time"] is None and frame["timestamp"] is not None:
                    frame["rel_time"] = frame["timestamp"] - base
    return frames


def _render_branch_tree(raw_branches: Any) -> List[str]:
    """Renders the MCTS decision tree as an ASCII box-drawing diagram."""
    if not raw_branches:
        return []

    nodes: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for raw in raw_branches:
        if not isinstance(raw, dict):
            continue
        branch_id = _first_key(raw, _BRANCH_ID_KEYS)
        if branch_id is None:
            continue
        branch_id = str(branch_id)

        parent = _first_key(raw, _BRANCH_PARENT_KEYS)
        parent = str(parent) if parent else None

        score = _first_key(raw, _BRANCH_SCORE_KEYS)
        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None

        files = _first_key(raw, _BRANCH_FILE_KEYS)
        if not isinstance(files, list):
            files = []

        nodes[branch_id] = {
            "id": branch_id,
            "parent": parent,
            "score": score,
            "hypothesis": str(_first_key(raw, _BRANCH_HYPOTHESIS_KEYS) or ""),
            "status": str(_first_key(raw, _BRANCH_STATUS_KEYS) or ""),
            "files": files,
        }
        if branch_id not in order:
            order.append(branch_id)

    if not nodes:
        return []

    children: Dict[str, List[str]] = {bid: [] for bid in nodes}
    roots: List[str] = []
    for branch_id in order:
        parent = nodes[branch_id]["parent"]
        if parent and parent in nodes and parent != branch_id:
            children[parent].append(branch_id)
        else:
            roots.append(branch_id)

    lines: List[str] = []
    emitted: set = set()

    def emit(branch_id: str, prefix: str, connector: str, ancestors: set) -> None:
        node = nodes[branch_id]
        label = branch_id
        if node["score"] is not None:
            label += f"  [score={node['score']:.2f}]"
        if node["status"]:
            label += f"  ({node['status']})"
        if node["files"]:
            label += f"  files={len(node['files'])}"
        if node["hypothesis"]:
            label += f"  — {node['hypothesis']}"
        lines.append(prefix + connector + label)
        emitted.add(branch_id)

        if connector.startswith("└"):
            child_prefix = prefix + "    "
        elif connector == "":
            child_prefix = prefix
        else:
            child_prefix = prefix + "│   "

        kids = children.get(branch_id, [])
        for position, child in enumerate(kids):
            last = position == len(kids) - 1
            if child in ancestors:
                lines.append(child_prefix + ("└── " if last else "├── ") + child + "  (cycle)")
                continue
            emit(child, child_prefix, "└── " if last else "├── ", ancestors | {child})

    for root in roots:
        if root in emitted:
            continue
        emit(root, "", "", {root})

    # A cycle with no entry point leaves nodes unreachable: surface them
    # instead of silently dropping them from the chronicle.
    for branch_id in order:
        if branch_id not in emitted:
            emit(branch_id, "", "", {branch_id})

    return lines


def render_time_travel_chronicle(
    task_id: str,
    project_name: str,
    frames: Any = None,
    branches: Any = None,
) -> str:
    """
    Renders the deterministic Time-Travel Chronicle document.

    Section 1 is the ASCII decision/state branch tree; section 2 is the
    frame-by-frame event table. Pure function: the same inputs always yield
    the same markdown.
    """
    normalised = _normalise_frames(frames)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    branch_count = len(branches) if isinstance(branches, list) else 0

    lines: List[str] = [
        f"# Time-Travel Replay: {task_id}",
        "",
        f"**Project:** `{project_name}` | **Generated:** {now_iso} | "
        f"**Frames:** {len(normalised)} | **Branches:** {branch_count}",
        "",
        "## 1. ASCII Decision & State Branch Tree",
        "",
        "```text",
    ]

    tree = _render_branch_tree(branches)
    lines.extend(tree if tree else ["(no speculative branches were recorded)"])

    lines.extend(
        [
            "```",
            "",
            "## 2. Frame-by-Frame Event Trace",
            "",
            "| Frame | Relative Time | Branch | Action | Target | Outcome |",
            "| :---: | :---: | :--- | :--- | :--- | :--- |",
        ]
    )

    if normalised:
        for frame in normalised:
            lines.append(
                f"| #{frame['index']} | {_format_rel_time(frame['rel_time'])} | "
                f"`{frame['branch'] or '-'}` | `{frame['action']}` | "
                f"`{frame['target']}` | {frame['outcome'] or '-'} |"
            )
    else:
        lines.append("| — | — | — | *(no frames recorded)* | — | — |")

    lines.extend(
        [
            "",
            "---",
            "*OnyxSi Kernel — Time-Travel Chronicle. Deterministic replay document.*",
        ]
    )
    return "\n".join(lines) + "\n"


class MicroWAL:
    """
    Zero-overhead 64-entry circular flight recorder.
    Captures the exact causal sequence leading up to any failure.
    """

    def __init__(self, capacity: int = MAX_WAL_EVENTS):
        self.capacity = capacity
        self._events: Deque[WalEvent] = deque(maxlen=capacity)
        self._seq = 0

    def record(self, action: str, target: str, status: str = "OK", details: str = "") -> WalEvent:
        self._seq += 1
        ev = WalEvent(
            seq=self._seq,
            timestamp=time.time(),
            action=action,
            target=target,
            status=status,
            details=details,
        )
        self._events.append(ev)
        return ev

    def get_recent_events(self) -> List[WalEvent]:
        return list(self._events)

    def write_crash_repro(self, output_path: str, crash_reason: str) -> str:
        """Writes CRASH_REPRO.md containing the last up to 64 recorded kernel events."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        lines = [
            "# OnyxSi Kernel Crash Reproduction Log (`CRASH_REPRO.md`)",
            "",
            f"- **Crash Reason**: `{crash_reason}`",
            f"- **Total Events Captured**: `{len(self._events)}` (Last {self.capacity} window)",
            "",
            "## Chronological Event Trace",
            "",
            "| Seq | Timestamp | Action | Target | Status | Details |",
            "| :---: | :---: | :--- | :--- | :---: | :--- |",
        ]
        for ev in self._events:
            lines.append(
                f"| {ev.seq} | {ev.timestamp:.4f} | `{ev.action}` | `{ev.target}` | `{ev.status}` | {ev.details} |"
            )

        content = "\n".join(lines) + "\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return content

    def write_time_travel_chronicle(
        self,
        output_path: str,
        task_id: str,
        project_name: str,
        branches: Any = None,
        frames: Any = None,
    ) -> str:
        """
        Writes the Time-Travel Chronicle to ``output_path``.

        When ``frames`` is omitted the recorder's own event window is used, so
        the chronicle always reflects what actually happened in this process.
        """
        if frames is None:
            frames = [
                {
                    "action": ev.action,
                    "target": ev.target,
                    "outcome": f"{ev.status} — {ev.details}" if ev.details else ev.status,
                    "timestamp": ev.timestamp,
                }
                for ev in self._events
            ]

        content = render_time_travel_chronicle(
            task_id=task_id,
            project_name=project_name,
            frames=frames,
            branches=branches,
        )
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return content
