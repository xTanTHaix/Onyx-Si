"""
Normal Tests for Phase 7.6: cross-process leases + conflict-free AST merging.

Two independent registries sharing one lock directory must contend for the same
node (simulating two daemon processes), and ``op=merge_patches`` must combine
non-overlapping agent patches without writing to disk.
"""

import json
import os
import tempfile

import pytest

from onyxsi.deltafs.ast_lease_registry import (
    AstLeaseConflictError,
    AstLeaseRegistry,
)
from onyxsi.server import OnyxSiServer


def _call(server: OnyxSiServer, method: str, params: dict):
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    return json.loads(server.process_line(request))["result"]


def _preflight(server: OnyxSiServer, radius: list) -> None:
    result = _call(
        server,
        "causal_flight",
        {"stage": "pre_flight", "blast_radius": radius, "hypothesis": "merge edits"},
    )
    assert result["status"] == "ok", result


def test_cross_process_lease_blocks_a_second_registry():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "mod.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("def a():\n    return 1\n")

        lock_dir = os.path.join(tmpdir, "leases")
        first = AstLeaseRegistry(lock_dir=lock_dir)
        second = AstLeaseRegistry(lock_dir=lock_dir)

        first.acquire_lease(target, "func_a", "agent-a")
        assert second.is_leased(target, "func_a") is True

        with pytest.raises(AstLeaseConflictError) as excinfo:
            second.acquire_lease(target, "func_a", "agent-b")
        assert excinfo.value.current_holder == "agent-a"
        assert excinfo.value.remaining_sec > 0

        # A different node in the same file is still free.
        second.acquire_lease(target, "func_b", "agent-b")

        first.release_lease(target, "func_a", "agent-a")
        assert second.is_leased(target, "func_a") is False
        second.acquire_lease(target, "func_a", "agent-b")


def test_expired_lockfile_is_stolen():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "mod.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("def a():\n    return 1\n")

        lock_dir = os.path.join(tmpdir, "leases")
        first = AstLeaseRegistry(lock_dir=lock_dir)
        second = AstLeaseRegistry(lock_dir=lock_dir)

        first.acquire_lease(target, "func_a", "agent-a", ttl_sec=0.01)
        import time

        time.sleep(0.05)
        lease = second.acquire_lease(target, "func_a", "agent-b")
        assert lease.agent_id == "agent-b"


def test_merge_patches_stages_a_clean_merge():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "mod.py")
        base = "def a():\n    return 1\n\ndef b():\n    return 2\n"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(base)

        server = OnyxSiServer(workspace_root=tmpdir)
        first = base.index("return 1")
        second = base.index("return 2")
        _preflight(server, ["mod.py"])

        result = _call(
            server,
            "vfs_mutate",
            {
                "op": "merge_patches",
                "path": "mod.py",
                "agent_id": "agent-0",
                "patches": [
                    {
                        "agent_id": "agent-a",
                        "node_id": "func_a",
                        "start_byte": first,
                        "end_byte": first + len("return 1"),
                        "new_content": "return 10",
                        "new_imports": ["import os"],
                    },
                    {
                        "agent_id": "agent-b",
                        "node_id": "func_b",
                        "start_byte": second,
                        "end_byte": second + len("return 2"),
                        "new_content": "return 20",
                    },
                ],
            },
        )
        assert result["status"] == "ok", result
        assert result["op"] == "merge_patches"

        staged = _call(server, "vfs_inspect", {"action": "read", "target": "mod.py"})
        assert "return 10" in staged["content"]
        assert "return 20" in staged["content"]
        assert "import os" in staged["content"]

        # Disk must still hold the original bytes until an explicit commit.
        with open(path, "r", encoding="utf-8", newline="") as fh:
            assert fh.read() == base

        _call(server, "ghost_sandbox", {"cmd": "discard"})
        server.shutdown()


def test_overlapping_patches_are_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "mod.py")
        base = "def a():\n    return 1\n"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(base)

        server = OnyxSiServer(workspace_root=tmpdir)
        _preflight(server, ["mod.py"])
        result = _call(
            server,
            "vfs_mutate",
            {
                "op": "merge_patches",
                "path": "mod.py",
                "patches": [
                    {
                        "agent_id": "agent-a",
                        "node_id": "n1",
                        "start_byte": 0,
                        "end_byte": 10,
                        "new_content": "x",
                    },
                    {
                        "agent_id": "agent-b",
                        "node_id": "n2",
                        "start_byte": 5,
                        "end_byte": 15,
                        "new_content": "y",
                    },
                ],
            },
        )
        assert result["status"] == "error"
        assert result["error"] == "MERGE_CONFLICT"
        server.shutdown()


def test_two_agents_cannot_hold_the_same_node_lease():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "mod.py"), "w", encoding="utf-8") as fh:
            fh.write("def a():\n    return 1\n")

        server = OnyxSiServer(workspace_root=tmpdir)
        _preflight(server, ["mod.py"])
        first = _call(
            server,
            "vfs_mutate",
            {
                "op": "replace_node",
                "path": "mod.py",
                "node_id": "a",
                "agent_id": "agent-a",
                "code": "def a():\n    return 7\n",
            },
        )
        assert first["status"] == "ok", first

        second = _call(
            server,
            "vfs_mutate",
            {
                "op": "replace_node",
                "path": "mod.py",
                "node_id": "a",
                "agent_id": "agent-b",
                "code": "def a():\n    return 9\n",
            },
        )
        assert second["status"] == "error"
        assert second["error"] == "LEASE_CONFLICT"
        assert second["holder"] == "agent-a"
        _call(server, "ghost_sandbox", {"cmd": "discard"})
        server.shutdown()
