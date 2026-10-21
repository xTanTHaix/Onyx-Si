"""
Normal Tests for Phase 7.7: incremental index cache + radix symbol lookup.

Pins three behaviours:
  * unchanged files are not re-parsed on rebuild (per-file mtime/size cache)
  * the radix trie answers exact and prefix symbol lookups
  * the total-bytes budget stops indexing instead of reading an unbounded repo
"""

import json
import os
import tempfile

from onyxsi.server import OnyxSiServer


def _call(server: OnyxSiServer, method: str, params: dict):
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    return json.loads(server.process_line(request))["result"]


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_rebuild_reparses_only_the_changed_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = []
        for name in ("one.py", "two.py", "three.py"):
            path = os.path.join(tmpdir, name)
            _write(path, f"def {name[:-3]}_func():\n    return 1\n")
            paths.append(path)

        server = OnyxSiServer(workspace_root=tmpdir)
        first = _call(server, "vfs_inspect", {"action": "symbols", "query": "one_func"})
        assert first["indexed_files"] == 3
        assert first["parsed_files"] == 3
        assert first["cached_files"] == 0

        _write(paths[0], "def one_func():\n    return 2\n\ndef extra():\n    return 3\n")
        stat = os.stat(paths[0])
        os.utime(paths[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

        second = _call(server, "vfs_inspect", {"action": "symbols", "query": "one_func"})
        assert second["indexed_files"] == 3
        assert second["parsed_files"] == 1
        assert second["cached_files"] == 2

        extra = _call(server, "vfs_inspect", {"action": "symbols", "query": "extra"})
        assert extra["count"] == 1
        server.shutdown()


def test_exact_and_prefix_symbol_lookup():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            os.path.join(tmpdir, "helpers.py"),
            "def alpha_one():\n    return 1\n\n"
            "def alpha_two():\n    return 2\n\n"
            "class Beta:\n    pass\n",
        )

        server = OnyxSiServer(workspace_root=tmpdir)

        exact = _call(
            server, "vfs_inspect", {"action": "symbols", "query": "alpha_one"}
        )
        assert exact["mode"] == "exact"
        assert exact["count"] == 1
        assert exact["matches"][0]["name"] == "alpha_one"
        assert exact["matches"][0]["files"] == ["helpers.py"]

        prefix = _call(
            server,
            "vfs_inspect",
            {"action": "symbols", "query": "alpha_", "mode": "prefix"},
        )
        names = {match["name"] for match in prefix["matches"]}
        assert names == {"alpha_one", "alpha_two"}

        missing = _call(
            server, "vfs_inspect", {"action": "symbols", "query": "alpha_zzz"}
        )
        assert missing["count"] == 0
        server.shutdown()


def test_total_index_budget_stops_the_scan():
    with tempfile.TemporaryDirectory() as tmpdir:
        body = "def f():\n    return 1\n" + "# padding\n" * 60
        _write(os.path.join(tmpdir, "a.py"), body)
        _write(os.path.join(tmpdir, "b.py"), body)

        server = OnyxSiServer(workspace_root=tmpdir, max_index_total_bytes=1024)
        result = _call(server, "vfs_inspect", {"action": "symbols", "query": "f"})
        assert result["indexed_files"] == 1
        server.shutdown()


def test_unicode_identifier_is_indexed_without_crashing_the_trie():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            os.path.join(tmpdir, "thai.py"),
            "def ฟังก์ชันทดสอบ():\n    return 1\n\n\ndef ascii_name():\n    return 2\n",
        )

        server = OnyxSiServer(workspace_root=tmpdir)
        ascii_hit = _call(
            server, "vfs_inspect", {"action": "symbols", "query": "ascii_name"}
        )
        assert ascii_hit["count"] == 1

        thai_hit = _call(
            server, "vfs_inspect", {"action": "symbols", "query": "ฟังก์ชันทดสอบ"}
        )
        assert "error" not in thai_hit
        assert thai_hit["ascii_only"] is True
        assert thai_hit["count"] == 0
        server.shutdown()
