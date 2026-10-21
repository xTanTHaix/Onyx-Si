"""
Contract tests for the Fixed 8 Meta-Tool surface and the schema validator.

These lock the two properties the surface actually promises:

1. exactly eight tools, with a JSON schema under the 1,250-token ceiling
2. every argument is validated *before* a handler runs (no permissive echo)
"""

import json

import pytest

from onyxsi.gateway.meta_router import (
    FIXED_8_META_TOOLS,
    MAX_SCHEMA_TOKENS,
    InvalidParamsError,
    MetaToolRouter,
    MethodNotFoundError,
    ToolNotBoundError,
    _validate_against_schema,
)


def test_tool_surface_is_exactly_the_declared_eight():
    router = MetaToolRouter()
    names = [tool["name"] for tool in router.list_tools()]
    assert names == list(FIXED_8_META_TOOLS)
    assert len(set(names)) == 8


def test_every_description_states_when_to_use_and_when_not_to():
    """A description that only says 'what' invites the model to misuse the tool."""
    for tool in MetaToolRouter().list_tools():
        description = tool["description"]
        assert "USE WHEN" in description, f"{tool['name']} lacks a USE WHEN clause"
        assert (
            "DO NOT USE" in description or "NOT a scratchpad" in description
        ), f"{tool['name']} lacks a negative trigger"


def test_schema_budget_is_enforced_at_construction():
    router = MetaToolRouter()
    measured = router.schema_token_count()
    assert measured <= MAX_SCHEMA_TOKENS
    # The payload is JSON-serialisable and is what tools/list actually returns.
    payload = json.dumps({"tools": router.list_tools()}, separators=(",", ":"))
    assert len(payload) // 4 <= MAX_SCHEMA_TOKENS


def test_every_tool_declares_a_closed_object_schema():
    for tool in MetaToolRouter().list_tools():
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False, f"{tool['name']} accepts unknown args"
        assert schema.get("properties")
        assert isinstance(schema.get("required"), list)


def test_validator_rejects_missing_required_and_unknown_properties():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"action": {"type": "string", "enum": ["tree"]}},
        "required": ["action"],
    }
    assert _validate_against_schema({"action": "tree"}, schema, "t") == []

    missing = _validate_against_schema({}, schema, "t")
    assert any("required" in err for err in missing)

    unknown = _validate_against_schema({"action": "tree", "x": 1}, schema, "t")
    assert any("unknown" in err.lower() for err in unknown)

    bad_enum = _validate_against_schema({"action": "delete"}, schema, "t")
    assert any("not one of" in err for err in bad_enum)


def test_validator_handles_bounds_arrays_and_patterns():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 5},
            "items": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string"}},
            "task_id": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,64}$"},
        },
        "required": ["count"],
    }

    assert _validate_against_schema({"count": 3}, schema, "t") == []
    assert _validate_against_schema({"count": 0}, schema, "t")
    assert _validate_against_schema({"count": 6}, schema, "t")
    assert _validate_against_schema({"count": 1, "items": []}, schema, "t")
    assert _validate_against_schema({"count": 1, "items": ["a", "b", "c"]}, schema, "t")
    assert _validate_against_schema({"count": 1, "items": [1]}, schema, "t")
    assert _validate_against_schema({"count": 1, "task_id": "../../etc"}, schema, "t")
    assert _validate_against_schema({"count": 1, "task_id": "TASK-1"}, schema, "t") == []


def test_invoke_is_the_single_choke_point():
    router = MetaToolRouter()
    router.register_handler("vfs_inspect", lambda args: {"status": "ok", "seen": args})

    # Valid call reaches the handler.
    assert router.invoke("vfs_inspect", {"action": "tree"})["status"] == "ok"

    # Invalid arguments never reach the handler.
    with pytest.raises(InvalidParamsError):
        router.invoke("vfs_inspect", {"action": "tree", "bogus": 1})

    # Unknown tool is a protocol error, not a crash.
    with pytest.raises(MethodNotFoundError):
        router.invoke("rm_rf", {})


def test_declared_but_unbound_tool_fails_closed():
    router = MetaToolRouter()
    assert router.is_bound("vfs_inspect") is False
    with pytest.raises(ToolNotBoundError):
        router.invoke("vfs_inspect", {"action": "tree"})

    router.register_handler("vfs_inspect", lambda args: {"status": "ok"})
    assert router.is_bound("vfs_inspect") is True


def test_observer_sees_success_and_failure_without_altering_results():
    events = []

    def observer(tool, ok, duration_us, error):
        events.append((tool, ok, duration_us, error))

    router = MetaToolRouter(observer=observer)
    router.register_handler("vfs_inspect", lambda args: {"status": "ok"})

    assert router.invoke("vfs_inspect", {"action": "tree"})["status"] == "ok"
    with pytest.raises(InvalidParamsError):
        router.invoke("vfs_inspect", {})

    assert [e[1] for e in events] == [True, False]
    assert all(isinstance(e[2], int) for e in events)


def test_notification_gets_no_reply_but_still_runs():
    router = MetaToolRouter()
    seen = []
    router.register_handler("vfs_inspect", lambda args: seen.append(args) or {"status": "ok"})

    assert router.dispatch_rpc({"jsonrpc": "2.0", "method": "vfs_inspect", "params": {"action": "tree"}}) is None
    assert seen == [{"action": "tree"}]
