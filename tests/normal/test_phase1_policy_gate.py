"""
Normal Tests: Phase 1.1 Policy Gate, Path Resolution & Secret Masker.
"""
import os
import tempfile
from onyxsi.gateway.policy_gate import (
    PolicyGate,
    PolicyGateConfig,
    PathTraversalBlockedError,
    SymlinkDepthExceededError,
)


def test_canonical_path_resolution_within_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        gate = PolicyGate(workspace_root=tmpdir)
        sub_file = os.path.join(tmpdir, "src", "main.py")
        os.makedirs(os.path.dirname(sub_file), exist_ok=True)
        with open(sub_file, "w") as f:
            f.write("# code")

        ok, resolved = gate.resolve_path("src/main.py")
        assert ok is True
        assert resolved == os.path.realpath(sub_file)


def test_path_traversal_blocked():
    with tempfile.TemporaryDirectory() as tmpdir:
        gate = PolicyGate(workspace_root=tmpdir)
        try:
            gate.resolve_path("../../etc/passwd")
            assert False, "Should block traversal outside workspace"
        except PathTraversalBlockedError:
            pass


def test_secret_masker():
    gate = PolicyGate()
    raw = "User secret: sk-1234567890abcdef1234567890 and ghp_123456789012345678901234567890123456"
    masked = gate.mask_secrets(raw)
    assert "sk-1234567890abcdef1234567890" not in masked
    assert "ghp_123456789012345678901234567890123456" not in masked
    assert "[REDACTED_SECRET]" in masked
