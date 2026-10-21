"""
Character Tests: Core Scenarios (CT-01 to CT-08)

Each scenario exercises the real OnyxSi component named in the blueprint instead
of re-testing Python itself.

- CT-01: Fixed 8-Meta-Tool Gateway initialization
- CT-02: DBP (bit-parallel / SWAR) Bitplane AST Skeletonizer
- CT-03: ContextFS Virtual Handle Resolution
- CT-04: Ghost-CoW In-RAM Overlay
- CT-05: Dual-OS Shared Memory Superblock
- CT-06: SPSC Ring Buffer Communication
- CT-07: Policy Gate Path Resolution
- CT-08: Sandbox Enclave Process Isolation
"""

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

import pytest

from onyxsi.gateway.meta_router import (
    FIXED_8_META_TOOLS,
    MAX_SCHEMA_TOKENS,
    MetaToolRouter,
    MethodNotFoundError,
)
from onyxsi.contextfs.bitplane_ast import BitplaneAstScanner, BitplaneAstPruner
from onyxsi.contextfs.virtual_handles import (
    VirtualHandleRegistry,
    StaleHandleError,
    HandleExpiredError,
)
from onyxsi.deltafs.ghost_overlay import GhostOverlayFS
from onyxsi.hal.shm_superblock import (
    MAGIC_NUMBER,
    SharedMemorySuperblock,
    SuperblockConfig,
    SuperblockSlotExhaustedError,
)
from onyxsi.hal.ring_buffer import SPSCRingBuffer, RingBufferConfig
from onyxsi.hal.sandbox_enclave import SandboxEnclave, SandboxConfig, SandboxError
from onyxsi.hal.syscall_guard import SyscallGuard, SyscallGuardConfig
from onyxsi.gateway.policy_gate import PolicyGate, PolicyGateConfig, PathTraversalBlockedError


class CT01FixedMetaToolGateway:
    def test_gateway_exposes_exactly_eight_tools(self):
        router = MetaToolRouter()
        names = tuple(t["name"] for t in router.list_tools())
        assert names == FIXED_8_META_TOOLS
        assert len(names) == 8

    def test_schema_stays_under_token_ceiling(self):
        router = MetaToolRouter()
        assert router.schema_token_count() <= MAX_SCHEMA_TOKENS

    def test_unknown_method_is_rejected(self):
        router = MetaToolRouter()
        with pytest.raises(MethodNotFoundError):
            router.dispatch_rpc({"jsonrpc": "2.0", "id": 1, "method": "shell_exec"})


class CT02DBPBitplane:
    @staticmethod
    def _sample_source(function_count: int = 100) -> str:
        blocks = []
        for i in range(function_count):
            blocks.append(
                f"def func_{i}(x: int) -> int:\n"
                f"    '''Handles case {i}.'''\n"
                f"    total = x\n"
                f"    for step in range({i}):\n"
                f"        total += step * 2\n"
                f"    return total\n"
            )
        return "\n".join(blocks)

    def test_bitplane_balance_detection(self):
        scanner = BitplaneAstScanner()
        balanced, depth, blocks = scanner.analyze_source_topology(self._sample_source().encode())
        assert balanced is True
        assert depth == 0
        assert blocks == 0

    def test_bitplane_flags_unbalanced_source(self):
        scanner = BitplaneAstScanner()
        balanced, depth, _ = scanner.analyze_source_topology(b"def broken():\n    if True {\n")
        assert balanced is False
        assert depth == 1

    def test_skeleton_pruning_keeps_signatures_and_drops_bodies(self):
        source = self._sample_source()
        result = BitplaneAstPruner.prune_source(source, "sample.py")
        assert "def func_0" in result.skeleton_code
        assert "for step in range" not in result.skeleton_code
        assert len(result.skeleton_code) < len(source)


class CT03ContextFSVirtualHandle:
    def test_virtual_handle_resolves_to_payload(self):
        registry = VirtualHandleRegistry(default_ttl_sec=3600.0)
        original = b"def handler():\n    return 200\n"
        handle = registry.register_code_handle(
            "handler.py", "fn_handler", "return 200", original
        )
        assert handle.handle_uri.startswith("ref://")
        assert registry.resolve(handle.handle_uri, current_source_bytes=original) == b"return 200"

    def test_stale_source_is_rejected(self):
        registry = VirtualHandleRegistry(default_ttl_sec=3600.0)
        original = b"def handler():\n    return 200\n"
        handle = registry.register_code_handle(
            "handler.py", "fn_handler", "return 200", original
        )
        mutated = b"def handler():\n    return 500\n"
        with pytest.raises(StaleHandleError):
            registry.resolve(handle.handle_uri, current_source_bytes=mutated)

    def test_expired_handle_is_rejected(self):
        import time as _time

        registry = VirtualHandleRegistry(default_ttl_sec=0.001)
        handle = registry.register_stream_handle("stream_a", b"payload")
        _time.sleep(0.02)
        with pytest.raises(HandleExpiredError):
            registry.resolve(handle.handle_uri)


class CT04GhostCoWOverlay:
    def test_edits_stay_in_ram_until_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "config.py")
            Path(target).write_text("DEBUG = False\n", encoding="utf-8")

            ghost = GhostOverlayFS(tmp)
            ghost.ghost_begin()
            ghost.ghost_mutate(target, "DEBUG = True\n")

            assert ghost.ghost_read_text(target) == "DEBUG = True\n"
            assert Path(target).read_text(encoding="utf-8") == "DEBUG = False\n"

            ghost.ghost_commit()
            assert Path(target).read_text(encoding="utf-8") == "DEBUG = True\n"

    def test_discard_reverts_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "config.py")
            Path(target).write_text("DEBUG = False\n", encoding="utf-8")

            ghost = GhostOverlayFS(tmp)
            ghost.ghost_begin()
            ghost.ghost_mutate(target, "DEBUG = True\n")
            ghost.ghost_discard()

            assert ghost.is_active() is False
            assert Path(target).read_text(encoding="utf-8") == "DEBUG = False\n"


class CT05DualOSSharedMemory:
    def test_superblock_round_trip(self):
        sb = SharedMemorySuperblock(size=1024 * 1024, name=f"ct05_{os.getpid()}")
        sb.initialize()
        try:
            assert sb.get_header().magic == MAGIC_NUMBER
            slot = sb.allocate_slot()
            sb.write_worker_slot(slot, b"WORKER_ALIVE")
            assert sb.read_worker_slot(slot, len(b"WORKER_ALIVE")) == b"WORKER_ALIVE"
            sb.free_slot(slot)
        finally:
            sb.cleanup()

    def test_magic_number_is_onyx(self):
        config = SuperblockConfig()
        assert config.magic == MAGIC_NUMBER
        assert MAGIC_NUMBER.to_bytes(4, "big") == b"ONYX"

    def test_slot_exhaustion_is_explicit(self):
        from onyxsi.hal.shm_superblock import WORKER_SLOT_COUNT

        sb = SharedMemorySuperblock(size=1024 * 1024, name=f"ct05x_{os.getpid()}")
        sb.initialize()
        try:
            for _ in range(WORKER_SLOT_COUNT):
                sb.allocate_slot()
            with pytest.raises(SuperblockSlotExhaustedError):
                sb.allocate_slot()
        finally:
            sb.cleanup()


class CT06SPSCRingBuffer:
    def test_fifo_ordering(self):
        buffer = SPSCRingBuffer(RingBufferConfig(size_mb=2))
        buffer.put(b"first")
        buffer.put(b"second")
        try:
            assert buffer.get(timeout_sec=1.0) == b"first"
            assert buffer.get(timeout_sec=1.0) == b"second"
        finally:
            buffer.cleanup()

    def test_empty_pop_times_out(self):
        from onyxsi.hal.ring_buffer import RingBufferUnderflowError

        buffer = SPSCRingBuffer(RingBufferConfig(size_mb=2))
        try:
            with pytest.raises(RingBufferUnderflowError):
                buffer.get(timeout_sec=0.05)
        finally:
            buffer.cleanup()


class CT07PolicyGatePathResolution:
    def test_traversal_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = PolicyGate(PolicyGateConfig(), workspace_root=tmp)
            with pytest.raises(PathTraversalBlockedError):
                gate.resolve_path("../../etc/passwd")

    def test_canonical_path_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "test.txt"
            target.write_text("test", encoding="utf-8")
            gate = PolicyGate(workspace_root=tmp)
            _, resolved = gate.resolve_path("test.txt")
            assert Path(resolved) == target.resolve()

    def test_secrets_are_masked(self):
        gate = PolicyGate()
        masked = gate.mask_secrets("Authorization: Bearer sk-1234567890abcdef1234567890abcdef12")
        assert "[REDACTED_SECRET]" in masked


class CT08SandboxEnclave:
    def test_paths_inside_workspace_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "inside.txt"
            target.write_text("x", encoding="utf-8")
            enclave = SandboxEnclave(SandboxConfig(workspace_root=tmp, allow_file_system=True))
            assert enclave.check_path_access(str(target), mode="r") is True
            assert enclave.assert_path_allowed(str(target), mode="r") == str(target.resolve())

    def test_paths_outside_workspace_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            enclave = SandboxEnclave(SandboxConfig(workspace_root=tmp))
            with pytest.raises(SandboxError):
                enclave.assert_path_allowed(os.path.join(tmp, "..", "escape.txt"))

    def test_containment_is_not_claimed_as_kernel_jail(self):
        """Policy layers must say so instead of pretending to be a kernel sandbox."""
        enclave = SandboxEnclave()
        guard = SyscallGuard(SyscallGuardConfig())
        assert enclave.enforcement_backend == "path-prefix"
        assert guard.is_enforcing() is False
        assert guard.is_blocked("ptrace") is True
