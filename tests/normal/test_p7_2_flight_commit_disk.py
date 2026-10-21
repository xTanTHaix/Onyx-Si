"""
Regression tests for Phase 5 Stage 4: ``causal_flight stage=commit`` must flush
the Ghost overlay to disk (Payload Commit), not merely append the flight log.

Before this guard the daemon answered ``status: ok`` on commit while the file on
disk kept its old bytes, so a "successful" flight silently changed nothing.
"""

import json
import os
import tempfile

from onyxsi.causal.trajectory import CausalTrajectoryEnforcer, FlightStage
from onyxsi.deltafs.ghost_overlay import GhostOverlayFS
from onyxsi.server import OnyxSiServer


def _result(server: OnyxSiServer, method: str, params: dict) -> dict:
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return json.loads(server.process_line(request))["result"]


def _raw(server: OnyxSiServer, method: str, params: dict) -> dict:
    payload = _result(server, method, params)
    return payload.get("raw", payload)


def test_causal_flight_commit_writes_payload_to_disk():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "calc.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a - b\n")

        server = OnyxSiServer(workspace_root=tmpdir)

        assert _raw(server, "causal_flight", {
            "stage": "pre_flight",
            "blast_radius": ["calc.py"],
            "hypothesis": "add() subtracts instead of adding",
        })["status"] == "ok"

        staged = _raw(server, "vfs_mutate", {
            "op": "replace_file",
            "path": "calc.py",
            "code": "def add(a, b):\n    return a + b\n",
        })
        assert staged["status"] == "ok"
        assert staged["staged_in_ghost"] is True
        assert open(target, encoding="utf-8").read() == "def add(a, b):\n    return a - b\n"

        assert _raw(server, "causal_flight", {"stage": "verify"})["status"] == "ok"
        assert _raw(server, "causal_flight", {"stage": "commit"})["status"] == "ok"

        assert open(target, encoding="utf-8").read() == "def add(a, b):\n    return a + b\n"

        log_path = os.path.join(tmpdir, ".onyxsi", "FLIGHT_LOG.md")
        assert os.path.exists(log_path)
        assert "COMMITTED" in open(log_path, encoding="utf-8").read()
        server.shutdown()


def test_causal_flight_commit_refuses_unparseable_payload():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "calc.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("value = 1\n")

        server = OnyxSiServer(workspace_root=tmpdir)
        _raw(server, "causal_flight", {
            "stage": "pre_flight", "blast_radius": ["calc.py"], "hypothesis": "inject syntax error"})

        assert _raw(server, "vfs_mutate", {
            "op": "replace_file", "path": "calc.py", "code": "def broken(:\n    pass\n",
        })["status"] == "ok"

        verify = _raw(server, "causal_flight", {"stage": "verify"})
        assert verify["status"] == "error"
        assert verify["error"] == "INVARIANT_FAILED"

        commit = _raw(server, "causal_flight", {"stage": "commit"})
        assert commit["status"] == "error"
        assert commit["error"] == "VERIFICATION_REQUIRED"

        assert open(target, encoding="utf-8").read() == "value = 1\n"
        server.shutdown()


def test_causal_flight_drift_abort_keeps_disk_untouched():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "calc.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("value = 1\n")

        server = OnyxSiServer(workspace_root=tmpdir)
        _raw(server, "causal_flight", {
            "stage": "pre_flight", "blast_radius": ["unrelated.py"], "hypothesis": "scope creep"})

        mutate = _raw(server, "vfs_mutate", {
            "op": "replace_file", "path": "calc.py", "code": "value = 2\n",
        })
        assert mutate["status"] == "error"
        assert mutate["error"] == "TRAJECTORY_DRIFT_ABORT"
        assert open(target, encoding="utf-8").read() == "value = 1\n"
        server.shutdown()


def test_commit_payload_flushes_attached_ghost_overlay():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "mod.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("value = 1\n")

        ghost = GhostOverlayFS(tmpdir)
        enforcer = CausalTrajectoryEnforcer(workspace_root=tmpdir, ghost_fs=ghost)

        ghost.ghost_begin()
        ghost.ghost_mutate(target, "value = 2\n")

        enforcer.begin_pre_flight(["mod.py"], "bump value")
        enforcer.record_ghost_burn("mod.py")
        enforcer.verify_invariants(invariants_passed=True)

        log_path = enforcer.commit_payload()

        assert enforcer.state.stage == FlightStage.PAYLOAD_COMMIT
        assert open(target, encoding="utf-8").read() == "value = 2\n"
        assert not ghost.is_active()
        assert "COMMITTED" in open(log_path, encoding="utf-8").read()


def test_commit_payload_without_ghost_overlay_still_logs():
    with tempfile.TemporaryDirectory() as tmpdir:
        enforcer = CausalTrajectoryEnforcer(workspace_root=tmpdir)

        enforcer.begin_pre_flight(["auth.py"], "detached enforcer")
        enforcer.record_ghost_burn("auth.py")
        enforcer.verify_invariants(invariants_passed=True)

        log_path = enforcer.commit_payload()
        assert os.path.exists(log_path)
        assert enforcer.state.stage == FlightStage.PAYLOAD_COMMIT
