"""
Normal Tests for Phase 7.1: Turnkey Headless Black-Box Daemon Loop.
Verifies JSON-RPC 2.0 communication over process_line without UI,
and tests the complete toolchain execution.
"""

import os
import json
import tempfile
from onyxsi.server import OnyxSiServer


def test_headless_daemon_tools_list_and_meta_tools():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        raw_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        raw_resp = server.process_line(raw_req)
        assert raw_resp is not None

        resp = json.loads(raw_resp)
        assert resp["id"] == 1
        tools = resp["result"]["tools"]
        assert len(tools) == 8
        tool_names = [t["name"] for t in tools]
        assert "vfs_inspect" in tool_names
        assert "vfs_mutate" in tool_names
        assert "ghost_sandbox" in tool_names
        assert "causal_flight" in tool_names
        assert "wiki_task_sync" in tool_names
        assert "graph_compute" in tool_names
        server.shutdown()


def test_headless_daemon_end_to_end_causal_workflow():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)

        # 1. wiki_task_sync init
        req1 = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "wiki_task_sync",
            "params": {"task_id": "TASK-100", "op": "init", "spec": "# Flight Test"},
        })
        resp1 = json.loads(server.process_line(req1))
        assert resp1["result"]["status"] == "ok"

        # 2. causal_flight: pre_flight
        req2 = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "causal_flight",
            "params": {"stage": "pre_flight", "blast_radius": ["core.py"], "hypothesis": "Fix bug in core"},
        })
        resp2 = json.loads(server.process_line(req2))
        assert resp2["result"]["status"] == "ok"

        # 3. vfs_mutate inside ghost overlay
        req3 = json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "vfs_mutate",
            "params": {"path": "core.py", "code": "# patched core\nx = 42\n"},
        })
        resp3 = json.loads(server.process_line(req3))
        assert resp3["result"]["status"] == "ok"
        assert resp3["result"]["staged_in_ghost"] is True

        # 4. graph_compute: ALU evaluation
        req4 = json.dumps({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "graph_compute",
            "params": {"engine": "alu", "type": "eval", "query": "100 * 2 + 5"},
        })
        resp4 = json.loads(server.process_line(req4))
        assert resp4["result"]["result"] == 205

        # 5. wiki_task_sync handoff
        req5 = json.dumps({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "wiki_task_sync",
            "params": {"task_id": "TASK-100", "op": "handoff"},
        })
        resp5 = json.loads(server.process_line(req5))
        assert "# Agent Handoff Protocol: TASK-100" in resp5["result"]["handoff_sheet"]

        server.shutdown()
