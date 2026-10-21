"""
Compact Radix Array Symbol Index.
Flat integer array-based Radix/Trie for symbol lookup in O(L) time.
Uses array('I') for memory compactness (<4 bytes per symbol overhead).
"""

import array
import time
from collections import deque
from typing import Optional, List, Dict
from dataclasses import dataclass, field

MAX_SYMBOL_LENGTH = 256
DEFAULT_CAPACITY = 1024


@dataclass(slots=True, frozen=True)
class SymbolEntry:
    name: str
    coordinate: int
    kind: int = 0
    flags: int = 0


@dataclass(slots=True, frozen=True)
class LookupResult:
    found: bool
    entry: Optional[SymbolEntry] = None
    matches: List[SymbolEntry] = field(default_factory=list)
    time_ms: float = 0.0


class CompactRadixSymbolIndex:
    KIND_MODULE = 0
    KIND_CLASS = 1
    KIND_FUNCTION = 2
    KIND_VARIABLE = 3

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._capacity = capacity
        self._symbols: List[SymbolEntry] = []
        self._name_to_idx: Dict[str, int] = {}
        # Flat integer trie: nodes represented as blocks of 256 child node pointers (or 0 if none)
        # Root node is index 0 (spans 0..255)
        self._trie = array.array('I', [0] * 256)
        # Value array mapping node_id -> symbol_id + 1 (0 means no symbol at this node)
        self._node_values = array.array('I', [0])

    def add_symbol(self, name: str, coordinate: int, kind: int = KIND_FUNCTION, flags: int = 0) -> int:
        if len(name) > MAX_SYMBOL_LENGTH:
            raise ValueError(f"Symbol length exceeds max: {len(name)} > {MAX_SYMBOL_LENGTH}")
        if not name.isascii():
            raise ValueError("Symbol name must be ASCII")

        if name in self._name_to_idx:
            return self._name_to_idx[name]

        sym_idx = len(self._symbols)
        entry = SymbolEntry(name=name, coordinate=coordinate, kind=kind, flags=flags)
        self._symbols.append(entry)
        self._name_to_idx[name] = sym_idx

        # Insert into trie
        current_node = 0
        for ch in name:
            ch_code = ord(ch) % 256
            child_node = self._trie[current_node * 256 + ch_code]
            if child_node == 0:
                # Allocate new node
                new_node_id = len(self._trie) // 256
                self._trie.extend([0] * 256)
                self._node_values.append(0)
                self._trie[current_node * 256 + ch_code] = new_node_id
                current_node = new_node_id
            else:
                current_node = child_node

        self._node_values[current_node] = sym_idx + 1
        return sym_idx

    def lookup(self, name: str) -> LookupResult:
        start = time.perf_counter()
        if not name.isascii() or len(name) > MAX_SYMBOL_LENGTH:
            return LookupResult(found=False, time_ms=(time.perf_counter() - start) * 1000)

        current_node = 0
        for ch in name:
            ch_code = ord(ch) % 256
            child_node = self._trie[current_node * 256 + ch_code]
            if child_node == 0:
                return LookupResult(found=False, time_ms=(time.perf_counter() - start) * 1000)
            current_node = child_node

        val = self._node_values[current_node]
        elapsed = (time.perf_counter() - start) * 1000
        if val > 0:
            sym = self._symbols[val - 1]
            return LookupResult(found=True, entry=sym, matches=[sym], time_ms=elapsed)
        return LookupResult(found=False, time_ms=elapsed)

    def prefix_search(self, prefix: str, max_results: int = 50) -> LookupResult:
        start = time.perf_counter()
        current_node = 0
        for ch in prefix:
            ch_code = ord(ch) % 256
            child_node = self._trie[current_node * 256 + ch_code]
            if child_node == 0:
                return LookupResult(found=False, time_ms=(time.perf_counter() - start) * 1000)
            current_node = child_node

        matches: List[SymbolEntry] = []
        # BFS traversal from current_node using O(1) deque operations
        queue = deque([current_node])
        while queue and len(matches) < max_results:
            node = queue.popleft()
            val = self._node_values[node]
            if val > 0:
                matches.append(self._symbols[val - 1])
            base = node * 256
            for c in range(256):
                nxt = self._trie[base + c]
                if nxt != 0:
                    queue.append(nxt)

        elapsed = (time.perf_counter() - start) * 1000
        return LookupResult(found=len(matches) > 0, matches=matches, time_ms=elapsed)
