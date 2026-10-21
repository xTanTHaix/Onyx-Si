"""
OnyxSi Knowledge Subsystem: Task Wiki, CSR Graph, Differential Test Runner, and Deterministic ALU.
100% Python Standard Library only. Zero External Dependencies. Zero UI.
"""

from onyxsi.knowledge.task_wiki import (
    TaskWikiManager,
    TaskWikiError,
    TaskNotFoundError,
    TaskCorruptedError,
)

from onyxsi.knowledge.csr_graph_bm25 import (
    CodeCSRGraph,
    TunedBM25Index,
    HybridSearchEngine,
    tokenize_code,
    EDGE_CALLS,
    EDGE_CALLED_BY,
    EDGE_IMPORTS,
    EDGE_IMPORTED_BY,
    EDGE_DEFINES,
    EDGE_TESTS,
    EDGE_TESTED_BY,
)

from onyxsi.knowledge.blast_test_runner import (
    BlastRadiusTestRunner,
    BlastTestResult,
)

from onyxsi.knowledge.compute_alu import (
    SafeMathEvaluator,
    eval_math,
    MatrixALU,
    IEEE754Inspector,
    ComputeAluError,
    UnsafeMathExpressionError,
    MathEvaluationError,
    DimensionMismatchError,
    SingularMatrixError,
)

__all__ = [
    # Task Wiki
    "TaskWikiManager",
    "TaskWikiError",
    "TaskNotFoundError",
    "TaskCorruptedError",
    # CSR Graph & BM25
    "CodeCSRGraph",
    "TunedBM25Index",
    "HybridSearchEngine",
    "tokenize_code",
    "EDGE_CALLS",
    "EDGE_CALLED_BY",
    "EDGE_IMPORTS",
    "EDGE_IMPORTED_BY",
    "EDGE_DEFINES",
    "EDGE_TESTS",
    "EDGE_TESTED_BY",
    # Blast Test Runner
    "BlastRadiusTestRunner",
    "BlastTestResult",
    # Compute ALU
    "SafeMathEvaluator",
    "eval_math",
    "MatrixALU",
    "IEEE754Inspector",
    "ComputeAluError",
    "UnsafeMathExpressionError",
    "MathEvaluationError",
    "DimensionMismatchError",
    "SingularMatrixError",
]
