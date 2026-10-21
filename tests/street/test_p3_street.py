"""
Street Chaos Tests for Phase 3: Ghost-CoW, DeltaFS & Multi-Agent AST Merger.
Stress-tests rapid in-RAM discard churn, sparse page spillover, and concurrent lease contention.
"""

import tempfile
from onyxsi.deltafs.ghost_overlay import GhostOverlayFS
from onyxsi.deltafs.cow_workspace import SparsePageDeltaMap, PAGE_SIZE
from onyxsi.deltafs.ast_lease_registry import AstLeaseRegistry, AstLeaseConflictError
from onyxsi.deltafs.ast_conflict_merger import ConflictFreeAstMerger, AgentPatch, AstMergeConflictError


def test_street_ghost_overlay_rapid_churn():
    with tempfile.TemporaryDirectory() as tmpdir:
        ghost = GhostOverlayFS(workspace_root=tmpdir)
        for i in range(200):
            ghost.ghost_begin()
            ghost.ghost_mutate(f"file_{i}.txt", f"data_{i}")
            ghost.ghost_discard()
        assert ghost.is_active() is False


def test_street_sparse_page_delta_stress():
    base = b"\x00" * (PAGE_SIZE * 20)
    pdm = SparsePageDeltaMap(page_size=PAGE_SIZE)

    for p in range(0, 20, 2):
        pdm.write_at(p * PAGE_SIZE + 10, b"DIRTY_PAGE", base_bytes=base)

    assert pdm.dirty_pages_count() == 10

    for p in range(1, 20, 2):
        data = pdm.read_at(p * PAGE_SIZE, 10, base_bytes=base)
        assert data == b"\x00" * 10


def test_street_ast_lease_stampede():
    registry = AstLeaseRegistry(default_ttl_sec=30.0)
    file_path = "core/engine.py"
    target_node = "ast_fn_dispatch"

    lease0 = registry.acquire_lease(file_path, target_node, "agent_0")
    assert lease0.agent_id == "agent_0"

    conflict_count = 0
    for i in range(1, 10):
        try:
            registry.acquire_lease(file_path, target_node, f"agent_{i}")
        except AstLeaseConflictError:
            conflict_count += 1

    assert conflict_count == 9


def test_street_ast_merger_overlapping_conflict_detected():
    base_code = "def sample(): return 123\n"
    patch1 = AgentPatch("a1", "node1", 14, 24, "return 456")
    patch2 = AgentPatch("a2", "node2", 18, 24, "return 789")

    try:
        ConflictFreeAstMerger.merge_patches(base_code, [patch1, patch2])
        raise AssertionError("Expected AstMergeConflictError")
    except AstMergeConflictError:
        pass
