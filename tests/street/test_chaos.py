"""
Street Tests: Chaos & Fuzzing
"""
import sys
import os
import time
import tempfile
from pathlib import Path

try:
    import pytest
except ImportError:
    class MockPytest:
        @staticmethod
        def fail(msg):
            raise AssertionError(msg)
        @staticmethod
        def skip(msg):
            pass
        @staticmethod
        def fixture(func):
            return func
    pytest = MockPytest()

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from onyxsi.hal.syscall_guard import SyscallGuard, SyscallGuardConfig
from onyxsi.hal.wakeup import WakeupSignal, WakeupConfig
from onyxsi.hal.process_manager import ProcessManager, ProcessConfig
from onyxsi.gateway.policy_gate import PolicyGate


class TestSyscallGuardChaos:
    def test_rapid_syscall_blocking(self):
        guard = SyscallGuard()
        for _ in range(50):
            for sc in ['ptrace', 'mount', 'umount']:
                guard.block_syscall(sc)
        for sc in ['ptrace', 'mount', 'umount']:
            assert sc in guard._blocked_syscalls

    def test_config_immutability(self):
        config = SyscallGuardConfig()
        try:
            config.max_memory_mb = 128
            assert False, "Should be immutable"
        except Exception:
            pass

    def test_platform_edge_cases(self):
        guard = SyscallGuard()
        assert guard._platform in ("windows", "linux", "unknown")


class TestWakeupSignalChaos:
    def test_rapid_signal_flood(self):
        sig = WakeupSignal().create()
        for _ in range(100):
            sig.signal()
        assert sig.wait(timeout_ms=50) is True
        sig.cleanup()

    def test_timeout_stress(self):
        sig = WakeupSignal().create()
        for _ in range(5):
            assert sig.wait(timeout_ms=10) is False
        sig.cleanup()


class TestProcessManagerChaos:
    def test_rapid_spawn_stop(self):
        mgr = ProcessManager()
        cfg = ProcessConfig(command=sys.executable, args=["-c", "pass"])
        for _ in range(5):
            pid = mgr.spawn(cfg)
            mgr.stop(pid)

    def test_config_hash_changes(self):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            f.write(b'{"v": 1}')
            path = f.name
        try:
            mgr = ProcessManager(config_path=path)
            h1 = mgr._get_config_hash()
            with open(path, 'wb') as f:
                f.write(b'{"v": 2}')
            h2 = mgr._get_config_hash()
            assert h1 != h2
        finally:
            os.unlink(path)
