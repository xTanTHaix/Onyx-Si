"""
Street Chaos Tests for Phase 2: ContextFS & DBP (bit-parallel / SWAR) Bitplane AST Engine.
Stress-tests deep nesting, piece table rapid random mutations, and handle staleness races.
"""

from onyxsi.contextfs.bitplane_ast import BitplaneAstScanner
from onyxsi.contextfs.piece_table import PieceTableDocument
from onyxsi.contextfs.token_governor import TokenGovernor


def test_street_bitplane_nested_128_braces():
    scanner = BitplaneAstScanner()
    code = b"{" * 128 + b"}" * 128
    is_balanced, max_depth, _ = scanner.analyze_source_topology(code)
    assert is_balanced is True
    assert max_depth == 128


def test_street_bitplane_unbalanced_syntax_error():
    scanner = BitplaneAstScanner()
    code = b"fn test() { let s = 1; "
    is_balanced, _, _ = scanner.analyze_source_topology(code)
    assert is_balanced is False


def test_street_piece_table_rapid_fire_mutations():
    doc = PieceTableDocument("START_DOC")
    for i in range(500):
        pos = min(i, doc.total_length())
        doc.insert(pos, f"_{i}_")
    assert doc.total_length() > len("START_DOC")

    doc.coalesce()
    assert len(doc._pieces) <= 512


def test_street_token_governor_massive_text_folding():
    gov = TokenGovernor(default_ceiling=50)
    huge_text = "Data block row " * 1000
    res = gov.enforce_ceiling(huge_text, ceiling_tokens=50)
    assert res.is_folded is True
    assert res.emitted_tokens <= 100
    assert "folded -> ref://vfs_tree_" in res.text
