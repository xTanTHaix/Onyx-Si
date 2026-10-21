#!/usr/bin/env python3
"""
Honest retrieval evaluation for the hybrid CSR+BM25 search engine.

The blueprint claimed "Recall@5 >= 95.4%" but shipped no corpus. This script
builds a *labelled* corpus from OnyxSi's own source:

  node        = one Python function/method
  document    = qualified name + docstring + body tokens
  ground truth = a query built ONLY from the function's docstring (never its
                 name), plus the function's direct call-graph neighbours

So the question being answered is: "given a natural-language description, does
the engine return the right function and its immediate collaborators?"

No embeddings, no test-set leakage: the query never contains the answer's name
unless the docstring happens to repeat it (which we strip).

Usage:
    python benchmarks/recall_eval.py            # writes recall_v1.0.json
    python benchmarks/recall_eval.py --check    # exit 1 below the gate
"""

import argparse
import ast
import json
import os
import random
import re
import sys
from typing import Dict, List, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from onyxsi.knowledge.csr_graph_bm25 import EDGE_CALLS, HybridSearchEngine

OUT = os.path.join(ROOT, "benchmarks", "recall_v1.0.json")

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at", "by",
    "is", "are", "be", "been", "was", "were", "it", "its", "this", "that",
    "with", "from", "as", "if", "then", "else", "return", "returns", "returned",
    "self", "args", "kwargs", "def", "class", "none", "true", "false", "int",
    "str", "bool", "list", "dict", "optional", "any", "call", "called", "calls",
    "using", "used", "use", "into", "all", "one", "two", "new", "old", "each",
    "every", "when", "while", "after", "before", "than", "not", "no", "yes",
    "this", "these", "those", "we", "you", "they", "our", "your", "their",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")


def _iter_source_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def _module_name(path: str) -> str:
    rel = os.path.relpath(path, SRC)
    return rel[:-3].replace(os.sep, ".")


def _collect_functions(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:  # pragma: no cover - repo is syntactically valid
        return []

    lines = source.splitlines()
    module = _module_name(path)
    results: List[Dict] = []

    def visit(node: ast.AST, prefix: str = ""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                doc = ast.get_docstring(child) or ""
                end_line = getattr(child, "end_lineno", child.lineno)
                body_lines = lines[child.lineno - 1: end_line]

                # The docstring is inside the body text; strip it so the
                # "held out" evaluation does not leak the query back in.
                doc_node = None
                if (
                    child.body
                    and isinstance(child.body[0], ast.Expr)
                    and isinstance(child.body[0].value, ast.Constant)
                    and isinstance(child.body[0].value.value, str)
                ):
                    doc_node = child.body[0]
                if doc_node is not None:
                    rel_start = doc_node.lineno - child.lineno
                    rel_end = getattr(doc_node, "end_lineno", doc_node.lineno) - child.lineno + 1
                    body_lines = body_lines[:rel_start] + body_lines[rel_end:]
                body = "\n".join(body_lines)
                calls: Set[str] = set()
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        if isinstance(func, ast.Name):
                            calls.add(func.id)
                        elif isinstance(func, ast.Attribute):
                            calls.add(func.attr)
                results.append(
                    {
                        "qualname": f"{module}:{qual}",
                        "module": module,
                        "name": child.name,
                        "qual_in_module": qual,
                        "docstring": doc,
                        "body": body,
                        "calls": sorted(calls),
                    }
                )
                visit(child, prefix=f"{qual}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix=f"{prefix}{child.name}.")

    visit(tree)
    return results


def _build_corpus(include_docstring: bool = True) -> Tuple[HybridSearchEngine, List[Dict], Dict[str, str]]:
    functions: List[Dict] = []
    for path in _iter_source_files(SRC):
        functions.extend(_collect_functions(path))

    # Resolve call edges only when the callee name is unambiguous.
    by_name: Dict[str, List[str]] = {}
    for fn in functions:
        by_name.setdefault(fn["name"], []).append(fn["qualname"])

    engine = HybridSearchEngine()
    for fn in functions:
        parts = [fn["name"].replace("_", " ")]
        if include_docstring:
            parts.append(fn["docstring"])
        parts.append(fn["body"][:1500])
        engine.index_node(fn["qualname"], " ".join(p for p in parts if p))

    for fn in functions:
        for call in fn["calls"]:
            targets = by_name.get(call, [])
            if len(targets) == 1 and targets[0] != fn["qualname"]:
                engine.add_relation(fn["qualname"], targets[0], EDGE_CALLS)

    engine.prepare()
    by_qual = {fn["qualname"]: fn for fn in functions}
    return engine, functions, by_qual


def _query_from_docstring(fn: Dict) -> str:
    """Natural-language query built from the docstring, with the name removed."""
    name_words = set(re.split(r"[_\W]+", fn["name"].lower()))
    words = [
        w.lower()
        for w in _WORD_RE.findall(fn["docstring"])
        if w.lower() not in STOPWORDS and w.lower() not in name_words
    ]
    return " ".join(words[:12])


def evaluate(max_queries: int = 150, top_k: int = 5, seed: int = 7, include_docstring: bool = True) -> Dict:
    engine, functions, _ = _build_corpus(include_docstring=include_docstring)

    adjacency: Dict[str, Set[str]] = {}
    for fn in functions:
        adjacency.setdefault(fn["qualname"], set())

    # Rebuild adjacency from the engine's graph so truth matches what was indexed.
    node_to_id = engine.graph.node_to_id
    id_to_node = engine.graph.id_to_node
    for source_name, source_id in node_to_id.items():
        neighbours = adjacency.setdefault(source_name, set())
        for target_id, _edge in engine.graph._fwd_adj.get(source_id, []):  # noqa: SLF001 - eval script
            neighbours.add(id_to_node[target_id])
        for target_id, _edge in engine.graph._rev_adj.get(source_id, []):  # noqa: SLF001
            neighbours.add(id_to_node[target_id])

    candidates = [fn for fn in functions if _query_from_docstring(fn)]
    rng = random.Random(seed)
    rng.shuffle(candidates)
    candidates = candidates[:max_queries]

    hits_at_1 = 0
    hits_at_5 = 0
    reciprocal_rank = 0.0
    recall_sum = 0.0
    per_query = []

    for fn in candidates:
        query = _query_from_docstring(fn)
        results = engine.search(query, top_k=top_k)
        ranked = [name for name, _score in results]
        target = fn["qualname"]

        rank = ranked.index(target) + 1 if target in ranked else 0
        hits_at_1 += 1 if rank == 1 else 0
        hits_at_5 += 1 if 0 < rank <= top_k else 0
        reciprocal_rank += (1.0 / rank) if rank else 0.0

        truth = {target} | adjacency.get(target, set())
        truth = {name for name in truth if name in node_to_id}
        found = len(truth & set(ranked))
        recall_sum += found / len(truth) if truth else 0.0

        per_query.append(
            {
                "target": target,
                "query": query,
                "rank": rank,
                "recall_at_5": round(found / len(truth), 4) if truth else 0.0,
                "relevant": len(truth),
                "returned": ranked,
            }
        )

    n = len(candidates) or 1
    return {
        "corpus": {
            "functions": len(functions),
            "edges": sum(len(v) for v in adjacency.values()) // 2,
            "queries": len(candidates),
            "top_k": top_k,
            "seed": seed,
        },
        "metrics": {
            "hit_at_1": round(hits_at_1 / n, 4),
            "hit_at_5": round(hits_at_5 / n, 4),
            "mrr": round(reciprocal_rank / n, 4),
            "recall_at_5": round(recall_sum / n, 4),
        },
        "per_query": per_query,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Honest retrieval evaluation")
    parser.add_argument("--check", action="store_true", help="exit 1 below the gate")
    args = parser.parse_args(argv)

    upper = evaluate(include_docstring=True)
    held_out = evaluate(include_docstring=False)

    report = {
        "corpus": upper["corpus"],
        # Docstrings are part of the indexed text here, so the query is a
        # subset of the document: this is an UPPER BOUND, not an estimate.
        "docstrings_indexed_upper_bound": upper["metrics"],
        # Docstrings are held out of the index: this is the honest number.
        "docstrings_held_out": held_out["metrics"],
        "upper_bound_per_query": upper["per_query"],
        "held_out_per_query": held_out["per_query"],
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")

    corpus = report["corpus"]
    print(
        f"corpus: {corpus['functions']} functions, {corpus['edges']} undirected edges, "
        f"{corpus['queries']} queries"
    )
    print("  mode                     hit@1    hit@5      MRR   recall@5")
    for label, metrics in (
        ("docstrings indexed (upper)", report["docstrings_indexed_upper_bound"]),
        ("docstrings held out (real)", report["docstrings_held_out"]),
    ):
        print(
            f"  {label:26s} {metrics['hit_at_1']:.4f}  {metrics['hit_at_5']:.4f}  "
            f"{metrics['mrr']:.4f}  {metrics['recall_at_5']:.4f}"
        )

    if args.check and report["docstrings_held_out"]["hit_at_5"] < 0.50:
        print("FAIL: held-out hit@5 below 0.50")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
