"""
Differential fuzzing for the bitplane scanner.

There are two production paths and one oracle:

* ``scan_chunk_64`` — the 64-byte Dual-Bitplane streaming scanner.
* ``_analyze_whole_buffer`` — the memchr event scan used by
  ``analyze_source_topology`` for inputs of 64 bytes or more.
* ``ReferenceScanner`` — a literal copy of the original per-byte state machine.

The optimized paths are only safe if they are *bit-for-bit* identical to the
oracle, so this module fuzzes both against it. Any divergence (ignored mask,
brace/paren/bracket planes, cross-chunk carry, balance result) fails the test.
"""

import random

import pytest

from onyxsi.contextfs.bitplane_ast import (
    MASK64,
    BitplaneAstScanner,
    CodeChunkBitplanes,
)

ALPHABET = b"{}()[]\"'\\/#*\n \t aZ09+-=;,._"


class ReferenceScanner:
    """Verbatim O(n) per-byte implementation kept as a test oracle."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.in_string_carry = False
        self.string_char_carry = 0
        self.in_block_comment_carry = False
        self.in_line_comment = False
        self.prev_escaped_carry = False
        self.prev_byte = 0
        self.pending_slash = False

    def scan_chunk_64(self, chunk):
        raw_quote_toggles = 0
        raw_string_span = 0
        raw_brace_open = 0
        raw_brace_close = 0
        raw_paren_open = 0
        raw_paren_close = 0
        raw_bracket_open = 0
        raw_bracket_close = 0
        raw_comments = 0

        escaped = self.prev_escaped_carry
        in_string = self.in_string_carry
        string_char = self.string_char_carry
        in_block_comment = self.in_block_comment_carry
        in_line_comment = self.in_line_comment
        # Mirrors the production fix: an escaped trailing '/' is a literal.
        last_byte_escaped = False

        n = len(chunk)
        for i in range(n):
            byte = chunk[i]
            bit = 1 << i

            if i == 0 and self.pending_slash:
                self.pending_slash = False
                if byte == 0x2F:
                    in_line_comment = True
                    raw_comments |= bit
                    continue
                if byte == 0x2A:
                    in_block_comment = True
                    raw_comments |= bit
                    continue

            if in_line_comment:
                if byte == 0x0A:
                    in_line_comment = False
                else:
                    raw_comments |= bit
                    continue

            if in_block_comment:
                closes_here = byte == 0x2F and (
                    (i > 0 and chunk[i - 1] == 0x2A)
                    or (i == 0 and self.prev_byte == 0x2A)
                )
                raw_comments |= bit
                if closes_here:
                    in_block_comment = False
                continue

            if in_string:
                raw_string_span |= bit
                if escaped:
                    escaped = False
                    continue
                if byte == 0x5C:
                    escaped = True
                elif byte == string_char:
                    raw_quote_toggles |= bit
                    in_string = False
                    string_char = 0
                continue

            if escaped:
                escaped = False
                last_byte_escaped = i == n - 1
                continue

            if byte == 0x5C:
                escaped = True
                continue

            if byte in (0x22, 0x27):
                raw_quote_toggles |= bit
                raw_string_span |= bit
                in_string = True
                string_char = byte
                continue

            if byte == 0x2F and i + 1 < n and chunk[i + 1] == 0x2F:
                in_line_comment = True
                raw_comments |= bit
                continue

            if byte == 0x23:
                in_line_comment = True
                raw_comments |= bit
                continue

            if byte == 0x2F and i + 1 < n and chunk[i + 1] == 0x2A:
                in_block_comment = True
                raw_comments |= bit
                continue

            if byte == 0x7B:
                raw_brace_open |= bit
            elif byte == 0x7D:
                raw_brace_close |= bit
            elif byte == 0x28:
                raw_paren_open |= bit
            elif byte == 0x29:
                raw_paren_close |= bit
            elif byte == 0x5B:
                raw_bracket_open |= bit
            elif byte == 0x5D:
                raw_bracket_close |= bit

        prev_in_string = self.in_string_carry
        self.prev_escaped_carry = escaped
        self.in_string_carry = in_string
        self.string_char_carry = string_char
        self.in_block_comment_carry = in_block_comment
        self.in_line_comment = in_line_comment
        if n:
            self.prev_byte = chunk[n - 1]
            self.pending_slash = (
                chunk[n - 1] == 0x2F
                and not last_byte_escaped
                and not in_string
                and not in_line_comment
                and not in_block_comment
            )

        if raw_quote_toggles == 0 and not prev_in_string:
            xor_mask = 0
        else:
            xor_mask = CodeChunkBitplanes.prefix_xor(raw_quote_toggles)
            if prev_in_string:
                xor_mask = (~xor_mask) & ((1 << n) - 1)
        ignore_mask = (xor_mask | raw_string_span | raw_quote_toggles | raw_comments) & MASK64

        return CodeChunkBitplanes(
            quote_toggles=raw_quote_toggles,
            ignore_mask=ignore_mask,
            brace_open_p=(raw_brace_open & ~ignore_mask) & MASK64,
            brace_close_n=(raw_brace_close & ~ignore_mask) & MASK64,
            paren_open_p=(raw_paren_open & ~ignore_mask) & MASK64,
            paren_close_n=(raw_paren_close & ~ignore_mask) & MASK64,
            bracket_open_p=(raw_bracket_open & ~ignore_mask) & MASK64,
            bracket_close_n=(raw_bracket_close & ~ignore_mask) & MASK64,
        )

    def analyze_source_topology(self, source):
        self.reset()
        current_depth = 0
        max_depth = 0
        top_level_blocks = 0
        for offset in range(0, len(source), 64):
            planes = self.scan_chunk_64(source[offset:offset + 64])
            combined = planes.brace_open_p | planes.brace_close_n
            while combined:
                bit = 1 << ((combined & -combined).bit_length() - 1)
                if planes.brace_open_p & bit:
                    current_depth += 1
                    max_depth = max(max_depth, current_depth)
                else:
                    current_depth -= 1
                    if current_depth < 0:
                        return False, max_depth, top_level_blocks
                    if current_depth == 0:
                        top_level_blocks += 1
                combined &= combined - 1
        balanced = (
            current_depth == 0
            and not self.in_string_carry
            and not self.in_block_comment_carry
        )
        return balanced, max_depth, top_level_blocks


def _planes(planes):
    return (
        planes.ignore_mask,
        planes.brace_open_p,
        planes.brace_close_n,
        planes.paren_open_p,
        planes.paren_close_n,
        planes.bracket_open_p,
        planes.bracket_close_n,
    )


def _random_source(rng, size):
    return bytes(rng.choice(ALPHABET) for _ in range(size))


@pytest.mark.parametrize("seed", range(120))
def test_scanner_matches_reference_on_fuzz(seed):
    rng = random.Random(seed)
    size = rng.randint(0, 600)
    source = _random_source(rng, size)

    fast = BitplaneAstScanner()
    ref = ReferenceScanner()

    for offset in range(0, len(source), 64):
        chunk = source[offset:offset + 64]
        got = fast.scan_chunk_64(chunk)
        want = ref.scan_chunk_64(chunk)
        assert _planes(got) == _planes(want), f"seed={seed} offset={offset} chunk={chunk!r}"

    assert fast.in_string_carry == ref.in_string_carry
    assert fast.in_block_comment_carry == ref.in_block_comment_carry
    assert fast.in_line_comment == ref.in_line_comment
    assert fast.prev_escaped_carry == ref.prev_escaped_carry
    assert fast.prev_byte == ref.prev_byte
    assert fast.pending_slash == ref.pending_slash


@pytest.mark.parametrize("seed", range(60))
def test_topology_result_matches_reference(seed):
    rng = random.Random(10_000 + seed)
    source = _random_source(rng, rng.randint(0, 900))

    assert BitplaneAstScanner().analyze_source_topology(source) == ReferenceScanner().analyze_source_topology(source)


def test_split_delimiters_across_chunks_are_identical():
    """The hard cases: '/', '*', quotes and escapes straddling a 64-byte edge."""
    payloads = [
        b"x" * 63 + b"/" + b"/comment\n" + b"y" * 10,
        b"x" * 63 + b"/" + b"*comment*/" + b"y" * 10,
        b"x" * 62 + b"*" + b"/" + b"z" * 10,
        b"x" * 63 + b"\\" + b"'" + b"q'" + b"z" * 10,
        b"'" + b"a" * 70 + b"'" + b"{" + b"}",
        b'"' + b"a\\" + b'"' * 3 + b"{}",
    ]
    for source in payloads:
        fast = BitplaneAstScanner()
        ref = ReferenceScanner()
        for offset in range(0, len(source), 64):
            chunk = source[offset:offset + 64]
            assert _planes(fast.scan_chunk_64(chunk)) == _planes(ref.scan_chunk_64(chunk)), chunk
        assert fast.in_string_carry == ref.in_string_carry
        assert fast.in_block_comment_carry == ref.in_block_comment_carry


def test_reference_scanner_method_is_used():
    """Guard against accidental reference drift: it must expose the API it mirrors."""
    ref = ReferenceScanner()
    assert hasattr(ref, "analyze_source_topology")


# --- Whole-buffer (memchr event scan) differential coverage -----------------

WHOLE_BUFFER_CASES = [
    b"",
    b"{",
    b"}",
    b"{}",
    b"{{{}}}",
    b"}{",
    b"'unterminated {",
    b'"unterminated {',
    b"'a\\'b'{}",                      # escaped quote does not close
    b'"a\\\\"{}',                      # escaped backslash, then a real close
    b"'a\\",                           # backslash swallows EOF
    b"/* unterminated {",
    b"/*{*/}",
    b"/*/}{",                          # overlapping opener/closer: "/*/"
    b"/**/{}",
    b"/***/{}",
    b"// line { comment\n{}",
    b"// no newline {",
    b"# python { comment\n{}",
    b"# no newline {",
    b"'#' {'",
    b"'//' {}",
    b"'/*' {}",
    b'"#{}"',
    b"\\{ }",                          # escaped brace is not structural
    b"\\{}",
    b"\\\\{}",                         # escaped backslash, then a brace
    b"a\\\n{}",                        # line continuation
    b"a\\//{b}\n{c}",                  # escaped slash must not open a comment
    b"\n".join([b"def f():", b"    return {'a': 1}", b"class C:", b"    pass"]),
    b"fn main() { let s = \"}{\"; /* } */ }",
    b"x" * 62 + b"\\//{y}\nz\n",       # escaped slash at a 64-byte edge
    b"x" * 63 + b"/" + b"/{y}\nz\n",   # "//" split across the 64-byte edge
    b"x" * 63 + b"/" + b"*{y}*/z\n",   # "/*" split across the 64-byte edge
    b"x" * 62 + b"*" + b"/{y}",        # "*/" split across the 64-byte edge
    b"x" * 63 + b"\\" + b"'{y}'",      # escaped quote split across the edge
    b"'" + b"a" * 70 + b"'{}",
    b'"' + b"a\\" + b'"' * 3 + b"{}",
    b"{" * 64 + b"}" * 64,
    b"{" * 65 + b"}" * 65,
]


@pytest.mark.parametrize("source", WHOLE_BUFFER_CASES, ids=range(len(WHOLE_BUFFER_CASES)))
def test_whole_buffer_matches_reference_on_targeted_cases(source):
    fast = BitplaneAstScanner()
    ref = ReferenceScanner()
    assert fast._analyze_whole_buffer(source) == ref.analyze_source_topology(source)
    assert fast.in_string_carry == ref.in_string_carry
    assert fast.in_block_comment_carry == ref.in_block_comment_carry
    assert fast.in_line_comment == ref.in_line_comment
    assert fast.prev_byte == ref.prev_byte
    assert fast.pending_slash == ref.pending_slash


@pytest.mark.parametrize("seed", range(80))
def test_whole_buffer_matches_reference_on_fuzz(seed):
    rng = random.Random(50_000 + seed)
    size = rng.choice([0, 1, 2, 63, 64, 65, 127, 128, 129, 191, 192, 193, rng.randint(0, 2400)])
    source = _random_source(rng, size)
    assert BitplaneAstScanner()._analyze_whole_buffer(source) == ReferenceScanner().analyze_source_topology(source)


@pytest.mark.parametrize("source", WHOLE_BUFFER_CASES, ids=range(len(WHOLE_BUFFER_CASES)))
def test_whole_buffer_agrees_with_chunked_path(source):
    """Both dispatch arms of analyze_source_topology must give the same triple."""
    scanner = BitplaneAstScanner()
    assert scanner._analyze_whole_buffer(source) == scanner._analyze_chunked(source)


@pytest.mark.parametrize("seed", range(40))
def test_topology_dispatch_covers_both_paths(seed):
    rng = random.Random(70_000 + seed)
    small = _random_source(rng, rng.randint(0, 63))
    large = _random_source(rng, rng.randint(64, 1500))
    assert BitplaneAstScanner().analyze_source_topology(small) == ReferenceScanner().analyze_source_topology(small)
    assert BitplaneAstScanner().analyze_source_topology(large) == ReferenceScanner().analyze_source_topology(large)
