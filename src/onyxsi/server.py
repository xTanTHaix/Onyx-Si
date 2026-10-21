"""
OnyxSi Phase 7.1: Turnkey Headless Black-Box Daemon Loop.

Unified daemon linking all 6 subsystem layers into a pure headless engine.
Communicates via JSON-RPC 2.0 over standard I/O (stdio).

Honesty contract (what this file does NOT do):
  * no meta-tool returns a fabricated constant result; unimplementable
    operations return an explicit ``status: "error"`` payload
  * every filesystem path passes through ``PolicyGate`` (traversal + symlink)
  * every tool argument is validated against the declared inputSchema
  * ``causal_flight`` stage=verify re-parses the staged files for real
  * ``causal_flight`` stage=bisect really binary-searches checkpoints

100% Python Standard Library only. Zero External Dependencies. Zero UI.
"""

import ast
import atexit
import json
import os
import shlex
import shutil
import signal
import sys
import tempfile
import textwrap
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from onyxsi.gateway.meta_router import (
    FIXED_8_META_TOOLS,
    MAX_FRAME_BYTES,
    MAX_SCHEMA_TOKENS,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    PARSE_ERROR,
    GatewayError,
    InvalidParamsError,
    MethodNotFoundError,
    MetaToolRouter,
    ToolNotBoundError,
)
from onyxsi.gateway.grammar_shield import GrammarShield
from onyxsi.gateway.stream_folder import StreamFolder
from onyxsi.gateway.policy_gate import PolicyGate, PolicyGateError
from onyxsi.gateway.pipeline_fusion import MacroPipelineExecutor, PipelineStep

from onyxsi.hal.telemetry import ToolTelemetry

from onyxsi.knowledge.task_wiki import (
    TaskWikiManager,
    TaskWikiError,
    TaskNotFoundError,
)
from onyxsi.knowledge.csr_graph_bm25 import HybridSearchEngine
from onyxsi.knowledge.blast_test_runner import BlastRadiusTestRunner
from onyxsi.knowledge.compute_alu import (
    eval_math,
    MatrixALU,
    IEEE754Inspector,
    ComputeAluError,
)

from onyxsi.contextfs.vfs_scanner import VfsScanner
from onyxsi.contextfs.bitplane_ast import BitplaneAstScanner, BitplaneAstPruner
from onyxsi.contextfs.token_governor import (
    TokenGovernor,
    TokenCeilingError,
    DEFAULT_TOKEN_CEILING,
)
from onyxsi.contextfs.virtual_handles import VirtualHandleRegistry
from onyxsi.contextfs.piece_table import PieceTableDocument

from onyxsi.deltafs.ghost_overlay import GhostOverlayFS, GhostError
from onyxsi.deltafs.ast_lease_registry import AstLeaseRegistry, AstLeaseConflictError
from onyxsi.deltafs.ast_conflict_merger import (
    AgentPatch,
    ConflictFreeAstMerger,
    AstMergeConflictError,
)
from onyxsi.deltafs.cow_workspace import DualOsCowWorkspace
from onyxsi.contracts import OnyxError

from onyxsi.hal.process_manager import (
    ProcessManager,
    ProcessConfig,
    ProcessManagerError,
)
from onyxsi.hal.sandbox_enclave import SandboxEnclave, SandboxConfig, SandboxError
from onyxsi.hal.syscall_guard import SyscallGuard, SyscallGuardConfig
from onyxsi.hal.trie_index import CompactRadixSymbolIndex
from onyxsi.hal.wakeup import WakeupConfig, WakeupSignal
from onyxsi.hal.shm_superblock import SharedMemorySuperblock

from onyxsi.causal.micro_wal import MicroWAL
from onyxsi.auditor import ZeroDepAndZeroUiAuditor

from onyxsi.causal.trajectory import (
    CausalTrajectoryEnforcer,
    FlightStage,
    PreFlightRequiredError,
    TrajectoryDriftAbort,
    VerificationRequiredError,
)
from onyxsi.causal.error_budget import (
    ErrorBudgetTracker,
    ErrorBudgetError,
    MutateLockedByBudgetError,
)
from onyxsi.causal.hypothesis_engine import HypothesisEngine, HypothesisBranch
from onyxsi.causal.antibody_memory import AntibodyMemory, AntibodyRejectionError
from onyxsi.causal.causal_bisect import CausalBisectEngine, BisectStep


# Boot self-check: the kernel audits *its own* package once per process. The
# workspace being served is a user project and may legitimately import anything,
# so it is deliberately out of scope.
_BOOT_AUDIT_CACHE: Optional[Dict[str, Any]] = None
_BOOT_AUDIT_LOCK = threading.Lock()


# One telemetry superblock per process. The daemon hosts one workspace per
# process, so a per-process mirror is both sufficient and bounded (a per-server
# name would leak a /dev/shm file for every short-lived test server).
_TELEMETRY_SHM: Optional[SharedMemorySuperblock] = None
_TELEMETRY_SHM_NAME: Optional[str] = None
_TELEMETRY_SHM_ERROR: Optional[str] = None
_TELEMETRY_SHM_LOCK = threading.RLock()
_TELEMETRY_SHM_REGISTERED = False


def _telemetry_superblock() -> Tuple[Optional[SharedMemorySuperblock], str]:
    global _TELEMETRY_SHM, _TELEMETRY_SHM_NAME, _TELEMETRY_SHM_ERROR
    global _TELEMETRY_SHM_REGISTERED
    with _TELEMETRY_SHM_LOCK:
        if _TELEMETRY_SHM_NAME is None:
            _TELEMETRY_SHM_NAME = f"onyxsi_tel_{os.getpid()}"
        if _TELEMETRY_SHM is None:
            try:
                _TELEMETRY_SHM = SharedMemorySuperblock(
                    size=1024 * 1024, name=_TELEMETRY_SHM_NAME
                ).initialize()
                _TELEMETRY_SHM_ERROR = None
                if not _TELEMETRY_SHM_REGISTERED:
                    atexit.register(_close_telemetry_superblock)
                    _TELEMETRY_SHM_REGISTERED = True
            except Exception as exc:  # noqa: BLE001 - shared memory is optional
                _TELEMETRY_SHM = None
                _TELEMETRY_SHM_ERROR = f"{type(exc).__name__}: {exc}"
        return _TELEMETRY_SHM, _TELEMETRY_SHM_NAME


def _close_telemetry_superblock() -> None:
    global _TELEMETRY_SHM
    with _TELEMETRY_SHM_LOCK:
        if _TELEMETRY_SHM is not None:
            try:
                _TELEMETRY_SHM.close()
            except Exception:  # noqa: BLE001 - exit path
                pass
            _TELEMETRY_SHM = None


def _boot_audit_summary() -> Dict[str, Any]:
    global _BOOT_AUDIT_CACHE
    with _BOOT_AUDIT_LOCK:
        if _BOOT_AUDIT_CACHE is not None:
            return _BOOT_AUDIT_CACHE
        package_root = os.path.dirname(os.path.abspath(__file__))
        try:
            audit = ZeroDepAndZeroUiAuditor(package_root).audit_directory()
            summary: Dict[str, Any] = {
                "passed": audit.passed,
                "files_scanned": audit.files_scanned,
                "violations": len(audit.violations),
                "scope": "onyxsi package",
            }
        except Exception as exc:  # noqa: BLE001 - a failed audit must not block boot
            summary = {
                "passed": False,
                "files_scanned": 0,
                "violations": 0,
                "scope": "onyxsi package",
                "error": str(exc),
            }
        _BOOT_AUDIT_CACHE = summary
        return summary


class OnyxSiServer:
    """
    Turnkey Headless Daemon serving the Fixed 8-Meta-Tool surface.
    Zero-interactive UI, pure JSON-RPC 2.0 communication.
    """

    def __init__(
        self,
        workspace_root: Optional[str] = None,
        require_pre_flight: bool = True,
        telemetry_out: Optional[str] = None,
        max_index_files: int = 5000,
        max_index_file_bytes: int = 2_097_152,
        max_index_total_bytes: int = 134_217_728,
        max_shadow_bytes: int = 512 * 1024 * 1024,
        project_name: Optional[str] = None,
    ):
        self.workspace_root = os.path.realpath(workspace_root or os.getcwd())
        self.running: bool = False
        self.require_pre_flight: bool = require_pre_flight
        self.telemetry_out = telemetry_out
        self.max_index_files = max(1, int(max_index_files))
        self.max_index_file_bytes = max(1024, int(max_index_file_bytes))
        self.max_index_total_bytes = max(1024, int(max_index_total_bytes))
        self.max_shadow_bytes = max(16 * 1024 * 1024, int(max_shadow_bytes))
        self._shutdown_event = threading.Event()
        self._tool_lock = threading.RLock()

        # HAL telemetry: every tool invocation lands in an anonymous SPSC ring
        # buffer. File-free by default; flushed only when telemetry_out is set.
        try:
            self.telemetry: Optional[ToolTelemetry] = ToolTelemetry()
        except Exception:  # noqa: BLE001 - telemetry must never block startup
            self.telemetry = None

        # Flight recorder (last 64 actions, used for CRASH_REPRO.md) + boot audit.
        self.wal = MicroWAL()
        self.boot_audit = _boot_audit_summary()
        self._call_count = 0
        self._error_count = 0
        self._last_tool = ""
        self._wakeup: Optional[WakeupSignal] = None

        # Cross-process telemetry mirror: a process-wide 1MB named superblock
        # whose slab holds the latest counters as JSON. A second process can
        # attach by name. Bounded to one file per daemon process.
        self.shm, self._shm_name = _telemetry_superblock()

        # Gateway & Protocol
        self.router = MetaToolRouter(observer=self._observe_tool)
        self.shield = GrammarShield()
        self.folder = StreamFolder()
        self.policy_gate = PolicyGate(workspace_root=self.workspace_root)

        # Knowledge & Task Wiki
        #
        # Task state and antibody rules are namespaced per project, so one
        # workspace never mixes two projects' task ids or immune rules.
        self.project_name = TaskWikiManager._sanitize_project_name(
            project_name or os.path.basename(self.workspace_root)
        )
        self.task_wiki = TaskWikiManager(self.workspace_root, project_name=self.project_name)
        self._task_wiki_managers: Dict[str, TaskWikiManager] = {self.project_name: self.task_wiki}
        self.search_engine = HybridSearchEngine()
        self.blast_runner = BlastRadiusTestRunner(self.search_engine.graph)

        # ContextFS & DeltaFS
        self.scanner = VfsScanner()
        self.ast_scanner = BitplaneAstScanner()
        self.ast_pruner = BitplaneAstPruner()
        self.handles = VirtualHandleRegistry()
        self.token_gov = TokenGovernor(handle_registry=self.handles)
        self.ghost_overlay = GhostOverlayFS(self.workspace_root)
        self.lease_registry = AstLeaseRegistry(
            lock_dir=os.path.join(self.workspace_root, ".onyxsi", "leases")
        )

        # HAL: process supervision + policy layers used by `ghost_sandbox test`.
        self.process_manager = ProcessManager()
        self.sandbox = SandboxEnclave(
            SandboxConfig(workspace_root=self.workspace_root, allow_file_system=True)
        )
        self.syscall_guard = SyscallGuard(SyscallGuardConfig())
        self.cow = DualOsCowWorkspace(
            spillover_dir=os.path.join(self.workspace_root, ".onyxsi", "spill")
        )

        # Causal Engine
        self.trajectory = CausalTrajectoryEnforcer(
            workspace_root=self.workspace_root,
            ghost_fs=self.ghost_overlay,
        )
        self.error_budget = ErrorBudgetTracker()
        self.hypothesis_engine = HypothesisEngine()
        self.antibody_path = os.path.join(
            self.workspace_root,
            ".onyxsi",
            "wiki",
            "projects",
            self.project_name,
            "ANTIBODIES.json",
        )
        self.antibody_memory = AntibodyMemory(
            workspace_root=self.workspace_root, project_name=self.project_name
        )

        # Causal bisect state: a manifest document holding one checkpoint per applied patch.
        self._flight_doc = PieceTableDocument(b"")
        self._flight_steps: List[BisectStep] = []
        self._flight_baseline_cp: Optional[int] = None
        self._orchestrate_depth = 0
        self._index_ready = False
        self._indexed_files = 0
        self._index_signature: Optional[Tuple[Tuple[str, int, int], ...]] = None
        self._index_lock = threading.Lock()
        # Per-file parse cache: full_path -> (mtime_ns, size, rel, module_text, symbols).
        # A rebuild only re-parses files whose signature changed.
        self._index_cache: Optional[Dict[str, Tuple[int, int, str, str, List[Tuple[str, str, int, List[str]]]]]] = {}
        self._symbol_locations: Dict[str, List[str]] = {}
        self.symbol_index = CompactRadixSymbolIndex()
        self._last_parse_stats: Tuple[int, int] = (0, 0)

        # Wire up all 8 Meta-Tools
        self._bind_handlers()
        self._setup_signals()

    # ------------------------------------------------------------------ signals
    def _setup_signals(self) -> None:
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
            if hasattr(signal, "SIGBREAK"):  # Windows console break
                signal.signal(signal.SIGBREAK, self._handle_signal)
        except (ValueError, OSError):
            # Signal registration is only legal on the main thread / main interpreter.
            pass

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.shutdown()

    def _bind_handlers(self) -> None:
        """Binds internal subsystem logic to the Fixed 8 Meta-Tools."""
        self.router.register_handler("vfs_inspect", self._handle_vfs_inspect)
        self.router.register_handler("vfs_mutate", self._handle_vfs_mutate)
        self.router.register_handler("ghost_sandbox", self._handle_ghost_sandbox)
        self.router.register_handler("causal_flight", self._handle_causal_flight)
        self.router.register_handler("hypothesis_branch", self._handle_hypothesis_branch)
        self.router.register_handler("wiki_task_sync", self._handle_wiki_task_sync)
        self.router.register_handler("graph_compute", self._handle_graph_compute)
        self.router.register_handler("mcp_orchestrate", self._handle_mcp_orchestrate)

    # ------------------------------------------------------------------ helpers
    def _resolve(self, target: str) -> str:
        """Resolves target against the workspace, raising on traversal."""
        _, full_path = self.policy_gate.resolve_path(target or ".")
        return full_path

    def _read_current(self, full_path: str) -> Optional[str]:
        """Reads the current view: Ghost overlay first, then disk."""
        if self.ghost_overlay.is_active():
            staged = self.ghost_overlay.ghost_read_text(full_path)
            if staged is not None:
                return staged
        if os.path.isfile(full_path):
            with open(full_path, "rb") as f:
                return f.read().decode("utf-8", errors="replace")
        return None

    # ---------------------------------------------------------------- telemetry
    def _observe_tool(
        self, tool: str, ok: bool, duration_us: int, error: Optional[str] = None
    ) -> None:
        """Router observer: SPSC telemetry + counters mirrored into shared memory."""
        self._call_count += 1
        self._last_tool = tool
        if not ok:
            self._error_count += 1
        if self.telemetry is not None:
            self.telemetry.record(tool, ok, duration_us, error)
        self._mirror_shm()

    def _mirror_shm(self) -> None:
        if self.shm is None:
            return
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "total": self._call_count,
                "errors": self._error_count,
                "last_tool": self._last_tool,
                "updated_at": time.time(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > 4096:
            return
        # NUL-pad the fixed-size record: a shorter write must not leave the tail
        # of a previous, longer record behind for readers to parse.
        record = payload + b"\x00" * (4096 - len(payload))
        with _TELEMETRY_SHM_LOCK:
            try:
                self.shm.write_slab(0, record)
            except Exception:  # noqa: BLE001 - mirror must never break a call
                pass

    def _read_shm_snapshot(self) -> Dict[str, Any]:
        if self.shm is None:
            return {
                "available": False,
                "reason": "superblock unavailable",
                "init_error": _TELEMETRY_SHM_ERROR,
            }
        with _TELEMETRY_SHM_LOCK:
            try:
                raw = self.shm.read_slab(0, 4096).split(b"\x00", 1)[0]
                snapshot: Dict[str, Any] = (
                    json.loads(raw.decode("utf-8")) if raw else {}
                )
                snapshot["available"] = True
                snapshot["name"] = self._shm_name
                snapshot["header_created_at"] = self.shm.get_header().created_at
                return snapshot
            except Exception as exc:  # noqa: BLE001 - read-back is diagnostic only
                return {"available": False, "error": str(exc)}

    @staticmethod
    def _err(code: str, message: str, **extra: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"status": "error", "error": code, "message": message}
        payload.update(extra)
        return payload

    # ---------------------------------------------------------- shadow test runner
    _SHADOW_DENY = frozenset(
        {
            "sudo",
            "su",
            "runas",
            "shutdown",
            "reboot",
            "halt",
            "poweroff",
            "mkfs",
            "format",
            "diskpart",
            "fdisk",
            "parted",
            "mount",
            "umount",
            "chroot",
            "unshare",
            "setuid",
            "setgid",
            "ptrace",
            "kexec",
            "swapon",
            "swapoff",
        }
    )

    @staticmethod
    def _split_command(command: str) -> List[str]:
        """Splits a command line without a shell (no globbing, pipes or injection)."""
        posix = os.name != "nt"
        tokens = shlex.split(command, posix=posix)
        if not posix:
            cleaned: List[str] = []
            for token in tokens:
                if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
                    token = token[1:-1]
                cleaned.append(token)
            tokens = cleaned
        return tokens

    def _materialize_shadow(self, shadow_root: str) -> int:
        """Copies the workspace into ``shadow_root`` (reflink where the OS allows)."""
        total = 0
        copied = 0
        for root, dirs, names in os.walk(self.workspace_root):
            dirs[:] = sorted(d for d in dirs if d not in self._SHADOW_SKIP_DIRS)
            for name in sorted(names):
                src = os.path.join(root, name)
                if os.path.islink(src) or not os.path.isfile(src):
                    continue
                try:
                    size = os.path.getsize(src)
                except OSError:
                    continue
                total += size
                if total > self.max_shadow_bytes:
                    raise ValueError(
                        f"Shadow copy exceeds {self.max_shadow_bytes} bytes; "
                        "raise --max-shadow-mb or run a narrower command"
                    )
                rel = os.path.relpath(src, self.workspace_root)
                dst = os.path.join(shadow_root, rel)
                if not self.cow.try_reflink_file(src, dst):
                    shutil.copy2(src, dst)
                copied += 1
        return copied

    def _apply_staged_to_shadow(self, shadow_root: str, staged: List[str]) -> int:
        """Writes the Ghost overlay on top of the shadow copy (real workspace untouched)."""
        applied = 0
        for full_path in staged:
            rel = os.path.relpath(full_path, self.workspace_root)
            if rel.startswith(".." + os.sep) or rel == ".." or os.path.isabs(rel):
                continue
            dst = os.path.join(shadow_root, rel)
            payload = self.ghost_overlay.ghost_read(full_path)
            if payload is None:
                try:
                    os.remove(dst)
                except OSError:
                    pass
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(payload)
            applied += 1
        return applied

    def _run_shadow_command(
        self, command: str, timeout_s: int, staged: List[str]
    ) -> Dict[str, Any]:
        try:
            argv = self._split_command(command)
        except ValueError as exc:
            return self._err("INVALID_COMMAND", str(exc))

        if not argv:
            return self._err("INVALID_COMMAND", "command is empty")

        head = os.path.basename(argv[0]).lower()
        if head in self._SHADOW_DENY or self.syscall_guard.is_blocked(head):
            return self._err(
                "POLICY_DENIED",
                f"Command '{argv[0]}' is on the deny list",
            )

        shadow_root = tempfile.mkdtemp(prefix="onyxsi-shadow-")
        try:
            try:
                copied = self._materialize_shadow(shadow_root)
            except ValueError as exc:
                return self._err("SHADOW_TOO_LARGE", str(exc))

            staged_files = self._apply_staged_to_shadow(shadow_root, staged)

            # The command may only see the shadow tree, never the real workspace.
            shadow_sandbox = SandboxEnclave(
                SandboxConfig(workspace_root=shadow_root, allow_file_system=True)
            )
            shadow_sandbox.activate()
            shadow_sandbox.assert_path_allowed(shadow_root, mode="r")

            config = ProcessConfig(
                command=argv[0],
                args=argv[1:],
                working_dir=shadow_root,
                timeout_sec=max(1, int(timeout_s)),
            )
            result = self.process_manager.run_capture(config)
            verdict = (
                "TIMEOUT"
                if result.timed_out
                else ("PASSED" if result.exit_code == 0 else "FAILED")
            )
            return {
                "status": "ok",
                "test_result": verdict,
                "mode": "shadow-workspace",
                "command": command,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": round(result.duration_ms, 3),
                "shadow_files": copied,
                "staged_files": staged_files,
                "stdout_tail": result.stdout[-4000:],
                "stderr_tail": result.stderr[-4000:],
                "enforcement": {
                    "workspace": "path-prefix; the command ran in a temp copy, the real workspace was not written",
                    "syscall": self.syscall_guard.enforcement_backend,
                    "network": "not isolated",
                },
            }
        except (OSError, ProcessManagerError, SandboxError) as exc:
            return self._err("TEST_RUN_FAILED", str(exc))
        finally:
            shutil.rmtree(shadow_root, ignore_errors=True)

    # ------------------------------------------------------------------ indexing
    # Skip-list for the lazy workspace index: vendored/derived trees are noise.
    _INDEX_SKIP_DIRS = frozenset(
        {
            ".git",
            ".hg",
            ".svn",
            "__pycache__",
            ".onyxsi",
            ".benchmarks",
            ".pytest_cache",
            ".mypy_cache",
            ".venv",
            ".venv312",
            "venv",
            "node_modules",
            "dist",
            "build",
        }
    )
    _SHADOW_SKIP_DIRS = _INDEX_SKIP_DIRS

    def _scan_index_candidates(self) -> List[Tuple[str, int, int]]:
        """Collect (path, mtime_ns, size) up to the file/size/total budget."""
        candidates: List[Tuple[str, int, int]] = []
        total_bytes = 0
        for root, dirs, names in os.walk(self.workspace_root):
            dirs[:] = sorted(d for d in dirs if d not in self._INDEX_SKIP_DIRS)
            for name in sorted(names):
                if len(candidates) >= self.max_index_files:
                    return candidates
                if not name.endswith(".py"):
                    continue
                full_path = os.path.join(root, name)
                try:
                    stat = os.stat(full_path)
                except OSError:
                    continue
                if stat.st_size > self.max_index_file_bytes:
                    continue
                if total_bytes + stat.st_size > self.max_index_total_bytes:
                    return candidates
                total_bytes += stat.st_size
                candidates.append((full_path, stat.st_mtime_ns, stat.st_size))
        return candidates

    def _ensure_workspace_index(self) -> int:
        """
        Lazily indexes the workspace for HybridSearchEngine (CSR graph + BM25).

        Two passes so forward references resolve: collect symbols first, then wire
        call/test edges. Parses are cached per file by ``(mtime_ns, size)``, so a
        rebuild after touching one file re-parses only that file. Bounded by file
        count, per-file size, and total bytes. Returns the number of indexed files.
        """
        with self._index_lock:
            candidates = self._scan_index_candidates()
            signature = tuple(candidates)
            if self._index_ready and signature == self._index_signature:
                return self._indexed_files

            from onyxsi.knowledge.csr_graph_bm25 import EDGE_CALLS, EDGE_TESTS

            self.search_engine = HybridSearchEngine()
            self.blast_runner = BlastRadiusTestRunner(self.search_engine.graph)
            self.symbol_index = CompactRadixSymbolIndex()
            self._symbol_locations = {}
            self._indexed_files = 0
            self._index_ready = True

            # (rel_path, module_text, [(sym, sym_text, kind, [called_names])])
            collected: List[
                Tuple[str, str, List[Tuple[str, str, int, List[str]]]]
            ] = []
            new_cache: Dict[
                str, Tuple[int, int, str, str, List[Tuple[str, str, int, List[str]]]]
            ] = {}
            reparsed = 0
            reused = 0

            for full_path, mtime_ns, size in candidates:
                cached = self._index_cache.get(full_path) if self._index_cache is not None else None
                if cached is not None and cached[0] == mtime_ns and cached[1] == size:
                    rel, module_text, per_file = cached[2], cached[3], cached[4]
                    reused += 1
                else:
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                            source = fh.read()
                        tree = ast.parse(source, filename=full_path)
                    except (OSError, SyntaxError, ValueError):
                        continue

                    rel = os.path.relpath(full_path, self.workspace_root).replace(os.sep, "/")
                    module_text = f"{rel} {ast.get_docstring(tree) or ''}"
                    per_file = []
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            kind = CompactRadixSymbolIndex.KIND_FUNCTION
                        elif isinstance(node, ast.ClassDef):
                            kind = CompactRadixSymbolIndex.KIND_CLASS
                        else:
                            continue
                        doc = ast.get_docstring(node) or ""
                        arg_names: List[str] = []
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            arg_names = [a.arg for a in node.args.args]
                        sym = f"{rel}:{node.name}"
                        sym_text = f"{node.name} {' '.join(arg_names)} {doc}"
                        callees: List[str] = []
                        for sub in ast.walk(node):
                            if isinstance(sub, ast.Call):
                                callee = getattr(sub.func, "id", None) or getattr(
                                    sub.func, "attr", None
                                )
                                if isinstance(callee, str):
                                    callees.append(callee)
                        per_file.append((sym, sym_text, kind, callees))
                    reparsed += 1

                new_cache[full_path] = (mtime_ns, size, rel, module_text, per_file)
                collected.append((rel, module_text, per_file))
                self._indexed_files += 1

            all_symbols: Dict[str, str] = {}
            for rel, module_text, per_file in collected:
                self.search_engine.index_node(rel, module_text)
                for sym, sym_text, kind, _callees in per_file:
                    self.search_engine.index_node(sym, sym_text)
                    name = sym.rsplit(":", 1)[-1]
                    all_symbols.setdefault(name, sym)
                    self._symbol_locations.setdefault(name, []).append(rel)
                    try:
                        self.symbol_index.add_symbol(name, 0, kind=kind)
                    except ValueError:
                        # The radix trie is ASCII-only; Unicode identifiers stay
                        # searchable through BM25 but not through the trie.
                        pass

            # Second pass: edges (forward calls + test coverage).
            for _rel, _module_text, per_file in collected:
                for sym, _sym_text, _kind, callees in per_file:
                    for callee in callees:
                        target = all_symbols.get(callee)
                        if target is None or target == sym:
                            continue
                        if sym.rsplit(":", 1)[-1].startswith("test_"):
                            self.search_engine.add_relation(sym, target, EDGE_TESTS)
                        self.search_engine.add_relation(sym, target, EDGE_CALLS)

            try:
                self.search_engine.prepare()
            except Exception as exc:  # noqa: BLE001 - indexing is best-effort, never fatal
                sys.stderr.write(f"[onyxsi:index] Search engine preparation failed: {exc}\n")
                self._index_cache = None
                return self._indexed_files

            self._index_cache = new_cache
            self._last_parse_stats = (reparsed, reused)
            self._index_signature = signature
            return self._indexed_files

    # 1. vfs_inspect
    def _handle_vfs_inspect(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self.handles.cleanup_expired()
        action = str(args.get("action", "tree"))
        target = str(args.get("target", "") or "")
        requested_ceiling = int(args.get("max_tokens", DEFAULT_TOKEN_CEILING))
        max_tokens = max(1, min(requested_ceiling, DEFAULT_TOKEN_CEILING))

        if action == "grammar":
            try:
                gbnf = self.router.export_gbnf()
            except Exception as exc:  # noqa: BLE001 - export must never crash the daemon
                return self._err("GRAMMAR_EXPORT_FAILED", str(exc))
            folded = self.token_gov.enforce_ceiling(
                gbnf, ceiling_tokens=max_tokens, context_hint="vfs_grammar"
            )
            return {
                "action": "grammar",
                "content": folded.text,
                "tokens": folded.emitted_tokens,
                "folded": folded.is_folded,
                "ref": folded.folded_ref,
                "retrievable": folded.retrievable,
            }

        if action == "symbols":
            query = str(args.get("query", "") or target or "").strip()
            if not query:
                return self._err("INVALID_PARAMS", "query is required for action=symbols")
            mode = str(args.get("mode", "exact"))
            if mode not in ("exact", "prefix"):
                return self._err("INVALID_PARAMS", "mode must be exact or prefix")
            self._ensure_workspace_index()
            result = (
                self.symbol_index.lookup(query)
                if mode == "exact"
                else self.symbol_index.prefix_search(query)
            )
            entries = (
                [result.entry]
                if mode == "exact" and result.found and result.entry is not None
                else ([] if mode == "exact" else result.matches)
            )
            return {
                "action": "symbols",
                "query": query,
                "mode": mode,
                "count": len(entries),
                "matches": [
                    {
                        "name": entry.name,
                        "files": self._symbol_locations.get(entry.name, [])[:5],
                    }
                    for entry in entries
                ],
                "time_ms": round(result.time_ms, 4),
                "indexed_files": self._indexed_files,
                "parsed_files": self._last_parse_stats[0],
                "cached_files": self._last_parse_stats[1],
                "ascii_only": True,
            }

        # ref:// handles are resolved from the in-RAM registry, not the filesystem.
        if target.startswith("ref://"):
            try:
                payload = self.handles.resolve_text(target)
            except Exception as exc:  # noqa: BLE001 - expired handle is a normal outcome
                return self._err("HANDLE_UNRESOLVED", str(exc), target=target)
            if action == "resolve":
                return {
                    "action": "resolve",
                    "target": target,
                    "kind": "handle",
                    "exists": True,
                    "chars": len(payload),
                }
            lines = payload.splitlines(keepends=True)
            total_lines = len(lines)
            try:
                start_line = int(args.get("start_line", 1) or 1)
                end_line = int(args.get("end_line", total_lines) or total_lines)
            except (TypeError, ValueError):
                return self._err("INVALID_RANGE", "start_line/end_line must be integers")
            if start_line < 1 or end_line < start_line:
                return self._err(
                    "INVALID_RANGE",
                    f"Invalid line range {start_line}..{end_line}",
                    total_lines=total_lines,
                )
            window = "".join(lines[start_line - 1 : end_line])
            folded = self.token_gov.enforce_ceiling(
                window, ceiling_tokens=max_tokens, context_hint="vfs_handle"
            )
            return {
                "action": "read",
                "target": target,
                "kind": "handle",
                "start_line": start_line,
                "end_line": min(end_line, total_lines),
                "total_lines": total_lines,
                "content": folded.text,
                "tokens": folded.emitted_tokens,
                "folded": folded.is_folded,
                "ref": folded.folded_ref,
                "retrievable": folded.retrievable,
            }

        try:
            full_path = self._resolve(target)
        except PolicyGateError as exc:
            return self._err("PATH_BLOCKED", str(exc), target=target)

        if action == "read":
            if not os.path.isfile(full_path):
                return self._err("NOT_A_FILE", f"Target file '{target}' does not exist", target=target)
            content = self._read_current(full_path)
            if content is None:
                return self._err("UNREADABLE", f"Target file '{target}' could not be read", target=target)
            lines = content.splitlines(keepends=True)
            total_lines = len(lines)
            try:
                start_line = int(args.get("start_line", 1) or 1)
                end_line = int(args.get("end_line", total_lines) or total_lines)
            except (TypeError, ValueError):
                return self._err("INVALID_RANGE", "start_line/end_line must be integers")
            if start_line < 1 or end_line < start_line:
                return self._err(
                    "INVALID_RANGE",
                    f"Invalid line range {start_line}..{end_line}",
                    total_lines=total_lines,
                )
            window = "".join(lines[start_line - 1 : end_line])
            folded = self.token_gov.enforce_ceiling(
                window, ceiling_tokens=max_tokens, context_hint="vfs_read"
            )
            return {
                "action": "read",
                "target": target,
                "kind": "file",
                "start_line": start_line,
                "end_line": min(end_line, total_lines),
                "total_lines": total_lines,
                "content": folded.text,
                "tokens": folded.emitted_tokens,
                "folded": folded.is_folded,
                "ref": folded.folded_ref,
                "retrievable": folded.retrievable,
            }

        if action == "tree":
            if not os.path.isdir(full_path):
                return self._err("NOT_A_DIRECTORY", f"'{target}' is not a directory", target=target)
            summary = self.scanner.scan(root_path=full_path, max_depth=4)
            folded = self.token_gov.enforce_ceiling(
                summary.tree_text, ceiling_tokens=max_tokens, context_hint="vfs_tree"
            )
            return {
                "action": "tree",
                "content": folded.text,
                "tokens": folded.emitted_tokens,
                "folded": folded.is_folded,
                "ref": folded.folded_ref,
                "retrievable": folded.retrievable,
            }

        if action == "skeleton":
            if not os.path.isfile(full_path):
                return self._err("NOT_A_FILE", f"Target file '{target}' does not exist", target=target)
            content = self._read_current(full_path)
            if content is None:
                return self._err("UNREADABLE", f"Target file '{target}' could not be read", target=target)
            skeleton_res = self.ast_pruner.prune_source(
                content, filename=target or "source.py"
            )
            folded = self.token_gov.enforce_ceiling(
                skeleton_res.skeleton_code, ceiling_tokens=max_tokens, context_hint="vfs_skeleton"
            )
            return {
                "action": "skeleton",
                "target": target,
                "content": folded.text,
                "tokens": folded.emitted_tokens,
                "folded": folded.is_folded,
                "ref": folded.folded_ref,
                "retrievable": folded.retrievable,
            }

        if action == "resolve":
            return {
                "action": "resolve",
                "target": target,
                "resolved": full_path,
                "exists": os.path.exists(full_path),
            }

        return self._err("UNKNOWN_ACTION", f"Unknown action: {action}")

    # 2. vfs_mutate
    def _handle_vfs_mutate(self, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.error_budget.check_mutate_allowed()
        except MutateLockedByBudgetError:
            return self._err(
                "ERROR_BUDGET_EXHAUSTED",
                "Error budget is 0. Call causal_flight stage=reflect before mutating.",
            )

        op = str(args.get("op", "replace_file"))
        path = str(args.get("path", ""))
        code = str(args.get("code", ""))
        agent_id = str(args.get("agent_id", "agent-0"))
        node_id = args.get("node_id")

        if self.require_pre_flight and self.trajectory.state.stage == FlightStage.IDLE:
            return self._err(
                "PRE_FLIGHT_REQUIRED",
                "Call causal_flight stage=pre_flight before mutating.",
            )

        try:
            full_path = self._resolve(path)
        except PolicyGateError as exc:
            return self._err("PATH_BLOCKED", str(exc), target=path)

        # Antibody screening
        try:
            self.antibody_memory.check_mutation(path, code)
        except AntibodyRejectionError as are:
            return self._err("ANTIBODY_TRIGGERED", str(are))

        patches = args.get("patches") or []
        if not isinstance(patches, list):
            return self._err("INVALID_PARAMS", "patches must be an array")
        if op not in ("replace_file", "merge_patches") and "code" not in args:
            return self._err("INVALID_PARAMS", f"code is required for op={op}")
        if op == "merge_patches" and not patches:
            return self._err(
                "INVALID_PARAMS", "op=merge_patches requires a non-empty patches array"
            )

        # AST-node lease (only meaningful when an agent targets a specific node).
        # Housekeeping first: expired leases from crashed processes are stealable
        # only after their lockfile is gone.
        self.lease_registry.cleanup_expired()
        lease_node = str(node_id) if node_id else "*"
        try:
            self.lease_registry.acquire_lease(full_path, lease_node, agent_id)
        except AstLeaseConflictError as exc:
            return self._err("LEASE_CONFLICT", str(exc), holder=exc.current_holder)

        try:
            if op == "replace_file":
                new_content = code
            elif op == "merge_patches":
                base = self._read_current(full_path)
                if base is None:
                    return self._err("NOT_A_FILE", f"'{path}' does not exist", target=path)
                parsed: List[AgentPatch] = []
                for index, raw in enumerate(patches):
                    if not isinstance(raw, dict):
                        return self._err(
                            "INVALID_PARAMS", "every patch must be an object", index=index
                        )
                    try:
                        patch = AgentPatch(
                            agent_id=str(raw.get("agent_id", agent_id)),
                            node_id=str(raw.get("node_id", f"patch-{index}")),
                            start_byte=int(raw["start_byte"]),
                            end_byte=int(raw["end_byte"]),
                            new_content=str(raw.get("new_content", "")),
                            new_imports=[
                                str(item) for item in (raw.get("new_imports") or [])
                            ],
                        )
                    except (KeyError, TypeError, ValueError) as exc:
                        return self._err(
                            "INVALID_PARAMS", f"invalid patch {index}: {exc}", index=index
                        )
                    try:
                        self.lease_registry.acquire_lease(
                            full_path, patch.node_id, patch.agent_id
                        )
                    except AstLeaseConflictError as exc:
                        return self._err(
                            "LEASE_CONFLICT",
                            str(exc),
                            holder=exc.current_holder,
                            node_id=patch.node_id,
                        )
                    parsed.append(patch)
                try:
                    new_content = ConflictFreeAstMerger.merge_patches(base, parsed)
                except (AstMergeConflictError, OnyxError, ValueError) as exc:
                    return self._err("MERGE_CONFLICT", str(exc))
            else:
                current = self._read_current(full_path)
                if current is None:
                    return self._err("NOT_A_FILE", f"'{path}' does not exist", target=path)
                new_content = self._apply_edit(
                    current=current,
                    op=op,
                    code=code,
                    node_id=node_id,
                    start_line=args.get("start_line"),
                    end_line=args.get("end_line"),
                )
        except ValueError as exc:
            return self._err("INVALID_EDIT", str(exc), op=op, target=path)

        if not self.ghost_overlay.is_active():
            self.ghost_overlay.ghost_begin()
        self.ghost_overlay.ghost_mutate(full_path, new_content)

        # Track causal drift + snapshot a real checkpoint for bisect.
        try:
            self.trajectory.record_ghost_burn(path)
        except TrajectoryDriftAbort as exc:
            self.ghost_overlay.ghost_discard()
            return self._err("TRAJECTORY_DRIFT_ABORT", str(exc), drift=exc.drift)

        self._snapshot_flight(path, node_id or op)

        return {
            "status": "ok",
            "op": op,
            "path": path,
            "node_id": node_id,
            "bytes": len(new_content),
            "staged_in_ghost": True,
            "checkpoint": self._flight_steps[-1].checkpoint_id if self._flight_steps else None,
        }

    @staticmethod
    def _find_ast_node(tree: ast.AST, dotted_name: str) -> Optional[ast.AST]:
        parts = [p for p in dotted_name.split(".") if p]
        body: List[ast.AST] = list(getattr(tree, "body", []))
        node: Optional[ast.AST] = None
        for part in parts:
            node = next(
                (
                    n
                    for n in body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and n.name == part
                ),
                None,
            )
            if node is None:
                return None
            body = list(getattr(node, "body", []))
        return node

    @staticmethod
    def _reindent(code: str, indent: str) -> str:
        dedented = textwrap.dedent(code).rstrip("\n")
        if not dedented:
            return ""
        lines = dedented.splitlines()
        return "\n".join((indent + ln) if ln.strip() else ln for ln in lines) + "\n"

    def _apply_edit(
        self,
        current: str,
        op: str,
        code: str,
        node_id: Optional[str],
        start_line: Optional[Any],
        end_line: Optional[Any],
    ) -> str:
        lines = current.splitlines(keepends=True)

        if op in ("replace_node", "insert_after"):
            if not node_id:
                raise ValueError(f"op={op} requires node_id (a def/class name such as 'Foo.bar')")
            try:
                tree = ast.parse(current)
            except SyntaxError as exc:
                raise ValueError(f"Target file is not valid Python, cannot locate node: {exc}")
            node = self._find_ast_node(tree, str(node_id))
            if node is None:
                raise ValueError(f"Node '{node_id}' not found in target file")
            start = node.lineno - 1
            end = node.end_lineno or node.lineno
            indent = " " * (len(lines[start]) - len(lines[start].lstrip())) if start < len(lines) else ""
            block = self._reindent(code, indent)
            if op == "replace_node":
                return "".join(lines[:start]) + block + "".join(lines[end:])
            return "".join(lines[:end]) + block + "".join(lines[end:])

        if op == "slice_patch":
            if start_line is None or end_line is None:
                raise ValueError("op=slice_patch requires start_line and end_line")
            s = int(start_line)
            e = int(end_line)
            if s < 1 or e < s or e > len(lines):
                raise ValueError(f"Invalid line range {s}..{e} for a {len(lines)}-line file")
            block = code if code.endswith("\n") else code + "\n"
            return "".join(lines[: s - 1]) + block + "".join(lines[e:])

        raise ValueError(f"Unknown op '{op}'")

    # 3. ghost_sandbox
    def _handle_ghost_sandbox(self, args: Dict[str, Any]) -> Dict[str, Any]:
        cmd = str(args.get("cmd", "test"))
        path = args.get("path")
        content = args.get("content")

        if cmd == "begin":
            session_id = self.ghost_overlay.ghost_begin()
            return {"status": "ok", "mode": "ghost_sandbox_active", "session_id": session_id}

        if cmd == "mutate":
            if not path or content is None:
                return self._err("MISSING_ARGUMENT", "cmd=mutate requires path and content")
            try:
                full_path = self._resolve(str(path))
            except PolicyGateError as exc:
                return self._err("PATH_BLOCKED", str(exc), target=path)
            if not self.ghost_overlay.is_active():
                self.ghost_overlay.ghost_begin()
            self.ghost_overlay.ghost_mutate(full_path, str(content))
            return {"status": "ok", "staged": str(path)}

        if cmd == "commit":
            try:
                committed = self.ghost_overlay.ghost_commit(
                    antibody_memory=self.antibody_memory,
                    context_hint=self.trajectory.state.hypothesis or "Bug fix commit",
                )
            except GhostError as exc:
                return self._err("COMMIT_FAILED", str(exc))
            payload: Dict[str, Any] = {"status": "ok", "committed_files": committed}
            if self.ghost_overlay.last_antibody_errors:
                payload["antibody_errors"] = list(self.ghost_overlay.last_antibody_errors)
            return payload

        if cmd == "fork":
            branch_id = str(args.get("branch_id") or "").strip()
            if not branch_id:
                return self._err("MISSING_ARGUMENT", "cmd=fork requires branch_id")
            from_branch = args.get("from_branch")
            try:
                new_id = self.ghost_overlay.ghost_fork(
                    branch_id,
                    from_branch=str(from_branch) if from_branch else None,
                    hypothesis=str(args.get("hypothesis") or ""),
                )
            except GhostError as exc:
                return self._err("FORK_FAILED", str(exc))
            return {
                "status": "ok",
                "branch_id": new_id,
                "branches": self.ghost_overlay.ghost_list_branches(),
            }

        if cmd == "switch_branch":
            branch_id = str(args.get("branch_id") or "").strip()
            if not branch_id:
                return self._err("MISSING_ARGUMENT", "cmd=switch_branch requires branch_id")
            try:
                self.ghost_overlay.ghost_switch_branch(branch_id)
            except GhostError as exc:
                return self._err("BRANCH_NOT_FOUND", str(exc))
            return {
                "status": "ok",
                "active_branch": branch_id,
                "branches": self.ghost_overlay.ghost_list_branches(),
            }

        if cmd == "list_branches":
            return {
                "status": "ok",
                "branches": self.ghost_overlay.ghost_list_branches(),
            }

        if cmd == "merge_best":
            scores = args.get("scores") or []
            if not isinstance(scores, list):
                return self._err("INVALID_PARAMS", "scores must be an array")
            if not scores:
                return self._err(
                    "MISSING_ARGUMENT",
                    "cmd=merge_best requires a non-empty scores array: "
                    "[{branch_id, passed_tests, total_tests, drift}]",
                )

            # Drift is measured by the causal engine, so an entry that does
            # not carry its own drift inherits the live trajectory value.
            drift_default = self.trajectory.state.last_drift
            scored: List[Dict[str, Any]] = []
            try:
                for entry in scores:
                    if not isinstance(entry, dict):
                        return self._err("INVALID_PARAMS", "every scores entry must be an object")
                    branch_id = str(entry.get("branch_id") or "").strip()
                    if not branch_id:
                        return self._err("INVALID_PARAMS", "every scores entry requires branch_id")
                    passed = int(entry.get("passed_tests", 0) or 0)
                    total = int(entry.get("total_tests", 0) or 0)
                    drift = float(entry.get("drift", drift_default) or 0.0)
                    score = self.ghost_overlay.ghost_score_branch(branch_id, passed, total, drift)
                    scored.append({"branch_id": branch_id, "score": round(score, 4)})
            except (GhostError, TypeError, ValueError) as exc:
                return self._err("BRANCH_SCORING_FAILED", str(exc))

            try:
                winner, score = self.ghost_overlay.ghost_merge_best_branch(
                    [entry["branch_id"] for entry in scored]
                )
            except GhostError as exc:
                return self._err("MERGE_FAILED", str(exc))

            return {
                "status": "ok",
                "winner": winner,
                "score": round(score, 4),
                "scores": scored,
                "branches": self.ghost_overlay.ghost_list_branches(),
                "hint": "The winning branch is active and still in RAM; cmd=commit writes it to disk.",
            }

        if cmd == "discard":
            self.ghost_overlay.ghost_discard()
            return {"status": "ok", "discarded": True}

        if cmd == "test":
            staged = self.ghost_overlay.modified_paths()
            if "command" in args and args.get("command") is not None:
                command = str(args.get("command") or "").strip()
                if not command:
                    return self._err("INVALID_COMMAND", "command is empty")
                timeout_s = int(args.get("timeout_s", 120) or 120)
                return self._run_shadow_command(command, timeout_s, staged)

            if not staged:
                return {
                    "status": "ok",
                    "test_result": "SKIPPED",
                    "reason": "No files staged in the ghost session",
                    "executed_tests": 0,
                }
            target = os.path.basename(staged[0])
            indexed_files = self._ensure_workspace_index()
            result = self.blast_runner.run_differential_tests(target)
            detail = result.to_dict()
            if result.executed_tests == 0:
                verdict = "SKIPPED"
            elif result.failed_tests > 0:
                verdict = "FAILED"
            else:
                verdict = "PASSED"
            return {
                "status": "ok",
                "test_result": verdict,
                "detail": detail,
                "indexed_files": indexed_files,
                "caveat": (
                    "Blast-radius selection uses the last indexed on-disk graph; "
                    "staged ghost content is not re-indexed."
                ),
            }

        return self._err("UNKNOWN_COMMAND", f"Unknown ghost_sandbox command: {cmd}")

    # 4. causal_flight
    def _handle_causal_flight(self, args: Dict[str, Any]) -> Dict[str, Any]:
        stage = str(args.get("stage", "pre_flight"))
        radius = list(args.get("blast_radius", []) or [])
        hypothesis = str(args.get("hypothesis", "Default hypothesis"))

        if stage == "pre_flight":
            self.trajectory.begin_pre_flight(radius or [self._default_radius()], hypothesis)
            self._reset_flight_bisect()
            return {
                "status": "ok",
                "stage": "Stage 1: Pre-Flight Inspection",
                "blast_radius": sorted(self.trajectory.state.expected_blast_radius),
            }

        if stage == "ghost_burn":
            target = str(args.get("target") or (radius[0] if radius else "core"))
            try:
                drift = self.trajectory.record_ghost_burn(target)
            except PreFlightRequiredError as exc:
                return self._err("PRE_FLIGHT_REQUIRED", str(exc))
            except TrajectoryDriftAbort as exc:
                return self._err("TRAJECTORY_DRIFT_ABORT", str(exc), drift=exc.drift)
            return {"status": "ok", "stage": "Stage 2: In-RAM Ghost Burn", "drift": drift}

        if stage == "verify":
            files = self.ghost_overlay.modified_paths()
            checks = [self._syntax_check(p) for p in files]
            ok_all = all(c["ok"] for c in checks) if checks else False
            try:
                self.trajectory.verify_invariants(invariants_passed=ok_all)
            except PreFlightRequiredError as exc:
                return self._err("PRE_FLIGHT_REQUIRED", str(exc))
            except TrajectoryDriftAbort as exc:
                return self._err("TRAJECTORY_DRIFT_ABORT", str(exc), drift=exc.drift)
            except VerificationRequiredError as exc:
                return self._err(
                    "INVARIANT_FAILED",
                    str(exc),
                    files=checks,
                    hint="Fix the staged files or discard the ghost session.",
                )
            return {"status": "ok", "stage": "Stage 3: Invariant Verification", "files": checks}

        if stage == "commit":
            try:
                self.trajectory.commit_payload()
            except (PreFlightRequiredError, VerificationRequiredError) as exc:
                return self._err("VERIFICATION_REQUIRED", str(exc))
            except GhostError as exc:
                return self._err("COMMIT_FAILED", str(exc))
            return {"status": "ok", "stage": "Stage 4: Payload Commit"}

        if stage == "reflect":
            root_cause = str(args.get("root_cause", "")).strip()
            strategy = str(args.get("strategy", "")).strip()
            try:
                remaining = self.error_budget.reflection_gate(root_cause, strategy)
            except ErrorBudgetError as exc:
                return self._err("REFLECTION_REJECTED", str(exc))
            payload: Dict[str, Any] = {
                "status": "ok",
                "budget_reset": True,
                "remaining_budget": remaining,
            }
            # Real HAL telemetry (SPSC ring buffer), not a fabricated counter.
            if self.telemetry is not None:
                payload["telemetry"] = self.telemetry.snapshot(recent=8)
            payload["boot_audit"] = self.boot_audit
            payload["shared_memory"] = self._read_shm_snapshot()
            payload["wal_events"] = len(self.wal.get_recent_events())
            return payload

        if stage == "bisect":
            return self._run_bisect()

        return self._err("UNKNOWN_STAGE", f"Unknown flight stage: {stage}")

    def _default_radius(self) -> str:
        return "core"

    def _reset_flight_bisect(self) -> None:
        self._flight_doc = PieceTableDocument(b"")
        self._flight_steps = []
        self._flight_baseline_cp = self._flight_doc.create_checkpoint()

    def _manifest_bytes(self) -> bytes:
        chunks: List[str] = []
        for path in self.ghost_overlay.modified_paths():
            text = self.ghost_overlay.ghost_read_text(path) or ""
            rel = os.path.relpath(path, self.workspace_root).replace(os.sep, "/")
            chunks.append(f"#--- FILE: {rel} ---\n{text}\n")
        return "".join(chunks).encode("utf-8")

    def _snapshot_flight(self, path: str, node_id: str) -> None:
        """Appends a real checkpoint of the staged-file manifest for bisect."""
        snapshot = self._manifest_bytes()
        self._flight_doc.replace(0, self._flight_doc.total_length(), snapshot)
        cp_id = self._flight_doc.create_checkpoint()
        self._flight_steps.append(
            BisectStep(
                step_index=len(self._flight_steps) + 1,
                checkpoint_id=cp_id,
                node_id=node_id,
                description=f"staged mutation of {path}",
            )
        )

    @staticmethod
    def _manifest_invariant(payload: bytes) -> bool:
        """Invariant: every staged .py file in the manifest parses."""
        text = payload.decode("utf-8", errors="replace")
        current_name = ""
        buffer: List[str] = []
        blocks: List[Tuple[str, str]] = []
        for line in text.splitlines(keepends=True):
            if line.startswith("#--- FILE: "):
                if current_name:
                    blocks.append((current_name, "".join(buffer)))
                current_name = line[len("#--- FILE: "):].strip().rstrip("- ").strip()
                buffer = []
            else:
                buffer.append(line)
        if current_name:
            blocks.append((current_name, "".join(buffer)))

        for name, body in blocks:
            if name.endswith(".py"):
                try:
                    ast.parse(body, filename=name)
                except SyntaxError:
                    return False
        return True

    def _run_bisect(self) -> Dict[str, Any]:
        if not self._flight_steps:
            return self._err(
                "NO_CHECKPOINTS",
                "No staged mutations recorded in this flight; nothing to bisect.",
            )
        report = CausalBisectEngine.bisect_failure(
            self._flight_doc,
            self._flight_steps,
            self._manifest_invariant,
            baseline_checkpoint_id=self._flight_baseline_cp,
        )
        culprit = report.culprit_step
        return {
            "status": "ok",
            "found_culprit": report.found_culprit,
            "culprit_step": culprit.step_index if culprit else None,
            "culprit_node": culprit.node_id if culprit else None,
            "culprit_checkpoint": culprit.checkpoint_id if culprit else None,
            "probes_executed": report.probes_executed,
            "steps_total": len(self._flight_steps),
            "rolled_back_to_checkpoint": report.rolled_back_to_checkpoint,
        }

    def _syntax_check(self, path: str) -> Dict[str, Any]:
        text = self.ghost_overlay.ghost_read_text(path)
        if text is None:
            return {"path": path, "ok": False, "error": "unreadable or deleted"}
        if path.endswith(".py"):
            try:
                ast.parse(text, filename=path)
            except SyntaxError as exc:
                return {"path": path, "ok": False, "error": f"SyntaxError: {exc}"}
            return {"path": path, "ok": True}
        if path.endswith((".rs", ".ts", ".js", ".go", ".c", ".cpp", ".java")):
            balanced, depth, _ = self.ast_scanner.analyze_source_topology(text.encode("utf-8"))
            if not balanced:
                return {"path": path, "ok": False, "error": f"unbalanced braces (max depth {depth})"}
            return {"path": path, "ok": True}
        return {"path": path, "ok": True, "note": "no syntax rule for this extension"}

    # 5. hypothesis_branch
    def _handle_hypothesis_branch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        mode = str(args.get("mode", "register"))
        branches = args.get("branches", []) or []

        if mode == "register":
            branch_objs = [
                HypothesisBranch(
                    branch_id=b.get("branch_id", f"H{idx}"),
                    description=b.get("description", b.get("statement", "")),
                    target_nodes=b.get("target_nodes", []),
                    falsification_probe=b.get("falsification_probe", b.get("probe", "probe")),
                )
                for idx, b in enumerate(branches)
            ]
            try:
                reg_ids = self.hypothesis_engine.register_branches(branch_objs)
            except Exception as exc:  # NonOrthogonal / MissingProbe
                return self._err("HYPOTHESIS_REJECTED", str(exc))
            return {"status": "ok", "registered_hypotheses": reg_ids}

        if mode == "falsify":
            h_id = str(args.get("hypothesis_id", "H0"))
            probe_succeeded = bool(args.get("probe_succeeded", False))
            lesson_text = str(args.get("lesson", "") or "")
            try:
                outcome = self.hypothesis_engine.falsify_branch(
                    h_id,
                    probe_succeeded=probe_succeeded,
                    raw_error_trace=lesson_text,
                    ghost_fs=self.ghost_overlay,
                )
            except Exception as exc:
                return self._err("HYPOTHESIS_REJECTED", str(exc))
            if outcome is None:
                return {"status": "ok", "hypothesis_id": h_id, "verdict": "VERIFIED"}
            return {
                "status": "ok",
                "hypothesis_id": h_id,
                "verdict": "FALSIFIED",
                "lesson": outcome.summary_line,
            }

        if mode == "select":
            branch = self.hypothesis_engine.get_verified_branch()
            if branch is None:
                return self._err(
                    "NO_VERIFIED_BRANCH",
                    "No branch is VERIFIED yet; falsify the dead ones first.",
                )
            return {
                "status": "ok",
                "selected": branch.branch_id,
                "description": branch.description,
                "target_nodes": list(branch.target_nodes),
            }

        return self._err("UNKNOWN_MODE", f"Unknown hypothesis mode: {mode}")

    # 6. wiki_task_sync
    def _task_wiki_for(self, project_name: Optional[str]) -> TaskWikiManager:
        """Returns the task wiki bound to a project, creating it on first use."""
        if not project_name:
            return self.task_wiki
        key = TaskWikiManager._sanitize_project_name(project_name)
        manager = self._task_wiki_managers.get(key)
        if manager is None:
            manager = TaskWikiManager(self.workspace_root, project_name=key)
            self._task_wiki_managers[key] = manager
        return manager

    def _handle_wiki_task_sync(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = str(args.get("task_id", "TASK-001"))
        op = str(args.get("op", "update"))
        status = args.get("status")
        raw_project = args.get("project_name")
        project = str(raw_project).strip() if raw_project else None

        try:
            wiki = self._task_wiki_for(project)
            if op == "init":
                spec = str(args.get("spec", "Task execution specification."))
                tdir = wiki.init_task(task_id, spec)
                return {
                    "status": "ok",
                    "task_dir": tdir,
                    "project_name": wiki.project_name,
                }
            if op == "update":
                progress = wiki.update_progress(task_id, status=status)
                return {
                    "status": "ok",
                    "progress": progress,
                    "project_name": wiki.project_name,
                }
            if op == "handoff":
                sheet = wiki.generate_handoff(task_id)
                return {
                    "status": "ok",
                    "handoff_sheet": sheet,
                    "project_name": wiki.project_name,
                }
            if op == "replay":
                frames = args.get("frames")
                branches = args.get("branches")
                if frames is None:
                    frames = [
                        {
                            "action": ev.action,
                            "target": ev.target,
                            "outcome": (
                                f"{ev.status} — {ev.details}" if ev.details else ev.status
                            ),
                            "timestamp": ev.timestamp,
                        }
                        for ev in self.wal.get_recent_events()
                    ]
                if branches is None:
                    branches = self.ghost_overlay.ghost_list_branches()
                chronicle = wiki.generate_time_travel_replay(
                    task_id, frames=frames, branches=branches
                )
                return {
                    "status": "ok",
                    "path": os.path.join(wiki.task_dir(task_id), "TIME_TRAVEL_REPLAY.md"),
                    "project_name": wiki.project_name,
                    "chronicle": chronicle,
                }
        except TaskNotFoundError as exc:
            return self._err("TASK_NOT_FOUND", str(exc))
        except TaskWikiError as exc:
            return self._err("TASK_WIKI_ERROR", str(exc))

        return self._err("UNKNOWN_OP", f"Unknown wiki op: {op}")

    # 7. graph_compute
    def _handle_graph_compute(self, args: Dict[str, Any]) -> Dict[str, Any]:
        engine = str(args.get("engine", "bm25"))
        query = str(args.get("query", "") or "")

        try:
            if engine == "bm25":
                indexed = self._ensure_workspace_index()
                results = self.search_engine.search(query, top_k=5)
                return {
                    "engine": "bm25",
                    "results": results,
                    "indexed_nodes": self.search_engine.graph.num_nodes(),
                    "indexed_files": indexed,
                }

            if engine == "blast_radius":
                self._ensure_workspace_index()
                blast_res = self.blast_runner.run_differential_tests(query)
                return {"engine": "blast_radius", "result": blast_res.to_dict()}

            if engine == "alu":
                op_type = str(args.get("type", "eval"))
                if op_type == "eval":
                    return {"engine": "alu", "type": "eval", "result": eval_math(query)}
                if op_type == "matrix_matmul":
                    return {
                        "engine": "alu",
                        "type": "matrix_matmul",
                        "result": MatrixALU.matmul(args.get("matrix_a", []), args.get("matrix_b", [])),
                    }
                if op_type == "matrix_inv":
                    return {
                        "engine": "alu",
                        "type": "matrix_inv",
                        "result": MatrixALU.inverse(args.get("matrix", [])),
                    }
                if op_type == "ieee754":
                    return {
                        "engine": "alu",
                        "type": "ieee754",
                        "result": IEEE754Inspector.inspect(float(query), bits=int(args.get("bits", 64))),
                    }
                return self._err("UNKNOWN_ALU_OP", f"Unknown ALU operation: {op_type}")
        except (ComputeAluError, ValueError, ZeroDivisionError, OverflowError, TypeError) as exc:
            return self._err("COMPUTE_ERROR", str(exc), engine=engine)

        return self._err("UNKNOWN_ENGINE", f"Unknown graph_compute engine: {engine}")

    # 8. mcp_orchestrate
    def _handle_mcp_orchestrate(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if self._orchestrate_depth >= 1:
            return self._err("NESTING_FORBIDDEN", "mcp_orchestrate cannot call itself (depth > 1)")

        steps_raw = args.get("steps", []) or []
        steps: List[PipelineStep] = []
        for idx, s in enumerate(steps_raw):
            steps.append(
                PipelineStep(
                    step_id=s.get("id", f"step_{idx}"),
                    tool_name=s.get("tool", "vfs_inspect"),
                    arguments=s.get("arguments", {}),
                    depends_on=s.get("depends_on", []),
                )
            )

        def runner(tool_name: str, step_args: Dict[str, Any]) -> Any:
            if tool_name not in FIXED_8_META_TOOLS:
                raise MethodNotFoundError(tool_name)
            return self.router.invoke(tool_name, step_args)

        self._orchestrate_depth += 1
        try:
            executor = MacroPipelineExecutor(dispatcher=runner)
            result = executor.execute_pipeline(steps)
        finally:
            self._orchestrate_depth -= 1

        return {
            "status": "ok",
            "pipeline_results": result.outputs,
            "execution_order": result.execution_order,
            "rolled_back_steps": getattr(result, "rolled_back_steps", []),
        }

    # ------------------------------------------------------------------ protocol
    @staticmethod
    def _error_response(req_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
        err: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": "2.0", "id": req_id, "error": err}

    def _handle_single_request(self, req: Any) -> Optional[Dict[str, Any]]:
        self.handles.cleanup_expired()
        if not isinstance(req, dict):
            return self._error_response(None, INVALID_REQUEST, "Request must be a JSON object")

        is_notification = "id" not in req
        req_id = req.get("id")

        if req.get("jsonrpc") != "2.0":
            if is_notification:
                return None
            return self._error_response(req_id, INVALID_REQUEST, "jsonrpc must be '2.0'")

        method = str(req.get("method", "") or "")
        params = req.get("params") or {}
        if method in ("resources/list", "resources/read"):
            if is_notification:
                return None
            try:
                if method == "resources/list":
                    result: Dict[str, Any] = {"resources": self._list_resources()}
                else:
                    uri = params.get("uri") if isinstance(params, dict) else None
                    if not isinstance(uri, str) or not uri:
                        raise InvalidParamsError(
                            "resources/read requires a non-empty string 'uri'"
                        )
                    result = self._read_resource(uri)
            except GatewayError as exc:
                return self._error_response(req_id, exc.code, str(exc), exc.data)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        try:
            with self._tool_lock:
                reply = self.router.dispatch_rpc(req)
            if reply is not None:
                action, target = self._wal_action_target(method, req)
                if action:
                    result = reply.get("result")
                    if method == "tools/call":
                        failed = bool(isinstance(result, dict) and result.get("isError"))
                    else:
                        failed = isinstance(result, dict) and (
                            "error" in result
                            or result.get("status") in ("blocked", "error")
                        )
                    self.wal.record(action, target, "ERROR" if failed else "OK")
            return reply
        except GatewayError as exc:
            if is_notification:
                return None
            return self._error_response(req_id, exc.code, str(exc), exc.data)
        except Exception as exc:  # noqa: BLE001 - protocol boundary must not crash
            crash_reason = f"{type(exc).__name__}: {exc}"
            self.wal.record(str(req.get("method", "")), self._wal_target(req), "CRASH", crash_reason)
            crash_path = os.path.join(self.workspace_root, ".onyxsi", "CRASH_REPRO.md")
            try:
                self.wal.write_crash_repro(crash_path, crash_reason)
            except OSError:
                crash_path = ""
            if is_notification:
                return None
            return self._error_response(
                req_id,
                INTERNAL_ERROR,
                f"Internal server error: {exc}",
                {"crash_repro": crash_path} if crash_path else None,
            )

    @staticmethod
    def _wal_target(req: Any) -> str:
        params = req.get("params")
        if not isinstance(params, dict):
            return ""
        return str(
            params.get("path")
            or params.get("target")
            or params.get("stage")
            or params.get("cmd")
            or params.get("query")
            or ""
        )

    @classmethod
    def _wal_action_target(cls, method: str, req: Any) -> Tuple[Optional[str], str]:
        """
        Resolves the flight-recorder action and target for a request.

        A client may dispatch a meta-tool directly (``method: "vfs_inspect"``)
        or through the standard MCP envelope (``method: "tools/call"`` with
        the tool name in ``params.name``). Both have to reach the recorder,
        otherwise the Time-Travel Chronicle is empty for every real editor.
        """
        if method == "tools/call":
            params = req.get("params")
            if not isinstance(params, dict):
                return None, ""
            name = str(params.get("name", "") or "")
            if not name:
                return None, ""
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            return name, cls._wal_target({"params": arguments})
        if method in FIXED_8_META_TOOLS:
            return method, cls._wal_target(req)
        return None, ""

    # ---------------------------------------------------------- MCP resources
    _RESOURCES = {
        "onyxsi://schema": (
            "tool-schema",
            "application/json",
            "Live JSON Schema of the Fixed 8 meta-tools.",
        ),
        "onyxsi://grammar": (
            "tool-grammar",
            "text/x-gbnf",
            "GBNF grammar compiled from the live tool schemas.",
        ),
        "onyxsi://audit": (
            "boot-audit",
            "application/json",
            "Zero-dependency / zero-UI audit of the kernel package.",
        ),
        "onyxsi://telemetry": (
            "telemetry",
            "application/json",
            "Call counters mirrored through the HAL shared-memory superblock.",
        ),
    }

    def _list_resources(self) -> List[Dict[str, str]]:
        return [
            {
                "uri": uri,
                "name": name,
                "mimeType": mime,
                "description": description,
            }
            for uri, (name, mime, description) in self._RESOURCES.items()
        ]

    def _read_resource(self, uri: str) -> Dict[str, Any]:
        entry = self._RESOURCES.get(uri)
        if entry is None:
            raise InvalidParamsError(f"Unknown resource URI: {uri}")
        _name, mime, _description = entry

        if uri == "onyxsi://schema":
            text = json.dumps(
                {"tools": self.router.list_tools()}, separators=(",", ":")
            )
        elif uri == "onyxsi://grammar":
            text = self.router.export_gbnf()
        elif uri == "onyxsi://audit":
            text = json.dumps(self.boot_audit, indent=2)
        else:
            text = json.dumps(
                {
                    "counters": {
                        "total": self._call_count,
                        "errors": self._error_count,
                        "last_tool": self._last_tool,
                    },
                    "shared_memory": self._read_shm_snapshot(),
                    "telemetry": (
                        self.telemetry.snapshot(recent=5)
                        if self.telemetry is not None
                        else None
                    ),
                    "wal_events": len(self.wal.get_recent_events()),
                },
                indent=2,
            )

        return {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}


    def process_line(self, line: str) -> Optional[str]:
        """Processes a single raw line of JSON-RPC 2.0 text and returns the formatted reply."""
        clean = line.strip()
        if not clean:
            return None

        if len(clean.encode("utf-8")) > MAX_FRAME_BYTES:
            return json.dumps(
                self._error_response(None, INVALID_REQUEST, f"Frame exceeds {MAX_FRAME_BYTES} bytes")
            )

        try:
            req = json.loads(clean)
        except json.JSONDecodeError as exc:
            try:
                shield_res = self.shield.parse_and_repair(clean)
                req = shield_res.data
                if not isinstance(req, (dict, list)):
                    raise ValueError("repair did not yield an object")
            except Exception:
                return json.dumps(
                    self._error_response(None, PARSE_ERROR, f"Parse error: {exc}")
                )

        if isinstance(req, list):
            if not req:
                return json.dumps(
                    self._error_response(None, INVALID_REQUEST, "Empty batch array")
                )
            replies = [r for r in (self._handle_single_request(item) for item in req) if r is not None]
            return json.dumps(replies) if replies else None

        reply = self._handle_single_request(req)
        return json.dumps(reply) if reply is not None else None

    def run_stdio_loop(self) -> None:
        """
        Runs the headless event loop reading stdin and responding to stdout.

        A HAL ``WakeupSignal`` (non-blocking socketpair) decouples the blocking
        stdin read from the loop, so ``shutdown()`` is observed promptly instead
        of waiting for the next line.
        """
        import queue

        self.running = True
        wakeup = WakeupSignal(WakeupConfig(timeout_ms=250)).create()
        self._wakeup = wakeup
        inbox: "queue.Queue[Optional[bytes]]" = queue.Queue()

        def _reader() -> None:
            try:
                for raw in sys.stdin.buffer:
                    inbox.put(raw)
                    wakeup.signal()
            except (OSError, ValueError):
                pass
            finally:
                inbox.put(None)
                wakeup.signal()

        threading.Thread(target=_reader, name="onyxsi-stdin", daemon=True).start()
        try:
            while self.running and not self._shutdown_event.is_set():
                wakeup.wait()
                while True:
                    try:
                        raw = inbox.get_nowait()
                    except queue.Empty:
                        break
                    if raw is None:
                        return
                    out = self.process_line(raw.decode("utf-8", errors="replace"))
                    if out is not None:
                        sys.stdout.write(out + "\n")
                        sys.stdout.flush()
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            wakeup.cleanup()
            self._wakeup = None
            self.shutdown()

    def shutdown(self) -> None:
        """Gracefully releases every lease, discards ghost state and stops the loop."""
        if not self.running and self._shutdown_event.is_set():
            return
        self.running = False
        self._shutdown_event.set()

        # Wake the stdio loop even if it is parked in select().
        if self._wakeup is not None:
            try:
                self._wakeup.signal()
            except Exception:  # noqa: BLE001 - shutdown must be best-effort
                pass

        # Release every active AST lease explicitly (no phantom method calls).
        for lease in list(self.lease_registry.get_active_leases()):
            try:
                self.lease_registry.release_lease(lease.file_path, lease.node_id, lease.agent_id)
            except Exception:  # noqa: BLE001 - shutdown must be best-effort
                pass

        # Drop uncommitted speculative state.
        try:
            if self.ghost_overlay.is_active():
                self.ghost_overlay.ghost_discard()
        except Exception:  # noqa: BLE001
            pass

        # Flush HAL telemetry only when the operator asked for a file, then
        # release the anonymous mmap backing the SPSC ring.
        if self.telemetry is not None:
            try:
                if self.telemetry_out:
                    self.telemetry.flush_jsonl(self.telemetry_out)
            except Exception:  # noqa: BLE001 - shutdown must never raise
                pass
            try:
                self.telemetry.close()
            except Exception:  # noqa: BLE001
                pass

        # Publish the final counters. The backing superblock is process-wide and
        # is unmapped once at exit, so short-lived servers cannot unlink a block
        # another live server still uses.
        if self.shm is not None:
            try:
                self._mirror_shm()
            except Exception:  # noqa: BLE001
                pass
            self.shm = None


def main(argv: Optional[List[str]] = None) -> int:
    """
    Console entry point: ``onyxsi [--workspace DIR] [--allow-mutation-without-flush]``.

    Runs the headless JSON-RPC daemon on stdin/stdout until EOF or a signal.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="onyxsi",
        description="OnyxSi headless JSON-RPC 2.0 daemon exposing the Fixed 8 Meta-Tools.",
    )
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Workspace root the daemon is allowed to touch (default: cwd).",
    )
    parser.add_argument(
        "--no-pre-flight-gate",
        action="store_true",
        help="Allow vfs_mutate without causal_flight pre_flight (NOT recommended).",
    )
    parser.add_argument(
        "--telemetry-out",
        default=None,
        metavar="PATH",
        help=(
            "Append per-tool JSONL telemetry (HAL SPSC ring buffer) to PATH on shutdown. "
            "Off by default: telemetry stays in RAM."
        ),
    )
    parser.add_argument(
        "--max-index-files",
        type=int,
        default=5000,
        metavar="N",
        help="Maximum number of .py files to index for search (default: 5000).",
    )
    parser.add_argument(
        "--max-index-file-mb",
        type=float,
        default=2.0,
        metavar="MB",
        help="Skip .py files larger than MB megabytes when indexing (default: 2).",
    )
    parser.add_argument(
        "--max-index-total-mb",
        type=float,
        default=128.0,
        metavar="MB",
        help="Total bytes of .py sources the indexer may read (default: 128).",
    )
    parser.add_argument(
        "--max-shadow-mb",
        type=float,
        default=512.0,
        metavar="MB",
        help="Maximum size of the temporary shadow copy used by ghost_sandbox test (default: 512).",
    )
    parser.add_argument(
        "--project-name",
        default=None,
        metavar="NAME",
        help=(
            "Project namespace for the task wiki and the antibody store "
            "(default: the workspace folder name)."
        ),
    )
    args = parser.parse_args(argv)

    server = OnyxSiServer(
        workspace_root=args.workspace,
        require_pre_flight=not args.no_pre_flight_gate,
        telemetry_out=args.telemetry_out,
        max_index_files=args.max_index_files,
        max_index_file_bytes=int(args.max_index_file_mb * 1024 * 1024),
        max_index_total_bytes=int(args.max_index_total_mb * 1024 * 1024),
        max_shadow_bytes=int(args.max_shadow_mb * 1024 * 1024),
        project_name=args.project_name,
    )
    server.run_stdio_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
