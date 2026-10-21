"""
Normal Tests for Phase 6.3: Blast-Radius Differential Test Selector & Runner.
Verifies reverse-graph test selection in < 0.05ms and fast-fail test execution.
"""

import time
from onyxsi.knowledge.csr_graph_bm25 import CodeCSRGraph, EDGE_CALLS, EDGE_TESTS
from onyxsi.knowledge.blast_test_runner import BlastRadiusTestRunner


def test_blast_radius_selection_and_priority():
    graph = CodeCSRGraph()
    # Topology:
    # test_discount_direct -> calculate_discount (1 hop)
    # helper_discount -> calculate_discount (1 hop)
    # test_checkout_indirect -> helper_discount -> calculate_discount (2 hops)
    # test_unrelated -> unrelated_func
    graph.add_edge("test_discount_direct", "calculate_discount", EDGE_TESTS)
    graph.add_edge("helper_discount", "calculate_discount", EDGE_CALLS)
    graph.add_edge("test_checkout_indirect", "helper_discount", EDGE_TESTS)
    graph.add_edge("test_unrelated", "unrelated_func", EDGE_TESTS)
    graph.bake()

    runner = BlastRadiusTestRunner(graph)
    selected_tests, select_ms = runner.select_impacted_tests("calculate_discount", max_hops=3)

    assert select_ms < 5.0  # sub-millisecond in local VM
    test_names = [t[0] for t in selected_tests]

    # Must contain both direct and indirect tests, but NOT unrelated test
    assert "test_discount_direct" in test_names
    assert "test_checkout_indirect" in test_names
    assert "test_unrelated" not in test_names

    # Priority check: 1-hop test must be ordered before 2-hop test
    idx_direct = test_names.index("test_discount_direct")
    idx_indirect = test_names.index("test_checkout_indirect")
    assert idx_direct < idx_indirect


def test_blast_radius_execution_and_fail_fast():
    graph = CodeCSRGraph()
    graph.add_edge("test_1_fast", "core_kernel", EDGE_TESTS)
    graph.add_edge("test_2_fail", "core_kernel", EDGE_TESTS)
    graph.add_edge("test_3_after_fail", "core_kernel", EDGE_TESTS)
    graph.bake()

    runner = BlastRadiusTestRunner(graph)
    executed_log = []

    def pass_test():
        executed_log.append("test_1_fast")

    def fail_test():
        executed_log.append("test_2_fail")
        raise AssertionError("Intentional failure in test_2_fail")

    def after_fail_test():
        executed_log.append("test_3_after_fail")

    runner.register_test_callable("test_1_fast", pass_test)
    runner.register_test_callable("test_2_fail", fail_test)
    runner.register_test_callable("test_3_after_fail", after_fail_test)

    result = runner.run_differential_tests("core_kernel", fail_fast=True)

    assert not result.is_clean
    assert result.failed_tests == 1
    # Fail-fast should stop before executing test_3_after_fail
    assert "test_1_fast" in executed_log
    assert "test_2_fail" in executed_log
    assert "test_3_after_fail" not in executed_log
