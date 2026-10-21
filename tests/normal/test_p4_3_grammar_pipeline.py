"""
Normal Tests for Phase 4.3: Upstream EBNF/GBNF Grammar Exporter & Macro Pipeline Fusion.
"""

from onyxsi.gateway.meta_router import MetaToolRouter
from onyxsi.gateway.grammar_export import GrammarExporter
from onyxsi.gateway.pipeline_fusion import (
    MacroPipelineExecutor,
    PipelineStep,
)


def test_gbnf_grammar_exporter_all_8_meta_tools():
    router = MetaToolRouter()
    specs = list(router._specs.values())
    gbnf = GrammarExporter.export_all_meta_tools_gbnf(specs)

    assert "root ::= tool-call" in gbnf
    assert "vfs-inspect-call ::=" in gbnf
    assert "mcp-orchestrate-call ::=" in gbnf


def test_macro_pipeline_3_step_prev_output_fusion():
    def mock_dispatch(tool_name: str, args: dict):
        if tool_name == "vfs_inspect":
            return f"inspected({args.get('target')})"
        if tool_name == "graph_compute":
            return f"impact_of({args.get('query')})"
        return "unknown"

    executor = MacroPipelineExecutor(dispatcher=mock_dispatch)
    steps = [
        PipelineStep(
            step_id="step1",
            tool_name="vfs_inspect",
            arguments={"action": "skeleton", "target": "auth.py"},
        ),
        PipelineStep(
            step_id="step2",
            tool_name="graph_compute",
            arguments={"engine": "blast_radius", "query": "$prev.output"},
            depends_on=["step1"],
        ),
        PipelineStep(
            step_id="step3",
            tool_name="vfs_inspect",
            arguments={"action": "resolve", "target": "$prev.output"},
            depends_on=["step2"],
        ),
    ]

    result = executor.execute_pipeline(steps)
    assert result.succeeded is True
    assert result.execution_order == ["step1", "step2", "step3"]
    assert result.outputs["step1"] == "inspected(auth.py)"
    assert result.outputs["step2"] == "impact_of(inspected(auth.py))"
    assert result.outputs["step3"] == "inspected(impact_of(inspected(auth.py)))"
