"""
Pending-action store for the confirm-before-write flow.

A pending action records the assistant message (with its tool_calls) and the
parsed arguments, keyed by a short opaque token that we put in callback_data.
TTL-bounded so abandoned confirmations don't leak memory.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional

from cachetools import TTLCache

# 30-min TTL is generous for a Telegram confirmation card.
_PENDING_TTL_SECONDS = 1800
_PENDING_MAX = 5000


@dataclass
class PendingToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class PendingAction:
    user_id: int
    assistant_message: Dict[str, Any]   # the OpenAI-format assistant msg w/ tool_calls
    tool_calls: List[PendingToolCall]
    summary: str
    created_at: float = 0.0
    extras: Dict[str, Any] = field(default_factory=dict)


_store: TTLCache = TTLCache(maxsize=_PENDING_MAX, ttl=_PENDING_TTL_SECONDS)
_lock = Lock()


def put(action: PendingAction) -> str:
    token = secrets.token_urlsafe(12)
    with _lock:
        _store[token] = action
    return token


def pop(token: str, user_id: int) -> Optional[PendingAction]:
    """Atomically fetch+remove. Validates user_id to prevent token replay across users."""
    with _lock:
        action = _store.pop(token, None)
    if action is None:
        return None
    if action.user_id != user_id:
        return None
    return action


def peek(token: str) -> Optional[PendingAction]:
    with _lock:
        return _store.get(token)


def clear_user(user_id: int) -> int:
    """Remove all pending actions for a user. Returns count removed."""
    with _lock:
        tokens = [t for t, a in _store.items() if a.user_id == user_id]
        for t in tokens:
            _store.pop(t, None)
    return len(tokens)
