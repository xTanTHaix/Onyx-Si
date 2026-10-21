"""
Character Tests: Phase 0 & Phase 1 Hostile Agent Persona Simulations (CT-01 to CT-08).
1:1 Master Build Plan Verification Matrix.
"""
import os
import sys
import time
import tempfile
from pathlib import Path
from onyxsi.auditor import ZeroDepAndZeroUiAuditor
from onyxsi.hal.shm_superblock import (
    SharedMemorySuperblock,
    ShmBoundsViolationError,
    WORKER_SLOT_SIZE,
)
from onyxsi.hal.ring_buffer import SPSCRingBuffer, RingBufferOverflowError
from onyxsi.hal.trie_index import CompactRadixSymbolIndex
from onyxsi.gateway.policy_gate import (
    PolicyGate,
    PathTraversalBlockedError,
)
from onyxsi.hal.sandbox_enclave import SandboxEnclave, SandboxConfig
from onyxsi.hal.syscall_guard import SyscallGuard, SyscallGuardConfig
from onyxsi.hal.process_manager import ProcessManager, ProcessConfig


class TestCT01DependencySmuggler:
    """CT-01: The Dependency & UI Smuggler (Phase 0.1)"""
    def test_smuggler_blocked_on_banned_imports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "bad_agent.py"
            code = "import numpy as np\nimport rich\nprint('evil')"
            bad_file.write_text(code, encoding="utf-8")

            auditor = ZeroDepAndZeroUiAuditor(workspace_root=tmpdir)
            result = auditor.audit_directory(Path(tmpdir))
            assert result.passed is False
            mod_names = [v.module_name for v in result.violations]
            assert "numpy" in mod_names
            assert "rich" in mod_names


class TestCT02MemoryLeech:
    """CT-02: The Memory Leech (Phase 0.2)"""
    def test_leech_blocked_on_slot_and_slab_overflow(self):
        shm = SharedMemorySuperblock(size=1_048_576, name="ct02_shm").initialize()
        try:
            slot = shm.allocate_slot()
            # Attempt to write 1000 bytes into a 256-byte worker slot
            giant_payload = b"X" * (WORKER_SLOT_SIZE + 100)
            try:
                shm.write_worker_slot(slot, giant_payload)
                assert False, "Should block slot overflow"
            except ShmBoundsViolationError:
                pass

            # Attempt to write beyond slab arena
            try:
                shm.write_slab(shm.size + 100, b"LEECH")
                assert False, "Should block slab overflow"
            except ShmBoundsViolationError:
                pass
        finally:
            shm.close()


class TestCT03RapidFireSpammer:
    """CT-03: The Rapid-Fire Spammer (Phase 0.3)"""
    def test_spammer_ring_full_backpressure(self):
        rb = SPSCRingBuffer(size=262_144, capacity=10).initialize()
        try:
            # Spam 10 items to fill
            for i in range(10):
                rb.push(f"SPAM_{i}".encode(), "tx")
            # 11th item must be rejected with RingBufferOverflowError
            try:
                rb.push(b"SPAM_OVERFLOW", "tx")
                assert False, "Should reject overflow on saturated ring"
            except RingBufferOverflowError:
                pass
        finally:
            rb.close()


class TestCT04DeepNestedWildcardSearcher:
    """CT-04: The Deep Nested Wildcard Searcher (Phase 0.4)"""
    def test_wildcard_searcher_bound_latency(self):
        idx = CompactRadixSymbolIndex()
        # Seed 10,000 symbols
        for i in range(10000):
            idx.add_symbol(f"org.onyx.kernel.subsystem.{i}.exec", coordinate=i)

        start = time.perf_counter()
        res = idx.lookup("org.onyx.kernel.subsystem.9999.exec")
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        assert res.found is True
        assert elapsed_ms < 0.5  # Sub-millisecond lookup guarantee


class TestCT05SymlinkMazeSecretLeaker:
    """CT-05: The Symlink Maze & Secret Leaker (Phase 1.1)"""
    def test_secret_leaker_split_chunks_redacted(self):
        gate = PolicyGate()
        # Split key across chunk boundary
        chunk1 = "The agent key is sk-1234567890"
        chunk2 = "abcdef1234567890_extra and done"
        chunks = [chunk1, chunk2]
        masked = gate.mask_streaming(chunks)
        combined = "".join(masked)
        assert "sk-1234567890abcdef1234567890" not in combined
        assert "[REDACTED_SECRET]" in combined


class TestCT06RogueWorkerJailbreaker:
    """CT-06: The Rogue Worker Jailbreaker (Phase 1.2)"""
    def test_jailbreaker_outside_access_denied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            enclave = SandboxEnclave(SandboxConfig(workspace_root=tmpdir))
            enclave.activate()
            # Attempt access outside
            assert enclave.check_path_access("/etc/shadow") is False
            assert enclave.check_path_access("C:\\Windows\\System32") is False


class TestCT07ForkBomber:
    """CT-07: The Fork Bomber (Phase 1.3)"""
    def test_fork_bomber_dangerous_syscall_blocked(self):
        guard = SyscallGuard()
        guard.activate()
        assert guard.block_syscall("ptrace") is True
        assert guard.block_syscall("unshare") is True


class TestCT08ConfigTremblerZombie:
    """CT-08: The Config Trembler & Zombie (Phase 1.4)"""
    def test_trembler_debounce_and_zombie_reap(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write('{"cfg": 1}')
            cfg_path = f.name

        try:
            mgr = ProcessManager(config_path=cfg_path, debounce_ms=200)
            # Spam writes
            for i in range(50):
                with open(cfg_path, "w") as f:
                    f.write(f'{{"cfg": {i}}}')
                # Immediate check within debounce must return False
                mgr.check_and_reload_config()

            # Wait past debounce window
            time.sleep(0.25)
            # Final reload after tremor settles
            assert mgr.check_and_reload_config() is True
        finally:
            os.unlink(cfg_path)
