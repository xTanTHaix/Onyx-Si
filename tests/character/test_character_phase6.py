"""
Character Tests for Phase 6: CT-25 through CT-28.
CT-25: The Amnesiac Handoff & Concurrent Task Collider
CT-26: The Grep Caveman & Cyclic Graph Trap
CT-27: The Full-Suite Time Waster
CT-28: The Mental Math Hallucinator & Eval Injector
"""

import tempfile
from onyxsi.knowledge.task_wiki import TaskWikiManager
from onyxsi.knowledge.csr_graph_bm25 import HybridSearchEngine, EDGE_CALLS, EDGE_TESTS
from onyxsi.knowledge.blast_test_runner import BlastRadiusTestRunner
from onyxsi.knowledge.compute_alu import (
    eval_math,
    MatrixALU,
    DimensionMismatchError,
    UnsafeMathExpressionError,
)


class TestCT25AmnesiacHandoffAndTaskCollider:
    """
    CT-25: First agent drops session mid-flight without chat history.
    Second agent picks up directly from AGENT_HANDOFF.md and PROGRESS.json.
    """

    def test_amnesiac_agent_seamless_continuation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki = TaskWikiManager(tmpdir)
            wiki.init_task("TASK-HANDOFF", "# Refactor Memory Superblock")
            wiki.update_progress(
                "TASK-HANDOFF",
                completed_milestone="M1_Analyze_Layout",
                active_milestone="M2_Implement_128B_Padding",
                ast_leases=["ast_struct_SuperblockHeader_f1e2d3c4"],
                negative_lesson="Do not use 64B padding on dual-socket AMD EPYC",
                modified_file="src/hal/superblock.py",
            )
            handoff_md = wiki.generate_handoff(
                "TASK-HANDOFF",
                next_actions=["Apply 128B cache-line padding", "Run alignment check"],
            )

            # Agent 2 starts with zero in-memory context:
            wiki_agent2 = TaskWikiManager(tmpdir)
            state2 = wiki_agent2.get_progress("TASK-HANDOFF")

            assert state2["active_milestone"] == "M2_Implement_128B_Padding"
            assert "Do not use 64B padding on dual-socket AMD EPYC" in state2["negative_lessons"]
            assert "ast_struct_SuperblockHeader_f1e2d3c4" in state2["ast_leases"]
            assert "Apply 128B cache-line padding" in handoff_md


class TestCT26GrepCavemanAndCyclicGraphTrap:
    """
    CT-26: Agent searches with informal/fuzzy queries in a 5,000-node cyclic codebase.
    CSR Graph + Tuned BM25 retrieves adjacent callers without getting stuck in cycles.
    """

    def test_fuzzy_search_in_cyclic_graph(self):
        engine = HybridSearchEngine(k1=1.5, b=0.75)
        # Build 500 cyclic modules
        for i in range(500):
            engine.index_node(f"module_{i}", f"service worker handling order checkout flow step {i}")
            engine.add_relation(f"module_{i}", f"module_{(i+1)%500}", EDGE_CALLS)

        engine.prepare()
        results = engine.search("checkout order service", top_k=5)
        assert len(results) == 5
        assert all("module_" in r[0] for r in results)


class TestCT27FullSuiteTimeWaster:
    """
    CT-27: Agent touches one small function and tries to run 2,000 tests.
    BlastRadiusTestRunner filters to only the 3 impacted tests for fail-fast execution.
    """

    def test_blast_radius_cuts_execution_from_thousands_to_handful(self):
        from onyxsi.knowledge.csr_graph_bm25 import CodeCSRGraph
        graph = CodeCSRGraph()

        # Connect 3 tests to target function, and 500 other tests to other functions
        graph.add_edge("test_discount_1", "calc_discount", EDGE_TESTS)
        graph.add_edge("test_discount_2", "calc_discount", EDGE_TESTS)
        graph.add_edge("test_discount_3", "calc_discount", EDGE_TESTS)

        for i in range(500):
            graph.add_edge(f"test_unrelated_{i}", f"other_func_{i}", EDGE_TESTS)
        graph.bake()

        runner = BlastRadiusTestRunner(graph)
        selected, select_ms = runner.select_impacted_tests("calc_discount", max_hops=2)

        # Instead of 503 tests, exactly 3 are selected
        assert len(selected) == 3
        test_names = {t[0] for t in selected}
        assert test_names == {"test_discount_1", "test_discount_2", "test_discount_3"}
        assert select_ms < 5.0


class TestCT28MentalMathHallucinatorAndEvalInjector:
    """
    CT-28: Agent hallucinates arithmetic or attempts to inject arbitrary Python code via eval.
    ComputeALU blocks code injection and enforces strict matrix dimensions.
    """

    def test_eval_injection_blocked_and_dimension_mismatch_detected(self):
        # 1. Reject code injection
        try:
            eval_math("open('/etc/shadow').read()")
            assert False, "Failed to block open()"
        except UnsafeMathExpressionError:
            pass

        # 2. Reject mismatched matrix multiplication
        # 2x3 matrix * 2x2 matrix cannot be multiplied
        matA = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]  # (2, 3)
        matB = [[1.0, 2.0], [3.0, 4.0]]            # (2, 2)
        try:
            MatrixALU.matmul(matA, matB)
            assert False, "Failed to catch matrix dimension mismatch"
        except DimensionMismatchError:
            pass
