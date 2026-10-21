"""
Normal Tests for Phase 5.3: Multi-Hypothesis Orthogonal Branching & Fast-Falsification Engine.
"""

from onyxsi.causal.hypothesis_engine import (
    HypothesisBranch,
    HypothesisEngine,
)


def test_orthogonal_hypotheses_and_fast_falsification():
    engine = HypothesisEngine()
    branches = [
        HypothesisBranch(
            branch_id="H1",
            description="Database connection pool timeout under high load",
            target_nodes=["ast_fn_acquire_db_conn"],
            falsification_probe="pytest tests/test_db_pool.py",
        ),
        HypothesisBranch(
            branch_id="H2",
            description="JWT token signature clock skew validation bug",
            target_nodes=["ast_fn_verify_jwt_clock"],
            falsification_probe="pytest tests/test_jwt_skew.py",
        ),
        HypothesisBranch(
            branch_id="H3",
            description="Redis cache key serialization encoding mismatch",
            target_nodes=["ast_fn_serialize_cache_key"],
            falsification_probe="pytest tests/test_redis_codec.py",
        ),
    ]

    reg_ids = engine.register_branches(branches)
    assert reg_ids == ["H1", "H2", "H3"]

    # Falsify H1 and H2 with verbose stack traces
    verbose_trace_1 = "\n".join([f"  File 'db.py', line {i}, in pool" for i in range(50)] + ["AssertionError: Pool healthy"])
    lesson1 = engine.falsify_branch("H1", probe_succeeded=False, raw_error_trace=verbose_trace_1)
    assert lesson1 is not None
    assert "\n" not in lesson1.summary_line
    assert "AssertionError: Pool healthy" in lesson1.summary_line

    lesson2 = engine.falsify_branch("H2", probe_succeeded=False, raw_error_trace="ValueError: Clock skew 0ms")
    assert lesson2 is not None
    assert len(engine.get_negative_lessons()) == 2

    # H3 succeeds
    lesson3 = engine.falsify_branch("H3", probe_succeeded=True)
    assert lesson3 is None
    verified = engine.get_verified_branch()
    assert verified is not None
    assert verified.branch_id == "H3"
