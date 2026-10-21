"""
Character Scenario Tests for Phase 3: CT-13 through CT-16.
"""

import ast
import os
import tempfile
from onyxsi.deltafs.ghost_overlay import GhostOverlayFS, GhostSyntaxError
from onyxsi.deltafs.cow_workspace import SparsePageDeltaMap, PAGE_SIZE
from onyxsi.deltafs.ast_lease_registry import AstLeaseRegistry, AstLeaseConflictError
from onyxsi.deltafs.ast_conflict_merger import ConflictFreeAstMerger, AgentPatch


class TestCT13SyntaxDestroyerGhostAbuser:
    """CT-13: Agent committing broken syntax into Ghost Overlay."""

    def test_broken_syntax_auto_discard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "app.py")
            with open(fpath, "w") as f:
                f.write("def stable(): return 1\n")

            ghost = GhostOverlayFS(workspace_root=tmpdir)
            ghost.ghost_begin()
            ghost.ghost_mutate(fpath, "def broken_syntax: (((\n")

            try:
                ghost.ghost_commit(validate_syntax=True)
                raise AssertionError("Expected GhostSyntaxError")
            except GhostSyntaxError:
                pass

            with open(fpath, "r") as f:
                assert f.read() == "def stable(): return 1\n"


class TestCT14SparsePageOverflower:
    """CT-14: Agent writing across 4KB page boundaries."""

    def test_page_boundary_crossing(self):
        base = b"\x00" * (PAGE_SIZE * 4)
        pdm = SparsePageDeltaMap(page_size=PAGE_SIZE)

        boundary_payload = b"X" * 20
        pdm.write_at(4090, boundary_payload, base_bytes=base)

        assert pdm.dirty_pages_count() == 2
        read_back = pdm.read_at(4090, 20, base_bytes=base)
        assert read_back == boundary_payload


class TestCT15ParallelStomper:
    """CT-15: Sub-agent stomping on an actively leased AST node."""

    def test_ast_lease_blocking_stomper(self):
        registry = AstLeaseRegistry(default_ttl_sec=60.0)
        file_path = "services/user.py"
        target_fn = "ast_fn_authenticate"

        registry.acquire_lease(file_path, target_fn, "worker_1")

        try:
            registry.acquire_lease(file_path, target_fn, "worker_2")
            raise AssertionError("Expected AstLeaseConflictError")
        except AstLeaseConflictError:
            pass


class TestCT16ChaoticCoWorkerTeam:
    """CT-16: Swarm of 4 agents modifying separate functions in the same file concurrently."""

    def test_four_agent_concurrent_merge(self):
        base_code = """\"\"\"User Service\"\"\"

def get_user():
    return {}

def update_user():
    return False

def delete_user():
    return None

def verify_token():
    return True
"""
        s1 = base_code.index("    return {}")
        e1 = s1 + len("    return {}")

        s2 = base_code.index("    return False")
        e2 = s2 + len("    return False")

        s3 = base_code.index("    return None")
        e3 = s3 + len("    return None")

        s4 = base_code.index("    return True")
        e4 = s4 + len("    return True")

        patches = [
            AgentPatch("agent_1", "ast_fn_get", s1, e1, "    return {'id': 1}", ["import json"]),
            AgentPatch("agent_2", "ast_fn_upd", s2, e2, "    return time.time() > 0", ["import time"]),
            AgentPatch("agent_3", "ast_fn_del", s3, e3, "    return os.getpid() > 0", ["import os"]),
            AgentPatch("agent_4", "ast_fn_vfy", s4, e4, "    return math.isfinite(1.0)", ["import math"]),
        ]

        merged = ConflictFreeAstMerger.merge_patches(base_code, patches)

        assert "import json" in merged
        assert "import time" in merged
        assert "import os" in merged
        assert "import math" in merged
        assert "{'id': 1}" in merged
        assert "time.time() > 0" in merged
        assert "os.getpid() > 0" in merged
        assert "math.isfinite(1.0)" in merged

        tree = ast.parse(merged)
        assert tree is not None
