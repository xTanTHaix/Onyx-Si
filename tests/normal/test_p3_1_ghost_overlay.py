"""
Normal Tests for Phase 3.1: Tier-0 In-RAM Ghost-CoW Overlay.
"""

import os
import tempfile
from onyxsi.deltafs.ghost_overlay import (
    GhostOverlayFS,
    GhostSyntaxError,
)


def test_ghost_overlay_speculative_edits_and_discard():
    with tempfile.TemporaryDirectory() as tmpdir:
        real_file = os.path.join(tmpdir, "config.py")
        with open(real_file, "w") as f:
            f.write("DEBUG = False\n")

        ghost = GhostOverlayFS(workspace_root=tmpdir)
        ghost.ghost_begin()

        ghost.ghost_mutate(real_file, "DEBUG = True\nPORT = 8080\n")
        assert ghost.ghost_read_text(real_file) == "DEBUG = True\nPORT = 8080\n"

        with open(real_file, "r") as f:
            assert f.read() == "DEBUG = False\n"

        ghost.ghost_discard()
        assert ghost.is_active() is False

        with open(real_file, "r") as f:
            assert f.read() == "DEBUG = False\n"


def test_ghost_overlay_syntax_validation_gate():
    with tempfile.TemporaryDirectory() as tmpdir:
        real_file = os.path.join(tmpdir, "service.py")
        with open(real_file, "w") as f:
            f.write("def run(): pass\n")

        ghost = GhostOverlayFS(workspace_root=tmpdir)
        ghost.ghost_begin()
        ghost.ghost_mutate(real_file, "def broken_syntax( pass\n")

        try:
            ghost.ghost_commit(validate_syntax=True)
            raise AssertionError("Expected GhostSyntaxError")
        except GhostSyntaxError:
            pass

        assert ghost.is_active() is False
        with open(real_file, "r") as f:
            assert f.read() == "def run(): pass\n"


def test_ghost_overlay_clean_commit():
    with tempfile.TemporaryDirectory() as tmpdir:
        real_file = os.path.join(tmpdir, "valid.py")
        with open(real_file, "w") as f:
            f.write("def ok(): return 1\n")

        ghost = GhostOverlayFS(workspace_root=tmpdir)
        ghost.ghost_begin()

        ghost.ghost_mutate(real_file, "def ok(): return 42\n")
        res = ghost.ghost_commit(validate_syntax=True)

        assert len(res) == 1
        with open(real_file, "r") as f:
            assert f.read() == "def ok(): return 42\n"
