"""
Normal Tests for Phase 3.4: Conflict-Free Multi-Agent AST Span & Import Merger.
"""

from onyxsi.deltafs.ast_conflict_merger import (
    ConflictFreeAstMerger,
    AgentPatch,
)


def test_conflict_free_ast_bottom_up_merge():
    base_code = """\"\"\"Service Module\"\"\"

def first_fn():
    return 1

def second_fn():
    return 2
"""
    # Find offsets for replacement
    idx1_start = base_code.index("    return 1")
    idx1_end = idx1_start + len("    return 1")

    idx2_start = base_code.index("    return 2")
    idx2_end = idx2_start + len("    return 2")

    patch_a = AgentPatch(
        agent_id="agent_alpha",
        node_id="ast_fn_first",
        start_byte=idx1_start,
        end_byte=idx1_end,
        new_content="    x = math.sqrt(100)\n    return int(x)",
        new_imports=["import math"],
    )

    patch_b = AgentPatch(
        agent_id="agent_beta",
        node_id="ast_fn_second",
        start_byte=idx2_start,
        end_byte=idx2_end,
        new_content="    return os.getpid()",
        new_imports=["import os"],
    )

    merged = ConflictFreeAstMerger.merge_patches(base_code, [patch_a, patch_b])

    assert "import math" in merged
    assert "import os" in merged
    assert "math.sqrt(100)" in merged
    assert "os.getpid()" in merged

    # Valid syntax check
    import ast
    tree = ast.parse(merged)
    assert tree is not None
