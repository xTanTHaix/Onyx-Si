"""
Normal Tests: Phase 1 Module Verification
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

from onyxsi.hal.syscall_guard import SyscallGuard, SyscallGuardConfig, SyscallGuardError
from onyxsi.hal.wakeup import WakeupSignal, WakeupConfig, WakeupError
from onyxsi.hal.process_manager import ProcessManager, ProcessConfig, ProcessManagerError


class TestSyscallGuard:
    def test_init_default_config(self):
        guard = SyscallGuard()
        assert guard.config.max_memory_mb == 64
        assert guard.config.max_processes == 100
        assert guard.config.block_dangerous_syscalls is True

    def test_init_custom_config(self):
        config = SyscallGuardConfig(
            max_memory_mb=32,
            max_processes=50,
            block_dangerous_syscalls=False
        )
        guard = SyscallGuard(config)
        assert guard.config.max_memory_mb == 32
        assert guard.config.max_processes == 50
        assert guard.config.block_dangerous_syscalls is False

    def test_platform_detection(self):
        guard = SyscallGuard()
        if sys.platform == "win32":
            assert guard._platform == "windows"
        elif sys.platform == "linux":
            assert guard._platform == "linux"
        else:
            assert guard._platform == "unknown"

    def test_block_syscall_dangerous(self):
        guard = SyscallGuard()
        assert guard.block_syscall('ptrace') is True

    def test_block_syscall_not_dangerous(self):
        guard = SyscallGuard()
        assert guard.block_syscall('read') is False

    def test_block_syscall_disabled(self):
        config = SyscallGuardConfig(block_dangerous_syscalls=False)
        guard = SyscallGuard(config)
        assert guard.block_syscall('ptrace') is False

    def test_activate_fallback(self):
        guard = SyscallGuard()
        assert guard.activate() is True


class TestWakeupSignal:
    def test_init_default_config(self):
        sig = WakeupSignal()
        assert sig.config.timeout_ms == 5000
        assert sig.config.broadcast is False
        assert sig.config.auto_reset is True

    def test_init_custom_config(self):
        config = WakeupConfig(timeout_ms=1000, broadcast=True, auto_reset=False)
        sig = WakeupSignal(config)
        assert sig.config.timeout_ms == 1000
        assert sig.config.broadcast is True
        assert sig.config.auto_reset is False

    def test_platform_detection(self):
        sig = WakeupSignal()
        if sys.platform == "win32":
            assert sig._platform == "windows"
        elif sys.platform == "linux":
            assert sig._platform == "linux"
        else:
            assert sig._platform == "unknown"

    def test_create_signal(self):
        sig = WakeupSignal().create()
        assert sig is not None
        sig.cleanup()

    def test_signal_and_wait(self):
        sig = WakeupSignal().create()
        assert sig.signal() is True
        assert sig.wait(timeout_ms=500) is True
        sig.cleanup()

    def test_wait_timeout(self):
        sig = WakeupSignal().create()
        t0 = time.time()
        res = sig.wait(timeout_ms=50)
        assert res is False
        assert time.time() - t0 >= 0.04
        sig.cleanup()

    def test_clear_signal(self):
        sig = WakeupSignal().create()
        sig.signal()
        assert sig.clear() is True
        sig.cleanup()


class TestProcessManager:
    def test_init_no_config(self):
        manager = ProcessManager()
        assert manager.config_path is None
        assert manager._workers == {}

    def test_init_with_config(self):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            f.write(b'{}')
            config_path = f.name
        try:
            manager = ProcessManager(config_path)
            assert manager.config_path == config_path
        finally:
            os.unlink(config_path)

    def test_get_config_hash(self):
        manager = ProcessManager()
        h1 = manager._get_config_hash()
        assert len(h1) == 16

    def test_spawn_worker(self):
        manager = ProcessManager()
        config = ProcessConfig(
            command=sys.executable,
            args=["-c", "import time; time.sleep(0.5)"],
            timeout_sec=5.0,
        )
        pid = manager.spawn(config)
        assert pid > 0
        st = manager.get_status(pid)
        assert st is not None
        assert st.pid == pid
        assert st.status == "running"
        manager.stop(pid)

    def test_stop_worker(self):
        manager = ProcessManager()
        config = ProcessConfig(
            command=sys.executable,
            args=["-c", "import time; time.sleep(0.5)"],
            timeout_sec=5.0,
        )
        pid = manager.spawn(config)
        assert manager.stop(pid) is True
        st = manager.get_status(pid)
        assert st.status == "stopped"

    def test_get_status_nonexistent(self):
        manager = ProcessManager()
        st = manager.get_status(99999)
        assert st is None

    def test_health_check_all(self):
        manager = ProcessManager()
        assert manager.health_check_all() == []

    def test_reload_config_no_change(self):
        manager = ProcessManager()
        assert manager.reload_config() is True

    def test_cleanup(self):
        manager = ProcessManager()
        manager.cleanup()
