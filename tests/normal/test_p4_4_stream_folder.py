"""
Normal Tests for Phase 4.4: DBP (bit-parallel / SWAR) Terminal Stream Folder.
"""

from onyxsi.gateway.stream_folder import StreamFolder


def test_stream_folder_5000_line_build_log_compression():
    folder = StreamFolder()
    lines = []
    for i in range(4850):
        lines.append(f"[INFO] Compiling module_{i}.c -> build/obj_{i}.o (0.02s)")
    lines.append("Traceback (most recent call last):")
    lines.append('  File "tests/test_core.py", line 142, in test_invariant')
    lines.append("AssertionError: Expected value 42, got 0")
    for i in range(4853, 5000):
        lines.append(f"[INFO] Cleaning temp artifact_{i}.tmp")

    raw_log = "\n".join(lines)
    res = folder.fold_stream(raw_log, stream_id="build_01")

    assert res.reduction_ratio >= 0.95
    assert res.handle_uri.startswith("ref://stream_sha256_")
    assert "AssertionError: Expected value 42, got 0" in res.folded_text
    assert 'File "tests/test_core.py", line 142' in res.folded_text

    # Verify full stream is recoverable from registry
    recovered = folder.registry.resolve_text(res.handle_uri)
    assert len(recovered.splitlines()) == len(lines)
