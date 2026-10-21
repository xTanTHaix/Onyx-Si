"""
Conflict-Free Multi-Agent AST Span & Import Merger.
Merges concurrent AST modifications from multiple sub-agents into a single document without git conflict markers.
Uses Bottom-Up Span Application to avoid coordinate shift distortion and performs deduplicated import unification.
"""

import ast
from dataclasses import dataclass, field
from typing import List
from onyxsi.contracts import OnyxErrorCode, OnyxError
from onyxsi.contextfs.piece_table import PieceTableDocument


class AstMergeConflictError(OnyxError):
    """Concurrent AST patches collide (OnyxErrorCode.E3004)."""

    def __init__(self, message: str, code: OnyxErrorCode = OnyxErrorCode.E3004, details=None):
        super().__init__(code, message, details)


@dataclass(slots=True, frozen=True)
class AgentPatch:
    """An isolated code modification targeted at a specific AST node."""
    agent_id: str
    node_id: str
    start_byte: int
    end_byte: int
    new_content: str
    new_imports: List[str] = field(default_factory=list)


class ConflictFreeAstMerger:
    """
    Automated zero-conflict patch merger for concurrent sub-agent swarms.
    Applies node updates bottom-up and aggregates module imports cleanly at file headers.
    """

    @classmethod
    def merge_patches(cls, base_code: str, patches: List[AgentPatch], language: str = "python") -> str:
        if not patches:
            return base_code

        # 1. Detect overlapping collision spans
        sorted_by_start = sorted(patches, key=lambda p: p.start_byte)
        for i in range(len(sorted_by_start) - 1):
            p1 = sorted_by_start[i]
            p2 = sorted_by_start[i + 1]
            zero_width_collision = (
                p1.start_byte == p1.end_byte
                and p2.start_byte == p2.end_byte
                and p1.start_byte == p2.start_byte
            )
            if p1.end_byte > p2.start_byte or zero_width_collision:
                raise AstMergeConflictError(
                    f"Overlapping AST patch collision between agent '{p1.agent_id}' ({p1.node_id}) "
                    f"and agent '{p2.agent_id}' ({p2.node_id}) across bytes {p2.start_byte}..{p1.end_byte}"
                )

        # 2. Apply modifications Bottom-Up on PieceTableDocument
        doc = PieceTableDocument(base_code)
        bottom_up_patches = sorted(patches, key=lambda p: p.start_byte, reverse=True)

        for patch in bottom_up_patches:
            span_len = patch.end_byte - patch.start_byte
            doc.replace(patch.start_byte, span_len, patch.new_content)

        merged_text = doc.get_text()

        # 3. Unify imports
        all_new_imports: List[str] = []
        for patch in patches:
            for imp in patch.new_imports:
                clean_imp = imp.strip()
                if clean_imp and clean_imp not in all_new_imports:
                    all_new_imports.append(clean_imp)

        if all_new_imports:
            merged_text = cls._inject_imports(merged_text, all_new_imports, language)

        # 4. Final AST Syntax Validation
        if language == "python":
            try:
                ast.parse(merged_text)
            except SyntaxError as e:
                raise OnyxError(OnyxErrorCode.E3004, f"Merged code failed AST syntax validation: {e}")

        return merged_text

    @staticmethod
    def _inject_imports(code: str, new_imports: List[str], language: str) -> str:
        lines = code.splitlines(keepends=True)
        existing_lines = {l.strip() for l in lines}
        to_inject = [imp for imp in new_imports if imp not in existing_lines]

        if not to_inject:
            return code

        # Find optimal insertion index: after module docstring if any
        insert_idx = 0
        in_docstring = False
        quote_char = ""

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not in_docstring:
                if stripped.startswith(('"""', "'''")):
                    quote_char = stripped[:3]
                    if stripped.count(quote_char) >= 2 and len(stripped) > 3:
                        # Single-line docstring
                        insert_idx = idx + 1
                        break
                    in_docstring = True
                elif stripped.startswith(("#", "//", "/*")):
                    continue
                elif stripped.startswith(("import ", "from ")):
                    insert_idx = idx
                    break
                elif stripped:
                    insert_idx = idx
                    break
            else:
                if quote_char in stripped:
                    in_docstring = False
                    insert_idx = idx + 1
                    break

        import_block = "\n".join(to_inject) + "\n"
        if insert_idx < len(lines):
            lines.insert(insert_idx, import_block)
        else:
            lines.append(import_block)

        return "".join(lines)
