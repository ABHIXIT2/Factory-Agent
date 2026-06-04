"""Generic token-based store for temporarily parking values pending user confirmation.

Used by pending.py (PendingAction) and selection.py (PendingSelection) to store
opaque tokens that map to user-owned payloads with automatic expiry and TTL.
"""

from __future__ import annotations

import secrets
from threading import Lock
from typing import Generic, Protocol, TypeVar

from cachetools import TTLCache


class HasUserId(Protocol):
    """Protocol for payloads that must have a user_id field for cross-user validation."""
    user_id: int


T = TypeVar("T", bound=HasUserId)


class TokenStore(Generic[T]):
    """Generic token store with TTL, max-size cap, and user-id validation.

    One token per payload. pop() is one-shot: after fetching, the token is consumed.
    Each operation validates user_id to prevent token replay across users.
    """

    def __init__(self, ttl: int, maxsize: int) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = Lock()

    def put(self, payload: T) -> str:
        """Store a payload and return an opaque token."""
        token = secrets.token_urlsafe(12)
        with self._lock:
            self._cache[token] = payload
        return token

    def pop(self, token: str, user_id: int) -> T | None:
        """Atomically fetch+remove. Validates user_id to prevent token replay across users.

        Token is consumed on any attempt (including cross-user) so a leaked token
        cannot be probed repeatedly by other users.
        """
        with self._lock:
            payload = self._cache.pop(token, None)
            if payload is None:
                return None
            if payload.user_id != user_id:
                return None
        return payload

    def peek(self, token: str) -> T | None:
        """Non-destructive read (for debugging/testing)."""
        with self._lock:
            return self._cache.get(token)

    def clear_user(self, user_id: int) -> int:
        """Remove all payloads for a user. Returns count removed."""
        with self._lock:
            tokens = [t for t, p in self._cache.items() if p.user_id == user_id]
            for t in tokens:
                self._cache.pop(t, None)
        return len(tokens)

    def clear(self) -> None:
        """Clear all payloads (mainly for testing)."""
        with self._lock:
            self._cache.clear()
