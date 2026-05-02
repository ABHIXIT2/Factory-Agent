# Factory Agent — Namkeen Factory Operations Bot

A Telegram-based AI assistant for managing factory operations: sales ledger,
customer credit tracking, production log, and cash flow. Built for the Namkeen
factory owner's father in Hindi/Hinglish.

## Overview

| Aspect | Value |
|---|---|
| **User** | Factory owner's father (non-English speaker) |
| **Interface** | Telegram (text; voice planned for v2) |
| **Data** | PostgreSQL on Supabase |
| **Deployment** | Fly.io (free, always-on) |
| **Cost** | ₹0/month |

### Core Ledgers

1. **Sales Log** — each shop's daily purchases (qty, rate, payment type)
2. **Customer Credit Ledger** — outstanding balances per shop
3. **Production Log** — daily production tracking
4. **Cash Flow Log** — all cash in/out movements

### Key Features

- **Parse → Confirm → Write** — every write tool emits an inline `[✅][❌]` confirmation card before the DB is touched
- **Fuzzy customer matching** — "Sharma" → "Sharma Namkeen", with sanitized search to block LIKE-injection
- **Audit trail** — every record stores the original user message, timestamp, and the user who confirmed it
- **Soft deletes** — recoverable; views auto-exclude deleted rows
- **Bounded sessions** — TTL cache (no memory leaks)
- **Rate limiting** — per-user sliding window
- **Retried DB calls** — transient connection errors back off and retry
- **Hinglish support** — Hindi, Hinglish, English (full Hindi + voice in v2)

## Quick Start

```bash
# 1. Install
python -m venv venv
venv\Scripts\activate           # Windows
# source venv/bin/activate      # Mac/Linux
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Fill in: GROQ_API_KEY, TELEGRAM_BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY

# 3. Initialize DB
# Run database/schema.sql in Supabase SQL editor (see SCHEMA.md)

# 4. Run
python main.py
```

Detailed setup is in [SETUP.md](SETUP.md). Cloud deploy is in [FLY_DEPLOY.md](FLY_DEPLOY.md).
**Important**: Read [SECRETS.md](SECRETS.md) for token safety and log protection before deploying.

## How to Read the Code

Start with **[CODE_TOUR.md](CODE_TOUR.md)** — a 30-minute walkthrough of the
package, with a real end-to-end trace. Then read in dependency order:
`config → utils → db → tools → pending → agent → bot → main`.

The package map and dependency diagram live in [`src/__init__.py`](src/__init__.py).

## File Structure

```text
Factory-Agent/
├── README.md               # this file
├── CODE_TOUR.md            # how to read the codebase
├── ARCHITECTURE.md         # system design
├── SCHEMA.md               # PostgreSQL schema (tables, views, triggers)
├── SECURITY.md             # security model (soft deletes, audit, recovery)
├── SECRETS.md              # secret safety (logging, token rotation, Fly deploy)
├── SETUP.md                # local setup walkthrough
├── FLY_DEPLOY.md           # Fly.io deployment guide
├── ROADMAP.md              # versioned feature roadmap (v1–v4)
│
├── main.py                 # entry point — DB ping, build app, run polling
├── requirements.txt        # runtime deps
├── requirements-dev.txt    # +pytest
├── pytest.ini              # pytest config
├── Dockerfile + fly.toml   # Fly.io
├── .env.example            # env var template
│
├── src/
│   ├── __init__.py         # package map + dependency diagram
│   ├── config.py           # env validation, tool schemas, system prompt
│   ├── utils.py            # validators, date parsing, formatting
│   ├── db.py               # Supabase wrapper (retried, soft-delete-aware)
│   ├── tools.py            # tool dispatch + LLM-arg validation
│   ├── pending.py          # TTL store of pending write actions
│   ├── agent.py            # LLM loop, sessions, rate limit, confirmation
│   └── bot.py              # Telegram handlers + callback handler
│
├── database/
│   └── schema.sql          # full PostgreSQL schema
│
└── tests/
    ├── conftest.py         # mock Groq + Supabase, env vars, state reset
    ├── test_utils.py       # validator + date-parsing tests
    ├── test_tools.py       # tool dispatch + validation + redaction tests
    └── test_agent.py       # mocked-Groq loop + confirmation flow tests
```

## Architecture (one-paragraph version)

`bot.py` is the I/O edge (Telegram). It calls `agent.agent_loop`, which runs
the LLM ↔ tool conversation. Read tools execute inline (`tools.py` validates
LLM-supplied arguments, then calls `db.py`). Write tools are **deferred**:
the agent stages a `PendingAction`, returns a confirmation card, and `bot.py`
renders inline buttons. On ✅, `agent.continue_after_confirmation` runs the
staged tool and asks the LLM for a closing reply. Soft deletes,
audit-logging, and retried DB calls live in `db.py`.

Full diagram and trace: [CODE_TOUR.md](CODE_TOUR.md).

## Running Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -v
```

40 tests, <1 second. Mocked Groq + Supabase — no network needed.

## Roadmap

Detail in [ROADMAP.md](ROADMAP.md):

- **v1 (shipped)** — sales/credit/production/cash-flow ledgers; confirm-before-write; Fly.io deploy
- **v2** — voice messages (Whisper); full Hindi
- **v3** — multi-user; daily auto-summaries; credit alerts
- **v4** — PDF/CSV exports; admin audit views

## Design Decisions

- **Why raw Python agent loop, not LangChain/CrewAI?** Transparency and control. ~270 lines vs 5000+ of framework abstractions.
- **Why Supabase (not SQLite)?** Cloud-accessible from the bot; multi-user-ready; free tier; automatic backups.
- **Why Parse → Confirm → Write?** LLMs hallucinate. "50 kg" → "500 kg" must not silently corrupt the ledger.
- **Why customer by ID, not name?** "Sharma" / "Sharma Namkeen" / "Sharma wale" are one customer. Fuzzy match resolves to a single ID.
- **Why cash flow log, not expense log?** Cash flow captures both directions — full financial picture.

## Operations

- **Logs**: stdout (visible in `fly logs`)
- **Audit**: query `audit_log` in Supabase to see every parsed write
- **Recovery**: `is_deleted = TRUE` rows are recoverable via SQL UPDATE — see [SECURITY.md](SECURITY.md)
