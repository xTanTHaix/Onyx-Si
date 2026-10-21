"""
Normal Tests for Phase 4.1: Headless Asyncio MCP Bridge & Fixed 8-Meta-Tool Surface Binding.
"""

import pytest

from onyxsi.gateway.meta_router import (
    FIXED_8_META_TOOLS,
    MAX_SCHEMA_TOKENS,
    MetaToolRouter,
    MethodNotFoundError,
    ToolNotBoundError,
    InvalidParamsError,
)


def test_meta_router_initialize_and_tools_list():
    router = MetaToolRouter()
    init_resp = router.dispatch_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp["result"]["serverInfo"]["name"] == "onyxsi-headless-kernel"

    list_resp = router.dispatch_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = list_resp["result"]["tools"]
    assert len(tools) == 8
    tool_names = tuple(t["name"] for t in tools)
    assert tool_names == FIXED_8_META_TOOLS


def test_meta_router_schema_token_ceiling():
    router = MetaToolRouter()
    token_count = router.schema_token_count()
    assert token_count <= MAX_SCHEMA_TOKENS


def test_meta_router_valid_tool_call_and_unknown_rejection():
    router = MetaToolRouter()
    router.register_handler(
        "vfs_inspect",
        lambda args: {"status": "ok", "action": args.get("action"), "target": args.get("target")},
    )
    call_resp = router.dispatch_rpc({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "vfs_inspect",
            "arguments": {"action": "tree", "target": "."},
        },
    })
    assert call_resp["result"]["raw"]["status"] == "ok"
    assert call_resp["result"]["isError"] is False

    try:
        router.dispatch_rpc({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "secret_internal_debug_tool", "arguments": {}},
        })
        raise AssertionError("Expected MethodNotFoundError")
    except MethodNotFoundError as exc:
        assert exc.code == -32601


def test_meta_router_unbound_tool_is_fail_closed():
    """A declared-but-unbound tool must raise, never echo the arguments back as success."""
    router = MetaToolRouter()
    try:
        router.dispatch_rpc({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "graph_compute", "arguments": {"engine": "alu", "query": "1+1"}},
        })
        raise AssertionError("Expected ToolNotBoundError")
    except ToolNotBoundError as exc:
        assert exc.tool_name == "graph_compute"


def test_meta_router_rejects_invalid_arguments():
    """Schema validation must reject missing required fields and unknown properties."""
    router = MetaToolRouter()
    router.register_handler("vfs_inspect", lambda args: {"status": "ok"})

    with pytest.raises(InvalidParamsError):
        router.dispatch_rpc({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "vfs_inspect", "arguments": {}},
        })

    with pytest.raises(InvalidParamsError):
        router.dispatch_rpc({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "vfs_inspect",
                "arguments": {"action": "tree", "definitely_not_a_field": 1},
            },
        })


def test_meta_router_notification_gets_no_reply():
    router = MetaToolRouter()
    assert router.dispatch_rpc({"jsonrpc": "2.0", "method": "tools/list"}) is None
