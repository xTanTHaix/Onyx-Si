"""
Street Tests: Phase 1 Kernel Sandbox & Supervisor Chaos.
Symlink cycles, process crash storms, config spamming debounce.
"""
import os
import sys
import time
import tempfile
from onyxsi.gateway.policy_gate import (
    PolicyGate,
    PathTraversalBlockedError,
    SymlinkDepthExceededError,
)
from onyxsi.hal.process_manager import ProcessManager, ProcessConfig


def test_street_symlink_cycle_torture():
    with tempfile.TemporaryDirectory() as tmpdir:
        gate = PolicyGate(workspace_root=tmpdir)
        link_a = os.path.join(tmpdir, "link_a")
        link_b = os.path.join(tmpdir, "link_b")

        # Create cycle link_a -> link_b -> link_a
        try:
            os.symlink(link_b, link_a)
            os.symlink(link_a, link_b)
            
            try:
                gate.resolve_path(link_a)
                assert False, "Should catch circular symlink"
            except PathTraversalBlockedError:
                pass
        except OSError:
            # On platforms where symlink requires privileges, skip gracefully
            pass


def test_street_rapid_worker_spawn_and_kill():
    mgr = ProcessManager()
    cfg = ProcessConfig(
        command=sys.executable,
        args=["-c", "import time; time.sleep(5.0)"],
    )
    pids = []
    try:
        for _ in range(10):
            pid = mgr.spawn(cfg)
            pids.append(pid)

        for pid in pids:
            mgr.stop(pid, force=True)
            st = mgr.get_status(pid)
            assert st.status == "stopped"
    finally:
        for pid in pids:
            try:
                mgr.stop(pid, force=True)
            except Exception:
                pass
