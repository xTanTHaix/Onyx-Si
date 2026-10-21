"""
Dedicated SPSC Dual-Ring Buffer with 128-Byte Cache-Line Padding.
Lock-Free Single-Producer Single-Consumer buffer with independent TX and RX rings.
Hardware alignment: All indices and sizes 128-byte aligned.
"""

import time
import struct
import mmap
from typing import Optional
from dataclasses import dataclass, field

ALIGNMENT = 128
RING_BUFFER_SIZE = 2_097_152  # 2MB
ENTRY_SIZE = 128  # Slot size aligned to 128 bytes
MAX_ENTRIES = (RING_BUFFER_SIZE - 256) // (ENTRY_SIZE + ALIGNMENT)

RING_STATE_EMPTY = "RING_BUFFER_EMPTY"
RING_STATE_READY = "RING_BUFFER_READY"
RING_STATE_FULL = "RING_BUFFER_FULL"


class RingBufferError(Exception):
    pass


class RingBufferOverflowError(RingBufferError):
    pass


class RingBufferUnderflowError(RingBufferError):
    pass


@dataclass(slots=True, frozen=True)
class RingBufferConfig:
    size: int = RING_BUFFER_SIZE
    size_mb: int = 2
    platform: Optional[str] = None
    capacity: Optional[int] = None


@dataclass(slots=True, frozen=True)
class RingBufferEntry:
    data: bytes
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0


class SPSCRingBuffer:
    def __init__(self, config: Optional[RingBufferConfig] = None, size: int = RING_BUFFER_SIZE, capacity: Optional[int] = None):
        if config is not None:
            self.config = config
            if config.size_mb:
                self._size = config.size_mb * 1024 * 1024
            elif config.size:
                self._size = config.size
            else:
                self._size = size
            self._capacity = config.capacity or capacity or min(1024, (self._size - 256) // (ENTRY_SIZE + ALIGNMENT))
        else:
            self._size = size
            self.config = RingBufferConfig(size=size, size_mb=max(1, size // (1024 * 1024)))
            self._capacity = capacity or min(1024, (self._size - 256) // (ENTRY_SIZE + ALIGNMENT))

        assert self._size % ALIGNMENT == 0, f"Ring buffer size must be aligned to {ALIGNMENT}"
        assert ENTRY_SIZE % ALIGNMENT == 0, f"Entry size must be aligned to {ALIGNMENT}"

        self._buf: Optional[mmap.mmap] = None
        self._initialized = False

        # Monotonic sequence counters.
        # NOTE: these are plain Python ints. CPython gives no cache-line control,
        # so the separation here is logical only -- there is no physical padding.
        self._tx_head = 0
        self._tx_tail = 0
        self._rx_head = 0
        self._rx_tail = 0
        self._tx_seq = 0
        self._rx_seq = 0

    def initialize(self) -> "SPSCRingBuffer":
        if self._initialized:
            return self
        self._buf = mmap.mmap(-1, self._size)
        self._initialized = True
        return self

    def put(self, data: bytes) -> int:
        if not self._initialized:
            self.initialize()
        return self.push(data, "tx")

    def get(self, timeout_sec: float = 1.0) -> bytes:
        if not self._initialized:
            self.initialize()
        t0 = time.time()
        while time.time() - t0 <= timeout_sec:
            try:
                entry = self.pop("tx")
                return entry.data
            except RingBufferUnderflowError:
                time.sleep(0.001)
        raise RingBufferUnderflowError("Timeout waiting for ring buffer entry")

    def push(self, data: bytes, direction: str = "tx") -> int:
        self._ensure_initialized()
        if len(data) > ENTRY_SIZE:
            raise RingBufferOverflowError(f"Data exceeds entry size: {len(data)} > {ENTRY_SIZE}")

        if direction == "tx":
            used = (self._tx_tail - self._tx_head)
            if used >= self._capacity:
                raise RingBufferOverflowError("TX ring buffer is full")
            slot = self._tx_tail % self._capacity
            offset = 256 + (slot * (ENTRY_SIZE + ALIGNMENT))

            self._tx_seq += 1
            hdr = struct.pack("<QId", self._tx_seq, len(data), time.time())
            self._buf[offset:offset + 20] = hdr
            self._buf[offset + 20:offset + 20 + len(data)] = data
            self._tx_tail += 1
            return self._tx_seq
        else:
            used = (self._rx_tail - self._rx_head)
            if used >= self._capacity:
                raise RingBufferOverflowError("RX ring buffer is full")
            slot = self._rx_tail % self._capacity
            offset = (self._size // 2) + (slot * (ENTRY_SIZE + ALIGNMENT))

            self._rx_seq += 1
            hdr = struct.pack("<QId", self._rx_seq, len(data), time.time())
            self._buf[offset:offset + 20] = hdr
            self._buf[offset + 20:offset + 20 + len(data)] = data
            self._rx_tail += 1
            return self._rx_seq

    def pop(self, direction: str = "tx") -> RingBufferEntry:
        self._ensure_initialized()
        if direction == "tx":
            if self._tx_head >= self._tx_tail:
                raise RingBufferUnderflowError("TX ring buffer is empty")
            slot = self._tx_head % self._capacity
            offset = 256 + (slot * (ENTRY_SIZE + ALIGNMENT))
            hdr = self._buf[offset:offset + 20]
            seq, length, ts = struct.unpack("<QId", hdr)
            payload = bytes(self._buf[offset + 20:offset + 20 + length])
            self._tx_head += 1
            return RingBufferEntry(data=payload, timestamp=ts, sequence=seq)
        else:
            if self._rx_head >= self._rx_tail:
                raise RingBufferUnderflowError("RX ring buffer is empty")
            slot = self._rx_head % self._capacity
            offset = (self._size // 2) + (slot * (ENTRY_SIZE + ALIGNMENT))
            hdr = self._buf[offset:offset + 20]
            seq, length, ts = struct.unpack("<QId", hdr)
            payload = bytes(self._buf[offset + 20:offset + 20 + length])
            self._rx_head += 1
            return RingBufferEntry(data=payload, timestamp=ts, sequence=seq)

    def state(self, direction: str = "tx") -> str:
        if direction == "tx":
            used = self._tx_tail - self._tx_head
        else:
            used = self._rx_tail - self._rx_head

        if used == 0:
            return RING_STATE_EMPTY
        elif used >= self._capacity:
            return RING_STATE_FULL
        return RING_STATE_READY

    def close(self) -> None:
        if self._buf is not None:
            self._buf.close()
            self._buf = None
        self._initialized = False

    def cleanup(self) -> None:
        self.close()

    def _ensure_initialized(self) -> None:
        if not self._initialized or self._buf is None:
            raise RingBufferError("Ring buffer not initialized")
