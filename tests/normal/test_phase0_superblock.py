"""
Normal Tests: Phase 0.2 Dual-OS 64MB Superblock Shared Memory Allocator.
"""
import os
from onyxsi.hal.shm_superblock import (
    SharedMemorySuperblock,
    SuperblockHeader,
    MAGIC_NUMBER,
    SUPERBLOCK_VERSION,
    ShmBoundsViolationError,
    SuperblockSlotExhaustedError,
)


def test_superblock_initialization_and_header():
    shm = SharedMemorySuperblock(size=1_048_576, name="test_shm_init").initialize()
    try:
        header = shm.get_header()
        assert header.magic == MAGIC_NUMBER
        assert header.version == SUPERBLOCK_VERSION
        assert header.created_at > 0
    finally:
        shm.close()


def test_superblock_slot_allocation_and_free():
    shm = SharedMemorySuperblock(size=1_048_576, name="test_shm_slots").initialize()
    try:
        slot0 = shm.allocate_slot()
        slot1 = shm.allocate_slot()
        assert slot0 == 0
        assert slot1 == 1

        shm.write_worker_slot(slot0, b"HELLO_WORKER_0")
        data0 = shm.read_worker_slot(slot0, 14)
        assert data0 == b"HELLO_WORKER_0"

        shm.free_slot(slot0)
        slot0_reused = shm.allocate_slot()
        assert slot0_reused == 0
    finally:
        shm.close()


def test_superblock_slab_read_write_and_bounds():
    shm = SharedMemorySuperblock(size=1_048_576, name="test_shm_slab").initialize()
    try:
        shm.write_slab(0, b"SLAB_PAYLOAD_DATA")
        read_back = shm.read_slab(0, 17)
        assert read_back == b"SLAB_PAYLOAD_DATA"

        try:
            shm.write_slab(-1, b"BAD")
            assert False, "Should raise bounds violation"
        except ShmBoundsViolationError:
            pass
    finally:
        shm.close()
