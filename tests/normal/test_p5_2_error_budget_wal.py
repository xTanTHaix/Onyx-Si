"""
Normal Tests for Phase 5.2: 3-Strike Error Budget, Mandatory Reflection Gate & Micro-WAL.
"""

import os
import tempfile
from onyxsi.causal.error_budget import (
    ErrorBudgetTracker,
    MutateLockedByBudgetError,
)
from onyxsi.causal.micro_wal import MicroWAL


def test_error_budget_3_strikes_lock_and_reflection_reset():
    tracker = ErrorBudgetTracker(max_budget=3)
    assert tracker.remaining_budget == 3

    tracker.record_failure("Test fail 1", attempted_strategy="Retry regex parse v1")
    tracker.record_failure("Test fail 2", attempted_strategy="Retry regex parse v2")
    tracker.check_mutate_allowed()  # Still 1 left

    tracker.record_failure("Test fail 3", attempted_strategy="Retry regex parse v3")
    assert tracker.remaining_budget == 0
    assert tracker.is_locked is True

    try:
        tracker.check_mutate_allowed()
        raise AssertionError("Expected MutateLockedByBudgetError")
    except MutateLockedByBudgetError:
        pass

    # Unlock via reflection_gate with a genuinely new strategy
    reset_budget = tracker.reflection_gate(
        root_cause_analysis="Regex cannot parse nested parentheses across line boundaries.",
        new_strategy="Switch to BitplaneAstScanner 64-byte stateful token stream.",
    )
    assert reset_budget == 3
    assert tracker.is_locked is False
    tracker.check_mutate_allowed()


def test_micro_wal_circular_recording_and_crash_repro():
    with tempfile.TemporaryDirectory() as tmpdir:
        wal = MicroWAL(capacity=64)
        for i in range(10):
            wal.record("vfs_mutate", f"file_{i}.py", "OK", f"patch_{i}")

        repro_path = os.path.join(tmpdir, "CRASH_REPRO.md")
        content = wal.write_crash_repro(repro_path, crash_reason="Simulated worker segfault")
        assert os.path.exists(repro_path)
        assert "Simulated worker segfault" in content
        assert "file_9.py" in content
