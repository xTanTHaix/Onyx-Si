"""
AST-Node Lease Lock Registry.
Enables fine-grained, concurrent multi-agent editing at individual AST node granularity
(ast_<kind>_<name>_<hash8>) rather than coarse whole-file locking.
Thread-safe with atomic TOCTOU-free lock acquisition.

When ``lock_dir`` is supplied, leases are additionally mirrored to a lockfile per
(canonical path, node) pair, so two *separate OnyxSi processes* pointed at the same
workspace contend for the same node. NOTE: this is advisory locking over a shared
directory; it assumes a POSIX-like O_EXCL guarantee (NTFS also provides it) and a
directory both processes can write.
"""

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from onyxsi.contracts import OnyxError, OnyxErrorCode


class AstLeaseConflictError(OnyxError):
    def __init__(self, file_path: str, node_id: str, current_holder: str, remaining_sec: float):
        self.file_path = file_path
        self.node_id = node_id
        self.current_holder = current_holder
        self.remaining_sec = remaining_sec
        super().__init__(
            OnyxErrorCode.E3003,
            f"AST Node '{node_id}' in '{os.path.basename(file_path)}' is currently locked by agent "
            f"'{current_holder}' (remaining lease: {remaining_sec:.2f}s)",
            details={
                "file_path": file_path,
                "node_id": node_id,
                "current_holder": current_holder,
                "remaining_sec": remaining_sec,
            },
        )


@dataclass(slots=True, frozen=True)
class AstLease:
    """An active AST-node lock lease."""
    file_path: str
    node_id: str
    agent_id: str
    acquired_at: float
    ttl_sec: float

    def is_expired(self, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.time()
        return (t - self.acquired_at) >= self.ttl_sec

    def remaining_sec(self, now: Optional[float] = None) -> float:
        t = now if now is not None else time.time()
        return max(0.0, self.ttl_sec - (t - self.acquired_at))


class AstLeaseRegistry:
    """
    Thread-safe granular AST-node lease lock manager.
    Multiple sub-agents can simultaneously mutate separate functions/classes in the same file.
    """

    def __init__(self, default_ttl_sec: float = 60.0, lock_dir: Optional[str] = None):
        self.default_ttl_sec = default_ttl_sec
        self.lock_dir = os.path.realpath(lock_dir) if lock_dir else None
        self._leases: Dict[Tuple[str, str], AstLease] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ cross-process
    def _fs_lock_path(self, canonical: str, node_id: str) -> str:
        digest = hashlib.sha256(f"{canonical}\x00{node_id}".encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.lock_dir or "", f"{digest}.lease")

    @staticmethod
    def _replace_atomic_retry(
        src: str,
        dst: str,
        max_retries: int = 5,
        delay_sec: float = 0.01,
    ) -> bool:
        """
        Atomically replaces dst with src.

        On Windows (NTFS), `os.replace` can raise a transient `PermissionError`
        (sharing violation) if another process currently holds an open read handle
        on the destination lock file. Retries up to `max_retries` with a brief sleep.
        Returns True on success, False if PermissionError persisted across all retries.
        """
        for attempt in range(max_retries):
            try:
                os.replace(src, dst)
                return True
            except PermissionError:
                if attempt < max_retries - 1:
                    time.sleep(delay_sec)
                else:
                    return False
            except OSError:
                raise
        return False

    def _fs_acquire(self, canonical: str, node_id: str, agent_id: str, ttl: float) -> None:
        """Creates an exclusive lockfile; steals it only when its lease expired."""
        if not self.lock_dir:
            return
        os.makedirs(self.lock_dir, exist_ok=True)
        path = self._fs_lock_path(canonical, node_id)
        payload = {
            "file_path": canonical,
            "node_id": node_id,
            "agent_id": agent_id,
            "acquired_at": time.time(),
            "ttl_sec": ttl,
        }

        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        current = json.load(fh)
                except (OSError, ValueError):
                    current = {}
                holder = str(current.get("agent_id", "unknown"))
                acquired = float(current.get("acquired_at", 0.0))
                held_ttl = float(current.get("ttl_sec", 0.0))
                if (time.time() - acquired) < held_ttl:
                    if holder == agent_id:
                        # Same agent re-acquiring its own lease: refresh it.
                        tmp = f"{path}.{os.getpid()}.tmp"
                        with open(tmp, "w", encoding="utf-8") as fh:
                            json.dump(payload, fh)
                        try:
                            if not self._replace_atomic_retry(tmp, path):
                                raise AstLeaseConflictError(
                                    file_path=canonical,
                                    node_id=node_id,
                                    current_holder=holder,
                                    remaining_sec=max(0.0, held_ttl - (time.time() - acquired)),
                                )
                            return
                        finally:
                            if os.path.exists(tmp):
                                try:
                                    os.remove(tmp)
                                except OSError:
                                    pass
                    raise AstLeaseConflictError(
                        file_path=canonical,
                        node_id=node_id,
                        current_holder=holder,
                        remaining_sec=max(0.0, held_ttl - (time.time() - acquired)),
                    )
                # Expired: try to steal atomically; a concurrent stealer wins the race.
                tmp = f"{path}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                try:
                    if not self._replace_atomic_retry(tmp, path):
                        raise AstLeaseConflictError(
                            file_path=canonical,
                            node_id=node_id,
                            current_holder=holder,
                            remaining_sec=ttl,
                        )
                    return
                finally:
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                return

        raise AstLeaseConflictError(
            file_path=canonical,
            node_id=node_id,
            current_holder="unknown",
            remaining_sec=ttl,
        )

    def _fs_release(self, canonical: str, node_id: str, agent_id: str) -> bool:
        if not self.lock_dir:
            return False
        path = self._fs_lock_path(canonical, node_id)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                current = json.load(fh)
        except (OSError, ValueError):
            return False
        if current.get("agent_id") != agent_id:
            return False
        try:
            os.remove(path)
            return True
        except OSError:
            return False

    def acquire_lease(
        self,
        file_path: str,
        node_id: str,
        agent_id: str,
        ttl_sec: Optional[float] = None,
    ) -> AstLease:
        canonical = os.path.realpath(file_path)
        key = (canonical, node_id)
        ttl = ttl_sec if ttl_sec is not None else self.default_ttl_sec

        with self._lock:
            now = time.time()
            existing = self._leases.get(key)
            if existing is not None and not existing.is_expired(now):
                if existing.agent_id != agent_id:
                    raise AstLeaseConflictError(
                        file_path=canonical,
                        node_id=node_id,
                        current_holder=existing.agent_id,
                        remaining_sec=existing.remaining_sec(now),
                    )
            self._fs_acquire(canonical, node_id, agent_id, ttl)
            lease = AstLease(
                file_path=canonical,
                node_id=node_id,
                agent_id=agent_id,
                acquired_at=now,
                ttl_sec=ttl,
            )
            self._leases[key] = lease
            return lease

    def release_lease(self, file_path: str, node_id: str, agent_id: str) -> bool:
        canonical = os.path.realpath(file_path)
        key = (canonical, node_id)
        with self._lock:
            existing = self._leases.get(key)
            if existing is not None and existing.agent_id == agent_id:
                del self._leases[key]
                released = True
            else:
                released = False
        self._fs_release(canonical, node_id, agent_id)
        return released

    def renew_lease(
        self,
        file_path: str,
        node_id: str,
        agent_id: str,
        additional_ttl_sec: float = 60.0,
    ) -> bool:
        canonical = os.path.realpath(file_path)
        key = (canonical, node_id)
        with self._lock:
            now = time.time()
            existing = self._leases.get(key)
            if existing is not None and existing.agent_id == agent_id and not existing.is_expired(now):
                new_ttl = existing.remaining_sec(now) + additional_ttl_sec
                self._leases[key] = AstLease(
                    file_path=canonical,
                    node_id=node_id,
                    agent_id=agent_id,
                    acquired_at=now,
                    ttl_sec=new_ttl,
                )
                self._fs_acquire(canonical, node_id, agent_id, new_ttl)
                return True
            return False

    def is_leased(self, file_path: str, node_id: str) -> bool:
        canonical = os.path.realpath(file_path)
        key = (canonical, node_id)
        with self._lock:
            existing = self._leases.get(key)
            if existing is not None and not existing.is_expired():
                return True
        if not self.lock_dir:
            return False
        path = self._fs_lock_path(canonical, node_id)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return (time.time() - float(data["acquired_at"])) < float(data["ttl_sec"])
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _fs_active_leases(self) -> List[AstLease]:
        if not self.lock_dir or not os.path.isdir(self.lock_dir):
            return []
        now = time.time()
        active: List[AstLease] = []
        for name in sorted(os.listdir(self.lock_dir)):
            if not name.endswith(".lease"):
                continue
            try:
                with open(os.path.join(self.lock_dir, name), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                acquired = float(data["acquired_at"])
                ttl = float(data["ttl_sec"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if now - acquired < ttl:
                active.append(
                    AstLease(
                        file_path=str(data.get("file_path", "")),
                        node_id=str(data.get("node_id", "")),
                        agent_id=str(data.get("agent_id", "")),
                        acquired_at=acquired,
                        ttl_sec=ttl,
                    )
                )
        return active

    def get_active_leases(self, file_path: Optional[str] = None) -> List[AstLease]:
        canonical = os.path.realpath(file_path) if file_path is not None else None
        merged: Dict[Tuple[str, str], AstLease] = {}
        with self._lock:
            now = time.time()
            for (fpath, node), lease in self._leases.items():
                if not lease.is_expired(now):
                    merged[(fpath, node)] = lease
        for lease in self._fs_active_leases():
            merged.setdefault((lease.file_path, lease.node_id), lease)

        return [
            lease
            for (fpath, _), lease in sorted(merged.items())
            if canonical is None or fpath == canonical
        ]

    def cleanup_expired(self) -> int:
        with self._lock:
            now = time.time()
            expired_keys = [k for k, l in self._leases.items() if l.is_expired(now)]
            for k in expired_keys:
                del self._leases[k]
        removed = len(expired_keys)

        if self.lock_dir and os.path.isdir(self.lock_dir):
            now = time.time()
            for name in os.listdir(self.lock_dir):
                if not name.endswith(".lease"):
                    continue
                path = os.path.join(self.lock_dir, name)
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    expired = (now - float(data["acquired_at"])) >= float(data["ttl_sec"])
                except (OSError, ValueError, KeyError, TypeError):
                    expired = True
                if expired:
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError:
                        pass
        return removed
