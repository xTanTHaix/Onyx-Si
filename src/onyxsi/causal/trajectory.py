"""
4-Stage Causal Flight Trajectory Enforcer & Drift Vector (< 15%).
Enforces strict execution ordering:
Stage 1 (PRE_FLIGHT) -> Stage 2 (GHOST_BURN) -> Stage 3 (INVARIANT_VERIFY) -> Stage 4 (PAYLOAD_COMMIT).
Aborts immediately if Causal Drift Vector exceeds 0.15 (15%).
"""

import os
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Set
from onyxsi.deltafs.ghost_overlay import GhostOverlayFS

MAX_CAUSAL_DRIFT = 0.15


class FlightStage(IntEnum):
    IDLE = 0
    PRE_FLIGHT = 1
    GHOST_BURN = 2
    INVARIANT_VERIFY = 3
    PAYLOAD_COMMIT = 4


class TrajectoryError(Exception):
    pass


class PreFlightRequiredError(TrajectoryError):
    pass


class VerificationRequiredError(TrajectoryError):
    pass


class TrajectoryDriftAbort(TrajectoryError):
    def __init__(self, drift: float, unauthorized_targets: Set[str]):
        self.drift = drift
        self.unauthorized_targets = unauthorized_targets
        super().__init__(
            f"TRAJECTORY_DRIFT_ABORT: Causal drift {drift:.2%} exceeds 15% limit "
            f"(unauthorized targets: {sorted(unauthorized_targets)})"
        )


@dataclass(slots=True)
class TrajectoryState:
    stage: FlightStage = FlightStage.IDLE
    hypothesis: str = ""
    expected_blast_radius: Set[str] = field(default_factory=set)
    modified_targets: Set[str] = field(default_factory=set)
    verified: bool = False
    last_drift: float = 0.0


class CausalTrajectoryEnforcer:
    """
    4-Stage Flight Controller for autonomous code modifications.
    Prevents blind patching and scope-creep drift.
    """

    def __init__(
        self,
        workspace_root: str = ".",
        max_drift: float = MAX_CAUSAL_DRIFT,
        ghost_fs: Optional[GhostOverlayFS] = None,
    ):
        self.workspace_root = os.path.realpath(workspace_root)
        self.max_drift = max_drift
        self.ghost_fs = ghost_fs
        self.state = TrajectoryState()

    def begin_pre_flight(self, expected_blast_radius: List[str], hypothesis: str) -> FlightStage:
        """Stage 1: Declares blast radius and initial hypothesis."""
        if not expected_blast_radius:
            raise TrajectoryError("Expected blast radius cannot be empty")
        self.state = TrajectoryState(
            stage=FlightStage.PRE_FLIGHT,
            hypothesis=hypothesis,
            expected_blast_radius=set(expected_blast_radius),
        )
        return self.state.stage

    def record_ghost_burn(self, target_node_or_file: str) -> float:
        """Stage 2: Records an in-RAM mutation and checks live Causal Drift Vector."""
        if self.state.stage < FlightStage.PRE_FLIGHT:
            raise PreFlightRequiredError("Cannot execute Ghost Burn before Stage 1 Pre-Flight inspection")

        self.state.stage = FlightStage.GHOST_BURN
        self.state.verified = False
        self.state.modified_targets.add(target_node_or_file)

        drift = self.compute_drift()
        self.state.last_drift = drift
        if drift > self.max_drift:
            unauth = self.state.modified_targets - self.state.expected_blast_radius
            self.abort_flight()
            raise TrajectoryDriftAbort(drift=drift, unauthorized_targets=unauth)
        return drift

    def compute_drift(self) -> float:
        """
        Computes Causal Drift Vector:
        Fraction of modified targets that fall outside the declared blast radius.
        """
        if not self.state.modified_targets:
            return 0.0
        unauthorized = self.state.modified_targets - self.state.expected_blast_radius
        return len(unauthorized) / len(self.state.modified_targets)

    def verify_invariants(self, invariants_passed: bool) -> FlightStage:
        """Stage 3: Verifies blast-radius test invariants and drift ceiling."""
        if self.state.stage < FlightStage.GHOST_BURN:
            raise PreFlightRequiredError("Cannot verify invariants before Stage 2 Ghost Burn")

        drift = self.compute_drift()
        self.state.last_drift = drift
        if drift > self.max_drift:
            unauth = self.state.modified_targets - self.state.expected_blast_radius
            self.abort_flight()
            raise TrajectoryDriftAbort(drift=drift, unauthorized_targets=unauth)

        if not invariants_passed:
            self.state.verified = False
            raise VerificationRequiredError("Invariant verification failed")

        self.state.stage = FlightStage.INVARIANT_VERIFY
        self.state.verified = True
        return self.state.stage

    def commit_payload(self) -> str:
        """Stage 4: Commits verified payload and appends entry to .onyxsi/FLIGHT_LOG.md."""
        if self.state.stage != FlightStage.INVARIANT_VERIFY or not self.state.verified:
            raise VerificationRequiredError("Cannot commit payload before passing Stage 3 Invariant Verification")

        # The payload lives in the Ghost overlay; flushing it to disk is what
        # "payload commit" means. Do it before logging success, so a failed
        # write never leaves a COMMITTED line behind.
        if self.ghost_fs is not None and self.ghost_fs.is_active():
            self.ghost_fs.ghost_commit()

        self.state.stage = FlightStage.PAYLOAD_COMMIT
        log_dir = os.path.join(self.workspace_root, ".onyxsi")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "FLIGHT_LOG.md")

        entry = (
            f"- **[{time.strftime('%Y-%m-%d %H:%M:%S')}]** Hypothesis: `{self.state.hypothesis}` | "
            f"Modified: `{sorted(self.state.modified_targets)}` | "
            f"Drift: `{self.state.last_drift:.4f}` | Status: `COMMITTED`\n"
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)

        return log_path

    def abort_flight(self) -> None:
        """Emergency abort: discards Ghost RAM and resets flight state."""
        if self.ghost_fs is not None and self.ghost_fs.is_active():
            self.ghost_fs.ghost_discard()
        self.state = TrajectoryState()
