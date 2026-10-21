"""
Normal Tests for Phase 7.3: practical index ceilings.

The search index used to be built once and then served stale results for the
rest of the process lifetime. These tests pin the two properties that make the
ceiling usable in practice:

1. A changed workspace triggers a rebuild (no restart required).
2. The file-count and file-size ceilings are configurable instead of hardcoded.
"""

import json
import os
import tempfile

from onyxsi.server import OnyxSiServer


def _bm25(server: OnyxSiServer, query: str):
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "graph_compute",
            "params": {"engine": "bm25", "query": query},
        }
    )
    response = json.loads(server.process_line(request))
    return response["result"]


def test_index_rebuilds_when_a_file_changes():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "mod.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("def alpha_symbol():\n    return 1\n")

        server = OnyxSiServer(workspace_root=tmpdir)
        first = _bm25(server, "alpha_symbol")
        assert first["indexed_files"] == 1
        assert any("alpha_symbol" in name for name, _score in first["results"])

        # Rewrite the file: the next query must see the new symbol, not the
        # stale graph captured on the first call.
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("def beta_symbol():\n    return 2\n")
        os.utime(path, None)

        second = _bm25(server, "beta_symbol")
        names = [name for name, _score in second["results"]]
        assert any("beta_symbol" in name for name in names)
        assert not any("alpha_symbol" in name for name in names)
        assert second["indexed_files"] == 1
        server.shutdown()


def test_index_file_ceiling_is_configurable():
    with tempfile.TemporaryDirectory() as tmpdir:
        for index in range(5):
            with open(
                os.path.join(tmpdir, f"m{index}.py"), "w", encoding="utf-8"
            ) as fh:
                fh.write(f"def symbol_{index}():\n    return {index}\n")

        server = OnyxSiServer(workspace_root=tmpdir, max_index_files=2)
        result = _bm25(server, "symbol")
        assert result["indexed_files"] == 2
        assert server.max_index_files == 2
        server.shutdown()


def test_index_skips_files_over_the_byte_ceiling():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "small.py"), "w", encoding="utf-8") as fh:
            fh.write("def small_symbol():\n    return 1\n")
        with open(os.path.join(tmpdir, "big.py"), "w", encoding="utf-8") as fh:
            fh.write("def big_symbol():\n    return 1\n" + "#" * 5000)

        server = OnyxSiServer(workspace_root=tmpdir, max_index_file_bytes=2048)
        result = _bm25(server, "small_symbol")
        assert result["indexed_files"] == 1
        server.shutdown()


def test_default_index_ceiling_covers_large_repos():
    """Users should not need a flag for a normal monorepo."""
    with tempfile.TemporaryDirectory() as tmpdir:
        server = OnyxSiServer(workspace_root=tmpdir)
        assert server.max_index_files == 5000
        assert server.max_index_file_bytes == 2 * 1024 * 1024
        server.shutdown()
