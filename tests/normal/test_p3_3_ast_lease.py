"""
Normal Tests for Phase 3.3: AST-Node Lease Lock Registry.
"""

from onyxsi.deltafs.ast_lease_registry import (
    AstLeaseRegistry,
    AstLeaseConflictError,
)


def test_concurrent_non_conflicting_leases_in_same_file():
    registry = AstLeaseRegistry(default_ttl_sec=60.0)
    file_path = "src/auth/service.py"

    lease_a = registry.acquire_lease(file_path, "ast_fn_login", "agent_alpha")
    assert lease_a.agent_id == "agent_alpha"

    lease_b = registry.acquire_lease(file_path, "ast_fn_logout", "agent_beta")
    assert lease_b.agent_id == "agent_beta"

    assert registry.is_leased(file_path, "ast_fn_login") is True
    assert registry.is_leased(file_path, "ast_fn_logout") is True


def test_lease_conflict_on_same_node():
    registry = AstLeaseRegistry(default_ttl_sec=60.0)
    file_path = "src/auth/service.py"

    registry.acquire_lease(file_path, "ast_fn_login", "agent_alpha")

    try:
        registry.acquire_lease(file_path, "ast_fn_login", "agent_beta")
        raise AssertionError("Expected AstLeaseConflictError")
    except AstLeaseConflictError as exc:
        assert "agent_alpha" in str(exc)


def test_lease_release_and_reacquisition():
    registry = AstLeaseRegistry(default_ttl_sec=60.0)
    file_path = "src/auth/service.py"

    registry.acquire_lease(file_path, "ast_fn_login", "agent_alpha")
    released = registry.release_lease(file_path, "ast_fn_login", "agent_alpha")
    assert released is True

    lease_b = registry.acquire_lease(file_path, "ast_fn_login", "agent_beta")
    assert lease_b.agent_id == "agent_beta"
