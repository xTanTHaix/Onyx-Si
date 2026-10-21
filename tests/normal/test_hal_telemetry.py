"""
HAL telemetry wiring tests.

Proves that the SPSC ring buffer is a real part of the daemon (not a facade
that nothing calls): every meta-tool invocation is recorded, ``reflect``
exposes the snapshot, and JSONL export is opt-in.
"""

import json
import os

import pytest

from onyxsi.hal.telemetry import ToolTelemetry
from onyxsi.hal.ring_buffer import SPSCRingBuffer
from onyxsi.server import OnyxSiServer


def _call(server, req_id, method, params=None):
    request = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        request["params"] = params
    raw = server.process_line(json.dumps(request))
    assert raw is not None
    return json.loads(raw)


@pytest.fixture()
def server(tmp_path):
    srv = OnyxSiServer(workspace_root=str(tmp_path))
    yield srv
    srv.shutdown()


def test_server_telemetry_is_backed_by_the_real_ring_buffer(server):
    assert server.telemetry is not None
    assert isinstance(server.telemetry, ToolTelemetry)
    assert isinstance(server.telemetry._buffer, SPSCRingBuffer)
    # Producer is wired into the router's single choke point (the server wrapper
    # forwards to the ring buffer and mirrors counters into shared memory).
    assert server.router._observer == server._observe_tool
    assert callable(server.router._observer)


def test_every_meta_tool_call_is_recorded(server, tmp_path):
    (tmp_path / "app.py").write_text("def handler():\n    return 1\n", encoding="utf-8")

    _call(server, 1, "vfs_inspect", {"action": "tree"})
    _call(server, 2, "vfs_inspect", {"action": "skeleton", "target": "app.py"})
    _call(server, 3, "graph_compute", {"engine": "alu", "type": "eval", "query": "2+2"})

    snap = server.telemetry.snapshot()
    assert snap["total"] >= 3
    assert snap["by_tool"]["vfs_inspect"] >= 2
    assert snap["by_tool"]["graph_compute"] >= 1
    assert snap["dropped"] == 0
    assert snap["duration_us"]["max"] is not None


def test_failed_tool_is_recorded_as_error(server):
    # Unbound/invalid call still goes through invoke() and must be observed.
    resp = _call(server, 1, "vfs_inspect", {"action": "tree", "nope": True})
    assert resp["error"]["code"] == -32602

    snap = server.telemetry.snapshot()
    assert snap["error"] >= 1
    assert "InvalidParamsError" in snap["errors"]


def test_reflect_exposes_the_telemetry_snapshot(server, tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    _call(server, 1, "vfs_inspect", {"action": "tree"})

    resp = _call(
        server,
        2,
        "causal_flight",
        {
            "stage": "reflect",
            "root_cause": "the handler ignored the request id",
            "strategy": "propagate the id through the router",
        },
    )
    assert resp["result"]["status"] == "ok"
    snap = resp["result"]["telemetry"]
    assert snap["total"] >= 1
    assert snap["buffer_state"] in ("RING_BUFFER_EMPTY", "RING_BUFFER_READY", "RING_BUFFER_FULL")


def test_ring_overflow_drops_oldest_without_raising():
    telemetry = ToolTelemetry(capacity=4)
    try:
        for i in range(10):
            telemetry.record("vfs_inspect", True, i)
        snap = telemetry.snapshot()
        assert snap["total"] == 4  # bounded history from this ring
        assert snap["dropped"] >= 1
        assert snap["recent"][-1]["duration_us"] == 9
    finally:
        telemetry.close()


def test_flush_jsonl_is_opt_in_and_parseable(tmp_path):
    out = tmp_path / "logs" / "telemetry.jsonl"

    srv = OnyxSiServer(workspace_root=str(tmp_path))
    assert not out.exists(), "telemetry must not write files unless asked"
    _call(srv, 1, "vfs_inspect", {"action": "tree"})
    srv.shutdown()
    assert not out.exists()

    srv2 = OnyxSiServer(workspace_root=str(tmp_path), telemetry_out=str(out))
    _call(srv2, 1, "vfs_inspect", {"action": "tree"})
    srv2.shutdown()

    assert out.exists()
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    assert lines
    assert lines[-1]["tool"] == "vfs_inspect"
    assert set(lines[-1]) >= {"seq", "tool", "ok", "duration_us", "timestamp"}


def test_observer_errors_never_break_a_tool_call(tmp_path):
    def exploding_observer(tool, ok, duration_us, error):
        raise RuntimeError("telemetry sink is down")

    srv = OnyxSiServer(workspace_root=str(tmp_path))
    try:
        srv.router.set_observer(exploding_observer)
        resp = _call(srv, 1, "vfs_inspect", {"action": "tree"})
        assert resp["result"]["action"] == "tree"
        assert "error" not in resp["result"]
    finally:
        srv.shutdown()


def test_close_is_idempotent(tmp_path):
    telemetry = ToolTelemetry()
    telemetry.record("vfs_inspect", True, 5)
    telemetry.close()
    telemetry.close()
    assert telemetry.closed is True
    assert telemetry.snapshot()["total"] == 1
