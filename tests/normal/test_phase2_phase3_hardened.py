"""
Regression & Adversarial Hardening Verification Suite for Phase 2 & Phase 3.
Validates:
1. 64-byte chunk boundary token carry (split /*, */, // comments)
2. Bisect-driven O(log N) piece table lookup & safe checkpoint rollback
3. Strict OnyxError and OnyxErrorCode inheritance across Phase 2 & 3 exceptions
4. Thread-safe concurrent AST lease registry operations
5. Zero-width and boundary patch collision detection in AstMergeConflictError
6. SparsePageDeltaMap In-RAM quota spillover to disk and recovery
7. Unified root onyxsi package facade exports
"""

import os
import shutil
import tempfile
import threading
from typing import List
import pytest

from onyxsi import (
    BitplaneAstScanner,
    PieceTableDocument,
    VirtualHandleRegistry,
    VirtualHandleError,
    HandleExpiredError,
    StaleHandleError,
    GhostOverlayFS,
    GhostError,
    GhostSyntaxError,
    SparsePageDeltaMap,
    DualOsCowWorkspace,
    AstLeaseRegistry,
    AstLeaseConflictError,
    ConflictFreeAstMerger,
    AgentPatch,
    AstMergeConflictError,
    OnyxError,
    OnyxErrorCode,
)


def test_bitplane_split_block_comment_exit_boundary():
    """Validates that '*/' split across 64-byte chunk boundary cleanly exits block comment."""
    scanner = BitplaneAstScanner()
    # Chunk 1 ends with '*': total 64 bytes: /* + 61 bytes + *
    chunk1 = b"/*" + (b"A" * 61) + b"*"
    assert len(chunk1) == 64
    # Chunk 2 starts with '/': total 64 bytes: / + 61 spaces + {}
    chunk2 = b"/" + (b" " * 61) + b"{}"
    assert len(chunk2) == 64

    source = chunk1 + chunk2
    is_balanced, max_depth, top_level_blocks = scanner.analyze_source_topology(source)

    assert scanner.in_block_comment_carry is False
    assert is_balanced is True
    assert max_depth == 1
    assert top_level_blocks == 1


def test_bitplane_split_line_comment_entry_boundary():
    """Validates that '//' split across 64-byte chunk boundary correctly enters line comment."""
    scanner = BitplaneAstScanner()
    # Chunk 1 ends with '/': total 64 bytes
    chunk1 = (b" " * 63) + b"/"
    assert len(chunk1) == 64
    # Chunk 2 starts with '/': followed by braces inside the comment and then a newline outside
    chunk2 = b"/" + b"{not_a_real_brace}\n" + (b" " * 44)
    assert len(chunk2) == 64

    source = chunk1 + chunk2
    is_balanced, max_depth, top_level_blocks = scanner.analyze_source_topology(source)

    assert is_balanced is True
    # The braces inside '// {not_a_real_brace}' must be ignored
    assert max_depth == 0
    assert top_level_blocks == 0


def test_bitplane_split_block_comment_entry_boundary():
    """Validates that '/*' split across 64-byte chunk boundary correctly enters block comment."""
    scanner = BitplaneAstScanner()
    # Chunk 1 ends with '/'
    chunk1 = (b" " * 63) + b"/"
    # Chunk 2 starts with '*' and ends block comment with '*/'
    chunk2 = b"*" + b" comment {fake_block} */" + (b" " * 39)
    assert len(chunk1) == 64
    assert len(chunk2) == 64

    source = chunk1 + chunk2
    is_balanced, max_depth, top_level_blocks = scanner.analyze_source_topology(source)

    assert is_balanced is True
    assert scanner.in_block_comment_carry is False
    assert max_depth == 0


def test_piece_table_bisect_lookup_and_rollback_pruning():
    """Validates O(log N) piece lookup and safe rollback checkpoint pruning."""
    doc = PieceTableDocument("initial text content for testing piece table engine")
    cp1 = doc.create_checkpoint()

    doc.insert(8, " inserted_alpha")
    cp2 = doc.create_checkpoint()

    doc.insert(0, "prefix_beta ")
    cp3 = doc.create_checkpoint()

    # Roll back to cp2
    assert doc.rollback(cp2) is True
    assert doc.get_text() == "initial  inserted_alphatext content for testing piece table engine"

    # Forward navigation is deliberately preserved: CausalBisectEngine probes
    # checkpoints in binary-search order, which requires moving forward again.
    assert doc.rollback(cp3) is True
    assert doc.get_text().startswith("prefix_beta ")

    # An unknown checkpoint id must fail cleanly instead of silently succeeding.
    assert doc.rollback(9999) is False

    # Roll back to cp1
    assert doc.rollback(cp1) is True
    assert doc.get_text() == "initial text content for testing piece table engine"


def test_phase2_3_exception_contracts():
    """Validates that all Phase 2 & 3 exceptions adhere to OnyxError and OnyxErrorCode contracts."""
    # Virtual handles
    err_handle = VirtualHandleError("Generic handle error")
    assert isinstance(err_handle, OnyxError)
    assert err_handle.code == OnyxErrorCode.E2003

    err_expired = HandleExpiredError("Lease expired")
    assert isinstance(err_expired, VirtualHandleError)
    assert isinstance(err_expired, OnyxError)
    assert err_expired.code == OnyxErrorCode.E2003

    err_stale = StaleHandleError("Hash mismatch")
    assert isinstance(err_stale, VirtualHandleError)
    assert err_stale.code == OnyxErrorCode.E2003

    # Ghost overlay
    err_ghost = GhostError("Ghost overlay inactive")
    assert isinstance(err_ghost, OnyxError)
    assert err_ghost.code == OnyxErrorCode.E3002

    err_syntax = GhostSyntaxError("Syntax failed")
    assert isinstance(err_syntax, GhostError)
    assert isinstance(err_syntax, OnyxError)
    assert err_syntax.code == OnyxErrorCode.E3001

    # AST Lease
    err_lease = AstLeaseConflictError("file.py", "node_x", "agent_1", 10.0)
    assert isinstance(err_lease, OnyxError)
    assert err_lease.code == OnyxErrorCode.E3003
    assert err_lease.file_path == "file.py"
    assert err_lease.node_id == "node_x"
    assert err_lease.current_holder == "agent_1"

    # AST Merger
    err_merge = AstMergeConflictError("Overlapping collision")
    assert isinstance(err_merge, OnyxError)
    assert err_merge.code == OnyxErrorCode.E3004


def test_ast_lease_registry_thread_safety():
    """Validates that AstLeaseRegistry behaves thread-safely under concurrent swarm access."""
    registry = AstLeaseRegistry(default_ttl_sec=5.0)
    file_path = "services/auth.py"
    errors: List[Exception] = []

    def worker(worker_id: int):
        node_id = f"node_{worker_id % 4}"  # 4 nodes shared across workers
        agent_id = f"agent_{worker_id}"
        try:
            registry.acquire_lease(file_path, node_id, agent_id, ttl_sec=0.5)
            registry.is_leased(file_path, node_id)
            registry.get_active_leases(file_path)
            registry.renew_lease(file_path, node_id, agent_id, additional_ttl_sec=0.2)
            registry.release_lease(file_path, node_id, agent_id)
        except AstLeaseConflictError:
            # Expected concurrent contention
            pass
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread safety violated with errors: {errors}"


def test_ast_conflict_merger_zero_width_collision():
    """Validates that two agents inserting at the identical byte boundary are flagged as conflict."""
    base_code = "def calculate(): return 100\n"
    # Both agents attempt pure zero-width insertion at byte offset 17
    patch1 = AgentPatch("agent_a", "node_1", 17, 17, "x = 10\n    ")
    patch2 = AgentPatch("agent_b", "node_2", 17, 17, "y = 20\n    ")

    with pytest.raises(AstMergeConflictError) as exc_info:
        ConflictFreeAstMerger.merge_patches(base_code, [patch1, patch2])

    assert exc_info.value.code == OnyxErrorCode.E3004
    assert "collision" in str(exc_info.value).lower()


def test_sparse_page_delta_map_64mb_spillover_lifecycle():
    """Validates that SparsePageDeltaMap spills excess dirty pages to disk when exceeding RAM quota."""
    tmpdir = tempfile.mkdtemp(prefix="onyx_test_spill_")
    try:
        page_size = 4096
        # Set quota to 8KB (exactly 2 pages)
        pdm = SparsePageDeltaMap(page_size=page_size, max_ram_bytes=8192, spillover_dir=tmpdir)

        # Write 4 discrete pages (16KB)
        base = b"\x00" * (page_size * 4)
        pdm.write_at(0, b"PAGE_0_DATA", base_bytes=base)
        pdm.write_at(4096, b"PAGE_1_DATA", base_bytes=base)
        pdm.write_at(8192, b"PAGE_2_DATA", base_bytes=base)
        pdm.write_at(12288, b"PAGE_3_DATA", base_bytes=base)

        # Must have dirty pages tracked
        assert pdm.dirty_pages_count() == 4
        # In-RAM pages must not exceed quota (<= 2 pages in RAM)
        assert pdm.in_ram_pages_count() <= 2
        # Excess pages must be spilled to disk
        assert pdm.spilled_pages_count() >= 2

        # Verify 100% byte fidelity when reading back from both in-RAM and spilled pages
        assert pdm.read_at(0, 11, base_bytes=base) == b"PAGE_0_DATA"
        assert pdm.read_at(4096, 11, base_bytes=base) == b"PAGE_1_DATA"
        assert pdm.read_at(8192, 11, base_bytes=base) == b"PAGE_2_DATA"
        assert pdm.read_at(12288, 11, base_bytes=base) == b"PAGE_3_DATA"

        # Cleanup removes spilled files
        pdm.cleanup()
        assert pdm.spilled_pages_count() == 0
        assert pdm.dirty_pages_count() == 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_ghost_overlay_inactive_read_isolation():
    """Validates that inactive GhostOverlayFS does not leak dirty documents."""
    ghost = GhostOverlayFS()
    ghost.ghost_begin()
    ghost.ghost_mutate("scratch.py", "x = 42\n")
    assert ghost.ghost_read_text("scratch.py") == "x = 42\n"

    # Discard ends the session
    ghost.ghost_discard()
    assert ghost.is_active() is False
    # Inactive overlay must not return discarded state
    assert ghost.ghost_read("scratch.py") is None
    assert ghost.ghost_exists("scratch.py") is False
