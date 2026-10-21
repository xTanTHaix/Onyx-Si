"""
Cross-Process Wakeup Signaling.
Dual-OS support: Windows 10/11 & Linux 6.2+.
Uses non-blocking socket pairs for reliable, cross-platform select() event waiting
without [WinError 10038] on Windows.
"""

import sys
import socket
import select
from typing import Optional
from dataclasses import dataclass


class WakeupError(Exception):
    pass


@dataclass(slots=True, frozen=True)
class WakeupConfig:
    timeout_ms: int = 5000
    broadcast: bool = False
    auto_reset: bool = True
    cleanup_on_exit: bool = True


class WakeupSignal:
    def __init__(self, config: Optional[WakeupConfig] = None):
        self.config = config or WakeupConfig()
        self._platform = "windows" if sys.platform == "win32" else ("linux" if sys.platform == "linux" else "unknown")
        self._sock_r: Optional[socket.socket] = None
        self._sock_w: Optional[socket.socket] = None
        self._signaled = False

    def create(self, name: Optional[str] = None) -> "WakeupSignal":
        try:
            r, w = socket.socketpair()
            r.setblocking(False)
            w.setblocking(False)
            self._sock_r = r
            self._sock_w = w
            self._signaled = False
            return self
        except OSError as e:
            raise WakeupError(f"Failed to create wakeup signal: {e}")

    def signal(self, count: int = 1) -> bool:
        if self._sock_w is None:
            raise WakeupError("Wakeup signal not created")
        try:
            self._sock_w.send(b"x" * count)
            self._signaled = True
            return True
        except OSError:
            return False

    def wait(self, timeout_ms: Optional[int] = None) -> bool:
        if self._sock_r is None:
            raise WakeupError("Wakeup signal not created")
        timeout_sec = (timeout_ms if timeout_ms is not None else self.config.timeout_ms) / 1000.0
        r_ready, _, _ = select.select([self._sock_r], [], [], timeout_sec)
        if r_ready:
            try:
                self._sock_r.recv(1024)
            except OSError:
                pass
            if self.config.auto_reset:
                self._signaled = False
            return True
        return False

    def clear(self) -> bool:
        if self._sock_r is not None:
            try:
                while True:
                    data = self._sock_r.recv(1024)
                    if not data:
                        break
            except OSError:
                pass
        self._signaled = False
        return True

    def cleanup(self) -> None:
        if self._sock_r is not None:
            try:
                self._sock_r.close()
            except OSError:
                pass
            self._sock_r = None
        if self._sock_w is not None:
            try:
                self._sock_w.close()
            except OSError:
                pass
            self._sock_w = None
        self._signaled = False
