"""
Dual-OS 64MB Superblock Shared Memory Allocator.
Structure:
- Header (128 bytes): Magic (0x4F4E5958), version, timestamps, flags
- Bitmap Slot Table (64KB): Slot allocations
- Worker Slot Table (256 slots x 256 bytes)
- Slab Payload Arena (Remaining of size)
"""

import os
import sys
import struct
import time
import mmap
import tempfile
from typing import Optional, Any
from dataclasses import dataclass, field

MAGIC_NUMBER = 0x4F4E5958  # 'ONYX'
SUPERBLOCK_VERSION = 1
SUPERBLOCK_SIZE = 67_108_864  # 64MB
BITMAP_SIZE = 65_536  # 64KB
WORKER_SLOT_COUNT = 256
WORKER_SLOT_SIZE = 256
HEADER_SIZE = 128


def _posix_shm_path(name: str) -> str:
    """
    Resolves the backing file for a named POSIX superblock.

    ``memfd_create`` cannot be opened by name from another process, so a
    ``memfd`` is *not* a shared-memory primitive for IPC. We use ``/dev/shm``
    (tmpfs, memory-backed) and fall back to the temp dir on systems without it.
    """
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    shm_dir = "/dev/shm"
    if not os.path.isdir(shm_dir):
        shm_dir = tempfile.gettempdir()
    return os.path.join(shm_dir, f"onyxsi_{safe}.shm")
SLAB_ARENA_OFFSET = HEADER_SIZE + BITMAP_SIZE + (WORKER_SLOT_COUNT * WORKER_SLOT_SIZE)


class SuperblockError(Exception):
    pass


class SuperblockCorruptedError(SuperblockError):
    pass


class SuperblockSlotExhaustedError(SuperblockError):
    pass


class ShmBoundsViolationError(SuperblockError):
    pass


@dataclass(slots=True, frozen=True)
class SuperblockConfig:
    size: int = SUPERBLOCK_SIZE
    size_mb: int = 64
    magic: int = MAGIC_NUMBER
    name: Optional[str] = None


@dataclass(slots=True, frozen=True)
class SuperblockHeader:
    magic: int = MAGIC_NUMBER
    version: int = SUPERBLOCK_VERSION
    created_at: float = field(default_factory=time.time)
    last_modified: float = field(default_factory=time.time)
    flags: int = 0
    reserved: int = 0

    def to_bytes(self) -> bytes:
        packed = struct.pack(
            "<IIddQQ",
            self.magic,
            self.version,
            float(self.created_at),
            float(self.last_modified),
            self.flags,
            self.reserved,
        )
        return packed + (bytes([0]) * (128 - len(packed)))

    @classmethod
    def from_bytes(cls, data: bytes) -> "SuperblockHeader":
        if len(data) < 40:
            raise SuperblockCorruptedError("Header too short")
        magic, version, created_at, last_modified, flags, reserved = struct.unpack(
            "<IIddQQ", data[:40]
        )
        if magic != MAGIC_NUMBER:
            raise SuperblockCorruptedError(f"Invalid magic number: 0x{magic:08X}")
        return cls(
            magic=magic,
            version=version,
            created_at=created_at,
            last_modified=last_modified,
            flags=flags,
            reserved=reserved,
        )


class SharedMemorySuperblock:
    def __init__(self, config: Optional[SuperblockConfig] = None, size: int = SUPERBLOCK_SIZE, name: Optional[str] = None):
        if config is not None:
            self.config = config
            if config.size_mb:
                self.size = config.size_mb * 1024 * 1024
            elif config.size:
                self.size = config.size
            else:
                self.size = size
            self.name = config.name or name or f"onyx_shm_{os.getpid()}"
        else:
            self.size = size
            self.name = name or f"onyx_shm_{os.getpid()}"
            self.config = SuperblockConfig(size=self.size, size_mb=max(1, self.size // (1024 * 1024)), name=self.name)

        self.slab_arena_size = max(0, self.size - SLAB_ARENA_OFFSET)
        self._buf: Optional[mmap.mmap] = None
        self._fd: Optional[int] = None
        self._initialized = False
        self._shm_path: Optional[str] = None
        self._owns_backing: bool = False

    @classmethod
    def attach(cls, name: str, size: int = SUPERBLOCK_SIZE) -> "SharedMemorySuperblock":
        """
        Maps a superblock created by another process (same machine, same size).

        Windows uses a tagged mapping; POSIX uses a ``/dev/shm`` file. Both are
        real cross-process views of the same bytes, unlike ``memfd_create``.
        """
        block = cls(size=size, name=name)
        if sys.platform == "win32":
            try:
                block._buf = mmap.mmap(
                    -1, block.size, tagname=block.name, access=mmap.ACCESS_WRITE
                )
            except OSError as exc:  # pragma: no cover - platform specific
                raise SuperblockError(f"Cannot attach to '{name}': {exc}") from exc
        else:
            path = _posix_shm_path(name)
            if not os.path.exists(path):
                raise SuperblockError(f"No shared superblock named '{name}' at {path}")
            block._shm_path = path
            block._fd = os.open(path, os.O_RDWR)
            block._buf = mmap.mmap(
                block._fd, block.size, mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE,
            )
        block._initialized = True
        if block.get_header().magic != block.config.magic:
            block.close()
            raise SuperblockCorruptedError(f"No valid superblock named '{name}'")
        return block

    def initialize(self) -> "SharedMemorySuperblock":
        if self._initialized:
            return self

        if sys.platform == "win32":
            self._buf = mmap.mmap(-1, self.size, tagname=self.name, access=mmap.ACCESS_WRITE)
        else:
            # A named file in /dev/shm is shareable by name across processes.
            path = _posix_shm_path(self.name)
            existed = os.path.exists(path)
            self._shm_path = path
            self._fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if os.fstat(self._fd).st_size < self.size:
                    os.ftruncate(self._fd, self.size)
            except OSError:  # pragma: no cover - exotic filesystems
                pass
            self._owns_backing = not existed
            self._buf = mmap.mmap(
                self._fd, self.size, mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE,
            )

        header = SuperblockHeader(magic=self.config.magic)
        header_bytes = header.to_bytes()
        self._buf[:len(header_bytes)] = header_bytes
        self._initialized = True
        return self

    def allocate(self) -> "SharedMemorySuperblock":
        """
        Alias for :meth:`initialize`.

        Maintained for backwards compatibility with earlier allocator lifecycle APIs.
        """
        return self.initialize()

    def get_header(self) -> SuperblockHeader:
        self._ensure_initialized()
        data = bytes(self._buf[:128])
        return SuperblockHeader.from_bytes(data)

    def allocate_slot(self) -> int:
        self._ensure_initialized()
        start_offset = HEADER_SIZE
        for i in range(WORKER_SLOT_COUNT):
            byte_idx = start_offset + (i // 8)
            bit_idx = i % 8
            current_byte = self._buf[byte_idx]
            if not (current_byte & (1 << bit_idx)):
                self._buf[byte_idx] = current_byte | (1 << bit_idx)
                return i
        raise SuperblockSlotExhaustedError("All worker slots exhausted")

    def free_slot(self, slot_idx: int) -> None:
        self._ensure_initialized()
        if not (0 <= slot_idx < WORKER_SLOT_COUNT):
            raise ShmBoundsViolationError(f"Slot index out of range: {slot_idx}")
        byte_idx = HEADER_SIZE + (slot_idx // 8)
        bit_idx = slot_idx % 8
        current_byte = self._buf[byte_idx]
        self._buf[byte_idx] = current_byte & ~(1 << bit_idx)

    def write_worker_slot(self, slot_idx: int, data: bytes) -> None:
        self._ensure_initialized()
        if not (0 <= slot_idx < WORKER_SLOT_COUNT):
            raise ShmBoundsViolationError(f"Slot index out of bounds: {slot_idx}")
        if len(data) > WORKER_SLOT_SIZE:
            raise ShmBoundsViolationError(f"Data exceeds slot size ({len(data)} > {WORKER_SLOT_SIZE})")
        offset = HEADER_SIZE + BITMAP_SIZE + (slot_idx * WORKER_SLOT_SIZE)
        self._buf[offset:offset + len(data)] = data

    def read_worker_slot(self, slot_idx: int, length: int = WORKER_SLOT_SIZE) -> bytes:
        self._ensure_initialized()
        if not (0 <= slot_idx < WORKER_SLOT_COUNT):
            raise ShmBoundsViolationError(f"Slot index out of bounds: {slot_idx}")
        offset = HEADER_SIZE + BITMAP_SIZE + (slot_idx * WORKER_SLOT_SIZE)
        return bytes(self._buf[offset:offset + length])

    def write_slab(self, offset: int, data: bytes) -> None:
        self._ensure_initialized()
        if offset < 0 or offset + len(data) > self.slab_arena_size:
            raise ShmBoundsViolationError(f"Slab write out of bounds: offset {offset}, len {len(data)}, max {self.slab_arena_size}")
        target_offset = SLAB_ARENA_OFFSET + offset
        self._buf[target_offset:target_offset + len(data)] = data

    def read_slab(self, offset: int, length: int) -> bytes:
        self._ensure_initialized()
        if offset < 0 or offset + length > self.slab_arena_size:
            raise ShmBoundsViolationError(f"Slab read out of bounds: offset {offset}, len {length}, max {self.slab_arena_size}")
        target_offset = SLAB_ARENA_OFFSET + offset
        return bytes(self._buf[target_offset:target_offset + length])

    def close(self) -> None:
        if self._buf is not None:
            try:
                self._buf.close()
            except (BufferError, OSError):
                pass
            self._buf = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        if self._owns_backing and self._shm_path:
            # Only the creator unlinks; a reader that attached keeps a valid
            # mapping until it unmaps (POSIX inode semantics).
            try:
                os.unlink(self._shm_path)
            except OSError:
                pass
            self._owns_backing = False
        self._shm_path = None
        self._initialized = False

    def cleanup(self) -> None:
        """
        Alias for :meth:`close`.

        Maintained to provide a uniform cleanup interface across HAL resource managers.
        """
        self.close()

    def _ensure_initialized(self) -> None:
        if not self._initialized or self._buf is None:
            raise SuperblockError("Superblock not initialized")
