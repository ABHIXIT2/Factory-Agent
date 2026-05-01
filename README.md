# Factory Agent — Namkeen Factory Operations Bot

A Telegram-based AI assistant for managing factory operations: sales ledger, customer credit tracking, production log, and cash flow. Built for the Namkeen factory owner's father in Hindi/Hinglish.

## Overview

**User**: Factory owner's father (non-English speaker)  
**Primary Interface**: Telegram (text + voice messages)  
**Data**: PostgreSQL (Supabase cloud database)  
**Deployment**: Fly.io (free, always-on)  
**Cost**: ₹0/month

### Core Ledgers

1. **Sales Log** — Record each shop's daily purchases (quantity, rate, payment type)
2. **Customer Credit Ledger** — Track outstanding balances per shop
3. **Production Log** — Daily production tracking (kg produced, packets made)
4. **Cash Flow Log** — All cash in/out movements (expenses, income, investments, etc.)

### Key Features (v1)

- **Parse → Confirm → Write flow** — No silent errors. LLM parses the message, shows a formatted confirmation card, and only writes after the user taps ✅
- **Customer by ID with fuzzy matching** — Say "Sharma" → bot finds "Sharma Namkeen", confirms, prevents duplicates
- **Audit trail** — Every record includes original message, timestamp, and who confirmed it
- **Inline buttons** — No typing "YES" — just tap buttons
- **Basic queries** — "Who owes the most?", "Today's sales?", outstanding balances
- **Hinglish support** — Basic English + Hinglish text (v2 adds full Hindi + voice)

## Quick Start (After Planning Review)

### 1. Get API Keys & Tokens
```bash
# Groq API (free)
# Get key from: https://console.groq.com

# Telegram Bot Token
# Create bot via BotFather (@BotFather) on Telegram

# Supabase (free PostgreSQL)
# Sign up at: https://supabase.com
# Create project, get connection string

# Fly.io (free deployment)
# Sign up at: https://fly.io
```

### 2. Set Up `.env`
```bash
cp .env.example .env
# Edit .env with your keys
```

### 3. Initialize Database
```bash
# Run schema.sql in Supabase dashboard
# (Full SQL provided in SCHEMA.md)
```

### 4. Install & Run
```bash
python -m venv venv
source venv/bin/activate  # or: venv\Scripts\activate on Windows
pip install -r requirements.txt
python main.py
```

### 5. Deploy to Fly.io
```bash
fly launch
fly secrets set GROQ_API_KEY=... TELEGRAM_BOT_TOKEN=... SUPABASE_URL=... SUPABASE_KEY=...
fly deploy
```

## Architecture

```
User (Telegram) 
  ↓
bot.py (Telegram handler + buttons)
  ↓
agent.py (Groq LLM + tool-calling loop)
  ↓
tools.py (Business logic: parse, confirm, validate)
  ↓
db.py (Database CRUD → Supabase)
  ↓
PostgreSQL (customers, sales, credit_ledger, cash_flow, audit_log)
```

## File Structure

```
Factory-Agent/
├── README.md               # You are here
├── SCHEMA.md               # Database schema explained
├── ARCHITECTURE.md         # System design & tool flows
├── ROADMAP.md              # Versioned feature roadmap
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variables template
├── Dockerfile              # Fly.io deployment
├── fly.toml                # Fly.io config
├── main.py                 # Entry point (runs bot + agent)
│
├── src/
│   ├── agent.py            # Groq LLM loop + tool-calling
│   ├── bot.py              # Telegram message handler + commands
│   ├── db.py               # Database operations (Supabase)
│   ├── config.py           # System prompt, tool schemas, constants
│   ├── tools.py            # Tool implementations (parse, confirm, save)
│   └── utils.py            # Date parsing, ₹ formatting, tables
│
├── database/
│   └── schema.sql          # Full PostgreSQL schema
│
└── tests/
    ├── test_db.py          # Database tests
    ├── test_agent.py       # Agent loop tests
    └── test_tools.py       # Tool logic tests
```

## Design Decisions

### Why Raw Python Agent Loop (Not LangChain/CrewAI)?
- **Transparency**: Every step is visible. Easy to debug when things go wrong.
- **Control**: You own the flow. LLM misparses something? You see it immediately.
- **Simplicity**: 200 lines of agent code vs. 5000+ lines of framework abstractions.

### Why Supabase (Not SQLite)?
- **Cloud-based**: Accessible from Fly.io bot (no device needed)
- **Multi-user ready**: Proper SQL, no file locks
- **Free 500MB**: Enough for months of data
- **Backup**: Automatic

### Why Parse → Confirm → Write?
Because the LLM can and will make mistakes. "50 kg Sharma" → "500 kg Sharma" is silent corruption without confirmation. This flow catches it.

### Why Customer by ID, Not Name?
Because "Sharma", "Sharma Namkeen", "Sharma wale" are three different strings but one customer. Fuzzy matching + ID prevents duplicates.

### Why Cash Flow Log Instead of Expense Log?
- **Expenses** = one-way (money out)
- **Cash Flow** = both directions (money in/out)
- Complete picture of factory financial health

## Development Timeline

| Phase | Duration | Goal |
|---|---|---|
| **v1** | Days 1–3 | Working bot. All 4 ledgers. Parse→Confirm→Write. Deployed. |
| **v2** | Week 2 | Hindi + voice messages. Inline buttons. Fuzzy matching improved. |
| **v3** | Week 3–4 | Smart insights. Daily auto-summaries. Credit alerts. |
| **v4** | Month 2 | PDF/CSV export. Admin views. |

## Verification Checklist

After each phase:
- [ ] Database connected and schema initialized
- [ ] Bot responds to `/start` and `/help`
- [ ] Can add a sale with confirm-before-write flow
- [ ] Can query outstanding balances
- [ ] Audit log records original message
- [ ] Deployed to Fly.io and working from phone

## Support & Debugging

- **Database**: Supabase dashboard shows all tables, queries, and backups
- **Agent**: Check `audit_log` table to see what the LLM parsed from each message
- **Bot**: Telegram bot logs go to stdout (visible in Fly.io logs via `fly logs`)

## Next Steps

1. **Review the plan** — Read SCHEMA.md, ARCHITECTURE.md, ROADMAP.md
2. **Get API keys** — Groq, Telegram, Supabase, Fly.io
3. **Approve the plan** — No changes? Let's build!
4. **Start Day 1** — Database setup, scaffold, `db.py`

---

**Built for**: Namkeen factory owner's father  
**Language**: Hindi/Hinglish (English in v1, full Hindi in v2)  
**Status**: Planning phase — ready for review
