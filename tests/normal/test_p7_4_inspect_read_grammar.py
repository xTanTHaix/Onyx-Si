"""
Normal Tests for Phase 7.4: full read + ref:// dereference + grammar export.

These pin the capabilities the MCP surface previously advertised but did not
implement: reading a file (line range included), resolving folded ``ref://``
handles, and exporting the live tool grammar.
"""

import json
import os
import tempfile

from onyxsi.server import OnyxSiServer


def _call(server: OnyxSiServer, method: str, params: dict):
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    return json.loads(server.process_line(request))["result"]


def test_grammar_action_exports_the_live_tool_grammar():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        result = _call(server, "vfs_inspect", {"action": "grammar"})
        assert result["action"] == "grammar"
        assert "root ::= tool-call" in result["content"]
        assert "vfs-inspect-call" in result["content"]
        assert result["tokens"] > 0
        server.shutdown()


def test_read_action_returns_a_line_range():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "sample.py")
        with open(path, "w", encoding="utf-8") as fh:
            for index in range(1, 11):
                fh.write(f"line_{index} = {index}\n")

        server = OnyxSiServer(workspace_root=tmpdir)

        whole = _call(server, "vfs_inspect", {"action": "read", "target": "sample.py"})
        assert whole["kind"] == "file"
        assert whole["total_lines"] == 10
        assert "line_1 = 1" in whole["content"]
        assert "line_10 = 10" in whole["content"]

        window = _call(
            server,
            "vfs_inspect",
            {"action": "read", "target": "sample.py", "start_line": 3, "end_line": 5},
        )
        assert "line_3 = 3" in window["content"]
        assert "line_5 = 5" in window["content"]
        assert "line_6 = 6" not in window["content"]
        assert window["start_line"] == 3
        assert window["end_line"] == 5
        server.shutdown()


def test_folded_read_returns_a_ref_that_round_trips():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "big.py")
        with open(path, "w", encoding="utf-8") as fh:
            for index in range(400):
                fh.write(f"payload_{index} = 'needle_{index}'\n")

        server = OnyxSiServer(workspace_root=tmpdir)

        folded = _call(
            server,
            "vfs_inspect",
            {"action": "read", "target": "big.py", "max_tokens": 25},
        )
        assert folded["folded"] is True
        assert folded["retrievable"] is True
        assert folded["ref"].startswith("ref://")

        resolved = _call(
            server, "vfs_inspect", {"action": "resolve", "target": folded["ref"]}
        )
        assert resolved["kind"] == "handle"
        assert resolved["exists"] is True
        assert resolved["chars"] > 1000

        # The handle is only useful if the full payload can be read back.
        full = _call(
            server,
            "vfs_inspect",
            {
                "action": "read",
                "target": folded["ref"],
                "start_line": 395,
                "end_line": 400,
            },
        )
        assert "needle_394" in full["content"]
        assert "needle_399" in full["content"]
        assert full["total_lines"] == 400
        server.shutdown()


def test_read_rejects_paths_outside_the_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        result = _call(
            server,
            "vfs_inspect",
            {"action": "read", "target": "../../etc/passwd"},
        )
        assert result["error"] == "PATH_BLOCKED"
        server.shutdown()


def test_unknown_ref_handle_is_reported_not_crashed():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        result = _call(
            server,
            "vfs_inspect",
            {"action": "read", "target": "ref://does_not_exist"},
        )
        assert result["error"] == "HANDLE_UNRESOLVED"
        server.shutdown()
