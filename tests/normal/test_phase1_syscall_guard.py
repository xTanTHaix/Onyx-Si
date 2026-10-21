"""
Normal Tests: Phase 1.3 Dual-OS Syscall & Process-Quota Shield.
"""
from onyxsi.hal.syscall_guard import SyscallGuard, SyscallGuardConfig


def test_syscall_guard_dangerous_syscall_blocking():
    guard = SyscallGuard(SyscallGuardConfig(block_dangerous_syscalls=True))
    assert guard.block_syscall("ptrace") is True
    assert guard.block_syscall("reboot") is True
    assert guard.block_syscall("read") is False


def test_syscall_guard_activation():
    guard = SyscallGuard()
    assert guard.activate() is True
    assert guard.is_active() is True
