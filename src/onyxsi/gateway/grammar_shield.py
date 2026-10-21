"""
Fast Schema Validator & Deterministic JSON Auto-Repair.
Repairs truncated JSON (missing closing braces/brackets/quotes, trailing commas)
and coerces basic mismatched types in <0.05ms while blocking deep-nesting bombs.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List

MAX_JSON_NESTING_DEPTH = 64
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


class GrammarShieldError(Exception):
    pass


class DeepNestingBlockedError(GrammarShieldError):
    pass


class SchemaValidationError(GrammarShieldError):
    pass


@dataclass(slots=True, frozen=True)
class ShieldResult:
    """Result of JSON repair and schema validation."""
    data: Dict[str, Any]
    was_repaired: bool
    coerced_fields: List[str]


class GrammarShield:
    """
    Deterministic syntactic shield for LLM tool calls.
    Auto-repairs malformed/truncated JSON and validates/coerces schema types.
    """

    def __init__(self, max_nesting_depth: int = MAX_JSON_NESTING_DEPTH):
        self.max_nesting_depth = max_nesting_depth

    def parse_and_repair(self, raw_json: str) -> ShieldResult:
        """Parses JSON string, applying deterministic repair if needed."""
        self._check_nesting_depth(raw_json)

        cleaned = _CONTROL_CHAR_RE.sub("", raw_json).strip()
        was_repaired = cleaned != raw_json.strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return ShieldResult(data=parsed, was_repaired=was_repaired, coerced_fields=[])
        except json.JSONDecodeError:
            pass

        repaired_str = self._repair_json_string(cleaned)
        try:
            parsed = json.loads(repaired_str)
        except json.JSONDecodeError as e:
            raise GrammarShieldError(f"Unrecoverable JSON syntax error: {e}")

        if not isinstance(parsed, dict):
            raise GrammarShieldError("Root JSON payload must be an object")

        return ShieldResult(data=parsed, was_repaired=True, coerced_fields=[])

    def validate_and_coerce(
        self,
        payload: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> ShieldResult:
        """Validates payload against JSON Schema and coerces primitive string/number mismatches."""
        props = schema.get("properties", {})
        required = schema.get("required", [])
        out: Dict[str, Any] = dict(payload)
        coerced: List[str] = []

        for req_key in required:
            if req_key not in out:
                raise SchemaValidationError(f"Missing required field: '{req_key}'")

        for key, val in list(out.items()):
            if key not in props:
                continue
            expected_type = props[key].get("type")
            enum_vals = props[key].get("enum")

            if expected_type == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
                if isinstance(val, str) and (val.isdigit() or (val.startswith("-") and val[1:].isdigit())):
                    out[key] = int(val)
                    coerced.append(key)
                elif isinstance(val, float) and val.is_integer():
                    out[key] = int(val)
                    coerced.append(key)
                elif expected_type == "integer":
                    raise SchemaValidationError(f"Field '{key}' cannot be coerced to integer: {val!r}")

            elif expected_type == "number" and (not isinstance(val, (int, float)) or isinstance(val, bool)):
                if isinstance(val, str):
                    try:
                        out[key] = float(val)
                        coerced.append(key)
                    except ValueError:
                        raise SchemaValidationError(f"Field '{key}' cannot be coerced to number: {val!r}")
                elif expected_type == "number":
                    raise SchemaValidationError(f"Field '{key}' must be number: {val!r}")

            elif expected_type == "boolean" and not isinstance(val, bool):
                if isinstance(val, str) and val.lower() in ("true", "false", "1", "0"):
                    out[key] = val.lower() in ("true", "1")
                    coerced.append(key)
                else:
                    raise SchemaValidationError(f"Field '{key}' cannot be coerced to boolean: {val!r}")

            elif expected_type == "string" and not isinstance(val, str):
                if isinstance(val, (int, float, bool)):
                    out[key] = str(val)
                    coerced.append(key)
                else:
                    raise SchemaValidationError(f"Field '{key}' must be string: {val!r}")

            if enum_vals and out[key] not in enum_vals:
                raise SchemaValidationError(f"Field '{key}' value {out[key]!r} not in allowed enum {enum_vals}")

        return ShieldResult(data=out, was_repaired=len(coerced) > 0, coerced_fields=coerced)

    def _check_nesting_depth(self, raw: str) -> None:
        depth = 0
        in_str = False
        escaped = False
        for ch in raw:
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in ("{", "["):
                depth += 1
                if depth > self.max_nesting_depth:
                    raise DeepNestingBlockedError(
                        f"JSON nesting depth {depth} exceeds limit {self.max_nesting_depth}"
                    )
            elif ch in ("}", "]"):
                depth = max(0, depth - 1)

    @staticmethod
    def _repair_json_string(s: str) -> str:
        s = _TRAILING_COMMA_RE.sub(r"\1", s)
        if s.endswith(","):
            s = s[:-1].rstrip()

        stack: List[str] = []
        in_str = False
        escaped = False
        for ch in s:
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                stack.append("}")
            elif ch == "[":
                stack.append("]")
            elif ch in ("}", "]") and stack and stack[-1] == ch:
                stack.pop()

        if in_str:
            s += '"'

        s = s.rstrip()
        if s.endswith(":"):
            s += "null"
        elif s.endswith(","):
            s = s[:-1].rstrip()

        while stack:
            s += stack.pop()

        return _TRAILING_COMMA_RE.sub(r"\1", s)
