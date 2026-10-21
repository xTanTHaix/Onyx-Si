"""
Normal Tests for Phase 6.1: Dedicated Folder-per-Task Wiki & Seamless Multi-Agent Handoff.
Verifies TASK_SPEC.md, PROGRESS.json, PROGRESS.md, EXECUTION_LOG.md, and AGENT_HANDOFF.md.
"""

import os
import json
import tempfile
from onyxsi.knowledge.task_wiki import TaskWikiManager


def test_task_wiki_initialization_and_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = TaskWikiManager(tmpdir)
        spec = "# Spec for TASK-001\nImplement high-speed radix sorting kernel."
        task_dir = manager.init_task("TASK-001", spec)

        assert os.path.isdir(task_dir)
        assert os.path.exists(os.path.join(task_dir, "TASK_SPEC.md"))
        assert os.path.exists(os.path.join(task_dir, "PROGRESS.json"))
        assert os.path.exists(os.path.join(task_dir, "PROGRESS.md"))
        assert os.path.exists(os.path.join(task_dir, "EXECUTION_LOG.md"))

        with open(os.path.join(task_dir, "TASK_SPEC.md"), "r", encoding="utf-8") as f:
            content = f.read()
        assert "Implement high-speed radix sorting kernel" in content


def test_task_wiki_progress_update_atomic_and_markdown_mirror():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = TaskWikiManager(tmpdir)
        manager.init_task(
            "TASK-002",
            "Task 2 specification",
            initial_plan=[{"name": "Stage 1", "status": "PENDING"}],
        )

        manager.update_progress(
            "TASK-002",
            status="IN_PROGRESS",
            completed_milestone="Stage 1",
            active_milestone="Stage 2",
            negative_lesson="Do not allocate in inner loop",
            note="Completed Stage 1 successfully.",
        )

        data = manager.get_progress("TASK-002")
        assert data["status"] == "IN_PROGRESS"
        assert data["active_milestone"] == "Stage 2"
        assert "Do not allocate in inner loop" in data["negative_lessons"]

        # Check PROGRESS.md mirror
        task_dir = manager._task_dir("TASK-002")
        with open(os.path.join(task_dir, "PROGRESS.md"), "r", encoding="utf-8") as f:
            md_text = f.read()
        assert "[x] **Stage 1** (COMPLETED)" in md_text
        assert "Do not allocate in inner loop" in md_text


def test_task_wiki_agent_handoff_generation():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = TaskWikiManager(tmpdir)
        manager.init_task("TASK-003", "# Refactor Database Schema")
        manager.update_progress(
            "TASK-003",
            completed_milestone="Design ER diagram",
            active_milestone="Write migrations",
            ast_leases=["ast_func_migrate_users_a1b2c3d4"],
            negative_lesson="Do not drop table with active connections",
            modified_file="src/db/migrations.py",
        )

        handoff = manager.generate_handoff(
            "TASK-003",
            next_actions=["Run migration dry-run", "Verify foreign key constraints"],
        )

        assert "# Agent Handoff Protocol: TASK-003" in handoff
        assert "Design ER diagram" in handoff
        assert "ast_func_migrate_users_a1b2c3d4" in handoff
        assert "Do not drop table with active connections" in handoff
        assert "src/db/migrations.py" in handoff
        assert "Run migration dry-run" in handoff

        # Check file exists on disk
        task_dir = manager._task_dir("TASK-003")
        assert os.path.exists(os.path.join(task_dir, "AGENT_HANDOFF.md"))
