"""
OnyxSi Data Contracts and Error Codes.
All core structures use slots=True for memory efficiency.
Zero external dependencies.
"""

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional, Dict
import json


# A project name becomes one path segment inside the wiki. Everything outside
# this set is folded to an underscore so a name can never introduce a
# separator, a drive letter, or a traversal.
_UNSAFE_PATH_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]")

MAX_PATH_SEGMENT_LEN = 128
DEFAULT_PATH_SEGMENT = "default"


def sanitize_path_segment(
    name: str,
    max_length: int = MAX_PATH_SEGMENT_LEN,
    default: str = DEFAULT_PATH_SEGMENT,
) -> str:
    """
    Normalises ``name`` into a safe single path segment.

    The result never contains a separator, never starts or ends with a dot or
    a space, and is never ``.`` or ``..`` — so a caller-supplied project name
    cannot escape the directory it is joined to. An empty or fully stripped
    name falls back to ``default``.
    """
    cleaned = _UNSAFE_PATH_SEGMENT.sub("_", str(name or "").strip())
    cleaned = cleaned.strip(". ")
    cleaned = cleaned[:max_length]
    if not cleaned or cleaned in (".", ".."):
        cleaned = default
    return cleaned



class OnyxErrorCode(IntEnum):
    """OnyxSi error codes."""
    # Phase 0: Scaffolding & Memory Plane
    E0001 = 1001  # FORBIDDEN_DEPENDENCY_OR_UI_ERROR
    E0002 = 1002  # SUPERBLOCK_CORRUPTED_ERROR
    E0003 = 1003  # SUPERBLOCK_SLOT_EXHAUSTED_ERROR
    E0004 = 1004  # SHM_BOUNDS_VIOLATION
    E0005 = 1005  # RING_BUFFER_FULL_ERROR
    E0006 = 1006  # RING_BUFFER_EMPTY_ERROR
    E0007 = 1007  # TRIE_INDEX_NOT_FOUND
    E0008 = 1008  # TRIE_INDEX_MISMATCH
    
    # Phase 1: Kernel Sandbox & Supervisor
    E1001 = 2001  # PATH_TRAVERSAL_BLOCKED
    E1002 = 2002  # SYMLINK_DEPTH_EXCEEDED
    E1003 = 2003  # SECRET_NOT_REDACTED
    E1004 = 2004  # SANDBOX_VIOLATION
    E1005 = 2005  # NETWORK_BLOCKED
    E1006 = 2006  # FILESYSTEM_BLOCKED
    E1007 = 2007  # PROCESS_QUOTA_EXCEEDED
    E1008 = 2008  # FORK_BOMB_BLOCKED
    E1009 = 2009  # SYSCALL_BLOCKED
    E1010 = 2010  # WORKER_TIMEOUT
    E1011 = 2011  # WORKER_REAPED
    E1012 = 2012  # CONFIG_INVALID
    E1013 = 2013  # CONFIG_RELOAD_FAILED
    E1014 = 2014  # CONFIG_DEBOUNCE_ACTIVE

    # Phase 2: ContextFS & DBP (bit-parallel / SWAR) Bitplane AST Engine
    E2001 = 3001  # AST_PARSE_FAILED
    E2002 = 3002  # TOKEN_CEILING_EXCEEDED
    E2003 = 3003  # HANDLE_EXPIRED_OR_INVALID
    E2004 = 3004  # PIECE_TABLE_CORRUPTED

    # Phase 3: Ghost-CoW Overlay, Userspace DeltaFS & Conflict-Free AST Merger
    E3001 = 4001  # GHOST_COMMIT_SYNTAX_ERROR
    E3002 = 4002  # GHOST_OVERLAY_ACTIVE
    E3003 = 4003  # AST_LEASE_CONFLICT
    E3004 = 4004  # AST_MERGE_CONFLICT
    E3005 = 4005  # COW_SNAPSHOT_FAILED

    # Phase 4: Headless Gateway Ingress, Syntactic Shield & Terminal Stream Folder
    E4001 = 5001  # METHOD_NOT_FOUND
    E4002 = 5002  # SLOWLORIS_TIMEOUT_ABORT
    E4003 = 5003  # DEEP_NESTING_BOMB_BLOCKED
    E4004 = 5004  # SCHEMA_VALIDATION_FAILED
    E4005 = 5005  # CYCLICAL_PIPELINE_ERROR
    E4006 = 5006  # PIPELINE_STEP_ROLLBACK

    # Phase 5: Causal Flight Brain, Automated Causal Bisect & Antibody Memory
    E5001 = 6001  # PRE_FLIGHT_REQUIRED_ERROR
    E5002 = 6002  # VERIFICATION_REQUIRED_ERROR
    E5003 = 6003  # TRAJECTORY_DRIFT_ABORT
    E5004 = 6004  # ERROR_BUDGET_EXHAUSTED_LOCKED
    E5005 = 6005  # NON_ORTHOGONAL_HYPOTHESIS_ERROR
    E5006 = 6006  # ANTIBODY_REJECTION_ERROR

    # General
    E9001 = 9001  # UNKNOWN_ERROR
    E9002 = 9002  # INTERNAL_ERROR
    E9003 = 9003  # INVALID_ARGUMENT
    E9004 = 9004  # NOT_IMPLEMENTED


class OnyxError(Exception):
    """Base exception for all OnyxSi operations."""
    def __init__(self, code: OnyxErrorCode, message: str, details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{self.code.name}] {self.message}")

    def to_dict(self) -> Dict[str, Any]:
        info = {
            "code": self.code.name,
            "code_value": self.code.value,
            "message": self.message,
            "details": self.details,
        }
        return {
            "error": info,
            "code": self.code.name,
            "code_value": self.code.value,
            "message": self.message,
            "details": self.details,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass(slots=True, frozen=True)
class DataContract:
    """Base data contract for type-safe validation."""
    name: str
    fields: Dict[str, Any] = field(default_factory=dict)

    def validate(self, data: Dict[str, Any]) -> bool:
        for field_name, expected_type in self.fields.items():
            if field_name not in data:
                return False
            val = data[field_name]
            if isinstance(expected_type, type) and not isinstance(val, expected_type):
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "fields": {k: str(v) for k, v in self.fields.items()}}


@dataclass(slots=True, frozen=True)
class ConfigContract:
    """Configuration schema contract."""
    version: str
    settings: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)


@dataclass(slots=True, frozen=True)
class TestResult:
    """Standard test result record."""
    name: str
    passed: bool
    duration_ms: float
    error_message: Optional[str] = None
