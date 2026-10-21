"""
Filesystem Containment Policy (Dual-OS).

HONEST SCOPE: this class performs real *path containment checks* (canonicalised
prefix comparison) and real *workspace-root enforcement*. It does NOT create an
OS jail: ``activate()`` flips a policy flag, it does not chroot/namespace/AppContainer
the process. Network isolation is likewise a declarative flag, not a firewall.

Use ``assert_path_allowed`` before opening a file; callers that skip the check get
no protection from this class.
"""

import os
import sys
from typing import Optional, List
from dataclasses import dataclass, field


class SandboxError(Exception):
    pass


@dataclass(slots=True, frozen=True)
class SandboxConfig:
    workspace_root: str = "."
    isolation_level: str = "strict"
    allow_network: bool = False
    allow_file_system: bool = False
    allowed_ports: List[int] = field(default_factory=list)
    max_memory_mb: int = 64
    max_processes: int = 10


class SandboxEnclave:
    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self._platform = "windows" if sys.platform == "win32" else ("linux" if sys.platform == "linux" else "unknown")
        self._activated = False
        self._workspace_root = os.path.realpath(self.config.workspace_root)

    @property
    def enforcement_backend(self) -> str:
        """'path-prefix' — real containment checks, no OS jail."""
        return "path-prefix"

    def activate(self) -> bool:
        if self._activated:
            return True
        self._activated = True
        return True

    def check_path_access(self, path: str, mode: str = "r") -> bool:
        canonical = os.path.realpath(path)
        if not (canonical == self._workspace_root or canonical.startswith(self._workspace_root + os.sep)):
            return False
        if mode in ("w", "a", "x") and not (self.config.allow_file_system or self._activated):
            return False
        return True

    def assert_path_allowed(self, path: str, mode: str = "r") -> str:
        """Raises SandboxError unless ``path`` stays inside the workspace root."""
        if not self.check_path_access(path, mode=mode):
            raise SandboxError(
                f"Path '{path}' is outside the sandbox workspace '{self._workspace_root}' "
                f"or the requested mode '{mode}' is not permitted"
            )
        return os.path.realpath(path)

    def is_active(self) -> bool:
        return self._activated
