"""
Normal Tests: Phase 0.3 Dedicated SPSC Dual-Ring Buffer (128-Byte Alignment).
"""
from onyxsi.hal.ring_buffer import (
    SPSCRingBuffer,
    RingBufferOverflowError,
    RingBufferUnderflowError,
    RING_STATE_EMPTY,
    RING_STATE_READY,
    RING_STATE_FULL,
)


def test_ring_buffer_push_pop_fifo():
    rb = SPSCRingBuffer(size=262_144, capacity=16).initialize()
    try:
        assert rb.state("tx") == RING_STATE_EMPTY
        seq1 = rb.push(b"MSG_1", direction="tx")
        seq2 = rb.push(b"MSG_2", direction="tx")
        assert seq1 == 1
        assert seq2 == 2
        assert rb.state("tx") == RING_STATE_READY

        item1 = rb.pop(direction="tx")
        assert item1.data == b"MSG_1"
        assert item1.sequence == 1

        item2 = rb.pop(direction="tx")
        assert item2.data == b"MSG_2"
        assert item2.sequence == 2
        assert rb.state("tx") == RING_STATE_EMPTY
    finally:
        rb.close()


def test_ring_buffer_underflow_and_overflow():
    rb = SPSCRingBuffer(size=262_144, capacity=2).initialize()
    try:
        # Underflow check
        try:
            rb.pop(direction="tx")
            assert False, "Should raise underflow"
        except RingBufferUnderflowError:
            pass

        # Fill to capacity
        rb.push(b"A", "tx")
        rb.push(b"B", "tx")
        assert rb.state("tx") == RING_STATE_FULL

        # Overflow check
        try:
            rb.push(b"C", "tx")
            assert False, "Should raise overflow"
        except RingBufferOverflowError:
            pass
    finally:
        rb.close()
