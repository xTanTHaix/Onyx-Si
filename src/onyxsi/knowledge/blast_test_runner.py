"""
OnyxSi Phase 6.3: Blast-Radius Differential Test Selector & Runner.
Traverses CSR reverse edges (CALLED_BY, TESTED_BY) in < 0.05ms to find only
the test functions (test_*) impacted by a modified AST node, prioritizing
1-hop direct callers for fail-fast execution in < 0.3 seconds.
100% Python Standard Library only. Zero External Dependencies. Zero UI.
"""

import time
import inspect
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from onyxsi.knowledge.csr_graph_bm25 import (
    CodeCSRGraph,
    EDGE_CALLS,
    EDGE_CALLED_BY,
    EDGE_TESTS,
    EDGE_TESTED_BY,
)


class BlastTestResult:
    """Encapsulates test selection metrics and execution results."""

    def __init__(
        self,
        target_node: str,
        selected_tests: List[Tuple[str, int]],
        selection_time_ms: float,
        executed_tests: int = 0,
        passed_tests: int = 0,
        failed_tests: int = 0,
        execution_time_ms: float = 0.0,
        has_warning: bool = False,
        warning_message: Optional[str] = None,
        failures: Optional[List[Tuple[str, str]]] = None,
        skipped_tests: int = 0,
        skipped_names: Optional[List[str]] = None,
    ):
        self.target_node = target_node
        self.selected_tests = selected_tests
        self.selection_time_ms = selection_time_ms
        self.executed_tests = executed_tests
        self.passed_tests = passed_tests
        self.failed_tests = failed_tests
        self.execution_time_ms = execution_time_ms
        self.has_warning = has_warning
        self.warning_message = warning_message
        self.failures = failures or []
        self.skipped_tests = skipped_tests
        self.skipped_names = skipped_names or []

    @property
    def is_clean(self) -> bool:
        """
        True only when at least one real test ran, none failed, and none were
        skipped for lack of a registered callable.

        A graph-only test name with no Python callable is NOT evidence of a pass;
        counting it as one is how a broken change ships green.
        """
        return self.failed_tests == 0 and self.skipped_tests == 0 and self.passed_tests > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_node": self.target_node,
            "selected_tests_count": len(self.selected_tests),
            "selected_tests": [{"name": t[0], "hop": t[1]} for t in self.selected_tests],
            "selection_time_ms": round(self.selection_time_ms, 4),
            "executed_tests": self.executed_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "skipped_tests": self.skipped_tests,
            "skipped_names": self.skipped_names,
            "execution_time_ms": round(self.execution_time_ms, 4),
            "is_clean": self.is_clean,
            "has_warning": self.has_warning,
            "warning_message": self.warning_message,
            "failures": self.failures,
        }


class BlastRadiusTestRunner:
    """
    Reverse-graph differential test selector and rapid runner.
    Takes a CodeCSRGraph and maps dependencies backwards to find test functions.
    """

    def __init__(self, graph: Optional[CodeCSRGraph] = None):
        self.graph = graph or CodeCSRGraph()
        self._test_registry: Dict[str, Callable[[], Any]] = {}

    def register_test_callable(self, test_name: str, fn: Callable[[], Any]) -> None:
        """Binds a test node name to an executable Python callable."""
        self._test_registry[test_name] = fn

    def select_impacted_tests(
        self,
        modified_node: str,
        max_hops: int = 3,
    ) -> Tuple[List[Tuple[str, int]], float]:
        """
        Walks CSR reverse edges from modified_node up to max_hops.
        Returns:
          - List of (test_name, hop_distance) sorted by hop ascending (1-hop direct callers first)
          - Selection duration in milliseconds (< 0.05ms target)
        """
        t0 = time.perf_counter()
        
        if not self.graph.is_baked:
            self.graph.bake()

        if modified_node not in self.graph.node_to_id:
            dur_ms = (time.perf_counter() - t0) * 1000.0
            return [], dur_ms

        start_id = self.graph.node_to_id[modified_node]

        # BFS over reverse CSR edges
        visited_hops: Dict[int, int] = {start_id: 0}
        queue: deque = deque([(start_id, 0)])

        selected_tests: List[Tuple[str, int]] = []

        while queue:
            curr_id, hops = queue.popleft()
            if hops >= max_hops:
                continue

            rev_edges = self.graph.get_reverse_neighbors(curr_id)
            for source_id, edge_type in rev_edges:
                source_name = self.graph.id_to_node[source_id]
                next_hop = hops + 1

                if source_id not in visited_hops or next_hop < visited_hops[source_id]:
                    visited_hops[source_id] = next_hop
                    queue.append((source_id, next_hop))

                    # Test detection rule: name starts with 'test_' or 'test' or has TEST/TESTED_BY edge
                    is_test = (
                        source_name.startswith("test_")
                        or source_name.startswith("test")
                        or edge_type in (EDGE_TESTS, EDGE_TESTED_BY)
                        or source_name in self._test_registry
                    )
                    if is_test:
                        selected_tests.append((source_name, next_hop))

        # Sort tests: 1-hop first (fail-fast), then 2-hop, then 3-hop; then alphabetically
        selected_tests.sort(key=lambda item: (item[1], item[0]))

        # Deduplicate while preserving order
        deduped: List[Tuple[str, int]] = []
        seen: Set[str] = set()
        for t_name, hop in selected_tests:
            if t_name not in seen:
                seen.add(t_name)
                deduped.append((t_name, hop))

        dur_ms = (time.perf_counter() - t0) * 1000.0
        return deduped, dur_ms

    def run_differential_tests(
        self,
        modified_node: str,
        max_hops: int = 3,
        fail_fast: bool = True,
    ) -> BlastTestResult:
        """
        Selects and executes impacted tests.
        """
        selected_tests, selection_ms = self.select_impacted_tests(modified_node, max_hops=max_hops)

        warning = None
        has_warning = False
        if not selected_tests:
            has_warning = True
            warning = f"NO_TEST_COVERAGE_WARNING: No test callers found within {max_hops} hops of '{modified_node}'"

        exec_start = time.perf_counter()
        passed = 0
        failed = 0
        skipped = 0
        skipped_names: List[str] = []
        failures: List[Tuple[str, str]] = []

        for test_name, hop in selected_tests:
            if test_name in self._test_registry:
                test_fn = self._test_registry[test_name]
                try:
                    test_fn()
                    passed += 1
                except Exception as ex:
                    failed += 1
                    failures.append((test_name, str(ex)))
                    if fail_fast:
                        break
            else:
                # No callable registered: the test was never executed. Counting
                # this as a pass would be a fabricated green result.
                skipped += 1
                skipped_names.append(test_name)

        exec_ms = (time.perf_counter() - exec_start) * 1000.0

        if skipped and not warning:
            has_warning = True
            warning = (
                f"UNEXECUTED_TESTS_WARNING: {skipped} selected test(s) have no registered "
                f"callable and were not run: {skipped_names[:5]}"
            )

        return BlastTestResult(
            target_node=modified_node,
            selected_tests=selected_tests,
            selection_time_ms=selection_ms,
            executed_tests=passed + failed,
            passed_tests=passed,
            failed_tests=failed,
            execution_time_ms=exec_ms,
            has_warning=has_warning,
            warning_message=warning,
            failures=failures,
            skipped_tests=skipped,
            skipped_names=skipped_names,
        )
