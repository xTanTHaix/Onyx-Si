"""
Normal Tests for Phase 5.4: In-RAM Automated Causal Bisect (O(log K)) & Codebase Antibody Memory.
"""

import os
import tempfile
from onyxsi.contextfs.piece_table import PieceTableDocument
from onyxsi.causal.causal_bisect import BisectStep, CausalBisectEngine
from onyxsi.causal.antibody_memory import (
    AntibodyMemory,
    AntibodyRecord,
    AntibodyRejectionError,
)


def test_in_ram_causal_bisect_16_steps_pinpoints_step_11():
    doc = PieceTableDocument("VALUE = 0\n")
    baseline_cp = doc.create_checkpoint()

    steps = []
    for i in range(1, 17):
        if i == 11:
            # Step 11 introduces the poison bug
            doc.insert(doc.total_length(), f"STEP_{i} = 'BUG_INJECTED'\n")
        else:
            doc.insert(doc.total_length(), f"STEP_{i} = 'OK'\n")
        cp_id = doc.create_checkpoint()
        steps.append(BisectStep(step_index=i, checkpoint_id=cp_id, node_id=f"ast_fn_step_{i}"))

    def invariant_check(content: bytes) -> bool:
        return b"BUG_INJECTED" not in content

    report = CausalBisectEngine.bisect_failure(
        doc=doc,
        steps=steps,
        invariant_fn=invariant_check,
        baseline_checkpoint_id=baseline_cp,
    )

    assert report.found_culprit is True
    assert report.culprit_step is not None
    assert report.culprit_step.step_index == 11
    assert report.probes_executed <= 5  # ceil(log2(16)) + 1
    assert report.rolled_back_to_checkpoint == steps[9].checkpoint_id  # Step 10 checkpoint
    assert b"BUG_INJECTED" not in doc.get_bytes()
    assert b"STEP_10 = 'OK'" in doc.get_bytes()


def test_antibody_memory_blocks_repeat_bug():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = os.path.join(tmpdir, ".onyxsi", "wiki", "ANTIBODIES.json")
        memory = AntibodyMemory(storage_path=json_path)

        memory.register_antibody(
            AntibodyRecord(
                antibody_id="AB-001",
                target_scope_glob="*.py",
                forbidden_pattern="select.select([os.pipe()",
                root_cause_summary="Windows Winsock select() raises WinError 10038 on anonymous pipes",
                prescribed_alternative="Use socket.socketpair() for cross-platform non-blocking select()",
            )
        )

        # Reload from disk to verify persistence
        memory2 = AntibodyMemory(storage_path=json_path)
        assert memory2.count() == 1

        # Safe mutation passes
        memory2.check_mutation("wakeup.py", "r, w = socket.socketpair()")

        # Toxic repeat pattern is rejected immediately
        try:
            memory2.check_mutation("wakeup.py", "rdy = select.select([os.pipe()[0]], [], [])")
            raise AssertionError("Expected AntibodyRejectionError")
        except AntibodyRejectionError as exc:
            assert exc.antibody.antibody_id == "AB-001"
            assert "socket.socketpair()" in str(exc)
