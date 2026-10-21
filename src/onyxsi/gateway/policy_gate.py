"""
Policy Gate, Canonical Path Resolver & Streaming Secret Masker.
"""

import os
import re
from typing import Optional, Tuple, List
from dataclasses import dataclass, field


class PolicyGateError(Exception):
    pass


class PathTraversalBlockedError(PolicyGateError):
    pass


class SymlinkDepthExceededError(PolicyGateError):
    pass


@dataclass(slots=True, frozen=True)
class PolicyGateConfig:
    max_symlink_depth: int = 8
    secret_masking_enabled: bool = True
    secret_patterns: List[str] = field(default_factory=lambda: [
        r'sk-[a-zA-Z0-9]{20,}',
        r'ghp_[a-zA-Z0-9]{36}',
        r'gho_[a-zA-Z0-9]{36}',
        r'xox[baprs]-[0-9a-zA-Z]{10,}',
        r'AKIA[0-9A-Z]{16}',
        r'password=[^&\s]+',
        r'token=[^&\s]+',
    ])
    mask_value: str = "[REDACTED_SECRET]"


class PolicyGate:
    def __init__(self, config: Optional[PolicyGateConfig] = None, workspace_root: str = "."):
        self.config = config or PolicyGateConfig()
        self.workspace_root = os.path.realpath(workspace_root)
        self._compiled_patterns = [re.compile(p) for p in self.config.secret_patterns]

    def _resolve_canonical_path(self, path: str) -> str:
        depth = 0
        curr = path
        visited = set()
        while os.path.islink(curr):
            depth += 1
            if depth > self.config.max_symlink_depth:
                raise SymlinkDepthExceededError(f"Symlink depth {depth} exceeds {self.config.max_symlink_depth}")
            if curr in visited:
                raise PathTraversalBlockedError(f"Circular symlink loop detected: {curr}")
            visited.add(curr)
            target = os.readlink(curr)
            if not os.path.isabs(target):
                target = os.path.join(os.path.dirname(curr), target)
            curr = target
        return os.path.realpath(curr)

    def resolve_path(self, path: str) -> Tuple[bool, str]:
        if not os.path.isabs(path):
            full_path = os.path.join(self.workspace_root, path)
        else:
            full_path = path

        resolved = self._resolve_canonical_path(full_path)
        if resolved != self.workspace_root and not resolved.startswith(self.workspace_root + os.sep):
            raise PathTraversalBlockedError(f"Path traversal detected outside workspace: {path} -> {resolved}")

        return True, resolved

    def mask_secrets(self, text: str) -> str:
        if not self.config.secret_masking_enabled:
            return text
        masked = text
        for pat in self._compiled_patterns:
            masked = pat.sub(self.config.mask_value, masked)
        return masked

    def mask_streaming(self, chunks: List[str], window_overlap: int = 64) -> List[str]:
        result = []
        carry = ""
        for chunk in chunks:
            combined = carry + chunk
            masked_comb = self.mask_secrets(combined)
            if len(masked_comb) > window_overlap:
                emit = masked_comb[:-window_overlap]
                carry = masked_comb[-window_overlap:]
            else:
                emit = ""
                carry = masked_comb
            if emit:
                result.append(emit)
        if carry:
            result.append(carry)
        return result
