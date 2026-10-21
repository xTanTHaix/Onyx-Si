"""
Normal Tests for Phase 2.3: VFS Directory Scanner & Token Ceiling Governor.
"""

import tempfile
import os
from onyxsi.contextfs.vfs_scanner import VfsScanner
from onyxsi.contextfs.token_governor import TokenGovernor


def test_vfs_scanner_skips_junk_directories():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create real project structure
        src = os.path.join(tmpdir, "src")
        git_dir = os.path.join(tmpdir, ".git")
        node_mod = os.path.join(tmpdir, "node_modules")
        os.makedirs(src, exist_ok=True)
        os.makedirs(git_dir, exist_ok=True)
        os.makedirs(node_mod, exist_ok=True)

        with open(os.path.join(src, "main.py"), "w") as f:
            f.write("print('hello')")
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write("git config")
        with open(os.path.join(node_mod, "pkg.json"), "w") as f:
            f.write("{}")

        scanner = VfsScanner()
        summary = scanner.scan(tmpdir)

        assert summary.total_files == 1  # Only src/main.py
        assert ".git" not in summary.tree_text
        assert "node_modules" not in summary.tree_text
        assert "main.py" in summary.tree_text


def test_token_governor_under_ceiling():
    gov = TokenGovernor(default_ceiling=100)
    short_text = "This is a brief summary of the project."
    res = gov.enforce_ceiling(short_text, ceiling_tokens=50)
    assert res.is_folded is False
    assert res.text == short_text
    assert res.folded_ref is None


def test_token_governor_over_ceiling_folds_gracefully():
    gov = TokenGovernor(default_ceiling=20)  # ~80 characters
    long_text = "Line " * 200  # 1000 characters
    res = gov.enforce_ceiling(long_text, ceiling_tokens=20, context_hint="test_tree")
    assert res.is_folded is True
    assert "folded -> ref://test_tree_" in res.text
    assert res.folded_ref is not None
    assert res.emitted_tokens <= res.original_tokens


def test_estimate_tokens_keeps_ascii_rule():
    # 4 ASCII chars per token, unchanged for English/code.
    assert TokenGovernor.estimate_tokens("") == 0
    assert TokenGovernor.estimate_tokens("abcd") == 1
    assert TokenGovernor.estimate_tokens("a" * 400) == 100


def test_estimate_tokens_counts_non_ascii_per_character():
    # Thai/CJK are routinely ~1 token per character; the old len//4 rule
    # under-counted them by roughly 4x.
    thai = "สวัสดีครับ"
    assert TokenGovernor.estimate_tokens(thai) == len(thai)

    mixed = "def f():\n    return 'สวัสดี'\n"
    ascii_chars = len(mixed.encode("ascii", "ignore"))
    assert TokenGovernor.estimate_tokens(mixed) == ascii_chars // 4 + (len(mixed) - ascii_chars)


def test_thai_payload_folds_at_the_same_ceiling_as_english():
    gov = TokenGovernor(default_ceiling=50)
    thai_text = "บรรทัดทดสอบ " * 40
    res = gov.enforce_ceiling(thai_text, ceiling_tokens=50, context_hint="thai_tree")
    assert res.is_folded is True
    assert res.emitted_tokens <= 50
