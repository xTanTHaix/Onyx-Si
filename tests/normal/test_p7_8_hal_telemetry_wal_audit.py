"""
Normal Tests for Phase 7.8: HAL telemetry, WAL crash reports, boot audit, wakeup.

These pin the modules that were previously not on the request path:
  * micro_wal records every tool call and writes CRASH_REPRO.md on a crash
  * shm_superblock mirrors counters and can be attached by name
  * auditor runs a boot self-check over the kernel package
  * wakeup drives the stdio loop (EOF wakes it instead of blocking forever)
"""

import json
import os
import subprocess
import sys
import tempfile

from onyxsi.auditor import ZeroDepAndZeroUiAuditor
from onyxsi.hal.shm_superblock import SharedMemorySuperblock
from onyxsi.server import OnyxSiServer, _boot_audit_summary


def _call(server: OnyxSiServer, method: str, params: dict):
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    response = json.loads(server.process_line(request))
    return response.get("result", response.get("error"))


def test_wal_records_calls_and_reflect_exposes_hal_counters():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        _call(server, "vfs_inspect", {"action": "tree"})

        reflect = _call(
            server,
            "causal_flight",
            {
                "stage": "reflect",
                "root_cause": "the cache key ignored mtime changes",
                "strategy": "include mtime_ns and size in the signature",
            },
        )
        assert reflect["status"] == "ok"
        assert reflect["telemetry"]["total"] >= 1
        assert reflect["shared_memory"]["available"] is True, reflect["shared_memory"]
        assert reflect["shared_memory"]["total"] >= 1
        assert reflect["wal_events"] >= 1
        assert reflect["boot_audit"]["passed"] is True

        events = server.wal.get_recent_events()
        assert any(event.action == "vfs_inspect" for event in events)
        server.shutdown()


def test_crash_repro_is_written_on_an_unexpected_exception():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)

        def explode(_args):
            raise RuntimeError("boom in handler")

        server.router.register_handler("vfs_inspect", explode)
        response = json.loads(
            server.process_line(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "vfs_inspect",
                        "params": {"action": "tree"},
                    }
                )
            )
        )
        assert response["error"]["code"] == -32603
        crash_path = response["error"]["data"]["crash_repro"]
        assert crash_path and os.path.isfile(crash_path)
        with open(crash_path, "r", encoding="utf-8") as fh:
            report = fh.read()
        assert "boom in handler" in report
        assert "vfs_inspect" in report
        server.shutdown()


def test_superblock_can_be_attached_by_name():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        _call(server, "vfs_inspect", {"action": "tree"})
        _call(server, "vfs_inspect", {"action": "resolve", "target": "."})

        local = server._read_shm_snapshot()
        assert local["available"] is True, local
        assert local["total"] >= 2

        attached = SharedMemorySuperblock.attach(server._shm_name, size=1024 * 1024)
        try:
            raw = attached.read_slab(0, 4096).split(b"\x00", 1)[0]
            remote = json.loads(raw.decode("utf-8"))
            assert remote["total"] == local["total"]
            assert remote["pid"] == os.getpid()
            assert remote["last_tool"] == "vfs_inspect"
        finally:
            attached.close()
        server.shutdown()


def test_boot_audit_scans_the_kernel_package():
    summary = _boot_audit_summary()
    assert summary["passed"] is True
    assert summary["files_scanned"] >= 10
    assert summary["violations"] == 0

    direct = ZeroDepAndZeroUiAuditor(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    assert direct.workspace_root.exists()


def test_stdio_loop_exits_on_eof_through_the_wakeup_signal():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    src = os.path.join(repo_root, "src")
    env = os.environ.copy()
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

    with tempfile.TemporaryDirectory() as tmpdir:
        proc = subprocess.run(
            [sys.executable, "-m", "onyxsi", "--workspace", tmpdir],
            input=request + "\n",
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    assert proc.returncode == 0, proc.stderr
    assert '"result"' in proc.stdout
    assert "onyxsi" in proc.stdout.lower()
