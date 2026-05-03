"""
Pending-action store: the parking lot for proposed-but-unconfirmed writes.

WHY THIS EXISTS
---------------
Writes (save_sale, record_payment, create_customer, save_production,
save_cash_flow) are destructive — they hit the DB and create real rows. We
never want the LLM to fire a write the moment it decides to. The user must
explicitly confirm with a button tap first.

That means there's a gap between "LLM proposes write" and "user confirms":
the proposed action has to live somewhere. That somewhere is THIS module.

THE FLOW
--------
    1. LLM emits tool_calls including a write tool (e.g. save_sale).
    2. agent_loop sees a write, does NOT execute it. Instead it builds a
       PendingAction holding:
         - the full assistant message (so we can replay tool_call_ids)
         - the parsed tool arguments
         - a pre-rendered confirmation summary
       and calls put(action) to get back a short opaque token.
    3. bot.py renders the summary with [✅ Haan, save] / [❌ Nahi] buttons,
       embedding `cf:y:<token>` and `cf:n:<token>` in callback_data.
    4. User taps a button → Telegram sends the callback_data back.
    5. bot.confirmation_callback parses the token, calls pop(token, user_id).
       pop() atomically fetches AND removes the action — one-shot only.
    6. agent.continue_after_confirmation replays the assistant message and
       executes every staged tool_call. Now the DB write happens.

WHY A TOKEN INSTEAD OF user_id AS KEY
-------------------------------------
- One-shot semantics: after pop(), the action is gone. Even if the user
  double-taps ✅, the second tap finds nothing — no double writes.
- A user can have multiple pending confirmations queued (e.g. asks two
  separate questions back-to-back). Tokens disambiguate them; user_id alone
  could not.

WHY pop() STILL VALIDATES user_id
---------------------------------
Defends against token replay across users. If user A's token somehow leaks
(shared screenshot, shared device, etc.), user B presenting that token in a
callback gets None back — only the original user can confirm their own
pending action.

MEMORY SAFETY
-------------
- TTL: 30 minutes. Abandoned confirmations expire automatically.
- Cap: 5000 entries. cachetools.TTLCache evicts oldest when full.
- Lock-protected: every put/pop/peek is thread-safe.

clear_user(user_id) is called on /clear so a user's stale pending actions
don't survive a session reset.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from cachetools import TTLCache

# 30-min TTL is generous for a Telegram confirmation card.
_PENDING_TTL_SECONDS = 1800
_PENDING_MAX = 5000


@dataclass
class PendingToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class PendingAction:
    user_id: int
    assistant_message: dict[str, Any]
    tool_calls: list[PendingToolCall]
    summary: str
    created_at: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)


_store: TTLCache = TTLCache(maxsize=_PENDING_MAX, ttl=_PENDING_TTL_SECONDS)
_lock = Lock()


def put(action: PendingAction) -> str:
    token = secrets.token_urlsafe(12)
    with _lock:
        _store[token] = action
    return token


def pop(token: str, user_id: int) -> PendingAction | None:
    """Atomically fetch+remove. Validates user_id to prevent token replay across users."""
    with _lock:
        action = _store.pop(token, None)
    if action is None:
        return None
    if action.user_id != user_id:
        return None
    return action


def peek(token: str) -> PendingAction | None:
    with _lock:
        return _store.get(token)


def clear_user(user_id: int) -> int:
    """Remove all pending actions for a user. Returns count removed."""
    with _lock:
        tokens = [t for t, a in _store.items() if a.user_id == user_id]
        for t in tokens:
            _store.pop(t, None)
    return len(tokens)
