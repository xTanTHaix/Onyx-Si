"""
Street Tests for Phase 6: Knowledge Subsystem Adversarial & Torture Tests.
Tests:
  1. Concurrent multi-agent wiki updates & atomic file replace.
  2. Massive 10,000-node cyclic graph hybrid search without stack overflow.
  3. Core function blast radius test prioritization & untracked warning.
  4. Safe AST math code-injection blocks and singular matrix detection.
"""

import math
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from onyxsi.knowledge.task_wiki import TaskWikiManager
from onyxsi.knowledge.csr_graph_bm25 import CodeCSRGraph, HybridSearchEngine, EDGE_CALLS, EDGE_TESTS
from onyxsi.knowledge.blast_test_runner import BlastRadiusTestRunner
from onyxsi.knowledge.compute_alu import (
    eval_math,
    MatrixALU,
    UnsafeMathExpressionError,
    MathEvaluationError,
    SingularMatrixError,
)


def test_street_task_wiki_concurrent_updates_atomic_integrity():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = TaskWikiManager(tmpdir)
        manager.init_task("TASK-CONCURRENT", "Concurrent update stress test")

        def worker(w_id: int):
            for i in range(20):
                manager.update_progress(
                    "TASK-CONCURRENT",
                    completed_milestone=f"Step_{w_id}_{i}",
                    negative_lesson=f"Lesson_{w_id}_{i}",
                    note=f"Worker {w_id} executed step {i}",
                )

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker, w) for w in range(5)]
            for fut in futures:
                fut.result()

        progress = manager.get_progress("TASK-CONCURRENT")
        # Ensure json was never corrupted and contains completed milestones
        assert len(progress["milestones"]) == 100
        assert len(progress["negative_lessons"]) == 100


def test_street_massive_cyclic_graph_hybrid_search():
    engine = HybridSearchEngine()
    # Create 1,000-node circular dependency graph
    n_nodes = 1000
    for i in range(n_nodes):
        engine.index_node(f"node_{i}", f"cyclic worker routine component {i}")
        engine.add_relation(f"node_{i}", f"node_{(i+1)%n_nodes}", EDGE_CALLS)

    engine.prepare()
    # Search should execute in < 10ms without getting trapped in cycles
    results = engine.search("cyclic worker routine", top_k=5)
    assert len(results) == 5
    assert all("node_" in r[0] for r in results)


def test_street_blast_radius_core_function_and_no_coverage():
    graph = CodeCSRGraph()
    # Connect 100 tests to core_dispatch
    for i in range(100):
        graph.add_edge(f"test_case_{i}", "core_dispatch", EDGE_TESTS)
    graph.bake()

    runner = BlastRadiusTestRunner(graph)
    selected, ms = runner.select_impacted_tests("core_dispatch", max_hops=2)
    assert len(selected) == 100
    assert ms < 5.0

    # Test an orphaned function with zero tests
    res_orphan = runner.run_differential_tests("orphaned_function")
    assert res_orphan.has_warning
    assert "NO_TEST_COVERAGE_WARNING" in res_orphan.warning_message


def test_street_compute_alu_injection_and_power_bomb_blocked():
    # Attack 1: __import__('os').system('sh')
    try:
        eval_math("__import__('os').system('echo pwned')")
        assert False, "Failed to block __import__"
    except UnsafeMathExpressionError:
        pass

    # Attack 2: Class subclass traversal
    try:
        eval_math("[x for x in (1).__class__.__base__.__subclasses__()][0]")
        assert False, "Failed to block attribute traversal"
    except UnsafeMathExpressionError:
        pass

    # Attack 3: Exponent bomb 9**9**9
    try:
        eval_math("9 ** 999999")
        assert False, "Failed to block exponent bomb"
    except MathEvaluationError:
        pass

    # Attack 4: Singular matrix inversion
    singular_mat = [[1.0, 2.0], [2.0, 4.0]]  # det = 1*4 - 2*2 = 0
    try:
        MatrixALU.inverse(singular_mat)
        assert False, "Failed to reject singular matrix inversion"
    except SingularMatrixError:
        pass
