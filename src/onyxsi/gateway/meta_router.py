"""
Headless MCP Bridge & Fixed 8-Meta-Tool Surface Binding.

Exposes strictly the 8 fixed Meta-Tools with a combined schema ceiling of
<= 1,800 tokens (enforced at construction time, not merely documented).
The output-folding ceiling is separate: TokenGovernor defaults to 1,250.

Defence layers:
  * strict argument validation against each tool's inputSchema
    (required fields, primitive types, enums, unknown-property rejection)
  * hard failure when a tool has no bound handler (no fail-open echo stub)
  * Slowloris byte-drip guard and a hard maximum frame size
  * JSON-RPC 2.0 notification semantics (no response when ``id`` is absent)
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from onyxsi.contextfs.token_governor import TokenGovernor

MAX_SCHEMA_TOKENS = 1800
MAX_FRAME_BYTES = 64 * 1024 * 1024  # 64MB hard cap per JSON-RPC frame

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

FIXED_8_META_TOOLS: Tuple[str, ...] = (
    "vfs_inspect",
    "vfs_mutate",
    "ghost_sandbox",
    "causal_flight",
    "hypothesis_branch",
    "wiki_task_sync",
    "graph_compute",
    "mcp_orchestrate",
)


class GatewayError(Exception):
    """Base class for all gateway-level protocol errors."""

    code: int = INTERNAL_ERROR
    data: Optional[Dict[str, Any]] = None

    def __init__(self, message: str, data: Optional[Dict[str, Any]] = None):
        self.data = data
        super().__init__(message)


class MethodNotFoundError(GatewayError):
    code = METHOD_NOT_FOUND

    def __init__(self, method_name: str):
        self.method_name = method_name
        super().__init__(
            f"METHOD_NOT_FOUND: '{method_name}' is not in the Fixed 8-Meta-Tool surface"
        )


class ToolNotBoundError(GatewayError):
    """Raised when a declared meta-tool has no registered handler (fail-closed)."""

    code = INTERNAL_ERROR

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        super().__init__(f"TOOL_NOT_BOUND: no handler registered for '{tool_name}'")


class InvalidParamsError(GatewayError):
    """Raised when tool arguments violate the declared inputSchema."""

    code = INVALID_PARAMS

    def __init__(self, message: str, data: Optional[Dict[str, Any]] = None):
        super().__init__(message, data=data)


class SlowlorisTimeoutError(GatewayError):
    code = INVALID_REQUEST


@dataclass(slots=True, frozen=True)
class MetaToolSpec:
    """Compact MCP tool definition designed for minimal token footprint."""

    name: str
    description: str
    input_schema: Dict[str, Any]

    def to_mcp_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_default_8_specs() -> Dict[str, MetaToolSpec]:
    """Wraps the compact prose surface (see ``_meta_specs``) into MetaToolSpec objects."""
    from onyxsi.gateway._meta_specs import build_raw_specs

    specs = [
        MetaToolSpec(
            name=raw["name"],
            description=raw["description"],
            input_schema=raw["input_schema"],
        )
        for raw in build_raw_specs()
    ]
    return {s.name: s for s in specs}


def _validate_against_schema(value: Any, schema: Dict[str, Any], path: str) -> List[str]:
    """Minimal, dependency-free JSON-Schema subset validator.

    Supports: type, enum, minimum/maximum, minItems/maxItems, pattern,
    required, properties, additionalProperties, items.
    """
    errors: List[str] = []

    expected = schema.get("type")
    if expected:
        py_type = _JSON_TYPE_MAP.get(expected)
        if py_type is not None:
            # bool is a subclass of int: keep them distinct
            if expected in ("integer", "number") and isinstance(value, bool):
                return [f"{path}: expected {expected}, got boolean"]
            if not isinstance(value, py_type):
                return [f"{path}: expected {expected}, got {type(value).__name__}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")

    if isinstance(value, str) and "pattern" in schema:
        import re as _re

        if not _re.fullmatch(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match pattern {schema['pattern']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} item(s)")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                errors.extend(_validate_against_schema(item, item_schema, f"{path}[{idx}]"))

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for required_key in schema.get("required", []):
            if required_key not in value:
                errors.append(f"{path}: missing required property '{required_key}'")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(props))
            if unknown:
                errors.append(f"{path}: unknown propert(y/ies) {unknown}")
        for key, sub_schema in props.items():
            if key in value and isinstance(sub_schema, dict):
                errors.extend(_validate_against_schema(value[key], sub_schema, f"{path}.{key}"))

    return errors


class MetaToolRouter:
    """
    Headless JSON-RPC 2.0 MCP router strictly exposing the 8 Fixed Meta-Tools.
    Enforces the <= 1,800 token schema ceiling at construction time.
    """

    def __init__(self, read_timeout_sec: float = 1.0, observer: Optional[Callable[..., None]] = None):
        self.read_timeout_sec = read_timeout_sec
        self._specs: Dict[str, MetaToolSpec] = _build_default_8_specs()
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        # Optional telemetry sink: observer(tool, ok, duration_us, error).
        # Wired by the daemon to the HAL SPSC ring buffer; never required.
        self._observer: Optional[Callable[..., None]] = observer
        # Fail-closed: a tool without an explicit handler is an error, not an echo.
        for name in FIXED_8_META_TOOLS:
            self._handlers[name] = self._unbound_handler(name)

        measured = self.schema_token_count()
        if measured > MAX_SCHEMA_TOKENS:
            raise GatewayError(
                f"SCHEMA_CEILING_EXCEEDED: {measured} tokens > {MAX_SCHEMA_TOKENS}",
                data={"measured": measured, "ceiling": MAX_SCHEMA_TOKENS},
            )

    def set_observer(self, observer: Optional[Callable[..., None]]) -> None:
        """Installs (or clears) the invocation observer used for telemetry."""
        self._observer = observer

    def _emit(self, tool: str, ok: bool, duration_us: int, error: Optional[str]) -> None:
        observer = self._observer
        if observer is None:
            return
        try:
            observer(tool, ok, duration_us, error)
        except Exception:  # noqa: BLE001 - telemetry must never break a tool call
            pass

    @staticmethod
    def _unbound_handler(tool_name: str) -> Callable[[Dict[str, Any]], Any]:
        def _handle(args: Dict[str, Any]) -> Dict[str, Any]:
            raise ToolNotBoundError(tool_name)

        _handle._unbound = True  # type: ignore[attr-defined]
        return _handle

    def is_bound(self, tool_name: str) -> bool:
        """True when a real handler (not the fail-closed stub) is registered."""
        handler = self._handlers.get(tool_name)
        return handler is not None and not getattr(handler, "_unbound", False)

    def register_handler(self, tool_name: str, handler: Callable[[Dict[str, Any]], Any]) -> None:
        if tool_name not in self._specs:
            raise MethodNotFoundError(tool_name)
        self._handlers[tool_name] = handler

    def list_tools(self) -> List[Dict[str, Any]]:
        return [self._specs[name].to_mcp_dict() for name in FIXED_8_META_TOOLS]

    def schema_token_count(self) -> int:
        payload = json.dumps({"tools": self.list_tools()}, separators=(",", ":"))
        return TokenGovernor.estimate_tokens(payload)

    def export_gbnf(self) -> str:
        """Compiles the live 8-tool schemas into a GBNF grammar document."""
        from onyxsi.gateway.grammar_export import GrammarExporter

        return GrammarExporter.export_all_meta_tools_gbnf(
            [self._specs[name] for name in FIXED_8_META_TOOLS]
        )

    def validate_arguments(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        """Raises InvalidParamsError when arguments violate the tool's inputSchema."""
        spec = self._specs.get(tool_name)
        if spec is None:
            raise MethodNotFoundError(tool_name)
        if not isinstance(arguments, dict):
            raise InvalidParamsError(f"arguments for '{tool_name}' must be an object")
        errors = _validate_against_schema(arguments, spec.input_schema, tool_name)
        if errors:
            raise InvalidParamsError(
                f"INVALID_PARAMS for '{tool_name}': " + "; ".join(errors),
                data={"tool": tool_name, "violations": errors},
            )

    def invoke(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Validates then executes a meta-tool. Single choke point for all callers."""
        if tool_name not in self._specs:
            raise MethodNotFoundError(tool_name)

        started = time.perf_counter()
        try:
            self.validate_arguments(tool_name, arguments)
            handler = self._handlers[tool_name]
            result = handler(arguments)
        except Exception as exc:
            duration_us = int((time.perf_counter() - started) * 1_000_000)
            self._emit(tool_name, False, duration_us, type(exc).__name__)
            raise

        duration_us = int((time.perf_counter() - started) * 1_000_000)
        failed = isinstance(result, dict) and (
            "error" in result or result.get("status") in ("blocked", "error")
        )
        self._emit(
            tool_name,
            not failed,
            duration_us,
            str(result.get("error")) if failed else None,
        )
        return result

    def dispatch_rpc(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Synchronous JSON-RPC 2.0 dispatcher. Returns None for notifications."""
        if not isinstance(request, dict):
            raise InvalidParamsError("JSON-RPC request must be an object")

        is_notification = "id" not in request
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params") or {}

        if not isinstance(params, dict):
            raise InvalidParamsError("params must be an object")
        if not isinstance(method, str) or not method:
            raise InvalidParamsError("method must be a non-empty string")

        def _reply(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        if method == "initialize":
            return _reply(
                {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "onyxsi-headless-kernel", "version": "1.0.0"},
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                }
            )

        if method == "ping":
            # The MCP spec requires a server to answer a liveness probe. An
            # unanswered ping looks like a dead transport to the client.
            return _reply({})

        if method == "tools/list":
            return _reply({"tools": self.list_tools()})

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise InvalidParamsError("tools/call arguments must be an object")
            out = self.invoke(tool_name, arguments)
            is_error = isinstance(out, dict) and ("error" in out or out.get("status") in ("blocked", "error"))
            return _reply(
                {
                    "content": [{"type": "text", "text": json.dumps(out)}],
                    "raw": out,
                    "isError": bool(is_error),
                }
            )

        if method in self._specs:
            # Direct meta-tool dispatch is a first-class path (used by the
            # daemon and by tests) and must be validated exactly like tools/call.
            out = self.invoke(method, params)
            return _reply(out)

        raise MethodNotFoundError(method)

    async def read_framed_message(
        self,
        chunk_producer: Callable[[], "asyncio.Future[bytes]"],
        timeout_sec: Optional[float] = None,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> Dict[str, Any]:
        """
        Reads a newline-delimited JSON-RPC frame from an async producer,
        aborting if a client drips bytes slower than timeout_sec or exceeds
        max_frame_bytes.
        """
        limit = timeout_sec if timeout_sec is not None else self.read_timeout_sec
        buf = bytearray()
        deadline = time.perf_counter() + limit

        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise SlowlorisTimeoutError(f"Slowloris stream timeout exceeded ({limit}s)")
            try:
                chunk = await asyncio.wait_for(chunk_producer(), timeout=remaining)
            except asyncio.TimeoutError:
                raise SlowlorisTimeoutError(f"Slowloris byte-drip timeout exceeded ({limit}s)")

            if not chunk:
                if not buf:
                    raise EOFError("Unexpected EOF on MCP input stream")
                break

            buf.extend(chunk)
            if len(buf) > max_frame_bytes:
                raise InvalidParamsError(
                    f"FRAME_TOO_LARGE: {len(buf)} bytes > {max_frame_bytes} limit"
                )
            if b"\n" in chunk:
                break

        raw_str = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
        if not raw_str:
            raise EOFError("Empty MCP frame after EOF")
        return json.loads(raw_str)
