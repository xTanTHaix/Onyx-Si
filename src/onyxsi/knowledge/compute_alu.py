"""
OnyxSi Phase 6.4: Deterministic Compute ALU.
Includes:
  1. Safe AST Math Evaluator (blocks __import__, eval(), attribute traversal, power bombs)
  2. Pure-Python 2D/3D/4D Matrix ALU (matmul, transpose, determinant, inverse, homogeneous transforms)
  3. Bitwise & IEEE-754 Binary Inspector (float16/32/64 sign, exp, mantissa, endianness, two's complement)
100% Python Standard Library only. Zero External Dependencies. Zero UI.
"""

import ast
import math
import struct
from typing import Any, Dict, List, Optional, Tuple, Union


class ComputeAluError(Exception):
    """Base exception for Compute ALU operations."""
    pass


class UnsafeMathExpressionError(ComputeAluError):
    """Raised when an expression contains unauthorized AST nodes, calls, or attributes."""
    pass


class MathEvaluationError(ComputeAluError):
    """Raised when math evaluation encounters a runtime error like division by zero or power overflow."""
    pass


class DimensionMismatchError(ComputeAluError):
    """Raised when matrix dimensions do not conform for multiplication or inversion."""
    pass


class SingularMatrixError(ComputeAluError):
    """Raised when attempting to invert a matrix whose determinant is zero or near zero."""
    pass


# Whitelist of allowed pure mathematical functions
_SAFE_MATH_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "ceil": math.ceil,
    "floor": math.floor,
    "pow": math.pow,
}

_SAFE_MATH_CONSTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


class SafeMathEvaluator(ast.NodeVisitor):
    """
    Evaluates arithmetic expressions via AST traversal.
    Strictly forbids:
      - Attribute lookups (e.g. `obj.attr`, `__class__`)
      - Unauthorized calls (e.g. `eval()`, `__import__()`, `open()`)
      - Loops, comprehensions, statements, assignments
      - Exponential explosion (base > 10,000 or exponent > 100)
    """

    MAX_POWER_BASE = 10000.0
    MAX_POWER_EXP = 100.0

    def visit(self, node: ast.AST) -> Any:
        method = "visit_" + node.__class__.__name__
        visitor = getattr(self, method, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node: ast.AST) -> Any:
        raise UnsafeMathExpressionError(
            f"Unauthorized syntax node: '{node.__class__.__name__}' is blocked by SafeMathEvaluator."
        )

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (int, float)):
            return node.value
        raise UnsafeMathExpressionError(f"Unauthorized constant type: {type(node.value).__name__}")

    def visit_Name(self, node: ast.Name) -> Any:
        ident = node.id
        if ident in _SAFE_MATH_CONSTS:
            return _SAFE_MATH_CONSTS[ident]
        raise UnsafeMathExpressionError(f"Unauthorized variable/name: '{ident}' is not allowed.")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        val = self.visit(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +val
        elif isinstance(node.op, ast.USub):
            return -val
        raise UnsafeMathExpressionError(f"Unauthorized unary operator: {node.op.__class__.__name__}")

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        left = self.visit(node.left)
        right = self.visit(node.right)

        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        elif isinstance(op, ast.Sub):
            return left - right
        elif isinstance(op, ast.Mult):
            return left * right
        elif isinstance(op, ast.Div):
            if right == 0:
                raise MathEvaluationError("Division by zero in math expression")
            return left / right
        elif isinstance(op, ast.FloorDiv):
            if right == 0:
                raise MathEvaluationError("Floor division by zero in math expression")
            return left // right
        elif isinstance(op, ast.Mod):
            if right == 0:
                raise MathEvaluationError("Modulo by zero in math expression")
            return left % right
        elif isinstance(op, ast.Pow):
            if abs(float(left)) > self.MAX_POWER_BASE or abs(float(right)) > self.MAX_POWER_EXP:
                raise MathEvaluationError(
                    f"Exponentiation exceeds safety limits: ({left} ** {right}) is blocked to prevent DoS."
                )
            try:
                res = left ** right
                if isinstance(res, complex):
                    raise MathEvaluationError("Complex numbers not supported in real ALU")
                return res
            except OverflowError:
                raise MathEvaluationError("Exponentiation overflow")
        else:
            raise UnsafeMathExpressionError(f"Unauthorized binary operator: {op.__class__.__name__}")

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise UnsafeMathExpressionError("Indirect or attribute calls are strictly prohibited.")

        func_name = node.func.id
        if func_name not in _SAFE_MATH_FUNCS:
            raise UnsafeMathExpressionError(f"Unauthorized function call: '{func_name}()' is not permitted.")

        fn = _SAFE_MATH_FUNCS[func_name]
        args = [self.visit(arg) for arg in node.args]
        try:
            return fn(*args)
        except Exception as e:
            raise MathEvaluationError(f"Function '{func_name}' failed: {e}")


def eval_math(expr: str) -> Union[int, float]:
    """Evaluates a mathematical string expression deterministically and safely."""
    if not isinstance(expr, str) or not expr.strip():
        raise MathEvaluationError("Empty expression")

    try:
        parsed = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise UnsafeMathExpressionError(f"Syntax error in math expression: {e}")

    evaluator = SafeMathEvaluator()
    return evaluator.visit(parsed)


# =========================================================================
# Matrix ALU
# =========================================================================

MatrixType = List[List[float]]


class MatrixALU:
    """Pure-Python deterministic matrix operations for 2D/3D/4D linear algebra."""

    @staticmethod
    def shape(M: MatrixType) -> Tuple[int, int]:
        rows = len(M)
        if rows == 0:
            return 0, 0
        cols = len(M[0])
        for r in M:
            if len(r) != cols:
                raise DimensionMismatchError("Jagged matrix rows")
        return rows, cols

    @staticmethod
    def matmul(A: MatrixType, B: MatrixType) -> MatrixType:
        rA, cA = MatrixALU.shape(A)
        rB, cB = MatrixALU.shape(B)

        if cA != rB:
            raise DimensionMismatchError(
                f"Cannot multiply matrix of shape ({rA}, {cA}) with ({rB}, {cB})"
            )

        # Transpose B for cache-friendly row-by-row dot product
        BT = MatrixALU.transpose(B)
        C: MatrixType = [[0.0] * cB for _ in range(rA)]

        for i in range(rA):
            rowA = A[i]
            for j in range(cB):
                colB = BT[j]
                dot = 0.0
                for k in range(cA):
                    dot += rowA[k] * colB[k]
                C[i][j] = dot

        return C

    @staticmethod
    def transpose(M: MatrixType) -> MatrixType:
        rows, cols = MatrixALU.shape(M)
        if rows == 0 or cols == 0:
            return []
        return [[M[r][c] for r in range(rows)] for c in range(cols)]

    @staticmethod
    def determinant(M: MatrixType) -> float:
        rows, cols = MatrixALU.shape(M)
        if rows != cols:
            raise DimensionMismatchError(f"Determinant requires square matrix, got ({rows}, {cols})")

        n = rows
        if n == 1:
            return float(M[0][0])
        if n == 2:
            return float(M[0][0] * M[1][1] - M[0][1] * M[1][0])
        if n == 3:
            return float(
                M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
                - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
                + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0])
            )

        # Gaussian elimination determinant with partial pivoting
        A = [list(map(float, row)) for row in M]
        det = 1.0

        for i in range(n):
            pivot = i
            max_val = abs(A[i][i])
            for r in range(i + 1, n):
                if abs(A[r][i]) > max_val:
                    max_val = abs(A[r][i])
                    pivot = r

            if max_val < 1e-15:
                return 0.0

            if pivot != i:
                A[i], A[pivot] = A[pivot], A[i]
                det = -det

            pivot_val = A[i][i]
            det *= pivot_val

            for r in range(i + 1, n):
                factor = A[r][i] / pivot_val
                for c in range(i + 1, n):
                    A[r][c] -= factor * A[i][c]

        return float(det)

    @staticmethod
    def inverse(M: MatrixType) -> MatrixType:
        rows, cols = MatrixALU.shape(M)
        if rows != cols:
            raise DimensionMismatchError(f"Inversion requires square matrix, got ({rows}, {cols})")

        det = MatrixALU.determinant(M)
        if abs(det) < 1e-12:
            raise SingularMatrixError(f"Matrix is singular with determinant {det:.4e}")

        n = rows
        # Gauss-Jordan elimination on [M | I]
        aug: List[List[float]] = []
        for i in range(n):
            row = [float(val) for val in M[i]]
            identity_row = [1.0 if j == i else 0.0 for j in range(n)]
            aug.append(row + identity_row)

        for i in range(n):
            # Pivot
            pivot = i
            max_val = abs(aug[i][i])
            for r in range(i + 1, n):
                if abs(aug[r][i]) > max_val:
                    max_val = abs(aug[r][i])
                    pivot = r

            if max_val < 1e-15:
                raise SingularMatrixError("Singular matrix encountered during elimination")

            if pivot != i:
                aug[i], aug[pivot] = aug[pivot], aug[i]

            pivot_val = aug[i][i]
            for c in range(2 * n):
                aug[i][c] /= pivot_val

            for r in range(n):
                if r != i:
                    factor = aug[r][i]
                    for c in range(2 * n):
                        aug[r][c] -= factor * aug[i][c]

        inv: MatrixType = []
        for i in range(n):
            inv.append(aug[i][n:])
        return inv

    # Homogeneous 3D/4D Transformations
    @staticmethod
    def identity4() -> MatrixType:
        return [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]

    @staticmethod
    def translation4(dx: float, dy: float, dz: float) -> MatrixType:
        M = MatrixALU.identity4()
        M[0][3] = float(dx)
        M[1][3] = float(dy)
        M[2][3] = float(dz)
        return M

    @staticmethod
    def scaling4(sx: float, sy: float, sz: float) -> MatrixType:
        M = MatrixALU.identity4()
        M[0][0] = float(sx)
        M[1][1] = float(sy)
        M[2][2] = float(sz)
        return M

    @staticmethod
    def rotation_z4(rad: float) -> MatrixType:
        c = math.cos(rad)
        s = math.sin(rad)
        M = MatrixALU.identity4()
        M[0][0] = c
        M[0][1] = -s
        M[1][0] = s
        M[1][1] = c
        return M

    @staticmethod
    def transform_vector4(M: MatrixType, v: List[float]) -> List[float]:
        if len(v) != 4:
            raise DimensionMismatchError(f"Vector4 requires 4 components, got {len(v)}")
        v_col = [[x] for x in v]
        res = MatrixALU.matmul(M, v_col)
        return [r[0] for r in res]


# =========================================================================
# IEEE-754 Binary Inspector & Bitwise Utilities
# =========================================================================

class IEEE754Inspector:
    """Inspects IEEE-754 floating-point binary bit layouts across float16, float32, float64."""

    @staticmethod
    def inspect(value: float, bits: int = 64) -> Dict[str, Any]:
        val = float(value)
        if bits == 64:
            raw_bytes = struct.pack(">d", val)
            int_bits = struct.unpack(">Q", raw_bytes)[0]
            sign = (int_bits >> 63) & 1
            exp_raw = (int_bits >> 52) & 0x7FF
            exp_bias = 1023
            mantissa_raw = int_bits & 0xFFFFFFFFFFFFF
            mantissa_bits = 52
            exp_bits = 11
        elif bits == 32:
            raw_bytes = struct.pack(">f", val)
            int_bits = struct.unpack(">I", raw_bytes)[0]
            sign = (int_bits >> 31) & 1
            exp_raw = (int_bits >> 23) & 0xFF
            exp_bias = 127
            mantissa_raw = int_bits & 0x7FFFFF
            mantissa_bits = 23
            exp_bits = 8
        elif bits == 16:
            raw_bytes = struct.pack(">e", val)
            int_bits = struct.unpack(">H", raw_bytes)[0]
            sign = (int_bits >> 15) & 1
            exp_raw = (int_bits >> 10) & 0x1F
            exp_bias = 15
            mantissa_raw = int_bits & 0x3FF
            mantissa_bits = 10
            exp_bits = 5
        else:
            raise ComputeAluError(f"Unsupported IEEE-754 bit width: {bits} (use 16, 32, or 64)")

        is_nan = (exp_raw == (1 << exp_bits) - 1) and (mantissa_raw != 0)
        is_inf = (exp_raw == (1 << exp_bits) - 1) and (mantissa_raw == 0)
        is_zero = (exp_raw == 0) and (mantissa_raw == 0)
        is_subnormal = (exp_raw == 0) and (mantissa_raw != 0)

        unbiased_exp = None
        if not (is_nan or is_inf or is_zero):
            if is_subnormal:
                unbiased_exp = 1 - exp_bias
            else:
                unbiased_exp = exp_raw - exp_bias

        bin_str = bin(int_bits)[2:].zfill(bits)
        hex_str = f"0x{int_bits:0{bits//4}X}"

        return {
            "value": val,
            "bits": bits,
            "hex": hex_str,
            "binary": bin_str,
            "sign": sign,
            "exponent_raw": exp_raw,
            "exponent_bias": exp_bias,
            "exponent_unbiased": unbiased_exp,
            "mantissa_raw": mantissa_raw,
            "mantissa_binary": bin(mantissa_raw)[2:].zfill(mantissa_bits),
            "is_nan": is_nan,
            "is_inf": is_inf,
            "is_zero": is_zero,
            "is_subnormal": is_subnormal,
        }

    @staticmethod
    def swap_endianness(value: int, byte_length: int = 8) -> int:
        fmt = {1: "B", 2: "H", 4: "I", 8: "Q"}.get(byte_length)
        if not fmt:
            raise ComputeAluError(f"Unsupported byte length for endianness swap: {byte_length}")
        packed = struct.pack("<" + fmt, value)
        return struct.unpack(">" + fmt, packed)[0]

    @staticmethod
    def to_twos_complement(value: int, bits: int = 64) -> int:
        if value < 0:
            return (1 << bits) + value
        return value & ((1 << bits) - 1)

    @staticmethod
    def from_twos_complement(value: int, bits: int = 64) -> int:
        mask = 1 << (bits - 1)
        if value & mask:
            return value - (1 << bits)
        return value & ((1 << bits) - 1)
