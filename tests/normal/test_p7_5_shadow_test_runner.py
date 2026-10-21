"""
Normal Tests for Phase 7.5: the real test runner.

``ghost_sandbox cmd=test command=...`` must run the project's own command in a
throwaway copy of the workspace, with staged Ghost content applied on top, and
must never write through to the real workspace.
"""

import json
import os
import sys
import tempfile

from onyxsi.server import OnyxSiServer


def _py(code: str) -> str:
    """Portable one-liner: uses the interpreter running the tests, not 'python'."""
    return f'"{sys.executable}" -c "{code}"'


def _call(server: OnyxSiServer, method: str, params: dict):
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    return json.loads(server.process_line(request))["result"]


def test_command_runs_in_shadow_and_reports_passed():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "value.py"), "w", encoding="utf-8") as fh:
            fh.write("X = 42\n")

        server = OnyxSiServer(workspace_root=tmpdir)
        result = _call(
            server,
            "ghost_sandbox",
            {"cmd": "test", "command": _py("import value; print(value.X)")},
        )
        assert result["status"] == "ok"
        assert result["test_result"] == "PASSED"
        assert result["exit_code"] == 0
        assert result["mode"] == "shadow-workspace"
        assert result["stdout_tail"].strip() == "42"
        assert result["shadow_files"] == 1
        assert result["enforcement"]["syscall"] == "none"
        server.shutdown()


def test_command_failure_is_reported_with_exit_code():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        result = _call(
            server,
            "ghost_sandbox",
            {"cmd": "test", "command": _py("import sys; sys.exit(3)")},
        )
        assert result["test_result"] == "FAILED"
        assert result["exit_code"] == 3
        assert result["timed_out"] is False
        server.shutdown()


def test_command_timeout_is_killed():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        result = _call(
            server,
            "ghost_sandbox",
            {
                "cmd": "test",
                "command": _py("import time; time.sleep(30)"),
                "timeout_s": 1,
            },
        )
        assert result["test_result"] == "TIMEOUT"
        assert result["timed_out"] is True
        assert result["duration_ms"] < 10000
        server.shutdown()


def test_staged_content_is_visible_but_the_real_workspace_is_untouched():
    with tempfile.TemporaryDirectory() as tmpdir:
        real_path = os.path.join(tmpdir, "value.py")
        with open(real_path, "w", encoding="utf-8") as fh:
            fh.write("X = 42\n")

        server = OnyxSiServer(workspace_root=tmpdir)
        assert _call(server, "ghost_sandbox", {"cmd": "begin"})["status"] == "ok"
        staged = _call(
            server,
            "ghost_sandbox",
            {"cmd": "mutate", "path": "value.py", "content": "X = 99\n"},
        )
        assert staged["status"] == "ok"

        result = _call(
            server,
            "ghost_sandbox",
            {"cmd": "test", "command": _py("import value; print(value.X)")},
        )
        assert result["test_result"] == "PASSED"
        assert result["stdout_tail"].strip() == "99"
        assert result["staged_files"] == 1

        with open(real_path, "r", encoding="utf-8") as fh:
            assert fh.read() == "X = 42\n"

        _call(server, "ghost_sandbox", {"cmd": "discard"})
        server.shutdown()


def test_denied_commands_never_spawn():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        result = _call(
            server, "ghost_sandbox", {"cmd": "test", "command": "sudo reboot"}
        )
        assert result["status"] == "error"
        assert result["error"] == "POLICY_DENIED"
        server.shutdown()


def test_empty_command_is_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        result = _call(server, "ghost_sandbox", {"cmd": "test", "command": "   "})
        assert result["error"] == "INVALID_COMMAND"
        server.shutdown()


def test_shadow_size_cap_is_enforced():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "blob.bin"), "wb") as fh:
            fh.write(b"x" * 4096)

        server = OnyxSiServer(workspace_root=tmpdir)
        server.max_shadow_bytes = 1024
        result = _call(
            server,
            "ghost_sandbox",
            {"cmd": "test", "command": _py("print(1)")},
        )
        assert result["status"] == "error"
        assert result["error"] == "SHADOW_TOO_LARGE"
        server.shutdown()
