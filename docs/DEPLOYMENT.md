# Deployment

Everything needed to run Labbu locally and ship it to the cloud, plus the secret
hygiene that keeps the bot token out of logs.

- [1. Local setup](#1-local-setup)
- [2. Deploy to Fly.io](#2-deploy-to-flyio)
- [3. Secret safety](#3-secret-safety)

---

## 1. Local setup

### Prerequisites

- Python 3.11+
- A [Supabase](https://supabase.com) project (free tier)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- At least one LLM key — Groq is required (`GROQ_API_KEY`); Gemini and Cerebras
  are optional and slot into the fallback chain (see
  [ARCHITECTURE.md §5](ARCHITECTURE.md#5-llm-providers--fallback)).

### Step 1 — Supabase

1. Create a project (region: Mumbai / closest to you). Wait ~2 min to initialise.
2. **Settings → API**: copy the project `URL` and the `anon public` key — these
   become `SUPABASE_URL` and `SUPABASE_KEY`.
3. **SQL Editor**: paste the entire contents of
   [`database/schema.sql`](../database/schema.sql) and run it. This creates 7
   tables, 2 views, and 2 cash-flow triggers (~30s).
4. Sanity check: run `SELECT NOW();` — you should see a timestamp.

### Step 2 — Telegram bot token

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`.
2. Pick a name and a username ending in `_bot`.
3. Copy the token it returns — this is `TELEGRAM_BOT_TOKEN`.

### Step 3 — Environment variables

Copy the template and fill in your values:

```bash
cp config/.env.example .env
```

Required keys: `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, `SUPABASE_URL`,
`SUPABASE_KEY`. The rest are optional and documented inline in
[`config/.env.example`](../config/.env.example) (provider keys, model overrides,
timezone, loop/rate/session limits). Placeholder-looking values (`test`, `fake`,
`none`, …) are rejected at startup by [`config.py`](../src/config.py).

### Step 4 — Install and run

```bash
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS / Linux

pip install -e ".[dev]"          # installs the package + dev tooling
python main.py
```

Expected startup:

```text
... - __main__ - INFO - Starting Labbu...
... - src.db - INFO - Connected to Supabase
... - __main__ - INFO - Bot configured. Starting polling...
```

The bot is now long-polling Telegram. Message it (`/start`, then e.g.
"Sharma ko 50 kg diya 120 rate pe") and confirm the row appears in Supabase.

### Tests

```bash
python -m pytest        # full suite (config in pyproject.toml)
python -m pytest -q     # quiet
python -m pytest -x     # stop on first failure
python -m pytest tests/test_tools.py   # one file
```

Groq and Supabase are mocked via [`tests/conftest.py`](../tests/conftest.py) — no
network or live keys needed.

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Missing required environment variables` | `.env` exists and all 4 required vars are filled (no blank values). |
| `Database connection failed` | Verify `SUPABASE_URL` / `SUPABASE_KEY`; test `SELECT NOW();` in the SQL Editor. |
| `... is a placeholder` on startup | A key still holds a template value — set a real one. |
| Bot doesn't respond | Username matches what you created; token is correct; `main.py` is running. |

---

## 2. Deploy to Fly.io

Free, always-on, no device required. Container build is
[`deploy/Dockerfile`](../deploy/Dockerfile); Fly config is
[`fly.toml`](../fly.toml) (app `factory-agent-namkeen`, region `bom`/Mumbai).

### Fly prerequisites

- Bot tested locally (above).
- [flyctl](https://fly.io/docs/flyctl/install/) installed; a free Fly account.

### Steps

```bash
fly auth login

# First time only — create the app (don't deploy yet):
fly launch        # pick region `bom` (Mumbai); decline immediate deploy

# Inject secrets (NEVER put these in fly.toml or git):
fly secrets set \
  GROQ_API_KEY="gsk_..." \
  TELEGRAM_BOT_TOKEN="123456789:ABC..." \
  SUPABASE_URL="https://your-project.supabase.co" \
  SUPABASE_KEY="eyJhbG..."
# add GOOGLE_AI_STUDIO_KEY / CEREBRAS_API_KEY too if you use them

fly secrets list      # names only, values hidden
fly deploy            # build image, push, start the bot
```

### Operate

```bash
fly logs              # recent logs
fly logs -f           # follow live
fly status            # VM / health
fly releases          # version history
fly rollback <ver>    # revert if a deploy breaks
```

To ship a change: `fly deploy` again (rebuild + restart, ~2 min).

### Cost

The bot uses well under the free tier (shared-cpu-1x / 256 MB). Effectively ₹0/month.

---

## 3. Secret safety

The bot holds four secrets — `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, `SUPABASE_KEY`,
and the (lower-risk) `SUPABASE_URL`. The main leakage risk is **third-party
loggers**: `httpx` logs full request URLs at INFO, and Telegram URLs embed the bot
token in the path (`https://api.telegram.org/bot<TOKEN>/...`).

### What the code does

[`main.py`](../main.py) silences the noisy SDK loggers on startup:

```python
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
```

This keeps token-bearing INFO lines out of stdout/log files while still surfacing
real WARN+ errors. **Never lower these back to INFO in production, and never log
the raw token or full request URLs.**

### Rules

- `.env` is git-ignored — never commit it. `fly.toml` holds **config only**, no secrets.
- Production secrets live in `fly secrets`, injected at runtime, never logged.
- In `tools.py` / `agent.py`, return a generic error to the user and keep details
  out of logs; the confirmation token is random hex, unrelated to any secret.

### Verify no leakage

```bash
# Local: run briefly, then scan the captured output
python main.py > run.log 2>&1
grep -iE "bot[0-9]+:|://api\.telegram" run.log    # expect: nothing

# Fly:
fly logs | grep -iE "token|key|bot[0-9]+:"         # expect: nothing
```

### If a token is ever exposed

1. Rotate it immediately via @BotFather (the old one is compromised).
2. Update `.env` locally and `fly secrets set TELEGRAM_BOT_TOKEN=<new>`.
3. Redeploy (`fly deploy`) and re-run the verification scan above.

### Pre-deploy checklist

- [ ] `.env` is git-ignored and uncommitted
- [ ] `fly.toml` contains no secrets
- [ ] All required secrets set via `fly secrets set`
- [ ] Logger silencing intact in `main.py`
- [ ] Local + `fly logs` leakage scans return nothing
