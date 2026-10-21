"""
Compact descriptions of the Fixed 8 Meta-Tool surface.

Kept in a separate module so the token budget of the surface can be measured
in isolation. Everything here is prose only: no behaviour lives in this file.

Budget rule: the JSON of all 8 tool definitions must stay under
``MAX_SCHEMA_TOKENS`` (1,800 tokens at the 4-chars-per-token estimate). The
output-folding ceiling is separate (``TokenGovernor`` defaults to 1,250).
Each description therefore follows a fixed 2-line shape:

    <what it does in one clause>. USE WHEN: <positive trigger>.
    DO NOT USE: <negative trigger>.
"""

from typing import Any, Dict, List


def build_raw_specs() -> List[Dict[str, Any]]:
    """Returns the 8 tool definitions as plain dicts (name/description/input_schema)."""
    return [
        {
            "name": "vfs_inspect",
            "description": (
                "Read-only view: action=tree (dir map), skeleton (AST outline), "
                "read (line range), symbols (exact/prefix symbol lookup), grammar "
                "(GBNF export), resolve (canonical path or ref:// handle). USE WHEN: "
                "orienting, reading code, fetching folded content, finding a symbol, "
                "or checking the tool grammar. DO NOT USE: to write or run tests."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "tree",
                            "skeleton",
                            "read",
                            "symbols",
                            "grammar",
                            "resolve",
                        ],
                    },
                    "target": {"type": "string"},
                    "max_tokens": {"type": "integer", "minimum": 1, "maximum": 1250},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "query": {"type": "string"},
                    "mode": {"type": "string", "enum": ["exact", "prefix"]},
                },
                "required": ["action"],
            },
        },
        {
            "name": "vfs_mutate",
            "description": (
                "Stage a write in the in-RAM Ghost overlay (disk untouched until commit). "
                "op=replace_file (default) | replace_node | insert_after | slice_patch | "
                "merge_patches (non-overlapping agent patches, unified imports). "
                "USE WHEN: applying a speculative code edit. "
                "DO NOT USE: for reads, or before causal_flight pre_flight."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": [
                            "replace_file",
                            "replace_node",
                            "insert_after",
                            "slice_patch",
                            "merge_patches",
                        ],
                    },
                    "path": {"type": "string"},
                    "code": {"type": "string"},
                    "node_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "patches": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["path"],
            },
        },
        {
            "name": "ghost_sandbox",
            "description": (
                "Ghost overlay lifecycle: begin, mutate (path+content), test (blast-radius "
                "selection, or an explicit command run in a shadow copy of the workspace), "
                "commit (atomic, fsync+replace), discard, plus fork / switch_branch / "
                "list_branches / merge_best for in-RAM MCTS branches. USE WHEN: you need "
                "speculative edits, a rollback point, competing fixes, or to run the "
                "project's own tests against staged content. DO NOT USE: before knowing "
                "the blast radius."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "cmd": {
                        "type": "string",
                        "enum": [
                            "begin",
                            "mutate",
                            "test",
                            "commit",
                            "discard",
                            "fork",
                            "switch_branch",
                            "list_branches",
                            "merge_best",
                        ],
                    },
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "command": {"type": "string"},
                    "timeout_s": {"type": "integer", "minimum": 1, "maximum": 3600},
                    "branch_id": {"type": "string"},
                    "from_branch": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "scores": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "branch_id": {"type": "string"},
                                "passed_tests": {"type": "integer", "minimum": 0},
                                "total_tests": {"type": "integer", "minimum": 0},
                                "drift": {"type": "number", "minimum": 0},
                            },
                            "required": ["branch_id"],
                        },
                    },
                },
                "required": ["cmd"],
            },
        },
        {
            "name": "causal_flight",
            "description": (
                "4-stage flight recorder. pre_flight declares blast radius; ghost_burn checks "
                "drift (<15%); verify re-parses staged files; commit logs; reflect resets "
                "the 3-strike budget; bisect finds the culprit checkpoint. "
                "USE WHEN: before/after any non-trivial edit. DO NOT USE: instead of running "
                "the affected tests."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stage": {
                        "type": "string",
                        "enum": [
                            "pre_flight",
                            "ghost_burn",
                            "verify",
                            "commit",
                            "reflect",
                            "bisect",
                        ],
                    },
                    "blast_radius": {"type": "array", "items": {"type": "string"}},
                    "hypothesis": {"type": "string"},
                    "target": {"type": "string"},
                    "root_cause": {"type": "string", "minLength": 10},
                    "strategy": {"type": "string", "minLength": 10},
                },
                "required": ["stage"],
            },
        },
        {
            "name": "hypothesis_branch",
            "description": (
                "Orthogonal hypotheses (Jaccard <= 0.70) with falsification probes. "
                "mode=register|falsify (pass the observed probe_succeeded)|select. "
                "USE WHEN: several plausible root causes to prune. "
                "DO NOT USE: to run the probe."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mode": {"type": "string", "enum": ["register", "falsify", "select"]},
                    "branches": {"type": "array", "items": {"type": "object"}},
                    "hypothesis_id": {"type": "string"},
                    "lesson": {"type": "string"},
                    "probe_succeeded": {"type": "boolean"},
                },
                "required": ["mode"],
            },
        },
        {
            "name": "wiki_task_sync",
            "description": (
                "Persist task state in .onyxsi/wiki/projects/<project>/tasks/TASK-<ID>/ "
                "(spec, progress, log), render AGENT_HANDOFF.md, and write "
                "TIME_TRAVEL_REPLAY.md. op=init|update|handoff|replay. USE WHEN: starting "
                "work, checkpointing, handing off. NOT a scratchpad; it writes real files."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,64}$"},
                    "op": {
                        "type": "string",
                        "enum": ["init", "update", "handoff", "replay"],
                    },
                    "status": {"type": "string"},
                    "spec": {"type": "string"},
                    "project_name": {"type": "string"},
                    "frames": {"type": "array", "items": {"type": "object"}},
                    "branches": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["task_id", "op"],
            },
        },
        {
            "name": "graph_compute",
            "description": (
                "Deterministic local compute. engine=bm25 (CSR+BM25 search) | blast_radius "
                "(select+run impacted tests) | alu (type=eval | matrix_matmul | matrix_inv | "
                "ieee754). USE WHEN: locating code, checking test impact, or arithmetic "
                "without an LLM. DO NOT USE: for free-form reasoning or file edits."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "engine": {"type": "string", "enum": ["bm25", "blast_radius", "alu"]},
                    "query": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["eval", "matrix_matmul", "matrix_inv", "ieee754"],
                    },
                    "matrix_a": {"type": "array"},
                    "matrix_b": {"type": "array"},
                    "matrix": {"type": "array"},
                    "bits": {"type": "integer", "enum": [16, 32, 64]},
                },
                "required": ["engine"],
            },
        },
        {
            "name": "mcp_orchestrate",
            "description": (
                "Run meta-tools as one dependency-ordered DAG; later steps read earlier output "
                "via '$prev.output' or '$<step>.output'. USE WHEN: a fix needs "
                "inspect->mutate->test in one round-trip. DO NOT USE: for one call, parallel "
                "work, or nesting."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {"type": "string"},
                                "tool": {
                                    "type": "string",
                                    "enum": [
                                        "vfs_inspect",
                                        "vfs_mutate",
                                        "ghost_sandbox",
                                        "causal_flight",
                                        "hypothesis_branch",
                                        "wiki_task_sync",
                                        "graph_compute",
                                    ],
                                },
                                "arguments": {"type": "object"},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "tool"],
                        },
                    }
                },
                "required": ["steps"],
            },
        },
    ]
