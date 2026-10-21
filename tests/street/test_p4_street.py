"""
Street Chaos Tests for Phase 4: Headless Gateway, Syntactic Shield & Terminal Stream Folder.
Tests Slowloris timeout, deep nesting bombs (500 levels), pipeline cycle detection,
mid-flight pipeline rollback, 50,000-line recursion traceback, and 2MB single-line folding.
"""

import asyncio
from onyxsi.gateway.meta_router import (
    MetaToolRouter,
    MethodNotFoundError,
    SlowlorisTimeoutError,
)
from onyxsi.gateway.grammar_shield import (
    GrammarShield,
    DeepNestingBlockedError,
)
from onyxsi.gateway.pipeline_fusion import (
    MacroPipelineExecutor,
    PipelineStep,
    CyclicalPipelineError,
    PipelineStepFailedError,
)
from onyxsi.gateway.stream_folder import StreamFolder


def test_street_slowloris_stream_timeout_and_eof():
    router = MetaToolRouter(read_timeout_sec=0.05)

    async def slow_drip():
        await asyncio.sleep(0.20)
        return b"{"

    try:
        asyncio.run(router.read_framed_message(slow_drip, timeout_sec=0.03))
        raise AssertionError("Expected SlowlorisTimeoutError")
    except SlowlorisTimeoutError:
        pass

    async def sudden_eof():
        return b""

    try:
        asyncio.run(router.read_framed_message(sudden_eof, timeout_sec=0.05))
        raise AssertionError("Expected EOFError")
    except EOFError:
        pass


def test_street_grammar_shield_500_deep_nesting_bomb_and_missing_3_braces():
    shield = GrammarShield(max_nesting_depth=64)
    bomb = '{"a": ' * 500 + '1' + '}' * 500

    try:
        shield.parse_and_repair(bomb)
        raise AssertionError("Expected DeepNestingBlockedError")
    except DeepNestingBlockedError:
        pass

    # Missing 3 closing braces and containing control characters
    broken = '{"outer": {"mid": {"inner": "val\x00\x07", '
    repaired = shield.parse_and_repair(broken)
    assert repaired.was_repaired is True
    assert repaired.data["outer"]["mid"]["inner"] == "val"


def test_street_pipeline_cycle_detection_and_midflight_rollback():
    rolled_back = []

    def dispatch(tool_name: str, args: dict):
        if tool_name == "fail_step":
            raise RuntimeError("Disk full simulated error")
        return "ok"

    executor = MacroPipelineExecutor(dispatcher=dispatch)

    # 1. Cycle detection: step1 -> step2 -> step1
    cyclical_steps = [
        PipelineStep("s1", "vfs_inspect", {}, depends_on=["s2"]),
        PipelineStep("s2", "vfs_inspect", {}, depends_on=["s1"]),
    ]
    try:
        executor.execute_pipeline(cyclical_steps)
        raise AssertionError("Expected CyclicalPipelineError")
    except CyclicalPipelineError:
        pass

    # 2. Mid-flight failure triggers reverse rollback of step 1
    failing_steps = [
        PipelineStep(
            "step1",
            "vfs_mutate",
            {},
            rollback_fn=lambda: rolled_back.append("step1"),
        ),
        PipelineStep(
            "step2",
            "fail_step",
            {},
            depends_on=["step1"],
            rollback_fn=lambda: rolled_back.append("step2"),
        ),
    ]
    try:
        executor.execute_pipeline(failing_steps)
        raise AssertionError("Expected PipelineStepFailedError")
    except PipelineStepFailedError:
        pass

    assert rolled_back == ["step1"]


def test_street_stream_folder_50000_recursion_and_2mb_single_line():
    folder = StreamFolder()

    # 50,000-line recursion stack trace
    rec_lines = ["Traceback (most recent call last):"]
    for _ in range(25000):
        rec_lines.append('  File "recurse.py", line 10, in infinite_call')
        rec_lines.append("    return infinite_call(n + 1)")
    rec_lines.append("RecursionError: maximum recursion depth exceeded")

    res = folder.fold_stream("\n".join(rec_lines), stream_id="rec_flood")
    assert res.emitted_lines < 30
    assert "RecursionError: maximum recursion depth exceeded" in res.folded_text

    # 2MB single line without newline
    mega_line = "X" * (2 * 1024 * 1024)
    res_mega = folder.fold_stream(mega_line, stream_id="mega_line")
    assert res_mega.emitted_bytes < 1024
    assert "inline-folded" in res_mega.folded_text
