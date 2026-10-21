"""
Normal Tests: Phase 1.2 Dual-OS Filesystem & Network Enclave.
"""
import os
import tempfile
from onyxsi.hal.sandbox_enclave import SandboxEnclave, SandboxConfig


def test_sandbox_activation():
    with tempfile.TemporaryDirectory() as tmpdir:
        enclave = SandboxEnclave(SandboxConfig(workspace_root=tmpdir))
        assert enclave.activate() is True
        assert enclave.is_active() is True


def test_sandbox_path_access_containment():
    with tempfile.TemporaryDirectory() as tmpdir:
        enclave = SandboxEnclave(SandboxConfig(workspace_root=tmpdir))
        inside_path = os.path.join(tmpdir, "data.txt")
        outside_path = os.path.abspath("/etc/hosts")

        assert enclave.check_path_access(inside_path) is True
        assert enclave.check_path_access(outside_path) is False
