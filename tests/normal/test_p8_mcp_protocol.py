"""
Normal Tests for the MCP protocol surface (Phase 8).

The MCP client lifecycle is what an editor integration actually depends on:
initialize -> notifications/initialized -> ping -> tools/list -> tools/call ->
resources/list -> resources/read. Every one of those is exercised here over
the real JSON-RPC dispatcher so a regression in the protocol layer is caught
before an editor is left with a half-connected server.
"""

import json
import os
import subprocess
import sys
import tempfile

from onyxsi.gateway.meta_router import FIXED_8_META_TOOLS
from onyxsi.server import OnyxSiServer

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BRIDGE = os.path.join(_REPO_ROOT, "onyxsi-mcp.py")


def _rpc(server, method, params=None, req_id=1):
    request = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
    return server.router.dispatch_rpc(request)


def _line(server, payload):
    """Sends a raw frame through the daemon's stdio entry point."""
    raw = server.process_line(json.dumps(payload))
    return json.loads(raw) if raw else None


def test_initialize_reports_protocol_version_and_capabilities():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        reply = _rpc(
            server,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1.0"},
            },
        )
        result = reply["result"]
        assert result["protocolVersion"] == "2025-06-18"
        assert result["serverInfo"]["name"] == "onyxsi-headless-kernel"
        assert result["capabilities"]["tools"]["listChanged"] is False
        assert result["capabilities"]["resources"]["subscribe"] is False


def test_ping_is_answered():
    """The MCP spec requires a liveness probe to be answered."""
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        reply = _rpc(server, "ping")
        assert reply["result"] == {}


def test_initialized_notification_gets_no_reply():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        reply = _line(server, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert reply is None


def test_tools_list_exposes_the_fixed_eight_with_closed_schemas():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        tools = _rpc(server, "tools/list")["result"]["tools"]
        assert [t["name"] for t in tools] == list(FIXED_8_META_TOOLS)
        for tool in tools:
            assert tool["inputSchema"]["additionalProperties"] is False
            assert tool["inputSchema"].get("required")


def test_tools_call_returns_content_and_raw_payload():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        reply = _rpc(
            server,
            "tools/call",
            {"name": "graph_compute", "arguments": {"engine": "alu", "type": "eval", "query": "6*7"}},
        )
        result = reply["result"]
        assert result["isError"] is False
        assert result["raw"]["result"] == 42
        assert json.loads(result["content"][0]["text"])["result"] == 42


def test_resources_list_and_read_are_served():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        resources = _line(server, {"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["result"]["resources"]
        uris = [r["uri"] for r in resources]
        assert "onyxsi://schema" in uris
        assert "onyxsi://grammar" in uris
        assert "onyxsi://audit" in uris

        audit = _line(
            server, {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "onyxsi://audit"}}
        )["result"]
        payload = json.loads(audit["contents"][0]["text"])
        assert payload["passed"] is True
        assert payload["violations"] == 0

        schema = _line(
            server, {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "onyxsi://schema"}}
        )["result"]
        live = json.loads(schema["contents"][0]["text"])
        assert len(live["tools"]) == 8


def test_bridge_script_speaks_json_rpc_over_stdio():
    """The zero-install bridge must answer a real initialize on stdin."""
    if not os.path.isfile(_BRIDGE):
        return
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1.0"},
            },
        }
    )
    completed = subprocess.run(
        [sys.executable, _BRIDGE, "--workspace", _REPO_ROOT],
        input=request + "\n",
        capture_output=True,
        text=True,
        timeout=120,
    )
    reply = json.loads(completed.stdout.strip().splitlines()[0])
    assert reply["result"]["serverInfo"]["name"] == "onyxsi-headless-kernel"
    assert reply["result"]["protocolVersion"] == "2025-06-18"


def test_flight_recorder_captures_both_dispatch_shapes():
    """
    A real editor calls tools through the ``tools/call`` envelope, not by
    naming the meta-tool as the JSON-RPC method. Both shapes must reach the
    recorder, otherwise the Time-Travel Chronicle comes out empty.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)

        # The MCP-standard envelope.
        _line(
            server,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "graph_compute",
                    "arguments": {"engine": "alu", "type": "eval", "query": "1+1"},
                },
            },
        )
        # The direct meta-tool dispatch.
        _line(
            server,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "graph_compute",
                "params": {"engine": "alu", "type": "eval", "query": "2+2"},
            },
        )

        actions = [ev.action for ev in server.wal.get_recent_events()]
        assert "graph_compute" in actions
        assert len(actions) == 2


def test_failed_tool_call_is_recorded_as_an_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        _line(
            server,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "vfs_inspect",
                    "arguments": {"action": "read", "target": "definitely-missing.py"},
                },
            },
        )
        events = server.wal.get_recent_events()
        assert events
        assert events[-1].action == "vfs_inspect"
        assert events[-1].status == "ERROR"
