"""
Normal Tests: Phase 0.4 Compact Radix Array Symbol Index.
"""
from onyxsi.hal.trie_index import CompactRadixSymbolIndex


def test_trie_exact_lookup():
    idx = CompactRadixSymbolIndex()
    idx.add_symbol("parse_ast", coordinate=1024, kind=CompactRadixSymbolIndex.KIND_FUNCTION)
    idx.add_symbol("parse_json", coordinate=2048, kind=CompactRadixSymbolIndex.KIND_FUNCTION)
    idx.add_symbol("execute", coordinate=4096, kind=CompactRadixSymbolIndex.KIND_FUNCTION)

    res1 = idx.lookup("parse_ast")
    assert res1.found is True
    assert res1.entry is not None
    assert res1.entry.name == "parse_ast"
    assert res1.entry.coordinate == 1024

    res_miss = idx.lookup("unknown_symbol")
    assert res_miss.found is False


def test_trie_prefix_search():
    idx = CompactRadixSymbolIndex()
    idx.add_symbol("onyx_init", 10)
    idx.add_symbol("onyx_close", 20)
    idx.add_symbol("onyx_flush", 30)
    idx.add_symbol("other_func", 40)

    res = idx.prefix_search("onyx_")
    assert res.found is True
    names = [e.name for e in res.matches]
    assert "onyx_init" in names
    assert "onyx_close" in names
    assert "onyx_flush" in names
    assert "other_func" not in names
