"""
In-RAM Automated Causal Bisect Engine (O(log K)).
Performs binary search over sequential NanoCheckpoints in PieceTableDocument
to isolate the exact mutation step that broke an invariant in O(log2 K) probes,
and automatically rolls the document back to the last known good state.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional
from onyxsi.contextfs.piece_table import PieceTableDocument


@dataclass(slots=True, frozen=True)
class BisectStep:
    """Metadata for a single sequential code mutation step in RAM."""
    step_index: int  # 1-indexed (1..K)
    checkpoint_id: int
    node_id: str
    description: str = ""


@dataclass(slots=True, frozen=True)
class BisectReport:
    """Result of an O(log K) In-RAM Causal Bisect run."""
    found_culprit: bool
    culprit_step: Optional[BisectStep]
    probes_executed: int
    rolled_back_to_checkpoint: Optional[int]


class CausalBisectEngine:
    """
    Binary search regression hunter operating directly on PieceTable NanoCheckpoints in RAM.
    Pinpoints the first failing step across K steps in ceil(log2(K)) test evaluations.
    """

    @staticmethod
    def bisect_failure(
        doc: PieceTableDocument,
        steps: List[BisectStep],
        invariant_fn: Callable[[bytes], bool],
        baseline_checkpoint_id: Optional[int] = None,
    ) -> BisectReport:
        """
        Finds the earliest step in `steps` where `invariant_fn(doc.get_bytes())` returns False.
        Automatically rolls `doc` back to the checkpoint immediately preceding the culprit step.
        """
        if not steps:
            return BisectReport(
                found_culprit=False,
                culprit_step=None,
                probes_executed=0,
                rolled_back_to_checkpoint=baseline_checkpoint_id,
            )

        lo = 0
        hi = len(steps) - 1
        probes = 0
        culprit_idx: Optional[int] = None

        while lo <= hi:
            mid = (lo + hi) // 2
            step = steps[mid]
            doc.rollback(step.checkpoint_id)
            probes += 1

            passed = invariant_fn(doc.get_bytes())
            if not passed:
                culprit_idx = mid
                hi = mid - 1
            else:
                lo = mid + 1

        if culprit_idx is None:
            # All steps passed; restore final step
            doc.rollback(steps[-1].checkpoint_id)
            return BisectReport(
                found_culprit=False,
                culprit_step=None,
                probes_executed=probes,
                rolled_back_to_checkpoint=steps[-1].checkpoint_id,
            )

        culprit = steps[culprit_idx]
        if culprit_idx > 0:
            safe_cp = steps[culprit_idx - 1].checkpoint_id
        else:
            safe_cp = baseline_checkpoint_id

        if safe_cp is not None:
            doc.rollback(safe_cp)

        return BisectReport(
            found_culprit=True,
            culprit_step=culprit,
            probes_executed=probes,
            rolled_back_to_checkpoint=safe_cp,
        )
