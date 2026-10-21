"""
OnyxSi ContextFS Subsystem.
Zero-Dependency DBP (bit-parallel / SWAR) Bitplane AST Engine, Piece Table & Virtual Handles.
"""

from onyxsi.contextfs.bitplane_ast import (
    CodeChunkBitplanes,
    BitplaneAstScanner,
    AstNodeSpan,
    SkeletonResult,
    BitplaneAstPruner,
)
from onyxsi.contextfs.piece_table import (
    PieceTableDocument,
    NanoCheckpoint,
    PieceRecord,
)
from onyxsi.contextfs.vfs_scanner import VfsScanner, VfsNode, VfsSummary
from onyxsi.contextfs.token_governor import TokenGovernor, TokenBudgetResult
from onyxsi.contextfs.virtual_handles import (
    VirtualHandleRegistry,
    VirtualHandle,
    VirtualHandleError,
    StaleHandleError,
    HandleExpiredError,
)

__all__ = [
    "CodeChunkBitplanes",
    "BitplaneAstScanner",
    "AstNodeSpan",
    "SkeletonResult",
    "BitplaneAstPruner",
    "PieceTableDocument",
    "NanoCheckpoint",
    "PieceRecord",
    "VfsScanner",
    "VfsNode",
    "VfsSummary",
    "TokenGovernor",
    "TokenBudgetResult",
    "VirtualHandleRegistry",
    "VirtualHandle",
    "VirtualHandleError",
    "StaleHandleError",
    "HandleExpiredError",
]
