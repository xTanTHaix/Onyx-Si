"""
Cross-process proof for the HAL shared-memory superblock.

The old POSIX implementation used ``memfd_create``, which cannot be opened by
name from another process — so the "shared" superblock was actually private on
Linux. This test spawns a real child process and asserts that the parent sees
the child's bytes through ``SharedMemorySuperblock.attach()``.

It also proves the slot allocator and slab arena are usable from two processes
at once, which is what the HAL claims.
"""

import json
import os
import subprocess
import sys
import uuid

import pytest

from onyxsi.hal.shm_superblock import (
    SharedMemorySuperblock,
    SuperblockConfig,
    SuperblockCorruptedError,
    SuperblockError,
)

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
SIZE = 1024 * 1024  # 1MB is plenty for the test and keeps the mmap small

CHILD = r"""
import json
import sys

sys.path.insert(0, sys.argv[2])

from onyxsi.hal.shm_superblock import SharedMemorySuperblock, SuperblockConfig

name = sys.argv[1]
block = SharedMemorySuperblock(SuperblockConfig(size_mb=1, name=name)).initialize()
slot = block.allocate_slot()
block.write_worker_slot(slot, b"hello-from-child")
block.write_slab(0, b"slab-payload")
print(json.dumps({"slot": slot}), flush=True)
sys.stdin.readline()
block.close()
"""


def _clean_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    return env


@pytest.mark.skipif(os.name != "nt" and not os.path.isdir("/dev/shm"), reason="needs named shared memory")
def test_parent_attaches_to_child_superblock():
    name = f"onyxsi_test_{uuid.uuid4().hex}"
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, name, SRC],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_clean_env(),
    )
    block = None
    try:
        ready = proc.stdout.readline()
        assert ready, f"child failed: {proc.stderr.read()}"
        slot = json.loads(ready)["slot"]

        block = SharedMemorySuperblock.attach(name, size=SIZE)
        assert block.get_header().magic == block.config.magic
        assert block.read_worker_slot(slot, 16).rstrip(b"\x00") == b"hello-from-child"
        assert block.read_slab(0, 12) == b"slab-payload"

        # The parent can allocate its own slot in the same bitmap.
        parent_slot = block.allocate_slot()
        assert parent_slot != slot
    finally:
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.write("\n")
                proc.stdin.flush()
            except OSError:
                pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=5)
        if block is not None:
            block.close()


def test_attach_to_missing_superblock_fails_loudly():
    with pytest.raises((SuperblockError, SuperblockCorruptedError)):
        SharedMemorySuperblock.attach(f"onyxsi_missing_{uuid.uuid4().hex}", size=SIZE)


def test_two_blocks_in_one_process_see_the_same_bytes():
    """Sanity check for the tag/path layer without a second interpreter."""
    name = f"onyxsi_same_{uuid.uuid4().hex}"
    writer = SharedMemorySuperblock(SuperblockConfig(size_mb=1, name=name)).initialize()
    reader = None
    try:
        slot = writer.allocate_slot()
        writer.write_worker_slot(slot, b"ping")
        reader = SharedMemorySuperblock.attach(name, size=SIZE)
        assert reader.read_worker_slot(slot, 4) == b"ping"
        reader.write_worker_slot(slot, b"pong")
        assert writer.read_worker_slot(slot, 4) == b"pong"
    finally:
        if reader is not None:
            reader.close()
        writer.close()
