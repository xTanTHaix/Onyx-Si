"""
Character Scenario Tests for Phase 5: CT-21 through CT-24.
"""

import os
import tempfile
from onyxsi.contextfs.piece_table import PieceTableDocument
from onyxsi.causal.trajectory import (
    CausalTrajectoryEnforcer,
    PreFlightRequiredError,
    TrajectoryDriftAbort,
)
from onyxsi.causal.error_budget import (
    ErrorBudgetTracker,
    MutateLockedByBudgetError,
)
from onyxsi.causal.micro_wal import MicroWAL
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


class TestCT21BlindPatcherAndScopeCreepDrifter:
    """CT-21: Agent skipping pre-flight and drifting into unauthorized database files."""

    def test_blind_patch_and_scope_drift_blocked(self):
        enforcer = CausalTrajectoryEnforcer()

        # 1. Blind patch without pre-flight
        try:
            enforcer.record_ghost_burn("login.py")
            raise AssertionError("Expected PreFlightRequiredError")
        except PreFlightRequiredError:
            pass

        # 2. Scope creep drift > 15%
        enforcer.begin_pre_flight(["login.py"], "Fix login typo")
        enforcer.record_ghost_burn("login.py")
        try:
            enforcer.record_ghost_burn("schema_migrations.sql")
            raise AssertionError("Expected TrajectoryDriftAbort")
        except TrajectoryDriftAbort:
            pass


class TestCT22InsanityLooperAndCrashVictim:
    """CT-22: Agent failing 3 times in a row and suffering sudden process crash."""

    def test_insanity_loop_locked_and_wal_repro_generated(self):
        budget = ErrorBudgetTracker(max_budget=3)
        wal = MicroWAL(capacity=64)

        for i in range(1, 4):
            wal.record("test_run", "test_auth.py", "FAIL", f"Attempt {i} failed")
            budget.record_failure(f"AssertionError #{i}", attempted_strategy="Same broken fix")

        try:
            budget.check_mutate_allowed()
            raise AssertionError("Expected MutateLockedByBudgetError")
        except MutateLockedByBudgetError:
            pass

        with tempfile.TemporaryDirectory() as tmpdir:
            repro = os.path.join(tmpdir, "CRASH_REPRO.md")
            text = wal.write_crash_repro(repro, "Power loss simulated")
            assert "Attempt 3 failed" in text


class TestCT23SingleHypothesisGamblerAndTraceHoarder:
    """CT-23: Agent cloning hypotheses and hoarding huge stack traces."""

    def test_clone_rejected_and_trace_compressed_to_one_line(self):
        engine = HypothesisEngine()
        try:
            engine.register_branches([
                HypothesisBranch("B1", "Patch retry timeout in client", ["client.py"], "pytest"),
                HypothesisBranch("B2", "Patch retry timeout in client", ["client.py"], "pytest"),
            ])
            raise AssertionError("Expected NonOrthogonalHypothesisError")
        except NonOrthogonalHypothesisError:
            pass

        engine.register_branches([
            HypothesisBranch("B1", "Patch retry timeout in client", ["client.py"], "pytest -k client"),
            HypothesisBranch("B2", "Rebuild token bucket limiter in server", ["limiter.py"], "pytest -k limiter"),
        ])
        huge_trace = "Traceback:\n" + ("  frame_line\n" * 500) + "KeyError: 'missing_token'"
        lesson = engine.falsify_branch("B1", probe_succeeded=False, raw_error_trace=huge_trace)
        assert lesson is not None
        assert len(lesson.summary_line.splitlines()) == 1
        assert "KeyError: 'missing_token'" in lesson.summary_line


class TestCT24RepeatOffenderAndMultiStepMysteryBreaker:
    """CT-24: Agent repeating known bad patterns and hiding a bug at Step #11 of 16."""

    def test_antibody_blocker_and_step_11_bisect(self):
        mem = AntibodyMemory()
        mem.register_antibody(
            AntibodyRecord(
                antibody_id="AB-PSUTIL",
                target_scope_glob="*.py",
                forbidden_pattern="import psutil",
                root_cause_summary="Violates Zero External Dependency law",
                prescribed_alternative="Use stdlib os / ctypes / resource",
            ),
            persist=False,
        )

        try:
            mem.check_mutation("sandbox.py", "import os\nimport psutil\n")
            raise AssertionError("Expected AntibodyRejectionError")
        except AntibodyRejectionError:
            pass

        doc = PieceTableDocument("x = 0\n")
        base_cp = doc.create_checkpoint()
        steps = []
        for s in range(1, 17):
            doc.insert(doc.total_length(), f"y_{s} = {'1/0' if s == 11 else s}\n")
            steps.append(BisectStep(s, doc.create_checkpoint(), f"node_{s}"))

        report = CausalBisectEngine.bisect_failure(doc, steps, lambda b: b"1/0" not in b, base_cp)
        assert report.found_culprit is True
        assert report.culprit_step.step_index == 11
