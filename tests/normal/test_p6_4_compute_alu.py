"""
Normal Tests for Phase 6.4: Deterministic Compute ALU.
Verifies Safe AST math evaluator, pure-python Matrix ALU, and IEEE-754 bitwise inspector.
"""

import math
from onyxsi.knowledge.compute_alu import (
    eval_math,
    MatrixALU,
    IEEE754Inspector,
    UnsafeMathExpressionError,
    DimensionMismatchError,
    SingularMatrixError,
)


def test_safe_math_evaluator_valid_arithmetic():
    assert eval_math("2 + 3 * 4") == 14
    assert eval_math("(100 - 25) / 5") == 15.0
    assert eval_math("2 ** 8") == 256
    assert eval_math("sqrt(16) + abs(-5)") == 9.0
    assert eval_math("sin(0) + cos(0)") == 1.0


def test_matrix_alu_matmul_transpose_inverse():
    # 2x2 matrix operations
    A = [[1.0, 2.0], [3.0, 4.0]]
    B = [[2.0, 0.0], [1.0, 2.0]]

    # Matmul: [[1*2 + 2*1, 1*0 + 2*2], [3*2 + 4*1, 3*0 + 4*2]] = [[4, 4], [10, 8]]
    C = MatrixALU.matmul(A, B)
    assert C == [[4.0, 4.0], [10.0, 8.0]]

    # Transpose
    AT = MatrixALU.transpose(A)
    assert AT == [[1.0, 3.0], [2.0, 4.0]]

    # Determinant: 1*4 - 2*3 = -2
    detA = MatrixALU.determinant(A)
    assert abs(detA - (-2.0)) < 1e-9

    # Inverse
    invA = MatrixALU.inverse(A)
    identity = MatrixALU.matmul(A, invA)
    assert abs(identity[0][0] - 1.0) < 1e-9
    assert abs(identity[0][1] - 0.0) < 1e-9
    assert abs(identity[1][0] - 0.0) < 1e-9
    assert abs(identity[1][1] - 1.0) < 1e-9


def test_homogeneous_4x4_transformation():
    # Translate (10, 20, 30) then apply to vector (1, 2, 3, 1)
    T = MatrixALU.translation4(10.0, 20.0, 30.0)
    v = [1.0, 2.0, 3.0, 1.0]
    v_out = MatrixALU.transform_vector4(T, v)
    assert v_out == [11.0, 22.0, 33.0, 1.0]


def test_ieee754_inspector_binary_layout():
    # Inspect 1.0 in float64
    info64 = IEEE754Inspector.inspect(1.0, bits=64)
    assert info64["sign"] == 0
    assert info64["exponent_unbiased"] == 0
    assert info64["hex"] == "0x3FF0000000000000"
    assert not info64["is_nan"]
    assert not info64["is_inf"]

    # Inspect -0.0
    info_zero = IEEE754Inspector.inspect(-0.0, bits=64)
    assert info_zero["sign"] == 1
    assert info_zero["is_zero"]

    # Inspect float32 2.5 (binary 10.1 = 1.01 * 2^1)
    info32 = IEEE754Inspector.inspect(2.5, bits=32)
    assert info32["sign"] == 0
    assert info32["exponent_unbiased"] == 1
    assert info32["hex"] == "0x40200000"
