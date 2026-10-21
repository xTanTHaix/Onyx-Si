"""
Tier-0 In-RAM Ghost-CoW Overlay.
Zero-disk-write simulation layer backed by PieceTableDocument and tombstones.

Guarantees:
  * while a session is active, no write ever reaches the real filesystem
  * ``ghost_commit()`` is crash-safe: every file is written to a temporary file
    in the destination directory, fsynced, then ``os.replace``-d into place
  * if any write or delete fails mid-commit, every already-touched path is
    restored from its in-memory backup before the error propagates
  * workspace containment is enforced on every path (traversal + symlink)

On top of that single-branch model the overlay can hold several *branches* of
a speculative edit at once. Each branch owns its own set of documents and
tombstones; forking clones the active branch in RAM, scoring ranks the
candidates, and merging keeps the winner alive so it can be committed.
"""

import ast
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from onyxsi.contextfs.piece_table import PieceTableDocument
from onyxsi.contextfs.bitplane_ast import BitplaneAstScanner
from onyxsi.contracts import OnyxError, OnyxErrorCode

_SAFE_BRANCH_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class GhostError(OnyxError):
    """Base ghost-overlay error (OnyxErrorCode.E3002 by default)."""

    def __init__(self, message: str, code: OnyxErrorCode = OnyxErrorCode.E3002, details=None):
        super().__init__(code, message, details)


class GhostSyntaxError(GhostError):
    """Syntax gate rejected the staged payload (OnyxErrorCode.E3001)."""

    def __init__(self, message: str, code: OnyxErrorCode = OnyxErrorCode.E3001, details=None):
        super().__init__(message, code, details)


class GhostCommitError(GhostError):
    """Atomic commit failed and rollback ran (OnyxErrorCode.E3005)."""

    def __init__(self, message: str, code: OnyxErrorCode = OnyxErrorCode.E3005, details=None):
        super().__init__(message, code, details)


@dataclass(slots=True)
class _Branch:
    """
    One speculative line of attack held entirely in RAM.

    ``docs`` and ``tombstones`` are the *same objects* the overlay exposes as
    the active window, so a mutation is visible to the branch that owns it
    without any copying or write-back.
    """

    branch_id: str
    parent: Optional[str] = None
    hypothesis: str = ""
    docs: Dict[str, PieceTableDocument] = field(default_factory=dict)
    tombstones: Set[str] = field(default_factory=set)
    score: Optional[float] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class GhostOverlayFS:
    """
    In-memory copy-on-write filesystem simulation layer.
    Allows speculative edits, test compilation, and syntax validation without touching disk.
    """

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = os.path.realpath(workspace_root)
        self._active = False
        self._session_id: Optional[str] = None
        self._lock = threading.Lock()

        self._branches: Dict[str, _Branch] = {}
        self._active_branch: str = "main"
        main = _Branch(branch_id="main")
        self._branches["main"] = main
        # The active window *is* the active branch's storage. Rebinding these
        # two names is what switching a branch actually means.
        self._modified_docs: Dict[str, PieceTableDocument] = main.docs
        self._tombstones: Set[str] = main.tombstones

        # Antibodies distilled during the last commit; surfaced instead of
        # swallowed so a failed auto-vaccine is never invisible.
        self.last_antibody_errors: List[str] = []

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def ghost_begin(self, session_id: Optional[str] = None) -> str:
        """Starts a new in-RAM speculative simulation session."""
        with self._lock:
            self._session_id = session_id or str(uuid.uuid4())[:8]
            self._branches = {}
            main = _Branch(branch_id="main")
            self._branches["main"] = main
            self._active_branch = "main"
            self._modified_docs = main.docs
            self._tombstones = main.tombstones
            self._active = True
            return self._session_id

    def ghost_mutate(self, path: str, new_content: Union[str, bytes]) -> None:
        """Applies speculative modification to a file in RAM."""
        canonical = self._canonicalize(path)
        with self._lock:
            if not self._active:
                raise GhostError("Ghost overlay session not active. Call ghost_begin() first.")
            self._tombstones.discard(canonical)
            self._modified_docs[canonical] = PieceTableDocument(new_content)

    def ghost_read(self, path: str) -> Optional[bytes]:
        """Reads file content from Ghost Overlay in RAM or falls back to disk."""
        canonical = self._canonicalize(path)
        with self._lock:
            if canonical in self._tombstones:
                return None
            if canonical in self._modified_docs:
                return self._modified_docs[canonical].get_bytes()

        if os.path.exists(canonical) and not os.path.isdir(canonical):
            try:
                with open(canonical, "rb") as f:
                    return f.read()
            except OSError:
                return None
        return None

    def ghost_read_text(self, path: str) -> Optional[str]:
        b = self.ghost_read(path)
        return b.decode("utf-8", errors="replace") if b is not None else None

    def ghost_delete(self, path: str) -> None:
        """Marks a file deleted in RAM (tombstone)."""
        canonical = self._canonicalize(path)
        with self._lock:
            if not self._active:
                raise GhostError("Ghost overlay session not active.")
            self._modified_docs.pop(canonical, None)
            self._tombstones.add(canonical)

    def ghost_exists(self, path: str) -> bool:
        """Checks if file exists within the combined Ghost overlay + disk view."""
        canonical = self._canonicalize(path)
        with self._lock:
            if canonical in self._tombstones:
                return False
            if canonical in self._modified_docs:
                return True
        return os.path.exists(canonical)

    def modified_paths(self) -> List[str]:
        """Canonical paths currently staged in the active branch (read-only view)."""
        with self._lock:
            return sorted(set(self._modified_docs) | set(self._tombstones))

    # ------------------------------------------------------------------ branches
    @staticmethod
    def _validate_branch_id(branch_id: str) -> str:
        candidate = str(branch_id or "").strip()
        if not _SAFE_BRANCH_ID.fullmatch(candidate) or candidate in (".", ".."):
            raise GhostError(
                f"Invalid branch id {branch_id!r}: expected 1-64 chars of [A-Za-z0-9_.-]"
            )
        return candidate

    def ghost_fork(
        self,
        branch_id: str,
        from_branch: Optional[str] = None,
        hypothesis: str = "",
    ) -> str:
        """
        Clones a branch into a new speculative line of attack, entirely in RAM.

        The clone is deep enough that editing the new branch cannot corrupt the
        one it was forked from. The new branch becomes the active one so the
        agent can keep editing straight away.
        """
        with self._lock:
            if not self._active:
                raise GhostError("Ghost overlay session not active. Call ghost_begin() first.")

            new_id = self._validate_branch_id(branch_id)
            source_id = from_branch or self._active_branch
            source = self._branches.get(source_id)
            if source is None:
                raise GhostError(f"Unknown branch: {source_id!r}")
            if new_id in self._branches:
                raise GhostError(f"Branch {new_id!r} already exists in this session")

            clone = _Branch(
                branch_id=new_id,
                parent=source_id,
                hypothesis=str(hypothesis or ""),
                docs={
                    path: PieceTableDocument(doc.get_bytes())
                    for path, doc in source.docs.items()
                },
                tombstones=set(source.tombstones),
            )
            self._branches[new_id] = clone
            self._active_branch = new_id
            self._modified_docs = clone.docs
            self._tombstones = clone.tombstones
            return new_id

    def ghost_switch_branch(self, branch_id: str) -> None:
        """
        Makes another branch the active one.

        The overlay keeps exactly one live window, so switching rebinds that
        window onto the target branch's own storage. Nothing is copied, and
        the branch left behind keeps every edit it had.
        """
        with self._lock:
            target = self._branches.get(str(branch_id))
            if target is None:
                raise GhostError(f"Unknown branch: {branch_id!r}")
            self._active_branch = target.branch_id
            self._modified_docs = target.docs
            self._tombstones = target.tombstones

    def ghost_list_branches(self) -> List[Dict[str, Any]]:
        """Summarises every branch held in RAM."""
        with self._lock:
            return [
                {
                    "branch_id": branch.branch_id,
                    "parent": branch.parent,
                    "hypothesis": branch.hypothesis,
                    "active": branch.branch_id == self._active_branch,
                    "modified_files": len(branch.docs) + len(branch.tombstones),
                    "score": branch.score,
                    "metrics": dict(branch.metrics),
                    "created_at": branch.created_at,
                }
                for branch in self._branches.values()
            ]

    def _branch_diff_bytes(self, branch: _Branch) -> int:
        """Total byte-size delta between a branch's staged content and disk."""
        total = 0
        for path, doc in branch.docs.items():
            staged = len(doc.get_bytes())
            disk = len(self._read_bytes_if_exists(path) or b"")
            total += abs(staged - disk)
        for path in branch.tombstones:
            total += len(self._read_bytes_if_exists(path) or b"")
        return total

    def ghost_score_branch(
        self,
        branch_id: str,
        passed_tests: int,
        total_tests: int,
        drift: float = 0.0,
    ) -> float:
        """
        Ranks a branch and stores the score.

        The score rewards the test pass ratio, penalises causal drift, and
        caps the penalty for a large diff so a big but correct patch is never
        buried under a big but wrong one.
        """
        with self._lock:
            branch = self._branches.get(str(branch_id))
            if branch is None:
                raise GhostError(f"Unknown branch: {branch_id!r}")

            diff_bytes = self._branch_diff_bytes(branch)
            score = (
                (float(passed_tests) / max(1, int(total_tests))) * 100.0
                - (float(drift) * 50.0)
                - min(10.0, diff_bytes / 10000.0)
            )
            branch.score = score
            branch.metrics = {
                "passed_tests": int(passed_tests),
                "total_tests": int(total_tests),
                "drift": float(drift),
                "diff_bytes": diff_bytes,
            }
            return score

    def ghost_merge_best_branch(
        self,
        candidate_branch_ids: Optional[List[str]] = None,
    ) -> Tuple[str, float]:
        """
        Keeps the best-scoring branch and discards the losers.

        The winner stays active with its content intact, so the caller can
        still commit it to disk. Every other branch is released from RAM.
        """
        with self._lock:
            if not self._active:
                raise GhostError("Ghost overlay session not active. Call ghost_begin() first.")

            if candidate_branch_ids is None:
                candidates = [b for b in self._branches.values() if b.score is not None]
            else:
                candidates = []
                for branch_id in candidate_branch_ids:
                    branch = self._branches.get(str(branch_id))
                    if branch is None:
                        raise GhostError(f"Unknown branch: {branch_id!r}")
                    candidates.append(branch)

            if not candidates:
                raise GhostError(
                    "No scored branch to merge: score at least one branch before merging."
                )

            # Highest score wins; created_at then branch_id break ties so the
            # result never depends on dict ordering.
            best = sorted(
                candidates,
                key=lambda b: (
                    -(b.score if b.score is not None else float("-inf")),
                    b.created_at,
                    b.branch_id,
                ),
            )[0]

            self._active_branch = best.branch_id
            self._modified_docs = best.docs
            self._tombstones = best.tombstones

            for branch_id in list(self._branches):
                if branch_id != best.branch_id:
                    del self._branches[branch_id]

            return (best.branch_id, float(best.score if best.score is not None else 0.0))

    # ------------------------------------------------------------------ commit
    def ghost_commit(
        self,
        validate_syntax: bool = True,
        antibody_memory: Optional[Any] = None,
        context_hint: str = "Bug fix commit",
    ) -> Dict[str, int]:
        """
        Commits speculative in-RAM modifications to disk atomically per file.

        Ordering: syntax gate -> write new files -> delete tombstones.
        Any failure triggers a full restore of every path already touched.

        When an ``antibody_memory`` is supplied, the before/after text of every
        committed Python file is handed to it so the fix becomes a permanent
        rule. That happens only after the write loop succeeded: a rolled-back
        commit must not leave a rule behind for code that never landed.
        """
        with self._lock:
            if not self._active:
                raise GhostError("Ghost overlay session not active.")

            if validate_syntax:
                scanner = BitplaneAstScanner()
                for path, doc in self._modified_docs.items():
                    content_bytes = doc.get_bytes()
                    content_str = doc.get_text()

                    if path.endswith(".py"):
                        try:
                            ast.parse(content_str, filename=path)
                        except SyntaxError as e:
                            self._discard_unlocked()
                            raise GhostSyntaxError(
                                f"Syntax validation failed on {os.path.basename(path)}: {e}"
                            )
                    elif path.endswith((".rs", ".ts", ".js", ".go", ".c", ".cpp", ".java")):
                        is_balanced, _, _ = scanner.analyze_source_topology(content_bytes)
                        if not is_balanced:
                            self._discard_unlocked()
                            raise GhostSyntaxError(
                                f"Brace balance validation failed on {os.path.basename(path)}"
                            )

            results: Dict[str, int] = {}
            # path -> original bytes (None when the path did not exist)
            backups: Dict[str, Optional[bytes]] = {}
            # (baseline_text, fixed_text, path) collected for the auto-vaccine.
            vaccine_candidates: List[Tuple[str, str, str]] = []

            try:
                for path, doc in self._modified_docs.items():
                    original = self._read_bytes_if_exists(path)
                    backups[path] = original
                    data = doc.get_bytes()
                    self._atomic_write(path, data)
                    results[path] = len(data)

                    if antibody_memory is not None and path.endswith(".py"):
                        baseline_text = (original or b"").decode("utf-8", errors="replace")
                        fixed_text = data.decode("utf-8", errors="replace")
                        if baseline_text.strip() and baseline_text != fixed_text:
                            vaccine_candidates.append((baseline_text, fixed_text, path))

                for path in self._tombstones:
                    if os.path.exists(path):
                        backups[path] = self._read_bytes_if_exists(path)
                        os.remove(path)
                        results[path] = -1

            except Exception as exc:
                rollback_failures = self._restore_backups(backups)
                detail = ""
                if rollback_failures:
                    detail = f"; ROLLBACK INCOMPLETE for {rollback_failures}"
                self._discard_unlocked()
                raise GhostCommitError(f"Atomic commit failed: {exc}{detail}") from exc

            # The payload is on disk. Distil the antibodies now.
            self.last_antibody_errors = []
            for baseline_text, fixed_text, path in vaccine_candidates:
                try:
                    antibody_memory.synthesize_from_ast_diff(
                        baseline_code=baseline_text,
                        fixed_code=fixed_text,
                        file_path=path,
                        context_hint=context_hint,
                    )
                except Exception as exc:  # noqa: BLE001 - the file is already committed
                    self.last_antibody_errors.append(f"{os.path.basename(path)}: {exc}")

            self._discard_unlocked()
            return results

    @staticmethod
    def _read_bytes_if_exists(path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    @staticmethod
    def _atomic_write(path: str, data: bytes) -> None:
        """Write ``data`` to ``path`` via tempfile + fsync + os.replace."""
        dir_name = os.path.dirname(path) or "."
        os.makedirs(dir_name, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".ghost_commit_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _restore_backups(self, backups: Dict[str, Optional[bytes]]) -> List[str]:
        """Best-effort restore of every touched path. Returns paths that failed."""
        failures: List[str] = []
        for path, original in backups.items():
            try:
                if original is None:
                    if os.path.exists(path):
                        os.remove(path)
                else:
                    self._atomic_write(path, original)
            except OSError:
                failures.append(path)
        return failures

    def ghost_discard(self) -> None:
        """Aborts speculative edits instantly (~0.0009ms in RAM). Disk remains untouched."""
        with self._lock:
            self._discard_unlocked()

    def _discard_unlocked(self) -> None:
        self._active = False
        self._session_id = None
        self._modified_docs.clear()
        self._tombstones.clear()
        self._branches.clear()
        self._active_branch = ""

    def _canonicalize(self, path: str) -> str:
        if not os.path.isabs(path):
            resolved = os.path.realpath(os.path.join(self.workspace_root, path))
        else:
            resolved = os.path.realpath(path)

        if resolved != self.workspace_root and not resolved.startswith(self.workspace_root + os.sep):
            raise GhostError(f"Path traversal outside workspace root blocked: {path} -> {resolved}")
        return resolved
