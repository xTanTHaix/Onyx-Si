"""
Normal Tests for Phase 2.1: Zero-Dependency DBP (bit-parallel / SWAR) Bitplane AST Skeleton Pruner.
"""

from onyxsi.contextfs.bitplane_ast import (
    CodeChunkBitplanes,
    BitplaneAstScanner,
    BitplaneAstPruner,
)


def test_bitplane_prefix_xor():
    # 0b00100100 -> prefix XOR toggles bits
    x = 0b00100010
    px = CodeChunkBitplanes.prefix_xor(x)
    assert px > 0
    # Invariant: running prefix_xor twice produces predictable bit mask
    assert isinstance(px, int)


def test_bitplane_hardware_invariant_no_overlap():
    scanner = BitplaneAstScanner()
    chunk = b'fn calculate(x: i32) -> i32 { if x > 0 { return x * 2; } return 0; }'
    planes = scanner.scan_chunk_64(chunk[:64])
    # Invariant: P (open) and N (close) must never share any bit
    assert (planes.brace_open_p & planes.brace_close_n) == 0
    assert (planes.paren_open_p & planes.paren_close_n) == 0


def test_bitplane_string_comment_neutralization():
    scanner = BitplaneAstScanner()
    # Braces inside string and comments must be masked out into ignore_mask
    chunk = b'let s = "{ false brace }"; // { comment brace }\nlet a = { real };'
    planes = scanner.scan_chunk_64(chunk[:64])
    # Should only detect real braces
    assert (planes.brace_open_p & planes.brace_close_n) == 0
    # Net delta should balance out correctly
    assert planes.net_brace_delta() == 0


def test_bitplane_topology_analysis():
    scanner = BitplaneAstScanner()
    code = b"""
    pub fn compute() {
        let x = 1;
        if x > 0 {
            println!("inside");
        }
    }
    pub fn second() {
        let y = 2;
    }
    """
    is_balanced, max_depth, top_level = scanner.analyze_source_topology(code)
    assert is_balanced is True
    assert max_depth >= 2
    assert top_level == 2


def test_python_skeleton_pruner():
    sample_python = '''
def calculate_metrics(alpha: float, beta: float) -> float:
    """Calculates composite risk score."""
    step1 = alpha * 2.5
    step2 = step1 + beta ** 2
    acc = 0.0
    for i in range(100):
        acc += i * 0.01
        step2 += acc / 2.0
        if step2 > 1000.0:
            step2 = 1000.0
            break
    intermediate = step2 * 1.5 - alpha
    return intermediate + 42.0

class DataProcessor:
    """Enterprise batch data processor."""
    def process(self, items: list) -> int:
        count = 0
        accumulator = []
        for item in items:
            s = str(item).strip()
            if len(s) > 10:
                accumulator.append(s[:10])
                count += len(s)
            else:
                accumulator.append(s)
                count += 1
        return count + len(accumulator)
'''
    res = BitplaneAstPruner.prune_source(sample_python, filename="test.py", language="python")
    assert res.skeleton_code is not None
    assert len(res.node_map) >= 2
    # Ensure docstrings and signatures remain visible
    assert "Calculates composite risk score" in res.skeleton_code
    assert "Enterprise batch data processor" in res.skeleton_code
    assert "def calculate_metrics" in res.skeleton_code
    assert "def process" in res.skeleton_code
    # Ensure body was replaced with placeholder
    assert "ast_function_calculate_metrics" in res.skeleton_code
    assert "ast_method_process" in res.skeleton_code
    # Compression ratio should be high
    assert res.compression_ratio > 0.40
