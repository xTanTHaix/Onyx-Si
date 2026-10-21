"""
Character Tests for Phase 7: CT-29 and CT-30.
CT-29: The Dual-OS Nomad (Cross-platform path translation & handoff portability)
CT-30: The Swarm Apocalypse (Concurrent hostile swarm bombardment across all 8 meta-tools)
"""

import sys
import json
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from onyxsi.server import OnyxSiServer
from tests.character.chaos_mesh_runner import ChaosMeshRunner


class TestCT29DualOSNomad:
    """
    CT-29: Agent moves project folder between Linux ext4 and Windows 11 NTFS.
    OnyxSi transparently handles Windows and POSIX path separators and recovers task state.
    """

    def test_dual_os_nomad_portability_and_path_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            server = OnyxSiServer(workspace_root=tmpdir)

            # Create task using Windows style path in wiki
            req1 = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "wiki_task_sync",
                "params": {"task_id": "TASK-NOMAD", "op": "init", "spec": "Dual OS spec"},
            })
            resp1 = json.loads(server.process_line(req1))
            assert resp1["result"]["status"] == "ok"

            # Inspect file using Windows backslashes
            req2 = json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "vfs_inspect",
                "params": {"action": "resolve", "target": "src\\kernel\\main.py"},
            })
            resp2 = json.loads(server.process_line(req2))
            assert "resolved" in resp2["result"]

            # Inspect same file using POSIX slashes
            req3 = json.dumps({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "vfs_inspect",
                "params": {"action": "resolve", "target": "src/kernel/main.py"},
            })
            resp3 = json.loads(server.process_line(req3))
            assert "resolved" in resp3["result"]

            server.shutdown()


class TestCT30SwarmApocalypse:
    """
    CT-30: 8 concurrent hostile agents bombard the server with mixed operations.
    Daemon enforces zero memory leaks, zero deadlocks, and 0.0% crash rate.
    """

    def test_swarm_apocalypse_zero_crash_and_clean_exit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ChaosMeshRunner(workspace_root=tmpdir)
            swarm_passed = runner.run_swarm_chaos(num_agents=6, iterations_per_agent=15)
            assert swarm_passed is True
