"""
Normal Tests for Phase 3.2: Tier-1 Dual-OS CoW DeltaFS & Sparse Page-Delta Map.
"""

from onyxsi.deltafs.cow_workspace import (
    SparsePageDeltaMap,
    DualOsCowWorkspace,
    PAGE_SIZE,
)


def test_sparse_page_delta_map_granularity():
    base = b"A" * (PAGE_SIZE * 3)  # 12KB base data
    pdm = SparsePageDeltaMap(page_size=PAGE_SIZE)

    # Write 10 bytes in the middle of page 1 (offset 4100)
    pdm.write_at(4100, b"1234567890", base_bytes=base)

    # Only page 1 should be marked dirty
    assert pdm.dirty_pages_count() == 1
    assert pdm.total_dirty_bytes() == PAGE_SIZE

    # Read back unchanged page 0
    read_p0 = pdm.read_at(0, 10, base_bytes=base)
    assert read_p0 == b"A" * 10

    # Read back modified region in page 1
    read_mod = pdm.read_at(4100, 10, base_bytes=base)
    assert read_mod == b"1234567890"


def test_dual_os_cow_workspace_fallback():
    cow = DualOsCowWorkspace()
    pdm = cow.get_delta_map("test_file.bin")
    assert pdm is not None
    assert pdm.dirty_pages_count() == 0
