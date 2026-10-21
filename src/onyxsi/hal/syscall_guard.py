"""
Syscall & Process-Quota Policy Registry (Dual-OS).

IMPORTANT / HONEST SCOPE
------------------------
This module records *policy intent*. It does NOT install seccomp-bpf, a Linux
namespace, a Windows Job Object or any kernel-level filter: doing that from pure
Python without ctypes/third-party code is not possible, and this project is
zero-dependency by design.

What it gives you:
  * a declarative list of forbidden syscalls / quota limits (``SyscallGuardConfig``)
  * a fast membership check (``is_blocked``) callers can enforce themselves
  * ``enforcement_backend`` so callers can tell whether a real backend exists

If you need real containment, run the whole daemon inside a container/VM and
treat this class as the policy document handed to that outer layer.
"""

import sys
from typing import Optional, List
from dataclasses import dataclass, field


class SyscallGuardError(Exception):
    pass


@dataclass(slots=True, frozen=True)
class SyscallGuardConfig:
    max_memory_mb: int = 64
    max_processes: int = 100
    block_dangerous_syscalls: bool = True
    dangerous_syscalls: List[str] = field(default_factory=lambda: [
        'ptrace', 'mount', 'umount', 'unshare', 'setuid', 'setgid',
        'chroot', 'pivot_root', 'kexec_load', 'reboot', 'swapon', 'swapoff'
    ])


class SyscallGuard:
    """Policy registry for forbidden syscalls and process quotas (no kernel hook)."""

    def __init__(self, config: Optional[SyscallGuardConfig] = None):
        self.config = config or SyscallGuardConfig()
        self._platform = "windows" if sys.platform == "win32" else ("linux" if sys.platform == "linux" else "unknown")
        self._blocked_syscalls: List[str] = []
        self._activated = False

        if self.config.block_dangerous_syscalls:
            self._blocked_syscalls.extend(self.config.dangerous_syscalls)

    @property
    def enforcement_backend(self) -> str:
        """'none' — this class records policy only; no kernel filter is installed."""
        return "none"

    def is_enforcing(self) -> bool:
        """Always False: kept explicit so callers cannot mistake policy for enforcement."""
        return False

    def is_blocked(self, name: str) -> bool:
        """True when ``name`` is on the forbidden list."""
        return self.config.block_dangerous_syscalls and name in self._blocked_syscalls

    def block_syscall(self, name: str) -> bool:
        if not self.config.block_dangerous_syscalls:
            return False
        if name in self.config.dangerous_syscalls:
            if name not in self._blocked_syscalls:
                self._blocked_syscalls.append(name)
            return True
        return False

    def activate(self) -> bool:
        self._activated = True
        return True

    def is_active(self) -> bool:
        return self._activated
