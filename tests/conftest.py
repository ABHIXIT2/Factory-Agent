"""
Test fixtures.

Two responsibilities, both must run before any `src.*` import:
1. Set fake env vars so `src.config` doesn't blow up at import time.
2. Stub out network clients (Groq + Supabase) so importing `src.agent`/`src.db`
   doesn't try to open real connections.
"""

import os
import sys
import types

# ---------------------------------------------------------------------------
# 1. Env vars (must precede src.config import)
# ---------------------------------------------------------------------------
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test-token")
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("RATE_LIMIT_MESSAGES", "1000")  # don't trip in tests

# ---------------------------------------------------------------------------
# 2. Stub Groq + supabase modules before src.* imports them.
# ---------------------------------------------------------------------------

# --- Groq stub --------------------------------------------------------------
_groq = types.ModuleType("groq")


class _StubGroq:
    def __init__(self, *_a, **_kw):
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._unstubbed)
        )

    @staticmethod
    def _unstubbed(*_a, **_kw):
        raise RuntimeError("Groq.chat.completions.create not stubbed in this test")


class _RateLimitError(Exception):
    """Stub for groq.RateLimitError — real class is only available when groq SDK is active."""


_groq.Groq = _StubGroq
_groq.RateLimitError = _RateLimitError
sys.modules.setdefault("groq", _groq)

# --- supabase stub ----------------------------------------------------------
_supabase = types.ModuleType("supabase")


class _StubClient:
    def __init__(self, *_a, **_kw):
        pass

    def table(self, _name):
        raise RuntimeError("supabase client not stubbed in this test")


def _create_client(*_a, **_kw):
    return _StubClient()


_supabase.create_client = _create_client
_supabase.Client = _StubClient
sys.modules.setdefault("supabase", _supabase)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture(autouse=True)
def _reset_agent_state():
    """Ensure session/rate state is fresh between tests."""
    from src import session, pending
    session._sessions.clear()
    session._rate.clear()
    pending._store.clear()
    yield
    session._sessions.clear()
    session._rate.clear()
    pending._store.clear()
