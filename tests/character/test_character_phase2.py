"""
Character Scenario Tests for Phase 2: CT-09 through CT-12.
"""

from onyxsi.contextfs.bitplane_ast import BitplaneAstScanner
from onyxsi.contextfs.piece_table import PieceTableDocument
from onyxsi.contextfs.token_governor import TokenGovernor
from onyxsi.contextfs.virtual_handles import VirtualHandleRegistry, StaleHandleError


class TestCT09StringBracePolyglotNightmare:
    """CT-09: Agent sending crazy fake braces inside string literals and comments."""

    def test_string_brace_nightmare(self):
        scanner = BitplaneAstScanner()
        nightmare = rb'''
        fn complex_service() {
            let fake1 = "{{{{ }}} \" {";
            let fake2 = "/* } */ }";
            // let fake3 = { }}} ;
            let real = 42;
        }
        '''
        is_balanced, max_depth, top_level = scanner.analyze_source_topology(nightmare)
        assert is_balanced is True
        assert top_level == 1
        assert max_depth >= 1


class TestCT10OneBytePatchManiac:
    """CT-10: Agent attempting 1,000 1-byte edits to shatter the piece table."""

    def test_one_byte_patch_coalescing(self):
        doc = PieceTableDocument("BASE_CODE")
        for _ in range(1000):
            doc.insert(doc.total_length(), "x")
        doc.coalesce()
        assert len(doc._pieces) <= 512
        assert doc.get_text() == "BASE_CODE" + ("x" * 1000)


class TestCT11ContextWindowExploder:
    """CT-11: Agent dumping massive text to blow context ceilings."""

    def test_token_ceiling_enforcement(self):
        gov = TokenGovernor(default_ceiling=1250)
        exploding_text = "fn generated_stub() { return None; }\n" * 500
        result = gov.enforce_ceiling(exploding_text, ceiling_tokens=1250)
        assert result.is_folded is True
        assert result.emitted_tokens <= 1250
        assert "folded -> ref://vfs_tree_" in result.text


class TestCT12StaleHandleNecromancer:
    """CT-12: Agent resurrecting stale or expired virtual handles."""

    def test_stale_handle_rejection(self):
        registry = VirtualHandleRegistry(default_ttl_sec=3600.0)
        original_code = b"def handler(): return 200\n"
        handle = registry.register_code_handle("handler.py", "fn_handler", "return 200", original_code)

        mutated_code = b"def handler(): return 500\n"

        try:
            registry.resolve(handle.handle_uri, current_source_bytes=mutated_code)
            raise AssertionError("Expected StaleHandleError")
        except StaleHandleError:
            pass
