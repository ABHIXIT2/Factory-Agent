# Factory Agent Roadmap

Versioned feature rollout. Each version is a stable, deployable product on its own.

---

## v1 — Core MVP · ✅ SHIPPED

**Goal:** Father records sales, credit, production. Safe, auditable, live on Fly.io.

### Shipped Features

- **Sales ledger** — text → confirmation card → DB write
- **Credit ledger** — automatic on credited sales; `record_payment` reduces balance
- **Production log** — daily entries, multiple batches per day allowed
- **Cash flow log** — automatic via PostgreSQL triggers (paid sales, payments) + manual expenses
- **Parse → Confirm → Write** — every write tool stages a `PendingAction`; user taps ✅/❌
- **Fuzzy customer matching** — sanitized LIKE search; ambiguous matches show selection UI
- **Inline buttons** — `[✅][❌]` confirmation, customer picker, payment-mode picker
- **Audit trail** — `original_message`, `confirmed_at`, `recorded_by` on every write + `audit_log` table
- **Soft deletes** — `is_deleted` flag; views auto-exclude deleted rows
- **Commands** — `/start`, `/help`, `/status`
- **Deployment** — Supabase + Fly.io (Mumbai region)

---

## v1.1 — Hardening · ✅ SHIPPED (unplanned)

Built during v1 but not in the original roadmap. Worth recording so the roadmap matches reality.

- **Dual-provider LLM** ([providers.py](../src/providers.py)) — Google Gemini primary, Groq fallback on rate-limit; transparent failover
- **Bounded sessions** ([session.py](../src/session.py)) — TTL cache, max-users cap, no memory leaks
- **Per-user rate limiting** — sliding window in `session.py`
- **Retried DB calls** ([db.py](../src/db.py)) — transient connection errors back off and retry
- **Structured logger** ([logger.py](../src/logger.py)) — emoji-tagged, indented, cache-info ready
- **TTL token store** ([token_store.py](../src/token_store.py)) — base for both pending actions and customer-selection UI
- **History compaction** — context window kept tight; tool results capped per-message in persisted history

---

## v1.2 — Multi-user Isolation · 🚧 NEXT (security fix, was buried in v3)

**Why this is urgent:** Writes already record `recorded_by`, but reads don't filter by user. Any Telegram user who finds the bot sees the father's whole ledger — sales, balances, customers, cash position. This is a v1 leak, not a v3 feature, and most fixes are one `WHERE recorded_by = ?` clause.

### v1.2 Scope

- **Per-user query filters** — `get_all_balances`, `query_sales`, `query_production`, `query_cash_flow`, `get_cash_position`, `list_customers` all scoped by `user_id`
- **Customer ownership** — `customers.created_by` already exists; respect it in `search_customer`
- **Allowlist (interim)** — `ALLOWED_USER_IDS` env var; bot ignores unknown chat IDs until proper isolation lands
- **Audit** — verify no read path bypasses the user filter

### v1.2 Definition of Done

- [ ] A second Telegram user starting the bot sees an empty ledger
- [ ] All `query_*` and `get_*` tools take `user_id` and filter on it
- [ ] `ALLOWED_USER_IDS` gate works in `bot.py`
- [ ] Tests cover cross-user isolation

---

## v2 — Voice · 🔜

**Goal:** Father can send voice messages instead of typing.

(Original v2's "full Hindi" item is mostly done — system prompt + `detect_user_lang` already handle Hindi/Hinglish/English. Splitting voice out so v2 isn't blocked on a smaller, already-shipped task.)

### v2 Scope

- **Voice → text** — Telegram voice note → Groq Whisper (`whisper-large-v3`) → text
- **Same agent pipeline** — transcription is treated as a normal text message; no separate flow
- **Confirmation** — show transcribed text in the confirmation card so user can ❌ if Whisper misheard
- **Error handling** — long audio (>1 min), background noise, unsupported codec

### v2 Effort

~2 days

### v2 Definition of Done

- [ ] Voice note → transcription → confirmation card → save works end-to-end
- [ ] Transcription appears in `original_message` (audit trail intact)
- [ ] Tested with at least 5 real Hindi voice notes

---

## v3 — Automation · 🔮

**Goal:** Daily summaries, alerts, scheduled reports — bot proactively talks to the user.

### v3 Scope

- **Daily 8 PM summary** — auto-sent each evening: total sales, production, expenses, outstanding
- **Weekly outstanding reminder** — Monday morning list of pending balances
- **Credit-limit breach alerts** — sent immediately when a sale pushes balance > `credit_limit`
- **Trend queries** — "this week's sales", "last month's expenses by category"
- **Scheduler** — APScheduler or Telegram-native job queue; survives Fly.io restarts

### v3 Effort

~3 days (scheduler setup is the bulk; the queries themselves are one-line view changes)

### v3 Definition of Done

- [ ] 8 PM summary fires reliably on Fly.io for 7 consecutive days
- [ ] Credit-limit alert fires on the same DB transaction that creates the breaching sale
- [ ] Trend queries return correct numbers vs. hand-written SQL

---

## v4 — Reports · 🔮

**Goal:** PDF/CSV exports for accountant/auditor review.

### v4 Scope

- **PDF monthly reports** — `/report` command → Hindi-formatted PDF in chat
- **CSV/Excel export** — any ledger, optional date range
- **Admin audit view** — `/audit` lists recent writes with user + timestamp
- **Per-user settings** — `/settings` for credit-alert threshold, alert frequency, timezone

### v4 Effort

~2 days

### v4 Definition of Done

- [ ] `/report` produces a PDF that the father can hand his accountant
- [ ] `/export sales 2026-04` returns a CSV
- [ ] `/audit` shows the last 20 writes for the requesting user

---

## Beyond v4 — parking lot

Recording these so they don't get lost, but **not committing** to them until v3 ships. Premature roadmapping rots fast.

- Predictive analytics ("you'll sell X kg next month")
- Per-customer profitability (needs cost data)
- Email digests
- REST API for external accounting tools

---

## What changed in this revision (2026-05-08)

1. **Added v1.1** — captures hardening work that was actually shipped (dual-provider, logger, retries, TTL stores) but not in the original plan.
2. **Added v1.2** — pulled multi-user isolation forward from v3. It's a security gap in the live bot, not a v3 feature.
3. **Trimmed v2** — voice only. Hindi support is largely already shipped via the system prompt and `detect_user_lang`; no need to gate v2 on it.
4. **Trimmed beyond-v4** — kept ideas as a parking lot, removed mobile-app/biometric-auth speculation that's far past current scope.

---

## Success Criteria (unchanged)

- **v1**: Father records all 4 ledgers without help · ✅
- **v1.2**: A second user can't see the father's data
- **v2**: Father prefers voice notes; rarely types
- **v3**: Father trusts the automated summaries
- **v4**: Owner generates monthly reports for the accountant

---

## Rollback Plan

Each version is independent and deployable. If a release breaks something:

1. Revert to previous tag
2. Debug in parallel
3. Redeploy when fixed

No forced upgrades — v1 users can stay on v1 forever.
