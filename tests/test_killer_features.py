"""
Feature tests for the v1.0.0-PROD refinement: project-namespaced task state,
in-RAM MCTS speculative forking, the autonomous AST antibody auto-vaccine, the
Time-Travel Chronicle, and the MCP JSON-RPC dispatch surface.

These run under pytest *and* under the zero-dependency stdlib runner, so they
avoid fixtures, marks and parametrisation on purpose.
"""

import json
import os
import tempfile

from onyxsi.causal.antibody_memory import AntibodyMemory, AntibodyRejectionError
from onyxsi.deltafs.ghost_overlay import GhostOverlayFS
from onyxsi.gateway.meta_router import MethodNotFoundError
from onyxsi.knowledge.task_wiki import TaskWikiManager
from onyxsi.server import OnyxSiServer


def test_project_namespaced_task_wiki():
    """Two projects in one workspace must not share task state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        alpha = TaskWikiManager(tmpdir, project_name="alpha")
        beta = TaskWikiManager(tmpdir, project_name="beta")

        dir_alpha = alpha.init_task("TASK-001", "# Alpha specification")
        dir_beta = beta.init_task("TASK-001", "# Beta specification")

        assert dir_alpha != dir_beta
        assert os.sep + "alpha" + os.sep in dir_alpha
        assert os.sep + "beta" + os.sep in dir_beta

        with open(os.path.join(dir_alpha, "TASK_SPEC.md"), "r", encoding="utf-8") as fh:
            alpha_spec = fh.read()
        with open(os.path.join(dir_beta, "TASK_SPEC.md"), "r", encoding="utf-8") as fh:
            beta_spec = fh.read()

        assert "Alpha specification" in alpha_spec
        assert "Alpha specification" not in beta_spec
        assert "Beta specification" in beta_spec

        alpha.update_progress("TASK-001", status="IN_PROGRESS", negative_lesson="do not race")
        assert alpha.get_progress("TASK-001")["status"] == "IN_PROGRESS"
        assert alpha.get_progress("TASK-001")["negative_lessons"] == ["do not race"]
        assert beta.get_progress("TASK-001")["status"] == "INITIALIZED"
        assert beta.get_progress("TASK-001")["negative_lessons"] == []

        # The antibody store is namespaced the same way.
        memory = AntibodyMemory(workspace_root=tmpdir, project_name="alpha")
        expected = os.path.join(
            tmpdir, ".onyxsi", "wiki", "projects", "alpha", "ANTIBODIES.json"
        )
        assert memory.storage_path == os.path.abspath(expected) or memory.storage_path == expected


def test_time_travel_markdown_chronicle():
    """The replay document is a real file with a real decision tree."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wiki = TaskWikiManager(tmpdir, project_name="chronicle")
        wiki.init_task("TASK-777", "# Replay specification")

        content = wiki.generate_time_travel_replay(
            "TASK-777",
            frames=[
                {"action": "mutate", "target": "src/auth.py", "outcome": "OK", "timestamp": 100.0},
                {"action": "ghost_burn", "target": "src/auth.py", "outcome": "OK", "timestamp": 100.25},
                {"action": "test", "target": "tests/test_auth.py", "outcome": "PASS", "timestamp": 101.9},
            ],
            branches=[
                {"branch_id": "main", "score": 0.0, "status": "root"},
                {
                    "branch_id": "branch_fast",
                    "parent": "main",
                    "score": 62.5,
                    "hypothesis": "skip the null check",
                    "modified_files": ["src/auth.py"],
                },
                {
                    "branch_id": "branch_robust",
                    "parent": "main",
                    "score": 95.0,
                    "hypothesis": "keep the null check",
                    "modified_files": ["src/auth.py"],
                },
            ],
        )

        replay_path = os.path.join(wiki.task_dir("TASK-777"), "TIME_TRAVEL_REPLAY.md")
        assert os.path.exists(replay_path)
        with open(replay_path, "r", encoding="utf-8") as fh:
            on_disk = fh.read()
        assert on_disk == content

        assert "# Time-Travel Replay: TASK-777" in content
        assert "ASCII Decision & State Branch Tree" in content
        assert "Frame-by-Frame Event Trace" in content
        assert "branch_fast" in content
        assert "branch_robust" in content
        assert "score=95.00" in content
        assert "keep the null check" in content
        assert "| #1 |" in content
        assert "| #3 |" in content
        assert "+1.900s" in content


def test_mcts_speculative_forking_in_ram():
    """Competing fixes are explored in RAM; the best one is what reaches disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "service.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("def run():\n    return 1\n")

        ghost = GhostOverlayFS(workspace_root=tmpdir)
        ghost.ghost_begin()
        ghost.ghost_mutate(target, "def run():\n    return 2\n")

        # Two competing branches, each forked from the same baseline.
        ghost.ghost_fork("branch_fast", hypothesis="fast but sloppy")
        ghost.ghost_mutate(target, "def run():\n    return 3\n")
        ghost.ghost_fork("branch_robust", from_branch="main", hypothesis="slow but correct")
        ghost.ghost_mutate(target, "def run():\n    return 4\n")

        # A fork must not leak into the branch it was taken from.
        assert ghost.ghost_read_text(target) == "def run():\n    return 4\n"
        ghost.ghost_switch_branch("main")
        assert ghost.ghost_read_text(target) == "def run():\n    return 2\n"
        ghost.ghost_switch_branch("branch_fast")
        assert ghost.ghost_read_text(target) == "def run():\n    return 3\n"

        # Score both branches, then merge the winner.
        fast_score = ghost.ghost_score_branch("branch_fast", passed_tests=3, total_tests=10)
        robust_score = ghost.ghost_score_branch("branch_robust", passed_tests=9, total_tests=10)
        assert robust_score > fast_score

        winner, score = ghost.ghost_merge_best_branch(["branch_fast", "branch_robust"])
        assert winner == "branch_robust"
        assert score == robust_score

        survivors = [b["branch_id"] for b in ghost.ghost_list_branches()]
        assert survivors == ["branch_robust"]

        # The winner is still staged, and it is what lands on disk.
        assert ghost.ghost_read_text(target) == "def run():\n    return 4\n"
        committed = ghost.ghost_commit()
        assert len(committed) == 1
        with open(target, "r", encoding="utf-8") as fh:
            assert fh.read() == "def run():\n    return 4\n"


def test_autonomous_ast_antibody_auto_vaccine():
    """A committed fix becomes a rule that blocks the defect it removed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "payment.py")
        baseline = (
            "def charge(amount):\n"
            "    if amount == None:\n"
            "        return 0\n"
            "    return apply(amount)\n"
        )
        fixed = (
            "def charge(amount):\n"
            "    if amount is None:\n"
            "        return 0\n"
            "    return apply(amount)\n"
        )
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(baseline)

        memory = AntibodyMemory(workspace_root=tmpdir, project_name="vaccine")
        ghost = GhostOverlayFS(workspace_root=tmpdir)
        ghost.ghost_begin()
        ghost.ghost_mutate(target, fixed)
        ghost.ghost_commit(antibody_memory=memory, context_hint="Fix None comparison")

        store = os.path.join(
            tmpdir, ".onyxsi", "wiki", "projects", "vaccine", "ANTIBODIES.json"
        )
        assert os.path.exists(store)
        assert memory.count() >= 1

        with open(store, "r", encoding="utf-8") as fh:
            records = json.load(fh)
        assert records
        assert any("== None" in r["forbidden_pattern"] for r in records)

        # The defective code is blocked; the repaired code is not.
        try:
            memory.check_mutation("payment.py", baseline)
            raise AssertionError("Expected AntibodyRejectionError")
        except AntibodyRejectionError as exc:
            assert exc.antibody.forbidden_pattern in baseline
            assert "is None" in exc.antibody.prescribed_alternative

        memory.check_mutation("payment.py", fixed)

        # A no-op pair yields no rule at all.
        assert memory.synthesize_from_ast_diff(fixed, fixed, target, "no change") is None


def test_server_json_rpc_dispatch():
    """The Fixed 8 Meta-Tool surface answers over JSON-RPC, new commands included."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "calc.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("def add(a, b):\n    return a - b\n")

        server = OnyxSiServer(workspace_root=tmpdir, project_name="dispatch")

        reply = server.router.dispatch_rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        tools = reply["result"]["tools"]
        assert len(tools) == 8
        assert "ghost_sandbox" in [t["name"] for t in tools]

        # A real tool call reaches the handler.
        reply = server.router.dispatch_rpc(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "graph_compute",
                "params": {"engine": "alu", "type": "eval", "query": "(2+3)*7"},
            }
        )
        assert reply["result"]["result"] == 35

        # An unknown tool is a protocol error, not a crash.
        try:
            server.router.dispatch_rpc(
                {"jsonrpc": "2.0", "id": 3, "method": "rm_rf", "params": {}}
            )
            raise AssertionError("Expected MethodNotFoundError")
        except MethodNotFoundError:
            pass

        # The MCTS commands are reachable through the dispatcher.
        reply = server.router.dispatch_rpc(
            {"jsonrpc": "2.0", "id": 4, "method": "ghost_sandbox", "params": {"cmd": "begin"}}
        )
        assert reply["result"]["status"] == "ok"

        reply = server.router.dispatch_rpc(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "ghost_sandbox",
                "params": {
                    "cmd": "mutate",
                    "path": "calc.py",
                    "content": "def add(a, b):\n    return a + b\n",
                },
            }
        )
        assert reply["result"]["status"] == "ok"

        reply = server.router.dispatch_rpc(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "ghost_sandbox",
                "params": {"cmd": "fork", "branch_id": "alt", "hypothesis": "another way"},
            }
        )
        assert reply["result"]["branch_id"] == "alt"

        reply = server.router.dispatch_rpc(
            {"jsonrpc": "2.0", "id": 7, "method": "ghost_sandbox", "params": {"cmd": "list_branches"}}
        )
        branch_ids = [b["branch_id"] for b in reply["result"]["branches"]]
        assert "main" in branch_ids and "alt" in branch_ids

        reply = server.router.dispatch_rpc(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "ghost_sandbox",
                "params": {
                    "cmd": "merge_best",
                    "scores": [
                        {"branch_id": "main", "passed_tests": 5, "total_tests": 10},
                        {"branch_id": "alt", "passed_tests": 9, "total_tests": 10},
                    ],
                },
            }
        )
        assert reply["result"]["winner"] == "alt"

        reply = server.router.dispatch_rpc(
            {"jsonrpc": "2.0", "id": 9, "method": "ghost_sandbox", "params": {"cmd": "commit"}}
        )
        assert reply["result"]["status"] == "ok"

        with open(target, "r", encoding="utf-8") as fh:
            assert "return a + b" in fh.read()

        # The task wiki and the replay chronicle are reachable too.
        reply = server.router.dispatch_rpc(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "wiki_task_sync",
                "params": {
                    "task_id": "TASK-001",
                    "op": "init",
                    "spec": "# Dispatch spec",
                    "project_name": "dispatch",
                },
            }
        )
        assert reply["result"]["status"] == "ok"
        assert reply["result"]["project_name"] == "dispatch"

        reply = server.router.dispatch_rpc(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "wiki_task_sync",
                "params": {
                    "task_id": "TASK-001",
                    "op": "replay",
                    "project_name": "dispatch",
                    "branches": [{"branch_id": "main", "score": 42.0}],
                },
            }
        )
        assert reply["result"]["status"] == "ok"
        assert os.path.exists(reply["result"]["path"])
        assert "ASCII Decision & State Branch Tree" in reply["result"]["chronicle"]
