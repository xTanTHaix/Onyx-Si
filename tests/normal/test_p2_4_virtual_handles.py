"""
Normal Tests for Phase 2.4: Virtual Handle Lease Registry.
"""

import time
from onyxsi.contextfs.virtual_handles import (
    VirtualHandleRegistry,
    StaleHandleError,
    HandleExpiredError,
)


def test_virtual_handle_registration_and_resolution():
    registry = VirtualHandleRegistry(default_ttl_sec=60.0)
    source = b"def core_logic(): return 42\n"
    handle = registry.register_code_handle(
        target_path="app.py",
        span_id="fn_core_logic",
        content="return 42",
        source_bytes=source,
    )

    assert handle.handle_uri.startswith("ref://sha256_")
    assert registry.count() == 1

    resolved = registry.resolve_text(handle.handle_uri, current_source_bytes=source)
    assert resolved == "return 42"


def test_virtual_handle_staleness_detection():
    registry = VirtualHandleRegistry(default_ttl_sec=60.0)
    source_v1 = b"def core_logic(): return 42\n"
    handle = registry.register_code_handle(
        target_path="app.py",
        span_id="fn_core_logic",
        content="return 42",
        source_bytes=source_v1,
    )

    source_v2 = b"def core_logic(): return 999\n"

    try:
        registry.resolve(handle.handle_uri, current_source_bytes=source_v2)
        raise AssertionError("Expected StaleHandleError")
    except StaleHandleError:
        pass


def test_virtual_handle_expiry_ttl():
    registry = VirtualHandleRegistry(default_ttl_sec=0.01)
    handle = registry.register_stream_handle(
        stream_id="stream_out_01",
        content="Log output batch 1",
        ttl_sec=0.001,
    )

    time.sleep(0.005)

    try:
        registry.resolve(handle.handle_uri)
        raise AssertionError("Expected HandleExpiredError")
    except HandleExpiredError:
        pass
