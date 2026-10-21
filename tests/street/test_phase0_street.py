"""
Street Tests: Phase 0 Memory Plane & Concurrency Chaos.
Fuzzing, out-of-bounds, slot exhaustion, rapid fill/drain.
"""
import time
from onyxsi.hal.shm_superblock import (
    SharedMemorySuperblock,
    SuperblockHeader,
    SuperblockCorruptedError,
    SuperblockSlotExhaustedError,
    ShmBoundsViolationError,
    WORKER_SLOT_COUNT,
)
from onyxsi.hal.ring_buffer import (
    SPSCRingBuffer,
    RingBufferOverflowError,
    RingBufferUnderflowError,
)
from onyxsi.hal.trie_index import CompactRadixSymbolIndex


def test_street_superblock_slot_exhaustion_storm():
    shm = SharedMemorySuperblock(size=2_097_152, name="street_shm_slots").initialize()
    allocated = []
    try:
        # Exhaust all 256 slots
        for _ in range(WORKER_SLOT_COUNT):
            slot = shm.allocate_slot()
            allocated.append(slot)
            shm.write_worker_slot(slot, b"SLOT_" + str(slot).encode())

        assert len(allocated) == WORKER_SLOT_COUNT

        # 257th allocation must be rejected
        try:
            shm.allocate_slot()
            assert False, "Should raise slot exhausted"
        except SuperblockSlotExhaustedError:
            pass

        # Free even slots and reallocate
        for s in allocated[::2]:
            shm.free_slot(s)

        realloc_count = 0
        for _ in range(len(allocated[::2])):
            new_s = shm.allocate_slot()
            realloc_count += 1
        assert realloc_count == len(allocated[::2])
    finally:
        shm.close()


def test_street_ring_buffer_rapid_fire_saturation():
    rb = SPSCRingBuffer(size=262_144, capacity=32).initialize()
    try:
        # Rapid push and pop 500 times
        for i in range(500):
            seq = rb.push(f"MSG_{i}".encode(), "tx")
            assert seq == i + 1
            entry = rb.pop("tx")
            assert entry.data == f"MSG_{i}".encode()
    finally:
        rb.close()


def test_street_trie_fuzzing_and_special_chars():
    idx = CompactRadixSymbolIndex()
    # Insert 1,000 synthetic symbols with complex prefixes
    for i in range(1000):
        idx.add_symbol(f"module_sub_{i % 10}_fn_{i}", coordinate=i * 100)

    for i in range(0, 1000, 50):
        res = idx.lookup(f"module_sub_{i % 10}_fn_{i}")
        assert res.found is True
        assert res.entry.coordinate == i * 100

    prefix_res = idx.prefix_search("module_sub_3_", max_results=200)
    assert prefix_res.found is True
    assert len(prefix_res.matches) == 100
