"""
Selection store: the parking lot for customer selection prompts.

When search_customer returns 2+ close fuzzy matches (no clear winner),
we show the user an inline button list to pick one. This module stores
that list temporarily so the callback handler can map button taps to
customer IDs without needing LLM reasoning.

FLOW
----
1. search_customer fuzzy-scores results. If multiple close matches exist
   (top match NOT isolated at 95%+), return selection_required flag.
2. agent.py sees selection_required, stores customer_list + original
   message in a PendingSelection, returns token to bot.
3. bot.py renders hardcoded message + inline buttons with callback_data
   "sel:<token>:<index>".
4. User taps a button → confirmation_callback parses "sel:...", calls
   pop(token, user_id), retrieves the selected customer, and re-runs
   agent_loop with the original message (customer now in history).

MEMORY SAFETY
-------------
- TTL: 30 minutes. Abandoned selections expire automatically.
- Cap: 5000 entries. cachetools.TTLCache evicts oldest when full.
- Lock-protected: every put/pop/peek is thread-safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.token_store import TokenStore

# 30-min TTL is generous for a Telegram selection card.
_SELECTION_TTL_SECONDS = 1800
_SELECTION_MAX = 5000


@dataclass
class CustomerOption:
    """One option in a selection list."""
    id: int
    shop_name: str


@dataclass
class PendingSelection:
    user_id: int
    original_message: str
    username: str
    first_name: str
    customer_options: list[CustomerOption]
    created_at: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)


_store: TokenStore = TokenStore(ttl=_SELECTION_TTL_SECONDS, maxsize=_SELECTION_MAX)


def put(selection: PendingSelection) -> str:
    """Store a selection prompt and return an opaque token."""
    return _store.put(selection)


def pop(token: str, user_id: int) -> PendingSelection | None:
    """Atomically fetch+remove. Validates user_id to prevent token replay."""
    return _store.pop(token, user_id)


def peek(token: str) -> PendingSelection | None:
    """Non-destructive read (for debugging/testing)."""
    return _store.peek(token)


def clear_user(user_id: int) -> int:
    """Remove all pending selections for a user. Returns count removed."""
    return _store.clear_user(user_id)
