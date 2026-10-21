"""
Normal Tests for Phase 6.2: Vectorless CSR Graph (SoA) & Tuned BM25 Search Engine.
Verifies CSR array packing, Okapi BM25 scoring, and 2-hop BFS hybrid search.
"""

import time
from onyxsi.knowledge.csr_graph_bm25 import (
    CodeCSRGraph,
    TunedBM25Index,
    HybridSearchEngine,
    tokenize_code,
    EDGE_CALLS,
    EDGE_IMPORTS,
    EDGE_TESTS,
)


def test_code_tokenization():
    tokens = tokenize_code("process_manager.CSRGraphBM25.get_or_create_node_v2")
    assert "process" in tokens
    assert "manager" in tokens
    assert "csr" in tokens
    assert "graph" in tokens
    assert "bm" in tokens
    assert "25" in tokens
    assert "node" in tokens


def test_csr_graph_baking_and_bidirectional_traversal():
    graph = CodeCSRGraph()
    graph.add_edge("func_a", "func_b", EDGE_CALLS)
    graph.add_edge("func_b", "func_c", EDGE_CALLS)
    graph.add_edge("test_a", "func_a", EDGE_TESTS)
    graph.bake()

    assert graph.is_baked
    assert graph.num_nodes() == 4

    id_b = graph.node_to_id["func_b"]
    fwd_b = graph.get_forward_neighbors(id_b)
    assert len(fwd_b) == 1
    assert graph.id_to_node[fwd_b[0][0]] == "func_c"

    rev_b = graph.get_reverse_neighbors(id_b)
    assert len(rev_b) == 1
    assert graph.id_to_node[rev_b[0][0]] == "func_a"


def test_hybrid_search_bm25_and_2hop_proximity():
    engine = HybridSearchEngine(k1=1.5, b=0.75)
    engine.index_node("calculate_tax", "calculates federal and state sales tax rates")
    engine.index_node("verify_auth_token", "authenticates user session tokens with jwt")
    engine.index_node("tax_rate_service", "service providing tax brackets and lookup")
    engine.index_node("user_profile_handler", "manages user accounts and profile data")

    # Connect tax nodes via CSR edges
    engine.add_relation("calculate_tax", "tax_rate_service", EDGE_CALLS)
    engine.prepare()

    t0 = time.perf_counter()
    results = engine.search("calculate tax rate", top_k=3)
    duration_ms = (time.perf_counter() - t0) * 1000.0

    assert duration_ms < 10.0  # target < 2ms in production, generous for VM
    assert len(results) >= 2
    top_node, top_score = results[0]
    assert "tax" in top_node
    # Both tax nodes should be in results
    names = [r[0] for r in results]
    assert "calculate_tax" in names
    assert "tax_rate_service" in names
