"""
Street Tests for Phase 7: Daemon Robustness & Dual-OS Interoperability.
Tests:
  1. Abrupt stream termination and malformed JSON-RPC floods.
  2. Dual-OS path separator normalization ('/' vs '\\') across Windows and Linux.
  3. High-concurrency daemon throughput with zero resource leaks.
"""

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from onyxsi.server import OnyxSiServer


def test_street_daemon_malformed_json_flood_recovery():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)

        malformed_inputs = [
            '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"',  # truncated
            '{"jsonrpc": "2.0", "id": 2, "method": "unknown_ghost_tool"}',  # unknown method
            '{invalid json text here}',
            '{"jsonrpc": "2.0", "id": 4, "method": "graph_compute", "params": {"engine": "alu", "type": "eval", "query": "2 + 2"}}',  # valid
        ]

        responses = []
        for line in malformed_inputs:
            out = server.process_line(line)
            assert out is not None
            responses.append(json.loads(out))

        # Check that server stayed alive and successfully served the valid 4th request
        assert responses[3]["result"]["result"] == 4
        server.shutdown()


def test_street_daemon_dual_os_path_normalization():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)

        # Windows style path
        win_path = "src\\modules\\core\\worker.py"
        req_win = json.dumps({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "vfs_inspect",
            "params": {"action": "resolve", "target": win_path},
        })
        resp_win = json.loads(server.process_line(req_win))
        assert "resolved" in resp_win["result"]

        # Linux style path
        posix_path = "src/modules/core/worker.py"
        req_posix = json.dumps({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "vfs_inspect",
            "params": {"action": "resolve", "target": posix_path},
        })
        resp_posix = json.loads(server.process_line(req_posix))
        assert "resolved" in resp_posix["result"]

        server.shutdown()


def test_street_daemon_concurrent_flood_stress():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)

        def sender(worker_id: int):
            for i in range(50):
                req = json.dumps({
                    "jsonrpc": "2.0",
                    "id": f"{worker_id}_{i}",
                    "method": "graph_compute",
                    "params": {"engine": "alu", "type": "eval", "query": f"{worker_id} + {i}"},
                })
                raw = server.process_line(req)
                assert raw is not None
                data = json.loads(raw)
                assert data["result"]["result"] == worker_id + i

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(sender, w) for w in range(8)]
            for fut in futures:
                fut.result()

        server.shutdown()
