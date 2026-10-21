"""
Character Scenario Tests for Phase 4: CT-17 through CT-20.
"""

import asyncio
from onyxsi.contextfs.token_governor import TokenGovernor
from onyxsi.gateway.meta_router import (
    MetaToolRouter,
    MethodNotFoundError,
    SlowlorisTimeoutError,
)
from onyxsi.gateway.grammar_shield import GrammarShield
from onyxsi.gateway.pipeline_fusion import (
    MacroPipelineExecutor,
    PipelineStep,
    CyclicalPipelineError,
)
from onyxsi.gateway.stream_folder import StreamFolder


class TestCT17SlowlorisAndGhostToolProber:
    """CT-17: Slowloris byte dripper and internal tool scanner."""

    def test_slowloris_and_prober_blocked(self):
        router = MetaToolRouter(read_timeout_sec=0.02)

        async def drip():
            await asyncio.sleep(0.10)
            return b'{"jsonrpc":'

        try:
            asyncio.run(router.read_framed_message(drip, timeout_sec=0.02))
            raise AssertionError("Expected SlowlorisTimeoutError")
        except SlowlorisTimeoutError:
            pass

        for secret_name in ("_internal_exec", "os_system", "raw_memory_dump", "admin_shell"):
            try:
                router.dispatch_rpc({
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "tools/call",
                    "params": {"name": secret_name, "arguments": {}},
                })
                raise AssertionError(f"Expected MethodNotFoundError for {secret_name}")
            except MethodNotFoundError:
                pass


class TestCT18TruncatedTokenVomiter:
    """CT-18: Agent vomiting half-finished JSON and stringified numbers."""

    def test_truncated_json_and_type_coercion(self):
        shield = GrammarShield()
        vomited = '{"action": "tree", "target": "src/onyxsi", "max_tokens": "500",'
        repaired = shield.parse_and_repair(vomited)
        assert repaired.was_repaired is True

        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "target": {"type": "string"},
                "max_tokens": {"type": "integer"},
            },
            "required": ["action", "target", "max_tokens"],
        }
        coerced = shield.validate_and_coerce(repaired.data, schema)
        assert coerced.data["max_tokens"] == 500


class TestCT19CyclicalDagSaboteur:
    """CT-19: Agent submitting circular dependency DAGs to hang the kernel."""

    def test_cycle_saboteur_rejected(self):
        executor = MacroPipelineExecutor(dispatcher=lambda t, a: "ok")
        steps = [
            PipelineStep("step_a", "vfs_inspect", {}, depends_on=["step_c"]),
            PipelineStep("step_b", "graph_compute", {}, depends_on=["step_a"]),
            PipelineStep("step_c", "vfs_mutate", {}, depends_on=["step_b"]),
        ]
        try:
            executor.execute_pipeline(steps)
            raise AssertionError("Expected CyclicalPipelineError")
        except CyclicalPipelineError:
            pass


class TestCT20InfiniteLogFlooder:
    """CT-20: Agent running --verbose --debug and flooding 50,000 log lines."""

    def test_log_flood_folded_under_650_tokens(self):
        folder = StreamFolder()
        flood = [f"[DEBUG 2026-10-18] Verbose trace tick #{i}: state=IDLE" for i in range(10000)]
        flood.append("RuntimeError: Critical kernel invariant violated at tick #10000")
        res = folder.fold_stream("\n".join(flood), stream_id="flood_20")

        tokens = TokenGovernor.estimate_tokens(res.folded_text)
        assert tokens <= 650
        assert "RuntimeError: Critical kernel invariant violated" in res.folded_text
