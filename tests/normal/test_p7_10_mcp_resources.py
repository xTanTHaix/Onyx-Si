"""
Normal Tests for Phase 7.10: MCP resources.

OnyxSi exposes read-only resources over the same stdio channel as the 8 tools:
schema, grammar, boot audit, and live telemetry. Resources are protocol methods,
not a ninth tool.
"""

import json
import tempfile

from onyxsi.server import OnyxSiServer


def _rpc(server: OnyxSiServer, method: str, params=None, req_id=1):
    request = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        request["id"] = req_id
    if params is not None:
        request["params"] = params
    raw = server.process_line(json.dumps(request))
    return json.loads(raw) if raw is not None else None


def test_resources_list_advertises_the_kernel_views():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        reply = _rpc(server, "resources/list")
        resources = {item["uri"]: item for item in reply["result"]["resources"]}
        assert set(resources) == {
            "onyxsi://schema",
            "onyxsi://grammar",
            "onyxsi://audit",
            "onyxsi://telemetry",
        }
        assert resources["onyxsi://grammar"]["mimeType"] == "text/x-gbnf"

        init = _rpc(
            server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}
        )
        assert "resources" in init["result"]["capabilities"]
        server.shutdown()


def test_schema_and_grammar_resources_are_live():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)

        schema = _rpc(server, "resources/read", {"uri": "onyxsi://schema"})
        payload = json.loads(schema["result"]["contents"][0]["text"])
        assert len(payload["tools"]) == 8

        grammar = _rpc(server, "resources/read", {"uri": "onyxsi://grammar"})
        text = grammar["result"]["contents"][0]["text"]
        assert "root ::= tool-call" in text
        assert grammar["result"]["contents"][0]["mimeType"] == "text/x-gbnf"
        server.shutdown()


def test_audit_and_telemetry_resources_report_real_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        _rpc(server, "vfs_inspect", {"action": "tree"})

        audit = _rpc(server, "resources/read", {"uri": "onyxsi://audit"})
        audit_payload = json.loads(audit["result"]["contents"][0]["text"])
        assert audit_payload["passed"] is True

        telemetry = _rpc(server, "resources/read", {"uri": "onyxsi://telemetry"})
        telemetry_payload = json.loads(telemetry["result"]["contents"][0]["text"])
        assert telemetry_payload["counters"]["total"] >= 1
        assert telemetry_payload["shared_memory"]["available"] is True, telemetry_payload["shared_memory"]
        server.shutdown()


def test_unknown_resource_uri_is_an_invalid_params_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        reply = _rpc(server, "resources/read", {"uri": "onyxsi://nope"})
        assert reply["error"]["code"] == -32602
        server.shutdown()


def test_resource_notifications_produce_no_reply():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        assert _rpc(server, "resources/list", req_id=None) is None
        server.shutdown()
