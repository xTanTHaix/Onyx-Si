"""
OnyxSi HAL Subsystem (Dual-OS Headless Hardware Substrate).

Shared-memory superblock, SPSC ring buffer, compact radix symbol index,
process supervisor, sandbox enclave, syscall guard and cross-process wakeup.

100% Python Standard Library only. Zero External Dependencies. Zero UI.
"""

from onyxsi.hal.shm_superblock import (
    MAGIC_NUMBER,
    SUPERBLOCK_SIZE,
    HEADER_SIZE,
    SLAB_ARENA_OFFSET,
    SuperblockError,
    SuperblockCorruptedError,
    SuperblockSlotExhaustedError,
    ShmBoundsViolationError,
    SuperblockConfig,
    SuperblockHeader,
    SharedMemorySuperblock,
)
from onyxsi.hal.ring_buffer import (
    ALIGNMENT,
    RING_BUFFER_SIZE,
    ENTRY_SIZE,
    MAX_ENTRIES,
    RingBufferError,
    RingBufferOverflowError,
    RingBufferUnderflowError,
    RingBufferConfig,
    RingBufferEntry,
    SPSCRingBuffer,
)
from onyxsi.hal.trie_index import (
    MAX_SYMBOL_LENGTH,
    DEFAULT_CAPACITY,
    SymbolEntry,
    LookupResult,
    CompactRadixSymbolIndex,
)
from onyxsi.hal.process_manager import (
    ProcessManagerError,
    ProcessConfig,
    ProcessStatus,
    ProcessManager,
)
from onyxsi.hal.sandbox_enclave import (
    SandboxError,
    SandboxConfig,
    SandboxEnclave,
)
from onyxsi.hal.syscall_guard import (
    SyscallGuardError,
    SyscallGuardConfig,
    SyscallGuard,
)
from onyxsi.hal.wakeup import (
    WakeupError,
    WakeupConfig,
    WakeupSignal,
)
from onyxsi.hal.telemetry import ToolTelemetry

__all__ = [
    "MAGIC_NUMBER",
    "SUPERBLOCK_SIZE",
    "HEADER_SIZE",
    "SLAB_ARENA_OFFSET",
    "SuperblockError",
    "SuperblockCorruptedError",
    "SuperblockSlotExhaustedError",
    "ShmBoundsViolationError",
    "SuperblockConfig",
    "SuperblockHeader",
    "SharedMemorySuperblock",
    "ALIGNMENT",
    "RING_BUFFER_SIZE",
    "ENTRY_SIZE",
    "MAX_ENTRIES",
    "RingBufferError",
    "RingBufferOverflowError",
    "RingBufferUnderflowError",
    "RingBufferConfig",
    "RingBufferEntry",
    "SPSCRingBuffer",
    "MAX_SYMBOL_LENGTH",
    "DEFAULT_CAPACITY",
    "SymbolEntry",
    "LookupResult",
    "CompactRadixSymbolIndex",
    "ProcessManagerError",
    "ProcessConfig",
    "ProcessStatus",
    "ProcessManager",
    "SandboxError",
    "SandboxConfig",
    "SandboxEnclave",
    "SyscallGuardError",
    "SyscallGuardConfig",
    "SyscallGuard",
    "WakeupError",
    "WakeupConfig",
    "WakeupSignal",
    "ToolTelemetry",
]
