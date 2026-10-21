"""
Codebase Antibody Memory (.onyxsi/wiki/projects/<project>/ANTIBODIES.json).
Transforms historical bug patterns and negative causal lessons into persistent
AST/token antibodies that block repeat-offender mutations in <0.05ms.

The memory can also *manufacture* its own antibodies: ``synthesize_from_ast_diff``
compares the code before a fix with the code after it, distils the defective
pattern that was removed, and stores it so the same mistake cannot be
re-introduced by a later mutation.
"""

import ast
import difflib
import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from onyxsi.contracts import sanitize_path_segment

# A synthesised pattern has to be specific enough not to block unrelated code,
# and general enough to survive a reformat. Lines outside this window are not
# worth turning into a rule.
MIN_ANTIBODY_PATTERN_LEN = 12
MAX_ANTIBODY_PATTERN_LEN = 160

# Lines that carry no behavioural signal: turning them into antibodies would
# block every file that merely contains them.
_TRIVIAL_LINES = frozenset(
    {
        "pass", "return", "break", "continue", "raise", "yield", "...",
        "else", "try", "finally", "except", "lambda", "not", "and", "or",
        "if", "for", "while", "with", "import", "from", "global", "nonlocal",
    }
)


class AntibodyRejectionError(Exception):
    def __init__(self, antibody: "AntibodyRecord"):
        self.antibody = antibody
        super().__init__(
            f"ANTIBODY_REJECTION [{antibody.antibody_id}]: Forbidden pattern '{antibody.forbidden_pattern}' "
            f"detected ({antibody.root_cause_summary}). Prescribed cure: {antibody.prescribed_alternative}"
        )


@dataclass(slots=True, frozen=True)
class AntibodyRecord:
    """A persistent structural bug antibody."""

    antibody_id: str
    target_scope_glob: str
    forbidden_pattern: str
    root_cause_summary: str
    prescribed_alternative: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "antibody_id": self.antibody_id,
            "target_scope_glob": self.target_scope_glob,
            "forbidden_pattern": self.forbidden_pattern,
            "root_cause_summary": self.root_cause_summary,
            "prescribed_alternative": self.prescribed_alternative,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "AntibodyRecord":
        return cls(
            antibody_id=data["antibody_id"],
            target_scope_glob=data.get("target_scope_glob", "*"),
            forbidden_pattern=data["forbidden_pattern"],
            root_cause_summary=data.get("root_cause_summary", ""),
            prescribed_alternative=data.get("prescribed_alternative", ""),
        )


def _is_significant(line: str) -> bool:
    """True when a source line carries enough signal to become a rule."""
    stripped = line.strip()
    if len(stripped) < MIN_ANTIBODY_PATTERN_LEN:
        return False
    if len(stripped) > MAX_ANTIBODY_PATTERN_LEN:
        return False
    if stripped.startswith("#"):
        return False
    if stripped.lower() in _TRIVIAL_LINES:
        return False
    if not any(ch.isalnum() for ch in stripped):
        return False
    if stripped[0] in ")]}":
        return False
    return True


def _pattern_score(stripped: str) -> float:
    """
    Ranks a candidate pattern by how likely it is to be the real defect.

    A call site, an assignment, or a comparison is where bugs live; a longer
    line is more specific, so fewer unrelated edits trip the rule.
    """
    score = min(len(stripped), 100) / 40.0
    if "(" in stripped:
        score += 2.0
    if "=" in stripped:
        score += 1.0
    if any(op in stripped for op in ("==", "!=", " is ", " is not", "<=", ">=")):
        score += 1.0
    if "." in stripped:
        score += 0.5
    return score


class AntibodyMemory:
    """
    High-speed codebase immune system backed by
    ``.onyxsi/wiki/projects/<project>/ANTIBODIES.json``.

    Screens candidate mutations against known toxic patterns in sub-millisecond
    time. When a project is namespaced, a pre-namespacing store at
    ``.onyxsi/wiki/ANTIBODIES.json`` is still loaded so an existing immune
    system is never silently dropped; new records always land in the
    namespaced file.
    """

    def __init__(
        self,
        storage_path: Optional[str] = None,
        workspace_root: Optional[str] = None,
        project_name: Optional[str] = None,
    ):
        self.storage_path: Optional[str] = storage_path
        self.legacy_storage_path: Optional[str] = None
        self.project_name: Optional[str] = None
        self._antibodies: Dict[str, AntibodyRecord] = {}
        self._wildcard_records: List[AntibodyRecord] = []

        if workspace_root:
            root = os.path.abspath(workspace_root)
            self.legacy_storage_path = os.path.join(root, ".onyxsi", "wiki", "ANTIBODIES.json")
            if self.storage_path is None:
                self.project_name = sanitize_path_segment(
                    project_name or os.path.basename(root)
                )
                self.storage_path = os.path.join(
                    root,
                    ".onyxsi",
                    "wiki",
                    "projects",
                    self.project_name,
                    "ANTIBODIES.json",
                )

        if self.storage_path and os.path.exists(self.storage_path):
            self.load()
        elif self.legacy_storage_path and os.path.exists(self.legacy_storage_path):
            self.load(path=self.legacy_storage_path)

    # ------------------------------------------------------------------ core
    def register_antibody(self, record: AntibodyRecord, persist: bool = True) -> None:
        self._antibodies[record.antibody_id] = record
        self._rebuild_fast_list()
        if persist and self.storage_path:
            self.save()

    def check_mutation(self, file_path: str, candidate_code: str) -> None:
        """
        Screens candidate_code against all active antibodies matching file_path.
        Raises AntibodyRejectionError immediately on match.
        """
        basename = os.path.basename(file_path)
        for rec in self._wildcard_records:
            if rec.forbidden_pattern in candidate_code:
                if (
                    rec.target_scope_glob == "*"
                    or fnmatch.fnmatch(file_path, rec.target_scope_glob)
                    or fnmatch.fnmatch(basename, rec.target_scope_glob)
                ):
                    raise AntibodyRejectionError(rec)

    def save(self) -> None:
        if not self.storage_path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.storage_path)), exist_ok=True)
        tmp_path = self.storage_path + ".tmp"
        payload = [r.to_dict() for r in self._antibodies.values()]
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, self.storage_path)

    def load(self, path: Optional[str] = None) -> int:
        target = path or self.storage_path
        if not target or not os.path.exists(target):
            return 0
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._antibodies.clear()
        for item in data:
            rec = AntibodyRecord.from_dict(item)
            self._antibodies[rec.antibody_id] = rec
        self._rebuild_fast_list()
        return len(self._antibodies)

    def __len__(self) -> int:
        return len(self._antibodies)

    def count(self) -> int:
        return len(self._antibodies)

    def _rebuild_fast_list(self) -> None:
        self._wildcard_records = list(self._antibodies.values())

    # ------------------------------------------------------------ auto-vaccine
    def synthesize_from_ast_diff(
        self,
        baseline_code: str,
        fixed_code: str,
        file_path: str,
        context_hint: str = "Bug fix commit",
        target_scope_glob: Optional[str] = None,
    ) -> Optional[AntibodyRecord]:
        """
        Distils an antibody out of a before/after code pair and stores it.

        The baseline is the defective code, the fixed code is the repair. The
        diff between them exposes the lines that were removed; the most
        defect-like of those becomes the forbidden pattern, and the line that
        replaced it becomes the prescribed cure.

        Returns the stored record, or ``None`` when the pair carries no usable
        signal (identical code, unparsable code, or a diff that touches only
        trivial lines). Nothing is written in that case — a rule that cannot
        be justified is worse than no rule.
        """
        if not baseline_code or not fixed_code or baseline_code == fixed_code:
            return None

        try:
            ast.parse(baseline_code)
            ast.parse(fixed_code)
        except (SyntaxError, ValueError):
            return None

        baseline_lines = baseline_code.splitlines()
        fixed_lines = fixed_code.splitlines()

        matcher = difflib.SequenceMatcher(None, baseline_lines, fixed_lines, autojunk=False)

        candidates: List[Tuple[float, int, str, str]] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            added = [fixed_lines[j] for j in range(j1, j2)]
            for offset, index in enumerate(range(i1, i2)):
                line = baseline_lines[index]
                if not _is_significant(line):
                    continue
                stripped = line.strip()
                alternative = ""
                if offset < len(added) and _is_significant(added[offset]):
                    alternative = added[offset].strip()
                candidates.append((_pattern_score(stripped), index, stripped, alternative))

        if not candidates:
            return None

        # Highest score wins; the earliest line breaks ties deterministically.
        candidates.sort(key=lambda c: (-c[0], c[1]))
        _score, _index, forbidden, alternative = candidates[0]

        scope = target_scope_glob or os.path.basename(file_path) or "*"
        digest = hashlib.sha256(
            f"{os.path.abspath(file_path)}\x00{forbidden}".encode("utf-8")
        ).hexdigest()[:10]
        antibody_id = f"AB-{digest}"

        record = AntibodyRecord(
            antibody_id=antibody_id,
            target_scope_glob=scope,
            forbidden_pattern=forbidden,
            root_cause_summary=(
                f"{context_hint}: the defective pattern '{forbidden}' was removed by the fix"
            ),
            prescribed_alternative=alternative
            or "Reuse the corrected implementation instead of re-introducing the removed pattern.",
        )

        self.register_antibody(record, persist=True)
        return record
