"""
Guards the measured-benchmark artifact (`benchmarks/results_v1.0.json`).

The blueprint quotes performance numbers; this test makes sure the numbers we
publish are (a) present, (b) internally consistent, and (c) still satisfy the
gates the benchmark declares. It does not re-run the benchmark (too slow and
machine-dependent) except for the deterministic schema measurement.
"""

import json
import os

from onyxsi.gateway.meta_router import MAX_SCHEMA_TOKENS, MetaToolRouter

RESULTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "benchmarks",
    "results_v1.0.json",
)
REPORT = os.path.join(os.path.dirname(RESULTS), "BENCHMARKS.md")


def _load():
    with open(RESULTS, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_benchmark_artifact_exists_and_is_parseable():
    assert os.path.isfile(RESULTS), "run python benchmarks/bench_onyxsi.py first"
    data = _load()
    assert data["environment"]["python"]
    assert data["wall_time_sec"] > 0


def test_all_declared_gates_pass():
    data = _load()
    failed = [gate for gate in data["gates"] if not gate["pass"]]
    assert not failed, f"regressed gates: {failed}"
    assert data["all_gates_pass"] is True


def test_schema_numbers_match_the_live_router():
    data = _load()
    live = MetaToolRouter().schema_token_count()
    assert data["schema"]["schema_tokens"] == live
    assert live <= MAX_SCHEMA_TOKENS
    assert data["schema"]["tools"] == 8


def test_latency_metrics_are_present_and_sane():
    data = _load()
    for section in ("rollback", "ghost_discard", "blast_select", "bisect", "antibody", "bm25", "roundtrip"):
        assert section in data, f"missing benchmark section: {section}"
        assert data[section]["p50_ms"] <= data[section]["p95_ms"] <= data[section]["max_ms"]
        assert data[section]["iterations"] > 0


def test_recall_is_measured_on_a_labelled_corpus():
    data = _load()
    recall = data["recall"]
    assert recall["corpus_functions"] > 100
    assert recall["queries"] > 50
    # The honest (held-out) numbers must be reported, not just the upper bound.
    assert 0.0 < recall["held_out_hit_at_5"] <= 1.0
    assert 0.0 < recall["held_out_recall_at_5"] <= 1.0
    assert recall["held_out_hit_at_1"] <= recall["held_out_hit_at_5"]
    # An upper bound by construction: the query is a subset of the document.
    assert recall["docstrings_indexed_hit_at_5"] >= recall["held_out_hit_at_5"]


def test_markdown_report_matches_the_json():
    data = _load()
    with open(REPORT, "r", encoding="utf-8") as fh:
        report = fh.read()
    assert "Measured Benchmarks" in report
    assert str(data["schema"]["schema_tokens"]) in report
    assert "What is **not** measured here" in report
