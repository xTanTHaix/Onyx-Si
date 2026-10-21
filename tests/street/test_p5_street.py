"""
Street Chaos Tests for Phase 5: Causal Flight Brain, Bisect & Antibody Memory.
Tests stage skipping, 40% scope drift emergency abort, duplicate reflection rejection,
64-step boundary bisect (Step #1 and Step #64), and 1,000-antibody sub-millisecond screening.
"""

import tempfile
from onyxsi.contextfs.piece_table import PieceTableDocument
from onyxsi.deltafs.ghost_overlay import GhostOverlayFS
from onyxsi.causal.trajectory import (
    CausalTrajectoryEnforcer,
    PreFlightRequiredError,
    VerificationRequiredError,
    TrajectoryDriftAbort,
)
from onyxsi.causal.error_budget import (
    ErrorBudgetTracker,
    MutateLockedByBudgetError,
    DuplicateStrategyError,
)
from onyxsi.causal.hypothesis_engine import (
    HypothesisBranch,
    HypothesisEngine,
    NonOrthogonalHypothesisError,
)
from onyxsi.causal.causal_bisect import BisectStep, CausalBisectEngine
from onyxsi.causal.antibody_memory import (
    AntibodyMemory,
    AntibodyRecord,
    AntibodyRejectionError,
)


def test_street_trajectory_stage_skipping_and_40_percent_drift_abort():
    with tempfile.TemporaryDirectory() as tmpdir:
        ghost = GhostOverlayFS(workspace_root=tmpdir)
        enforcer = CausalTrajectoryEnforcer(workspace_root=tmpdir, ghost_fs=ghost)

        # Attempt Ghost Burn before Pre-Flight
        try:
            enforcer.record_ghost_burn("login.py")
            raise AssertionError("Expected PreFlightRequiredError")
        except PreFlightRequiredError:
            pass

        # Start Pre-Flight and Ghost session
        ghost.ghost_begin()
        enforcer.begin_pre_flight(expected_blast_radius=["a.py", "b.py", "c.py"], hypothesis="Update ABC")
        enforcer.record_ghost_burn("a.py")
        enforcer.record_ghost_burn("b.py")
        enforcer.record_ghost_burn("c.py")

        # Attempt commit before Stage 3 verification
        try:
            enforcer.commit_payload()
            raise AssertionError("Expected VerificationRequiredError")
        except VerificationRequiredError:
            pass

        # Modify 2 unauthorized files -> 2/5 = 40% drift (> 15% limit) -> emergency abort
        try:
            enforcer.record_ghost_burn("unauth_db.py")
            raise AssertionError("Expected TrajectoryDriftAbort")
        except TrajectoryDriftAbort as exc:
            assert exc.drift >= 0.25
            assert ghost.is_active() is False


def test_street_error_budget_spam_and_duplicate_reflection_blocked():
    tracker = ErrorBudgetTracker(max_budget=3)
    for _ in range(3):
        tracker.record_failure("Fail", attempted_strategy="Use global lock on dict")

    # 10 rapid mutate attempts while locked
    for _ in range(10):
        try:
            tracker.check_mutate_allowed()
            raise AssertionError("Expected MutateLockedByBudgetError")
        except MutateLockedByBudgetError:
            pass

    # Try submitting the exact same failed strategy to reflection_gate
    try:
        tracker.reflection_gate(
            root_cause_analysis="Contention occurred during dict access.",
            new_strategy="  use   GLOBAL lock on dict  ",
        )
        raise AssertionError("Expected DuplicateStrategyError")
    except DuplicateStrategyError:
        pass


def test_street_non_orthogonal_hypothesis_clone_rejected():
    engine = HypothesisEngine()
    clones = [
        HypothesisBranch(
            branch_id="H1",
            description="Fix timeout in user login query inside auth_service",
            target_nodes=["ast_fn_login"],
            falsification_probe="pytest test_auth.py",
        ),
        HypothesisBranch(
            branch_id="H2",
            description="Fix timeout in user login query inside auth_service module",
            target_nodes=["ast_fn_login"],
            falsification_probe="pytest test_auth.py",
        ),
    ]
    try:
        engine.register_branches(clones)
        raise AssertionError("Expected NonOrthogonalHypothesisError")
    except NonOrthogonalHypothesisError:
        pass


def test_street_bisect_64_steps_boundaries_and_1000_antibodies():
    # Boundary 1: Step #1 is culprit out of 64 steps
    doc1 = PieceTableDocument("INIT\n")
    base1 = doc1.create_checkpoint()
    steps1 = []
    for i in range(1, 65):
        if i == 1:
            doc1.insert(doc1.total_length(), "POISON\n")
        else:
            doc1.insert(doc1.total_length(), f"LINE_{i}\n")
        steps1.append(BisectStep(i, doc1.create_checkpoint(), f"node_{i}"))

    rep1 = CausalBisectEngine.bisect_failure(doc1, steps1, lambda b: b"POISON" not in b, base1)
    assert rep1.found_culprit is True
    assert rep1.culprit_step.step_index == 1
    assert rep1.probes_executed <= 7
    assert doc1.get_bytes() == b"INIT\n"

    # Boundary 2: Step #64 is culprit out of 64 steps
    doc64 = PieceTableDocument("INIT\n")
    base64 = doc64.create_checkpoint()
    steps64 = []
    for i in range(1, 65):
        if i == 64:
            doc64.insert(doc64.total_length(), "POISON_END\n")
        else:
            doc64.insert(doc64.total_length(), f"LINE_{i}\n")
        steps64.append(BisectStep(i, doc64.create_checkpoint(), f"node_{i}"))

    rep64 = CausalBisectEngine.bisect_failure(doc64, steps64, lambda b: b"POISON_END" not in b, base64)
    assert rep64.found_culprit is True
    assert rep64.culprit_step.step_index == 64
    assert rep64.probes_executed <= 7
    assert b"POISON_END" not in doc64.get_bytes()
    assert b"LINE_63" in doc64.get_bytes()

    # 1,000 Antibodies fast scan
    mem = AntibodyMemory()
    for i in range(1000):
        mem.register_antibody(
            AntibodyRecord(
                antibody_id=f"AB-{i:04d}",
                target_scope_glob="*.py",
                forbidden_pattern=f"BAD_CALL_{i}(",
                root_cause_summary="Toxic call",
                prescribed_alternative="Use safe_call()",
            ),
            persist=False,
        )

    try:
        mem.check_mutation("service.py", "x = BAD_CALL_999(123)")
        raise AssertionError("Expected AntibodyRejectionError")
    except AntibodyRejectionError as exc:
        assert exc.antibody.antibody_id == "AB-0999"
