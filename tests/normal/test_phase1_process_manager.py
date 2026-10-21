"""
Normal Tests: Phase 1.4 Headless Process Supervisor & Wakeup Signaler.
"""
import sys
import time
import tempfile
from onyxsi.hal.process_manager import ProcessManager, ProcessConfig
from onyxsi.hal.wakeup import WakeupSignal


def test_wakeup_signaling():
    sig = WakeupSignal().create()
    try:
        assert sig.signal() is True
        assert sig.wait(timeout_ms=500) is True
        # After auto-reset, subsequent wait should timeout
        assert sig.wait(timeout_ms=50) is False
    finally:
        sig.cleanup()


def test_process_spawn_monitor_and_stop():
    mgr = ProcessManager()
    cfg = ProcessConfig(
        command=sys.executable,
        args=["-c", "import time; time.sleep(1.0)"],
        timeout_sec=5.0,
    )
    pid = mgr.spawn(cfg)
    assert pid > 0
    st = mgr.get_status(pid)
    assert st.status == "running"

    mgr.stop(pid, force=True)
    st_after = mgr.get_status(pid)
    assert st_after.status == "stopped"


def test_config_hot_reload_debounce():
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write('{"v": 1}')
        cfg_path = f.name

    try:
        mgr = ProcessManager(config_path=cfg_path, debounce_ms=100)
        # Immediate check should be false since no change
        assert mgr.check_and_reload_config() is False

        # Modify config
        with open(cfg_path, "w") as f:
            f.write('{"v": 2}')

        # Wait past debounce window
        time.sleep(0.15)
        assert mgr.check_and_reload_config() is True
    finally:
        import os
        os.unlink(cfg_path)
