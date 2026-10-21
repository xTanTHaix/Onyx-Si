"""
Virtual Handle Lease Registry.
Manages ref:// URI handles for source code spans and folded telemetry streams.
Enforces SHA-256 staleness checking, lease TTL expiry, and thread-safe access.
"""

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from onyxsi.contracts import OnyxError, OnyxErrorCode


class VirtualHandleError(OnyxError):
    """Base error for ref:// handle failures (OnyxErrorCode.E2003)."""

    def __init__(
        self,
        message: str,
        code: OnyxErrorCode = OnyxErrorCode.E2003,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(code, message, details)


class HandleExpiredError(VirtualHandleError):
    pass


class StaleHandleError(VirtualHandleError):
    pass


@dataclass(slots=True, frozen=True)
class VirtualHandle:
    """Descriptor for an active virtual handle reference lease."""
    handle_uri: str
    span_id: str
    source_sha256: str
    payload: bytes
    created_at: float
    ttl_sec: float
    target_path: Optional[str] = None
    is_stream: bool = False

    def is_expired(self, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.time()
        return (t - self.created_at) > self.ttl_sec


class VirtualHandleRegistry:
    """
    Thread-safe lease registry for zero-overhead virtual handles.
    Guarantees agents never mutate or read outdated code by validating source hashes on resolution.
    """

    def __init__(self, default_ttl_sec: float = 3600.0):
        self.default_ttl_sec = default_ttl_sec
        self._handles: Dict[str, VirtualHandle] = {}
        self._lock = threading.Lock()

    def register_code_handle(
        self,
        target_path: str,
        span_id: str,
        content: Union[str, bytes],
        source_bytes: bytes,
        ttl_sec: Optional[float] = None,
    ) -> VirtualHandle:
        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        src_hash = hashlib.sha256(source_bytes).hexdigest()[:16]
        handle_uri = f"ref://sha256_{src_hash}_{span_id}"

        ttl = ttl_sec if ttl_sec is not None else self.default_ttl_sec
        handle = VirtualHandle(
            handle_uri=handle_uri,
            span_id=span_id,
            source_sha256=src_hash,
            payload=payload,
            created_at=time.time(),
            ttl_sec=ttl,
            target_path=target_path,
            is_stream=False,
        )
        with self._lock:
            self._handles[handle_uri] = handle
        return handle

    def register_stream_handle(
        self,
        stream_id: str,
        content: Union[str, bytes],
        ttl_sec: Optional[float] = None,
        uri_prefix: Optional[str] = None,
    ) -> VirtualHandle:
        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        content_hash = hashlib.sha256(payload).hexdigest()[:16]
        if uri_prefix:
            safe_prefix = "".join(
                ch if (ch.isalnum() or ch == "_") else "_" for ch in uri_prefix
            ).strip("_") or "stream"
            handle_uri = f"ref://{safe_prefix}_{stream_id}_{content_hash}"
        else:
            handle_uri = f"ref://stream_sha256_{content_hash}_{stream_id}"

        ttl = ttl_sec if ttl_sec is not None else self.default_ttl_sec
        handle = VirtualHandle(
            handle_uri=handle_uri,
            span_id=stream_id,
            source_sha256=content_hash,
            payload=payload,
            created_at=time.time(),
            ttl_sec=ttl,
            is_stream=True,
        )
        with self._lock:
            self._handles[handle_uri] = handle
        return handle

    def resolve(
        self,
        handle_uri: str,
        current_source_bytes: Optional[bytes] = None,
    ) -> bytes:
        """
        Resolves handle_uri to its payload.
        Validates TTL and source SHA-256 freshness.
        """
        with self._lock:
            handle = self._handles.get(handle_uri)
            if handle is None:
                raise HandleExpiredError(f"Handle not found or expired: {handle_uri}")

            if handle.is_expired():
                del self._handles[handle_uri]
                raise HandleExpiredError(f"Handle lease has expired: {handle_uri}")

        if current_source_bytes is not None and not handle.is_stream:
            current_hash = hashlib.sha256(current_source_bytes).hexdigest()[:16]
            if current_hash != handle.source_sha256:
                raise StaleHandleError(
                    f"File has changed since handle was leased ({handle.source_sha256} != {current_hash})"
                )

        return handle.payload

    def resolve_text(
        self,
        handle_uri: str,
        current_source_bytes: Optional[bytes] = None,
    ) -> str:
        payload = self.resolve(handle_uri, current_source_bytes)
        return payload.decode("utf-8", errors="replace")

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [uri for uri, h in self._handles.items() if h.is_expired(now)]
            for uri in expired:
                del self._handles[uri]
            return len(expired)

    def count(self) -> int:
        with self._lock:
            return len(self._handles)
