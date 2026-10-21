"""
Normal Tests for Phase 5.1: 4-Stage Causal Flight Trajectory Enforcer & Drift Vector.
"""

import os
import tempfile
from onyxsi.causal.trajectory import (
    CausalTrajectoryEnforcer,
    FlightStage,
)


def test_trajectory_full_4_stage_flight_and_log():
    with tempfile.TemporaryDirectory() as tmpdir:
        enforcer = CausalTrajectoryEnforcer(workspace_root=tmpdir)

        # Stage 1: Pre-Flight
        stage = enforcer.begin_pre_flight(
            expected_blast_radius=["auth.py", "session.py"],
            hypothesis="Fix token expiration calculation in auth.py",
        )
        assert stage == FlightStage.PRE_FLIGHT

        # Stage 2: Ghost Burn
        drift = enforcer.record_ghost_burn("auth.py")
        assert drift == 0.0
        assert enforcer.state.stage == FlightStage.GHOST_BURN

        # Stage 3: Invariant Verify
        stage = enforcer.verify_invariants(invariants_passed=True)
        assert stage == FlightStage.INVARIANT_VERIFY

        # Stage 4: Payload Commit
        log_path = enforcer.commit_payload()
        assert os.path.exists(log_path)
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Fix token expiration calculation" in content
        assert "COMMITTED" in content
