"""
OnyxSi Phase 6.1: Dedicated Folder-per-Task Wiki & Seamless Multi-Agent Handoff.
Manages .onyxsi/wiki/projects/<project>/tasks/TASK-<ID>/ with atomic file
operations (os.replace) and generates comprehensive AGENT_HANDOFF.md for
zero-second handoff.

Task state is namespaced per project so that a monorepo — or a machine that
switches between workspaces — never mixes two projects' task IDs, progress
state, or antibody rules. A legacy lookup fallback keeps pre-namespacing
task folders reachable, and ``migrate_legacy_tasks()`` moves them across
deliberately rather than silently.

100% Python Standard Library only. Zero External Dependencies. Zero UI.
"""

import os
import re
import shutil
import json
import time
import tempfile
import threading
from typing import Any, Dict, List, Optional

from onyxsi.causal.micro_wal import render_time_travel_chronicle
from onyxsi.contracts import sanitize_path_segment

_SAFE_TASK_ID = re.compile(r"[A-Z0-9_.-]{1,64}")

MAX_PROJECT_NAME_LEN = 128
DEFAULT_PROJECT_NAME = "default"
LEGACY_TASKS_DIRNAME = "tasks"


class TaskWikiError(Exception):
    """Base exception for task wiki operations."""

    pass


class TaskNotFoundError(TaskWikiError):
    """Raised when the specified task ID cannot be found."""

    pass


class TaskCorruptedError(TaskWikiError):
    """Raised when task metadata or spec file is corrupted."""

    pass


class TaskWikiManager:
    """
    Manages dedicated folder-per-task wiki structures under:
      <workspace>/.onyxsi/wiki/projects/<project>/tasks/TASK-<ID>/

    Structure per task:
      - TASK_SPEC.md: Immutable initial specification and acceptance criteria.
      - PROGRESS.json: Structured state machine, milestone tracker, and active leases.
      - PROGRESS.md: Human-readable markdown mirror of PROGRESS.json.
      - EXECUTION_LOG.md: Append-only chronological trace of agent actions and outcomes.
      - CRASH_REPRO.md: Micro-WAL crash replay log (if crash occurred).
      - AGENT_HANDOFF.md: Standardized handoff sheet for a seamless multi-agent relay.
      - TIME_TRAVEL_REPLAY.md: Decision tree + frame-by-frame time-travel chronicle.
    """

    def __init__(self, workspace_root: str, project_name: Optional[str] = None):
        self.workspace_root = os.path.abspath(workspace_root)
        self.project_name = self._sanitize_project_name(
            project_name or os.path.basename(self.workspace_root)
        )
        self.wiki_root = self.tasks_root()
        os.makedirs(self.wiki_root, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ layout
    @staticmethod
    def _sanitize_project_name(name: str) -> str:
        """
        Normalises a project name into a safe single path segment.

        Delegates to the shared contract helper so the task wiki and the
        antibody memory can never disagree about where a project lives.
        """
        return sanitize_path_segment(
            name,
            max_length=MAX_PROJECT_NAME_LEN,
            default=DEFAULT_PROJECT_NAME,
        )

    def project_root(self, project_name: Optional[str] = None) -> str:
        """Returns the namespaced root of a project inside the wiki."""
        project = (
            self.project_name
            if project_name is None
            else self._sanitize_project_name(project_name)
        )
        return os.path.join(self.workspace_root, ".onyxsi", "wiki", "projects", project)

    def tasks_root(self, project_name: Optional[str] = None) -> str:
        """Returns the task folder root of a project inside the wiki."""
        return os.path.join(self.project_root(project_name), LEGACY_TASKS_DIRNAME)

    def legacy_tasks_root(self) -> str:
        """Returns the pre-namespacing task root (``.onyxsi/wiki/tasks``)."""
        return os.path.join(self.workspace_root, ".onyxsi", "wiki", LEGACY_TASKS_DIRNAME)

    def _task_dir(self, task_id: str, project_name: Optional[str] = None) -> str:
        """
        Resolves a task id to its folder, rejecting anything that escapes the root.

        Accepts 'TASK-001', 'task-001' or '001'; normalises to 'TASK-001'.

        When the manager's own project is addressed and no namespaced folder
        exists yet, a pre-namespacing folder at ``.onyxsi/wiki/tasks/`` is
        returned instead. That keeps every task written before namespacing
        reachable without ever moving a user's data behind their back.
        """
        raw = str(task_id).strip().upper()
        if raw.startswith("TASK-"):
            raw = raw[5:]

        if not raw or not _SAFE_TASK_ID.fullmatch(raw) or raw in (".", ".."):
            raise TaskWikiError(
                f"Invalid task id {task_id!r}: expected 1-64 chars of [A-Z0-9_.-] after 'TASK-'"
            )

        tasks_root = os.path.realpath(self.tasks_root(project_name))
        candidate = os.path.realpath(os.path.join(tasks_root, f"TASK-{raw}"))
        if candidate != tasks_root and not candidate.startswith(tasks_root + os.sep):
            raise TaskWikiError(f"Task id {task_id!r} resolves outside the wiki root")

        if project_name is None and not os.path.isdir(candidate):
            legacy_root = os.path.realpath(self.legacy_tasks_root())
            legacy = os.path.realpath(os.path.join(legacy_root, f"TASK-{raw}"))
            if legacy != legacy_root and legacy.startswith(legacy_root + os.sep):
                if os.path.isdir(legacy):
                    return legacy

        return candidate

    # ------------------------------------------------------------------ writes
    @staticmethod
    def _atomic_write_text(target_path: str, content: str) -> None:
        """Writes content to a temporary file in the same directory and replaces atomically."""
        dir_name = os.path.dirname(target_path)
        os.makedirs(dir_name, exist_ok=True)

        # Write to temporary file in the same folder to guarantee same filesystem for atomic rename
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_atomic_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target_path)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

    @staticmethod
    def _atomic_write_json(target_path: str, data: Dict[str, Any]) -> None:
        serialized = json.dumps(data, indent=2, ensure_ascii=False)
        TaskWikiManager._atomic_write_text(target_path, serialized)

    # ------------------------------------------------------------------ tasks
    def init_task(
        self,
        task_id: str,
        spec_content: str,
        initial_plan: Optional[List[Dict[str, Any]]] = None,
        author: str = "OnyxSi-Kernel",
        project_name: Optional[str] = None,
    ) -> str:
        """
        Initializes a new task folder with TASK_SPEC.md, PROGRESS.json, and EXECUTION_LOG.md.
        Returns the absolute path to the task folder.
        """
        with self._lock:
            task_dir = self._task_dir(task_id, project_name)
            os.makedirs(task_dir, exist_ok=True)

            spec_path = os.path.join(task_dir, "TASK_SPEC.md")
            if not os.path.exists(spec_path):
                self._atomic_write_text(spec_path, spec_content.strip() + "\n")

            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            progress_data = {
                "task_id": os.path.basename(task_dir),
                "project_name": self.project_name,
                "created_at": now_iso,
                "updated_at": now_iso,
                "author": author,
                "status": "INITIALIZED",
                "active_milestone": None,
                "milestones": initial_plan or [],
                "ast_leases": [],
                "falsified_hypotheses": [],
                "negative_lessons": [],
                "modified_files": [],
            }

            progress_json_path = os.path.join(task_dir, "PROGRESS.json")
            self._atomic_write_json(progress_json_path, progress_data)

            self._sync_progress_markdown(task_dir, progress_data)

            log_path = os.path.join(task_dir, "EXECUTION_LOG.md")
            if not os.path.exists(log_path):
                initial_log = (
                    f"# Execution Log: {os.path.basename(task_dir)}\n\n"
                    f"- [{now_iso}] Task initialized by {author}.\n"
                )
                self._atomic_write_text(log_path, initial_log)

            return task_dir

    def get_progress(self, task_id: str, project_name: Optional[str] = None) -> Dict[str, Any]:
        """Returns the parsed PROGRESS.json data for the specified task."""
        with self._lock:
            task_dir = self._task_dir(task_id, project_name)
            progress_path = os.path.join(task_dir, "PROGRESS.json")
            if not os.path.exists(progress_path):
                raise TaskNotFoundError(f"Task {task_id} does not exist at {progress_path}")

            try:
                with open(progress_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                raise TaskCorruptedError(f"Failed to read PROGRESS.json for {task_id}: {e}")

    def update_progress(
        self,
        task_id: str,
        status: Optional[str] = None,
        active_milestone: Optional[str] = None,
        completed_milestone: Optional[str] = None,
        ast_leases: Optional[List[str]] = None,
        falsified_hypothesis: Optional[Dict[str, Any]] = None,
        negative_lesson: Optional[str] = None,
        modified_file: Optional[str] = None,
        note: Optional[str] = None,
        project_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Updates PROGRESS.json atomically and syncs PROGRESS.md and EXECUTION_LOG.md.
        """
        with self._lock:
            data = self.get_progress(task_id, project_name)
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            data["updated_at"] = now_iso

            if status is not None:
                data["status"] = status

            if active_milestone is not None:
                data["active_milestone"] = active_milestone

            if completed_milestone is not None:
                # Mark milestone as completed
                found = False
                for m in data.get("milestones", []):
                    if m.get("name") == completed_milestone or m.get("id") == completed_milestone:
                        m["status"] = "COMPLETED"
                        m["completed_at"] = now_iso
                        found = True
                        break
                if not found:
                    data.setdefault("milestones", []).append({
                        "name": completed_milestone,
                        "status": "COMPLETED",
                        "completed_at": now_iso,
                    })

            if ast_leases is not None:
                data["ast_leases"] = list(ast_leases)

            if falsified_hypothesis:
                data.setdefault("falsified_hypotheses", []).append(falsified_hypothesis)

            if negative_lesson:
                lessons = data.setdefault("negative_lessons", [])
                if negative_lesson not in lessons:
                    lessons.append(negative_lesson)

            if modified_file:
                files = data.setdefault("modified_files", [])
                if modified_file not in files:
                    files.append(modified_file)

            task_dir = self._task_dir(task_id, project_name)
            progress_json_path = os.path.join(task_dir, "PROGRESS.json")
            self._atomic_write_json(progress_json_path, data)
            self._sync_progress_markdown(task_dir, data)

            if note:
                self.append_execution_log(task_id, note, project_name)

            return data

    def append_execution_log(self, task_id: str, entry: str, project_name: Optional[str] = None) -> None:
        """Appends a timestamped line to EXECUTION_LOG.md."""
        with self._lock:
            task_dir = self._task_dir(task_id, project_name)
            log_path = os.path.join(task_dir, "EXECUTION_LOG.md")
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            line = f"- [{now_iso}] {entry.strip()}\n"
            if not os.path.exists(log_path):
                header = f"# Execution Log: {os.path.basename(task_dir)}\n\n"
                content = header + line
                self._atomic_write_text(log_path, content)
            else:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()

    def generate_handoff(
        self,
        task_id: str,
        next_actions: Optional[List[str]] = None,
        author: str = "OnyxSi-Agent-Handoff",
        project_name: Optional[str] = None,
    ) -> str:
        """
        Generates AGENT_HANDOFF.md inside the task folder.
        Contains all necessary context for a fresh sub-agent to resume work seamlessly.
        """
        with self._lock:
            task_dir = self._task_dir(task_id, project_name)
            data = self.get_progress(task_id, project_name)

            spec_path = os.path.join(task_dir, "TASK_SPEC.md")
            spec_snippet = "No specification provided."
            if os.path.exists(spec_path):
                try:
                    with open(spec_path, "r", encoding="utf-8") as f:
                        spec_snippet = f.read().strip()
                except OSError:
                    pass

            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            milestones = data.get("milestones", [])
            completed = [m.get("name", m.get("id", "Unknown")) for m in milestones if m.get("status") == "COMPLETED"]
            pending = [m.get("name", m.get("id", "Unknown")) for m in milestones if m.get("status") != "COMPLETED"]

            leases = data.get("ast_leases", [])
            negative_lessons = data.get("negative_lessons", [])
            falsified = data.get("falsified_hypotheses", [])
            modified_files = data.get("modified_files", [])

            actions_list = next_actions or [
                f"Resume from milestone: {data.get('active_milestone') or (pending[0] if pending else 'Verification')}",
                "Verify AST leases and acquire needed locks via ast_lease_registry",
                "Execute blast_test_runner on modified files to verify invariants",
            ]

            lines = [
                f"# Agent Handoff Protocol: {data.get('task_id', task_id)}",
                f"**Generated:** {now_iso} | **Status:** `{data.get('status', 'UNKNOWN')}` | **Signoff Agent:** `{author}`",
                "",
                "## 1. Executive Summary & Specification",
                f"> {spec_snippet.splitlines()[0] if spec_snippet else 'Task execution in progress'}",
                "",
                "## 2. Milestone Progress",
                f"- **Active Milestone:** `{data.get('active_milestone') or 'None'}`",
                "- **Completed Milestones:**",
            ]
            if completed:
                for c in completed:
                    lines.append(f"  - [x] {c}")
            else:
                lines.append("  - *(None yet)*")

            lines.append("- **Pending Milestones:**")
            if pending:
                for p in pending:
                    lines.append(f"  - [ ] {p}")
            else:
                lines.append("  - *(All milestones complete)*")

            lines.extend([
                "",
                "## 3. Active AST Leases Held",
            ])
            if leases:
                for l in leases:
                    lines.append(f"- `LOCK`: `{l}`")
            else:
                lines.append("- *(Zero active leases held)*")

            lines.extend([
                "",
                "## 4. Negative Causal Lessons (Do NOT Repeat These Paths)",
            ])
            if negative_lessons:
                for nl in negative_lessons:
                    lines.append(f"- ⚠️ {nl}")
            else:
                lines.append("- *(No failed attempts recorded)*")

            if falsified:
                lines.append("\n**Falsified Hypotheses Archive:**")
                for fh in falsified:
                    lines.append(f"- Hypothesis: {fh.get('statement', 'Unknown')} | Falsified by: {fh.get('probe', 'Test')}")

            lines.extend([
                "",
                "## 5. Modified Files & Impact Radius",
            ])
            if modified_files:
                for mf in modified_files:
                    lines.append(f"- `{mf}`")
            else:
                lines.append("- *(No persistent files touched yet)*")

            lines.extend([
                "",
                "## 6. Immediate Next Concrete Actions",
            ])
            for idx, action in enumerate(actions_list, 1):
                lines.append(f"{idx}. {action}")

            lines.append("\n---\n*OnyxSi Kernel Task Wiki — Seamless 0-Second Handoff Certified.*")

            content = "\n".join(lines) + "\n"
            handoff_path = os.path.join(task_dir, "AGENT_HANDOFF.md")
            self._atomic_write_text(handoff_path, content)
            return content

    # ------------------------------------------------------------ time-travel
    def generate_time_travel_replay(
        self,
        task_id: str,
        frames: Optional[List[Dict[str, Any]]] = None,
        branches: Optional[List[Dict[str, Any]]] = None,
        project_name: Optional[str] = None,
    ) -> str:
        """
        Renders and stores ``TIME_TRAVEL_REPLAY.md`` inside the task folder.

        The document is the ASCII decision tree of the speculative branches that
        were explored, followed by the frame-by-frame trace of what happened.
        Writing is atomic, so a crashed replay never leaves a half-written
        chronicle behind.
        """
        with self._lock:
            task_dir = self._task_dir(task_id, project_name)
            os.makedirs(task_dir, exist_ok=True)

            project = (
                self.project_name
                if project_name is None
                else self._sanitize_project_name(project_name)
            )
            content = render_time_travel_chronicle(
                task_id=os.path.basename(task_dir),
                project_name=project,
                frames=frames,
                branches=branches,
            )
            self._atomic_write_text(os.path.join(task_dir, "TIME_TRAVEL_REPLAY.md"), content)
            return content

    # ---------------------------------------------------------------- migration
    def migrate_legacy_tasks(self, project_name: Optional[str] = None) -> List[str]:
        """
        Moves pre-namespacing task folders into the project's namespaced root.

        This is deliberately opt-in: nothing moves until a caller asks for it.
        A task whose destination already exists is left untouched and reported
        as skipped rather than overwritten.
        """
        with self._lock:
            legacy_root = self.legacy_tasks_root()
            target_root = self.tasks_root(project_name)
            if not os.path.isdir(legacy_root):
                return []

            os.makedirs(target_root, exist_ok=True)
            migrated: List[str] = []

            for name in sorted(os.listdir(legacy_root)):
                if not name.upper().startswith("TASK-"):
                    continue
                source = os.path.join(legacy_root, name)
                if not os.path.isdir(source):
                    continue
                destination = os.path.join(target_root, name)
                if os.path.exists(destination):
                    continue
                shutil.move(source, destination)
                migrated.append(name)

            return migrated

    # ------------------------------------------------------------------ mirror
    def task_dir(self, task_id: str, project_name: Optional[str] = None) -> str:
        """Public accessor for the folder a task id resolves to."""
        return self._task_dir(task_id, project_name)

    def _sync_progress_markdown(self, task_dir: str, data: Dict[str, Any]) -> None:
        """Mirrors PROGRESS.json into PROGRESS.md for human/reviewer scan."""
        lines = [
            f"# Progress: {data.get('task_id', 'TASK')}",
            f"- **Status:** `{data.get('status', 'INITIALIZED')}`",
            f"- **Updated:** {data.get('updated_at')}",
            f"- **Active Milestone:** `{data.get('active_milestone') or 'None'}`",
            "",
            "## Milestones",
        ]
        for m in data.get("milestones", []):
            st = "x" if m.get("status") == "COMPLETED" else " "
            lines.append(f"- [{st}] **{m.get('name', 'Milestone')}** ({m.get('status', 'PENDING')})")

        if data.get("negative_lessons"):
            lines.append("\n## Negative Lessons Learned")
            for nl in data["negative_lessons"]:
                lines.append(f"- ⚠️ {nl}")

        if data.get("ast_leases"):
            lines.append("\n## Active AST Leases")
            for al in data["ast_leases"]:
                lines.append(f"- `{al}`")

        progress_md_path = os.path.join(task_dir, "PROGRESS.md")
        self._atomic_write_text(progress_md_path, "\n".join(lines) + "\n")
