"""
OnyxSi Phase 6.2: Vectorless Compressed Sparse Row (CSR) Graph & Tuned BM25 Search.
Builds a high-performance code relationship graph (CALLS, IMPORTS, DEFINES, TESTS)
using Structure of Arrays (array('I'), array('B')) with a tuned BM25 (k1=1.5, b=0.75)
and 2-hop BFS hybrid search (0.65 * BM25 + 0.35 * Proximity) without vector databases.
100% Python Standard Library only. Zero External Dependencies. Zero UI.
"""

import math
import re
import array
import heapq
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple


# Edge type constants
EDGE_CALLS = 1
EDGE_CALLED_BY = 2
EDGE_IMPORTS = 3
EDGE_IMPORTED_BY = 4
EDGE_DEFINES = 5
EDGE_TESTS = 6
EDGE_TESTED_BY = 7

EDGE_TYPE_NAMES = {
    EDGE_CALLS: "CALLS",
    EDGE_CALLED_BY: "CALLED_BY",
    EDGE_IMPORTS: "IMPORTS",
    EDGE_IMPORTED_BY: "IMPORTED_BY",
    EDGE_DEFINES: "DEFINES",
    EDGE_TESTS: "TESTS",
    EDGE_TESTED_BY: "TESTED_BY",
}


def tokenize_code(text: str) -> List[str]:
    """
    Splits identifiers into constituent words, handling:
      - snake_case (e.g. process_manager -> process, manager)
      - camelCase / PascalCase (e.g. CSRGraphBM25 -> csr, graph, bm, 25)
      - digits and special characters
    """
    if not text:
        return []
    
    # First split on non-alphanumeric
    raw_tokens = re.findall(r"[A-Za-z0-9]+", text)
    tokens: List[str] = []
    
    for tok in raw_tokens:
        # Split camelCase / PascalCase
        sub = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[0-9]+", tok)
        if sub:
            for s in sub:
                tokens.append(s.lower())
        else:
            tokens.append(tok.lower())
            
    return tokens


class CodeCSRGraph:
    """
    Memory-compact Compressed Sparse Row (CSR) Graph representation using
    Structure of Arrays (SoA) with native array('I') and array('B').
    Maintains both forward and reverse CSR indexes for ultra-fast bidirectional traversal.
    """

    def __init__(self):
        self.node_to_id: Dict[str, int] = {}
        self.id_to_node: List[str] = []
        
        # Intermediate adjacency list before baking into CSR
        self._fwd_adj: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
        self._rev_adj: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
        
        # Frozen CSR arrays
        self.is_baked: bool = False
        self.row_ptr: array.array = array.array("I")
        self.col_idx: array.array = array.array("I")
        self.edge_type: array.array = array.array("B")
        
        self.reverse_row_ptr: array.array = array.array("I")
        self.reverse_col_idx: array.array = array.array("I")
        self.reverse_edge_type: array.array = array.array("B")

    def get_or_create_node(self, name: str) -> int:
        clean_name = str(name).strip()
        if clean_name in self.node_to_id:
            return self.node_to_id[clean_name]
        
        node_id = len(self.id_to_node)
        self.node_to_id[clean_name] = node_id
        self.id_to_node.append(clean_name)
        self.is_baked = False
        return node_id

    def add_edge(self, u_name: str, v_name: str, edge_type: int = EDGE_CALLS) -> None:
        u = self.get_or_create_node(u_name)
        v = self.get_or_create_node(v_name)
        
        self._fwd_adj[u].append((v, edge_type))
        self._rev_adj[v].append((u, edge_type))
        self.is_baked = False

    def __len__(self) -> int:
        return len(self.id_to_node)

    def num_nodes(self) -> int:
        return len(self.id_to_node)

    def bake(self) -> None:
        """Bakes adjacency lists into contiguous CSR SoA arrays.

        No-op when the topology has not changed since the last bake: repeated
        ``prepare()`` calls (one per search) must not rebuild O(V+E) arrays.
        """
        if self.is_baked:
            return
        n = len(self.id_to_node)
        
        # Forward CSR
        self.row_ptr = array.array("I", [0] * (n + 1))
        self.col_idx = array.array("I")
        self.edge_type = array.array("B")
        
        total_edges = 0
        for i in range(n):
            self.row_ptr[i] = total_edges
            edges = self._fwd_adj.get(i, [])
            for target, etype in edges:
                self.col_idx.append(target)
                self.edge_type.append(etype)
                total_edges += 1
        self.row_ptr[n] = total_edges

        # Reverse CSR
        self.reverse_row_ptr = array.array("I", [0] * (n + 1))
        self.reverse_col_idx = array.array("I")
        self.reverse_edge_type = array.array("B")
        
        rev_edges = 0
        for i in range(n):
            self.reverse_row_ptr[i] = rev_edges
            r_edges = self._rev_adj.get(i, [])
            for source, etype in r_edges:
                self.reverse_col_idx.append(source)
                self.reverse_edge_type.append(etype)
                rev_edges += 1
        self.reverse_row_ptr[n] = rev_edges
        
        self.is_baked = True

    def get_forward_neighbors(self, node_id: int) -> List[Tuple[int, int]]:
        if not self.is_baked:
            self.bake()
        if node_id >= len(self.id_to_node):
            return []
        
        start = self.row_ptr[node_id]
        end = self.row_ptr[node_id + 1]
        results = []
        for idx in range(start, end):
            results.append((self.col_idx[idx], self.edge_type[idx]))
        return results

    def get_reverse_neighbors(self, node_id: int) -> List[Tuple[int, int]]:
        if not self.is_baked:
            self.bake()
        if node_id >= len(self.id_to_node):
            return []
        
        start = self.reverse_row_ptr[node_id]
        end = self.reverse_row_ptr[node_id + 1]
        results = []
        for idx in range(start, end):
            results.append((self.reverse_col_idx[idx], self.reverse_edge_type[idx]))
        return results

    def bfs_neighbors(self, start_id: int, max_hops: int = 2) -> Dict[int, int]:
        """
        Runs BFS outward up to max_hops with bitset/set tracking to prevent cyclic traversal.
        Returns mapping of node_id -> hop_distance.
        """
        if not self.is_baked:
            self.bake()
            
        visited: Dict[int, int] = {start_id: 0}
        queue: deque = deque([(start_id, 0)])
        
        while queue:
            curr_id, hops = queue.popleft()
            if hops >= max_hops:
                continue
                
            # Both forward and reverse edges contribute to proximity
            fwd = self.get_forward_neighbors(curr_id)
            rev = self.get_reverse_neighbors(curr_id)
            
            for nxt_id, _ in fwd + rev:
                if nxt_id not in visited:
                    visited[nxt_id] = hops + 1
                    queue.append((nxt_id, hops + 1))
                    
        return visited


class TunedBM25Index:
    """
    Deterministic Okapi BM25 index tuned for source code retrieval (k1=1.5, b=0.75).
    Index operates over string representations / signatures / docstrings of nodes.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_len: List[int] = []
        self.avg_doc_len: float = 0.0
        self.total_docs: int = 0

        # Term -> list of (doc_id, tf)
        self.inverted_index: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        self.doc_frequencies: Dict[str, int] = defaultdict(int)
        # Per-document term postings, so re-indexing a doc_id replaces instead
        # of appending duplicates (duplicates inflate df and can zero the IDF).
        self._doc_terms: Dict[int, set] = {}
        self._dirty = True

    def add_document(self, doc_id: int, text: str) -> None:
        """Adds or *replaces* a document, keeping the postings duplicate-free."""
        tokens = tokenize_code(text)
        length = len(tokens)
        self._dirty = True

        # Ensure array size matches doc_id
        while len(self.doc_len) <= doc_id:
            self.doc_len.append(0)

        # Drop any previous postings for this doc_id.
        for old_term in self._doc_terms.get(doc_id, set()):
            postings = self.inverted_index.get(old_term)
            if postings is None:
                continue
            kept = [p for p in postings if p[0] != doc_id]
            if kept:
                self.inverted_index[old_term] = kept
            else:
                del self.inverted_index[old_term]

        self.doc_len[doc_id] = length
        self.total_docs = max(self.total_docs, doc_id + 1)

        term_counts: Dict[str, int] = defaultdict(int)
        for t in tokens:
            term_counts[t] += 1

        for term, count in term_counts.items():
            self.inverted_index[term].append((doc_id, count))

        self._doc_terms[doc_id] = set(term_counts)

    def build(self) -> None:
        """Recomputes corpus statistics from the postings (authoritative, not incremental)."""
        if not self._dirty:
            return
        if self.total_docs > 0:
            self.avg_doc_len = sum(self.doc_len) / float(self.total_docs)
        else:
            self.avg_doc_len = 0.0

        # df must equal the number of *distinct* documents containing the term.
        self.doc_frequencies = defaultdict(int)
        for term, postings in self.inverted_index.items():
            self.doc_frequencies[term] = len({doc_id for doc_id, _ in postings})
        self._dirty = False

    def score(self, query: str) -> Dict[int, float]:
        """Calculates BM25 score for all matching documents."""
        q_tokens = tokenize_code(query)
        scores: Dict[int, float] = {}

        if self.total_docs == 0 or self.avg_doc_len == 0.0 or not q_tokens:
            return scores

        # Hoist everything that is constant across postings: this loop visits
        # every (term, document) pair and used to do 5 attribute lookups each.
        inverted = self.inverted_index
        doc_len = self.doc_len
        doc_freq = self.doc_frequencies
        k1 = self.k1
        k1_plus_1 = k1 + 1.0
        k1_minus_b = k1 * (1.0 - self.b)
        length_norm = k1 * self.b / self.avg_doc_len
        total_docs = self.total_docs
        log = math.log

        for term in q_tokens:
            postings = inverted.get(term)
            if not postings:
                continue

            n = doc_freq[term]
            # Robertson-Spärck Jones IDF with smoothing
            idf = log((total_docs - n + 0.5) / (n + 0.5) + 1.0)
            if idf < 0.0:
                idf = 0.0

            for doc_id, tf in postings:
                denominator = tf + k1_minus_b + length_norm * doc_len[doc_id]
                term_score = idf * ((tf * k1_plus_1) / denominator)
                scores[doc_id] = scores.get(doc_id, 0.0) + term_score

        return scores


class HybridSearchEngine:
    """
    Unified Hybrid Search Engine:
      HybridScore = 0.65 * NormalizedBM25 + 0.35 * GraphProximity
    Provides < 2ms semantic/structural search across large codebases without vector embeddings.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.graph = CodeCSRGraph()
        self.bm25 = TunedBM25Index(k1=k1, b=b)
        self.node_text: Dict[int, str] = {}

    def index_node(self, name: str, text: str) -> int:
        node_id = self.graph.get_or_create_node(name)
        full_text = f"{name} {text}"
        self.node_text[node_id] = full_text
        self.bm25.add_document(node_id, full_text)
        return node_id

    def add_relation(self, source: str, target: str, edge_type: int = EDGE_CALLS) -> None:
        self.graph.add_edge(source, target, edge_type)

    def prepare(self) -> None:
        self.graph.bake()
        self.bm25.build()

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        Executes hybrid search combining BM25 keyword matching and 2-hop CSR graph expansion.
        Returns top_k (node_name, hybrid_score) sorted descending.
        """
        self.prepare()
        bm25_scores = self.bm25.score(query)
        if not bm25_scores:
            return []

        # Normalize BM25 scores to [0.0, 1.0] without materialising a second
        # 5k-entry dict (we only ever divide by the max).
        max_bm25 = max(bm25_scores.values())
        if max_bm25 <= 0.0:
            max_bm25 = 1.0
        inv_max = 1.0 / max_bm25

        # Top 3 seed nodes (nlargest avoids sorting the whole corpus twice).
        seeds = heapq.nlargest(3, bm25_scores.items(), key=lambda kv: kv[1])

        proximity_scores: Dict[int, float] = {}
        for seed_id, seed_weight in seeds:
            normalized_weight = seed_weight * inv_max
            # 2-hop BFS on CSR graph
            hops_map = self.graph.bfs_neighbors(seed_id, max_hops=2)
            for neighbor_id, hops in hops_map.items():
                if hops == 0:
                    score = 1.0
                elif hops == 1:
                    score = 0.75
                elif hops == 2:
                    score = 0.40
                else:
                    continue
                candidate = score * normalized_weight
                if candidate > proximity_scores.get(neighbor_id, 0.0):
                    proximity_scores[neighbor_id] = candidate

        # Combine scores. Candidates are only the nodes BM25 hit or the graph
        # reached, so untouched nodes are never scored.
        id_to_node = self.graph.id_to_node

        def hybrid_score(cid: int) -> float:
            return (
                0.65 * (bm25_scores.get(cid, 0.0) * inv_max)
                + 0.35 * proximity_scores.get(cid, 0.0)
            )

        candidates = set(bm25_scores)
        candidates.update(proximity_scores)
        top = heapq.nlargest(top_k, candidates, key=lambda cid: (hybrid_score(cid), -cid))
        return [(id_to_node[cid], round(hybrid_score(cid), 4)) for cid in top]
