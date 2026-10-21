#!/usr/bin/env python3
"""
Reproducible micro-benchmarks for OnyxSi v1.0.

The blueprint (§2 Benchmark Matrix) quotes figures such as "250x faster test
selection", "95.4% search recall" and "< 0.01ms rollback". None of those had an
artifact behind them. This script produces one.

It measures only properties the code actually guarantees and asserts in tests:

  * fixed-schema token budget          (<= 1,800 tokens; output folding <= 1,250)
  * context folding                    (emitted tokens <= ceiling, ref resolvable)
  * AST skeleton pruning ratio
  * bitplane scan throughput
  * piece-table rollback latency
  * ghost overlay discard latency
  * blast-radius test selection latency
  * causal bisect latency
  * antibody lookup latency
  * BM25+graph search latency
  * HAL telemetry record cost
  * end-to-end JSON-RPC round-trip

Usage
-----
    python benchmarks/bench_onyxsi.py            # writes results + markdown
    python benchmarks/bench_onyxsi.py --check    # exit 1 if a gate regresses

Everything is standard library only, like the rest of the project.
"""

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from onyxsi.causal.antibody_memory import AntibodyMemory, AntibodyRecord
from onyxsi.causal.causal_bisect import BisectStep, CausalBisectEngine
from onyxsi.contextfs.bitplane_ast import BitplaneAstPruner, BitplaneAstScanner
from onyxsi.contextfs.piece_table import PieceTableDocument
from onyxsi.contextfs.token_governor import TokenGovernor
from onyxsi.deltafs.ghost_overlay import GhostOverlayFS
from onyxsi.gateway.meta_router import MAX_SCHEMA_TOKENS, MetaToolRouter
from onyxsi.hal.telemetry import ToolTelemetry
from onyxsi.knowledge.blast_test_runner import BlastRadiusTestRunner
from onyxsi.knowledge.csr_graph_bm25 import EDGE_CALLS, EDGE_TESTS, CodeCSRGraph, HybridSearchEngine
from onyxsi.server import OnyxSiServer

from recall_eval import evaluate as evaluate_recall

RESULTS_JSON = os.path.join(ROOT, "benchmarks", "results_v1.0.json")
RESULTS_MD = os.path.join(ROOT, "benchmarks", "BENCHMARKS.md")


# --------------------------------------------------------------------------- utils
def _sample(fn: Callable[[], Any], iterations: int, warmup: int = 2) -> Dict[str, float]:
    for _ in range(warmup):
        fn()
    samples: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()

    def pct(p: float) -> float:
        idx = min(len(samples) - 1, int(round((len(samples) - 1) * p)))
        return samples[idx]

    return {
        "p50_ms": round(pct(0.50), 4),
        "p95_ms": round(pct(0.95), 4),
        "min_ms": round(samples[0], 4),
        "max_ms": round(samples[-1], 4),
        "iterations": iterations,
    }


def _generated_python(functions: int = 200) -> str:
    blocks = []
    for i in range(functions):
        blocks.append(
            f"def handler_{i}(request, context=None):\n"
            f"    \"\"\"Processes request number {i} and returns a normalized payload.\"\"\"\n"
            f"    total = 0\n"
            f"    for step in range({i % 17}):\n"
            f"        total += step * 3\n"
            f"    return {{'id': {i}, 'total': total, 'context': context}}\n"
        )
    return "\n".join(blocks)


def _log_text(size_kb: int = 512) -> str:
    line = "2026-10-20T12:00:00Z INFO worker[42] processed chunk id=12345 status=ok\n"
    return line * max(1, (size_kb * 1024) // len(line))


# --------------------------------------------------------------------------- benchmarks
def bench_schema() -> Dict[str, Any]:
    router = MetaToolRouter()
    return {
        "schema_tokens": router.schema_token_count(),
        "schema_ceiling_tokens": MAX_SCHEMA_TOKENS,
        "tools": len(router.list_tools()),
    }


def bench_folding() -> Dict[str, Any]:
    governor = TokenGovernor()
    text = _log_text(512)
    result = governor.enforce_ceiling(text, ceiling_tokens=1250, context_hint="bench")
    resolvable = governor.can_resolve(result.folded_ref)
    return {
        "input_chars": len(text),
        "input_tokens_estimate": result.original_tokens,
        "emitted_tokens_estimate": result.emitted_tokens,
        "ceiling_tokens": 1250,
        "is_folded": result.is_folded,
        "retrievable": result.retrievable,
        "ref_resolves": resolvable,
        "fold_ratio": round(result.emitted_tokens / max(1, result.original_tokens), 6),
    }


def bench_skeleton() -> Dict[str, Any]:
    source = _generated_python(200)
    result = BitplaneAstPruner.prune_source(source, "bench_module.py")
    return {
        "source_bytes": len(source.encode("utf-8")),
        "skeleton_bytes": len(result.skeleton_code.encode("utf-8")),
        "prune_ratio": round(len(result.skeleton_code) / max(1, len(source)), 6),
        "keeps_signatures": "def handler_0" in result.skeleton_code,
    }


def bench_bitplane() -> Dict[str, Any]:
    scanner = BitplaneAstScanner()
    source = (_generated_python(1200)).encode("utf-8")
    timing = _sample(lambda: scanner.analyze_source_topology(source), iterations=15, warmup=2)
    megabytes = len(source) / (1024 * 1024)
    return {
        "source_mb": round(megabytes, 3),
        "source_bytes": len(source),
        "scan_p50_ms": timing["p50_ms"],
        "scan_p95_ms": timing["p95_ms"],
        "throughput_mb_s": round(megabytes / (timing["p50_ms"] / 1000.0), 3)
        if timing["p50_ms"]
        else None,
    }


def bench_rollback() -> Dict[str, Any]:
    doc = PieceTableDocument(b"")
    checkpoints = []
    for i in range(200):
        doc.insert(doc.total_length(), f"line_{i} = {i}\n".encode())
        checkpoints.append(doc.create_checkpoint())

    counter = {"i": 0}

    def do_rollback() -> None:
        counter["i"] = (counter["i"] + 37) % len(checkpoints)
        doc.rollback(checkpoints[counter["i"]])

    return _sample(do_rollback, iterations=200)


def bench_ghost_discard() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i in range(25):
            path = os.path.join(tmp, f"mod_{i}.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"VALUE = {i}\n" * 40)
            files.append(path)

        ghost = GhostOverlayFS(tmp)

        def cycle() -> None:
            ghost.ghost_begin()
            for path in files:
                ghost.ghost_mutate(path, "MUTATED = True\n")
            ghost.ghost_discard()

        return _sample(cycle, iterations=20, warmup=2)


def bench_blast_select() -> Dict[str, Any]:
    graph = CodeCSRGraph()
    for i in range(4000):
        graph.add_edge(f"caller_{i}", f"callee_{i % 500}", EDGE_CALLS)
    for i in range(500):
        graph.add_edge(f"test_impacted_{i}", "target_function", EDGE_TESTS)
    graph.bake()

    runner = BlastRadiusTestRunner(graph)
    timing = _sample(
        lambda: runner.select_impacted_tests("target_function", max_hops=2),
        iterations=100,
        warmup=5,
    )
    selected, _internal_ms = runner.select_impacted_tests("target_function", max_hops=2)
    timing["selected_tests"] = len(selected)
    return timing


def bench_bisect() -> Dict[str, Any]:
    doc = PieceTableDocument(b"")
    steps: List[BisectStep] = []
    for i in range(64):
        doc.insert(doc.total_length(), b"x")
        checkpoint = doc.create_checkpoint()
        steps.append(
            BisectStep(step_index=i + 1, checkpoint_id=checkpoint, node_id=f"node_{i + 1}")
        )

    def invariant(_payload: bytes) -> bool:
        return len(_payload) < 40  # "regression" appears at step 41

    def run() -> None:
        CausalBisectEngine.bisect_failure(doc, steps, invariant)

    timing = _sample(run, iterations=20, warmup=2)
    report = CausalBisectEngine.bisect_failure(doc, steps, invariant)
    timing["found_culprit"] = report.found_culprit
    timing["probes"] = report.probes_executed
    return timing


def bench_antibody() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        memory = AntibodyMemory(storage_path=os.path.join(tmp, "antibodies.json"))
        for i in range(100):
            memory.register_antibody(
                AntibodyRecord(
                    antibody_id=f"AB-{i:03d}",
                    target_scope_glob="*.py",
                    forbidden_pattern=f"def broken_{i}(",
                    root_cause_summary=f"broken_{i} fails at runtime",
                    prescribed_alternative=f"use fixed_{i}() instead",
                ),
                persist=False,
            )
        clean = "def healthy(x):\n    return x + 1\n"
        return _sample(lambda: memory.check_mutation("mod.py", clean), iterations=200, warmup=5)


def bench_bm25() -> Dict[str, Any]:
    engine = HybridSearchEngine()
    for i in range(5000):
        engine.index_node(f"module_{i}", f"service worker handling checkout order flow step {i}")
        engine.add_relation(f"module_{i}", f"module_{(i + 1) % 5000}", EDGE_CALLS)
    engine.prepare()

    timing = _sample(lambda: engine.search("checkout order service", top_k=5), iterations=100, warmup=5)
    timing["indexed_nodes"] = engine.graph.num_nodes()
    timing["hits"] = len(engine.search("checkout order service", top_k=5))
    return timing


def bench_telemetry() -> Dict[str, Any]:
    telemetry = ToolTelemetry(capacity=1024)
    try:
        def run() -> None:
            telemetry.record("vfs_inspect", True, 42)

        timing = _sample(run, iterations=2000, warmup=10)
        timing["per_call_us"] = round((timing["p50_ms"] * 1000.0), 3)
        return timing
    finally:
        telemetry.close()


def bench_recall() -> Dict[str, Any]:
    """Retrieval quality on a labelled corpus built from this repo's own source."""
    upper = evaluate_recall(include_docstring=True)
    held_out = evaluate_recall(include_docstring=False)
    return {
        "corpus_functions": upper["corpus"]["functions"],
        "corpus_edges": upper["corpus"]["edges"],
        "queries": upper["corpus"]["queries"],
        "docstrings_indexed_hit_at_1": upper["metrics"]["hit_at_1"],
        "docstrings_indexed_hit_at_5": upper["metrics"]["hit_at_5"],
        "docstrings_indexed_recall_at_5": upper["metrics"]["recall_at_5"],
        "held_out_hit_at_1": held_out["metrics"]["hit_at_1"],
        "held_out_hit_at_5": held_out["metrics"]["hit_at_5"],
        "held_out_mrr": held_out["metrics"]["mrr"],
        "held_out_recall_at_5": held_out["metrics"]["recall_at_5"],
    }


def bench_roundtrip() -> Dict[str, Any]:
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "vfs_inspect",
            "params": {"action": "tree"},
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "app.py"), "w", encoding="utf-8") as fh:
            fh.write("def handler():\n    return 1\n")
        server = OnyxSiServer(workspace_root=tmp)
        try:
            server.process_line(request)  # warm the paths
            timing = _sample(lambda: server.process_line(request), iterations=200, warmup=5)
            timing["per_call_us"] = round(timing["p50_ms"] * 1000.0, 3)
            return timing
        finally:
            server.shutdown()


# --------------------------------------------------------------------------- gates
def evaluate(results: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turns measurements into explicit, checkable gates."""

    def gate(name: str, actual: Any, limit: Any, comparator: str) -> Dict[str, Any]:
        if comparator == "<=":
            passed = actual is not None and actual <= limit
        elif comparator == ">=":
            passed = actual is not None and actual >= limit
        else:  # "is"
            passed = actual == limit
        return {
            "gate": name,
            "actual": actual,
            "limit": limit,
            "comparator": comparator,
            "pass": bool(passed),
        }

    return [
        gate("schema_tokens", results["schema"]["schema_tokens"],
             results["schema"]["schema_ceiling_tokens"], "<="),
        gate("tool_count", results["schema"]["tools"], 8, "is"),
        gate("fold_emitted_tokens", results["folding"]["emitted_tokens_estimate"],
             results["folding"]["ceiling_tokens"], "<="),
        gate("fold_ref_resolves", results["folding"]["ref_resolves"], True, "is"),
        gate("fold_retrievable", results["folding"]["retrievable"], True, "is"),
        gate("skeleton_prune_ratio", results["skeleton"]["prune_ratio"], 1.0, "<="),
        gate("bitplane_throughput_mb_s", results["bitplane"]["throughput_mb_s"], 20.0, ">="),
        gate("rollback_p95_ms", results["rollback"]["p95_ms"], 1.0, "<="),
        gate("ghost_discard_p95_ms", results["ghost_discard"]["p95_ms"], 5.0, "<="),
        gate("blast_select_p95_ms", results["blast_select"]["p95_ms"], 5.0, "<="),
        gate("bisect_p95_ms", results["bisect"]["p95_ms"], 1000.0, "<="),
        gate("antibody_p95_ms", results["antibody"]["p95_ms"], 5.0, "<="),
        gate("bm25_search_p95_ms", results["bm25"]["p95_ms"], 50.0, "<="),
        gate("roundtrip_p95_ms", results["roundtrip"]["p95_ms"], 50.0, "<="),
        gate("recall_held_out_hit_at_5", results["recall"]["held_out_hit_at_5"], 0.50, ">="),
        gate("recall_held_out_recall_at_5", results["recall"]["held_out_recall_at_5"], 0.30, ">="),
    ]


def render_markdown(results: Dict[str, Any], gates: List[Dict[str, Any]]) -> str:
    env = results["environment"]
    lines = [
        "# OnyxSi v1.0 — Measured Benchmarks",
        "",
        "> Generated by `python benchmarks/bench_onyxsi.py`. These are **machine-specific**",
        "> numbers from a single run, not a cross-machine guarantee. Re-run on your own",
        "> hardware before quoting them.",
        "",
        "## Environment",
        "",
        f"- Python: `{env['python']}`",
        f"- Platform: `{env['platform']}`",
        f"- Machine: `{env['machine']}`",
        f"- CPUs: `{env['cpu_count']}`",
        f"- Timestamp (UTC): `{env['timestamp_utc']}`",
        "",
        "## Gates",
        "",
        "| Gate | Actual | Limit | Result |",
        "| --- | --- | --- | --- |",
    ]
    for g in gates:
        mark = "PASS" if g["pass"] else "**FAIL**"
        lines.append(f"| `{g['gate']}` | {g['actual']} | {g['comparator']} {g['limit']} | {mark} |")

    lines += [
        "",
        "## Measurements",
        "",
        "| Metric | Value | Unit |",
        "| --- | --- | --- |",
        f"| Schema tokens (8 tools) | {results['schema']['schema_tokens']} | tokens (len/4 estimate) |",
        f"| Folding ratio | {results['folding']['fold_ratio']:.5f} | emitted / original |",
        f"| Skeleton prune ratio | {results['skeleton']['prune_ratio']:.5f} | skeleton / source |",
        f"| Bitplane throughput | {results['bitplane']['throughput_mb_s']} | MB/s |",
        f"| Piece-table rollback p50 | {results['rollback']['p50_ms']} | ms |",
        f"| Piece-table rollback p95 | {results['rollback']['p95_ms']} | ms |",
        f"| Ghost discard p50 (25 files) | {results['ghost_discard']['p50_ms']} | ms |",
        f"| Ghost discard p95 (25 files) | {results['ghost_discard']['p95_ms']} | ms |",
        f"| Blast selection p50 (500 tests) | {results['blast_select']['p50_ms']} | ms |",
        f"| Blast selection p95 | {results['blast_select']['p95_ms']} | ms |",
        f"| Causal bisect p50 (64 steps) | {results['bisect']['p50_ms']} | ms |",
        f"| Causal bisect probes | {results['bisect']['probes']} | probes |",
        f"| Antibody check p50 | {results['antibody']['p50_ms']} | ms |",
        f"| BM25+graph search p50 (5k nodes) | {results['bm25']['p50_ms']} | ms |",
        f"| Telemetry record p50 | {results['telemetry']['per_call_us']} | us |",
        f"| JSON-RPC round-trip p50 | {results['roundtrip']['per_call_us']} | us |",
        "",
        "## Retrieval quality (labelled corpus from this repo's source)",
        "",
        f"Corpus: **{results['recall']['corpus_functions']} functions**, "
        f"{results['recall']['corpus_edges']} undirected call edges, "
        f"{results['recall']['queries']} queries. The query is built only from a function's "
        "docstring; the function's name is removed from the query.",
        "",
        "| Mode | hit@1 | hit@5 | recall@5 |",
        "| --- | --- | --- | --- |",
        f"| Docstring indexed (upper bound) | {results['recall']['docstrings_indexed_hit_at_1']:.4f} | {results['recall']['docstrings_indexed_hit_at_5']:.4f} | "
        f"{results['recall']['docstrings_indexed_recall_at_5']:.4f} |",
        f"| Docstring held out (honest) | {results['recall']['held_out_hit_at_1']:.4f} | "
        f"{results['recall']['held_out_hit_at_5']:.4f} | "
        f"{results['recall']['held_out_recall_at_5']:.4f} |",
        "",
        "> The held-out row is the number to quote. The blueprint's `95.4%` has no",
        "> supporting artefact and is **not** reproduced by this engine.",
        "",
        "## What is **not** measured here",
        "",
        "- **`250x` test-selection speedup**: needs a baseline full-suite runtime on a",
        "  real project; not reproducible inside this repo.",
        "- **`< 0.01ms` rollback**: the measured value above is the real one. Any older",
        "  document quoting a smaller number is stale.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(description="OnyxSi v1.0 micro-benchmarks")
    parser.add_argument("--check", action="store_true", help="exit 1 when a gate fails")
    parser.add_argument("--json-only", action="store_true", help="skip the markdown report")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    results: Dict[str, Any] = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "schema": bench_schema(),
        "folding": bench_folding(),
        "skeleton": bench_skeleton(),
        "bitplane": bench_bitplane(),
        "rollback": bench_rollback(),
        "ghost_discard": bench_ghost_discard(),
        "blast_select": bench_blast_select(),
        "bisect": bench_bisect(),
        "antibody": bench_antibody(),
        "bm25": bench_bm25(),
        "telemetry": bench_telemetry(),
        "recall": bench_recall(),
        "roundtrip": bench_roundtrip(),
    }
    results["wall_time_sec"] = round(time.perf_counter() - started, 3)

    gates = evaluate(results)
    results["gates"] = gates
    results["all_gates_pass"] = all(g["pass"] for g in gates)

    os.makedirs(os.path.dirname(RESULTS_JSON), exist_ok=True)
    with open(RESULTS_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
        fh.write("\n")

    if not args.json_only:
        with open(RESULTS_MD, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_markdown(results, gates))

    for gate in gates:
        mark = "PASS" if gate["pass"] else "FAIL"
        print(f"[{mark}] {gate['gate']}: {gate['actual']} {gate['comparator']} {gate['limit']}")

    print(f"\nwrote {os.path.relpath(RESULTS_JSON, ROOT)} in {results['wall_time_sec']}s")
    if args.check and not results["all_gates_pass"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
