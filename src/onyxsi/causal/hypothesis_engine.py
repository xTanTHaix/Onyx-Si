"""
Multi-Hypothesis Orthogonal Branching & Fast-Falsification Engine.
Enforces structural orthogonality across competing debugging hypotheses,
executes fast-falsification probes, and compresses failed stack traces into
1-line Negative Causal Lessons.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from onyxsi.deltafs.ghost_overlay import GhostOverlayFS

MAX_JACCARD_OVERLAP = 0.70
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


class HypothesisError(Exception):
    pass


class NonOrthogonalHypothesisError(HypothesisError):
    pass


class MissingFalsificationProbeError(HypothesisError):
    pass


@dataclass(slots=True)
class HypothesisBranch:
    """A competing structural debugging hypothesis."""
    branch_id: str
    description: str
    target_nodes: List[str]
    falsification_probe: str
    status: str = "PENDING"  # "PENDING", "FALSIFIED", "VERIFIED"


@dataclass(slots=True, frozen=True)
class NegativeCausalLesson:
    """Single-line compressed lesson distilled from a falsified hypothesis."""
    branch_id: str
    target_nodes: Tuple[str, ...] = ()
    summary_line: str = ""


class HypothesisEngine:
    """
    Manages orthogonal hypothesis branching and fast falsification.
    Prevents single-track gambling and context-bloating stack trace hoarding.
    """

    def __init__(self, max_similarity: float = MAX_JACCARD_OVERLAP):
        self.max_similarity = max_similarity
        self._branches: Dict[str, HypothesisBranch] = {}
        self._negative_lessons: List[NegativeCausalLesson] = []

    def register_branches(self, branches: List[HypothesisBranch]) -> List[str]:
        """Validates that all branches have falsification probes and are mutually orthogonal."""
        if not branches:
            raise HypothesisError("Must provide at least one hypothesis branch")

        for b in branches:
            if not b.falsification_probe or not b.falsification_probe.strip():
                raise MissingFalsificationProbeError(
                    f"Branch '{b.branch_id}' is missing a mandatory falsification_probe"
                )

        for i in range(len(branches)):
            for j in range(i + 1, len(branches)):
                sim = self._compute_similarity(branches[i], branches[j])
                if sim > self.max_similarity:
                    raise NonOrthogonalHypothesisError(
                        f"Branches '{branches[i].branch_id}' and '{branches[j].branch_id}' are non-orthogonal "
                        f"(similarity {sim:.2f} > {self.max_similarity:.2f})"
                    )

        registered_ids = []
        for b in branches:
            self._branches[b.branch_id] = b
            registered_ids.append(b.branch_id)
        return registered_ids

    def falsify_branch(
        self,
        branch_id: str,
        probe_succeeded: bool,
        raw_error_trace: str = "",
        ghost_fs: Optional[GhostOverlayFS] = None,
    ) -> Optional[NegativeCausalLesson]:
        """
        Evaluates falsification probe outcome.
        If probe fails (probe_succeeded=False), discards Ghost RAM and compresses trace to 1 line.
        """
        if branch_id not in self._branches:
            raise HypothesisError(f"Unknown hypothesis branch: {branch_id}")

        branch = self._branches[branch_id]
        if probe_succeeded:
            branch.status = "VERIFIED"
            return None

        branch.status = "FALSIFIED"
        if ghost_fs is not None and ghost_fs.is_active():
            ghost_fs.ghost_discard()

        root_err = self._extract_single_line_reason(raw_error_trace)
        summary = (
            f"[FALSIFIED:{branch_id}] Targets={','.join(branch.target_nodes)} | "
            f"Probe='{branch.falsification_probe}' -> {root_err}"
        )[:180]

        lesson = NegativeCausalLesson(
            branch_id=branch_id,
            target_nodes=tuple(branch.target_nodes),
            summary_line=summary,
        )
        self._negative_lessons.append(lesson)
        return lesson

    def get_verified_branch(self) -> Optional[HypothesisBranch]:
        for b in self._branches.values():
            if b.status == "VERIFIED":
                return b
        return None

    def get_negative_lessons(self) -> List[NegativeCausalLesson]:
        return list(self._negative_lessons)

    @staticmethod
    def _compute_similarity(b1: HypothesisBranch, b2: HypothesisBranch) -> float:
        tokens1: Set[str] = set(t.lower() for t in _TOKEN_RE.findall(b1.description)) | set(b1.target_nodes)
        tokens2: Set[str] = set(t.lower() for t in _TOKEN_RE.findall(b2.description)) | set(b2.target_nodes)
        if not tokens1 and not tokens2:
            return 1.0
        union = tokens1 | tokens2
        if not union:
            return 0.0
        return len(tokens1 & tokens2) / len(union)

    @staticmethod
    def _extract_single_line_reason(raw_trace: str) -> str:
        if not raw_trace:
            return "Falsification probe failed"
        lines = [ln.strip() for ln in raw_trace.splitlines() if ln.strip()]
        for ln in reversed(lines):
            if any(k in ln for k in ("Error", "Exception", "FAILED", "assert", "panic")):
                return ln[:100]
        return lines[-1][:100] if lines else "Probe failed"
