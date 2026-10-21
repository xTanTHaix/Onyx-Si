"""
Process Manager & Worker Lifecycle.
Spawns, monitors, hot-reloads, and reaps worker processes.
"""

import os
import subprocess
import time
import hashlib
import threading
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass, field


class ProcessManagerError(Exception):
    pass


@dataclass(slots=True, frozen=True)
class ProcessConfig:
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    working_dir: Optional[str] = None
    timeout_sec: float = 30.0
    restart_on_failure: bool = True
    max_restarts: int = 3
    restart_delay_sec: float = 1.0


@dataclass(slots=True)
class ProcessStatus:
    pid: int
    status: str  # "running", "stopped", "error"
    start_time: float
    exit_code: Optional[int] = None
    last_config_hash: Optional[str] = None


@dataclass(slots=True)
class ProcessRunResult:
    """Captured result of a short-lived child process (test runner, linters)."""

    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: float


class ProcessManager:
    def __init__(self, config_path: Optional[str] = None, debounce_ms: int = 100):
        self.config_path = config_path
        self.debounce_ms = debounce_ms
        self._workers: Dict[int, ProcessStatus] = {}
        self._procs: Dict[int, subprocess.Popen] = {}
        # RLock, not Lock: health_check_all() re-enters get_status().
        self._lock = threading.RLock()
        self._last_reload_time = 0.0
        self._last_config_hash = self._get_config_hash()

    def _get_config_hash(self) -> str:
        if not self.config_path or not os.path.exists(self.config_path):
            return "0" * 16
        try:
            with open(self.config_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()[:16]
        except OSError:
            return "0" * 16

    def check_and_reload_config(self) -> bool:
        now = time.time()
        if (now - self._last_reload_time) * 1000.0 < self.debounce_ms:
            return False

        new_hash = self._get_config_hash()
        if new_hash != self._last_config_hash:
            self._last_config_hash = new_hash
            self._last_reload_time = now
            return True
        return False

    def reload_config(self) -> bool:
        self.check_and_reload_config()
        return True

    def spawn(self, config: ProcessConfig) -> int:
        cmd = [config.command] + config.args
        env = os.environ.copy()
        env.update(config.env)
        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=config.working_dir or os.getcwd(),
                # Never hand the child a pipe we do not drain: an unread PIPE
                # deadlocks the worker once the 64KB buffer fills and leaks fds.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            pid = proc.pid
            with self._lock:
                self._procs[pid] = proc
                self._workers[pid] = ProcessStatus(
                    pid=pid,
                    status="running",
                    start_time=time.time(),
                )
            return pid
        except (OSError, subprocess.SubprocessError) as e:
            raise ProcessManagerError(f"Worker spawn failed: {e}")

    @staticmethod
    def _kill_process_tree(proc: subprocess.Popen) -> None:
        """
        Robustly terminates a process and any descendant processes.

        On Windows (`nt`), `proc.kill()` (calling Win32 `TerminateProcess`) only terminates
        the immediate parent PID. Any spawned child processes (compilers, test runners, linters)
        remain running as orphans, holding file locks on workspace shadow directories.
        `taskkill /F /T /PID <pid>` walks the process tree and terminates all descendants.
        """
        if proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            proc.kill()
        except (OSError, subprocess.SubprocessError):
            pass

    def run_capture(
        self,
        config: ProcessConfig,
        preexec_fn: Optional[Callable[[], None]] = None,
    ) -> ProcessRunResult:
        """
        Runs a short-lived child to completion and captures its output.

        Unlike :meth:`spawn` (long-running workers whose output is discarded),
        this drains stdout/stderr and enforces ``config.timeout_sec``. A timed-out
        child is killed (including its entire process tree on Windows) and reported
        with ``timed_out=True`` rather than raising.
        """
        cmd = [config.command] + list(config.args)
        env = os.environ.copy()
        env.update(config.env)
        started = time.perf_counter()

        kwargs = {
            "env": env,
            "cwd": config.working_dir or os.getcwd(),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if preexec_fn is not None and os.name != "nt":
            kwargs["preexec_fn"] = preexec_fn

        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProcessManagerError(f"Worker spawn failed: {exc}")

        timed_out = False
        try:
            out, err = proc.communicate(timeout=config.timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_process_tree(proc)
            out, err = proc.communicate()

        duration_ms = (time.perf_counter() - started) * 1000.0
        return ProcessRunResult(
            exit_code=proc.returncode,
            stdout=out or "",
            stderr=err or "",
            timed_out=timed_out,
            duration_ms=duration_ms,
        )

    def get_status(self, pid: int) -> Optional[ProcessStatus]:
        with self._lock:
            if pid not in self._workers:
                return None
            proc = self._procs.get(pid)
            if proc is not None:
                ret = proc.poll()
                if ret is not None:
                    self._workers[pid].status = "stopped"
                    self._workers[pid].exit_code = ret
            return self._workers[pid]

    def stop(self, pid: int, force: bool = False) -> bool:
        with self._lock:
            if pid not in self._workers:
                return False
            proc = self._procs.get(pid)
            if proc is not None:
                try:
                    if force:
                        self._kill_process_tree(proc)
                    else:
                        proc.terminate()
                        proc.wait(timeout=1.0)
                except (OSError, subprocess.SubprocessError):
                    self._kill_process_tree(proc)
                self._workers[pid].status = "stopped"
                self._workers[pid].exit_code = proc.poll()
            return True

    def health_check_all(self) -> List[bool]:
        with self._lock:
            res = []
            for pid in list(self._workers.keys()):
                st = self.get_status(pid)
                res.append(st.status == "running" if st else False)
            return res

    def cleanup(self) -> None:
        with self._lock:
            for pid in list(self._procs.keys()):
                proc = self._procs.get(pid)
                if proc is not None:
                    self._kill_process_tree(proc)
            self._workers.clear()
            self._procs.clear()
