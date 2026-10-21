"""
Token Ceiling Governor.

Enforces a hard output token ceiling (default <= 1,250 tokens). When a payload
exceeds the ceiling it is folded: a visible head is kept and the *complete*
original payload is registered as a retrievable ``ref://<context>_<digest>_<hash>``
virtual handle.

Honest guarantees (replacing the previous silent 20% truncation):

1. Folded bytes are registered in a ``VirtualHandleRegistry``, so the emitted
   ``ref://`` URI actually resolves. When no registry is reachable the result is
   marked ``retrievable=False`` instead of advertising a dead URI.
2. The emitted text is re-measured and shrunk until ``emitted_tokens <= ceiling``
   holds for real, including tiny ceilings.
3. The marker is shortened when the budget is tight, and a last-resort hard
   truncation is flagged with ``retrievable=False`` rather than pretending the
   dropped bytes are recoverable.

``estimate_tokens`` is a heuristic, NOT a real tokenizer. ASCII is estimated at
the usual 4-chars-per-token; every non-ASCII character (Thai, CJK, emoji) counts
as one token because those scripts are routinely under-counted by the 4:1 rule.
Callers still must not treat it as exact.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Optional

DEFAULT_TOKEN_CEILING = 1250
CHARS_PER_TOKEN = 4
MIN_USEFUL_CEILING = 1

_DEFAULT_REGISTRY: Optional[Any] = None


def _default_registry() -> Any:
    """Lazily creates the process-wide fallback handle registry."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        from onyxsi.contextfs.virtual_handles import VirtualHandleRegistry

        _DEFAULT_REGISTRY = VirtualHandleRegistry()
    return _DEFAULT_REGISTRY


class TokenCeilingError(Exception):
    """Raised only when a ceiling < 1 token makes any output impossible."""


@dataclass(slots=True, frozen=True)
class TokenBudgetResult:
    """Outcome of token budgeting and ceiling enforcement."""

    text: str
    original_tokens: int
    emitted_tokens: int
    is_folded: bool
    folded_ref: Optional[str] = None
    folded_chars: int = 0
    retrievable: bool = False
    is_estimate: bool = True
    truncated_without_ref: bool = False


class TokenGovernor:
    """
    Guarantees no MCP response or VFS tree blows past the context ceiling while
    keeping every folded byte retrievable through a virtual handle.
    """

    def __init__(
        self,
        default_ceiling: int = DEFAULT_TOKEN_CEILING,
        handle_registry: Optional[Any] = None,
    ):
        if default_ceiling < MIN_USEFUL_CEILING:
            raise TokenCeilingError(
                f"Ceiling {default_ceiling} is below the {MIN_USEFUL_CEILING}-token minimum"
            )
        self.default_ceiling = default_ceiling
        # ``None`` means "use the shared process-wide registry".
        self.handle_registry = handle_registry

    @property
    def registry(self) -> Any:
        return self.handle_registry if self.handle_registry is not None else _default_registry()

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """
        Script-aware heuristic (not a tokenizer).

        ASCII uses the 4-chars-per-token rule; each non-ASCII character counts
        as one token. This keeps English output identical to the previous
        estimate while no longer under-counting Thai/CJK by roughly 4x.
        """
        if not text:
            return 0
        ascii_chars = len(text.encode("ascii", "ignore"))
        non_ascii_chars = len(text) - ascii_chars
        return max(1, ascii_chars // CHARS_PER_TOKEN + non_ascii_chars)

    def can_resolve(self, uri: Optional[str]) -> bool:
        """True when ``uri`` is a live handle in the active registry."""
        if not uri:
            return False
        try:
            self.registry.resolve(uri)
            return True
        except Exception:
            return False

    def _fit_head(self, text: str, marker: str, ceiling: int) -> int:
        """
        Largest head length such that ``text[:n] + marker`` fits ``ceiling``.

        Uses the real estimator instead of ``ceiling * 4`` characters, so a
        payload of Thai/CJK characters is folded to fit the same token budget
        as English instead of overflowing it.
        """
        low, high = 0, len(text)
        best = 0
        while low <= high:
            mid = (low + high) // 2
            if self.estimate_tokens(text[:mid] + marker) <= ceiling:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return best

    def enforce_ceiling(
        self,
        text: str,
        ceiling_tokens: Optional[int] = None,
        context_hint: str = "vfs_tree",
    ) -> TokenBudgetResult:
        """Folds ``text`` until the emitted payload provably fits the ceiling."""
        ceiling = ceiling_tokens if ceiling_tokens is not None else self.default_ceiling
        if ceiling < MIN_USEFUL_CEILING:
            raise TokenCeilingError(f"Requested ceiling {ceiling} is below 1 token")

        orig_tokens = self.estimate_tokens(text)
        if orig_tokens <= ceiling:
            return TokenBudgetResult(
                text=text,
                original_tokens=orig_tokens,
                emitted_tokens=orig_tokens,
                is_folded=False,
            )

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        ref_uri: Optional[str] = None
        try:
            handle = self.registry.register_stream_handle(
                stream_id=digest,
                content=text,
                uri_prefix=context_hint,
            )
            ref_uri = handle.handle_uri
        except Exception:
            ref_uri = None

        if ref_uri is not None:
            markers = (
                f"\n\n[... {len(text)} chars folded -> {ref_uri}]\n\n",
                f"[+{len(text)}->{ref_uri}]",
            )
        else:
            markers = (
                f"\n\n[... {len(text)} chars folded but NOT retrievable]\n\n",
                f"[+{len(text)}..]",
            )

        for marker in markers:
            head_chars = self._fit_head(text, marker, ceiling)
            emitted = text[:head_chars] + marker
            emitted_tokens = self.estimate_tokens(emitted)
            if emitted_tokens <= ceiling:
                return TokenBudgetResult(
                    text=emitted,
                    original_tokens=orig_tokens,
                    emitted_tokens=emitted_tokens,
                    is_folded=True,
                    folded_ref=ref_uri,
                    folded_chars=len(text) - head_chars,
                    retrievable=ref_uri is not None,
                )

        # Last resort: even the shortest marker does not fit. Truncate and say so.
        ellipsis = "..."
        keep = self._fit_head(text, ellipsis, ceiling)
        emitted = text[:keep] + ellipsis
        return TokenBudgetResult(
            text=emitted,
            original_tokens=orig_tokens,
            emitted_tokens=self.estimate_tokens(emitted),
            is_folded=True,
            folded_ref=ref_uri,
            folded_chars=len(text) - keep,
            retrievable=False,
            truncated_without_ref=True,
        )
