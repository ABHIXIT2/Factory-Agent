# Architecture

How Labbu works end-to-end — from a Telegram message to a database record — and
the design rules that keep it safe. Everything here is verified against the code;
references use `file:function` so you can jump straight to the source.

> **Database reference:** the authoritative schema (tables, views, triggers,
> constraints) is [`database/schema.sql`](../database/schema.sql). This document
> summarises it; that file is the source of truth.

---

## 1. What it is

A **Telegram bot** ("Labbu") that acts as an AI ledger assistant for a Namkeen
(Indian snack) factory, operated in Hindi / Hinglish / English. It manages four
ledgers:

1. **Sales** — each shop's purchases (qty, rate, paid/credited)
2. **Customer credit** — outstanding balances per shop
3. **Production** — daily production output
4. **Cash flow** — all cash in/out (much of it auto-generated, see §6)

- **Runtime:** Python 3.11+, `asyncio`, packaged via `pyproject.toml` (`factory-agent`).
- **Interface:** Telegram (`python-telegram-bot` ≥22), long-polling. No web frontend.
- **LLM:** multi-provider with transparent fallback — see §5.
- **Database:** PostgreSQL on **Supabase**, accessed through the Supabase Python
  client (`supabase==2.4.0`). No raw SQL, no explicit `BEGIN/COMMIT` transactions.
- **Identity:** the Telegram `user_id`. No passwords/OAuth. All session state is
  in-memory, per-user, TTL-bounded.

---

## 2. Layered structure

Top imports bottom; there are no import cycles. The canonical diagram lives in
[`src/__init__.py`](../src/__init__.py); the dependency-ordered read path is:

```
config → utils → db → tools → providers → pending → session → agent → bot → main
```

```
bot.py        Telegram I/O edge: commands, message handler, ✅/❌ + selection callbacks
  └─ agent.py        LLM ↔ tool loop; confirm-before-write; create_customer → re-run chaining
       ├─ providers.py   LLM clients (Gemini / Groq / Cerebras), retry, fallback, usage bars
       ├─ session.py     TTL history cache, sliding-window rate limit, history compaction
       ├─ pending.py     TTL store of staged write actions (the confirmation token)
       ├─ selection.py   TTL store for the ambiguous-customer picker
       │     └─ token_store.py   generic TTL store base (used by pending + selection)
       ├─ render.py      confirmation card + closing message (Jinja2)
       ├─ messages.py    i18n string rendering (hi-Hind / hi-Latn / en)
       └─ tools.py       validate LLM args → dispatch → JSON result
             └─ db.py         Supabase client, tenacity retries, soft-delete-aware, audit logging
                  └─ config.py     env validation, TOOLS schemas, system-prompt loader
                       └─ utils.py      pure helpers: validators, date parsing, fuzzy matching, formatting
```

`logger.py` is a cross-cutting structured logger used by `agent.py`, `bot.py`,
and `tools.py`.

---

## 3. The core pattern: Parse → Confirm → Write

**No write touches the database without the user confirming first.** LLMs
misparse ("50 kg" → "500 kg"); a silent write would corrupt the ledger. So writes
are *deferred*: the agent stages them and asks for a tap on ✅ before executing.

1. User sends text → [`bot.handle_text_message`](../src/bot.py) →
   [`agent.agent_loop`](../src/agent.py#L163).
2. Rate-limit check ([`session.check_rate_limit`](../src/session.py)) → load TTL
   session history → call the LLM with `TOOLS` and `tool_choice="auto"`.
3. **Read tools** (`search_customer`, `query_*`, `get_*`) execute inline; their
   results are cleaned ([`agent._clean_tool_result`](../src/agent.py#L108)) and
   fed back to the LLM; the loop continues (up to `MAX_ITERATIONS`, default 8).
4. **Write tools** (see `WRITE_TOOLS` in [`agent.py`](../src/agent.py#L61):
   `save_sale`, `record_payment`, `save_production`, `save_cash_flow`,
   `create_customer`, `delete_record`) are **deferred**. The agent stages a
   `PendingAction` (a TTL token via [`pending.put`](../src/pending.py)), builds a
   confirmation summary, and returns it. `bot.py` renders the inline `[✅][❌]`
   buttons. If *any* tool in a single LLM response is a write, the whole batch is
   deferred.
5. On ✅ → [`agent.continue_after_confirmation`](../src/agent.py#L392) executes
   the staged tool(s) via [`tools.execute_tool`](../src/tools.py), writes through
   `db.py` (+ audit log), and renders the closing message from a **template** —
   no extra LLM round-trip. On ❌ → [`agent.cancel_pending`](../src/agent.py#L485)
   drops the staged action and records the cancellation in history.

### Ambiguous-customer picker

If `search_customer` finds multiple close matches it returns
`selection_required`. The agent stages a `PendingSelection`
([`selection.py`](../src/selection.py)) and `bot.py` shows a numbered-button
picker. The user's choice re-runs the original message with the customer resolved.

### create_customer → re-run chaining

If the user says "Sharma ko sale karo" but Sharma doesn't exist, the flow is:
search fails → confirm `create_customer` → on ✅, the new customer is injected
into history ([`session.inject_created_customer`](../src/session.py)) and the
*original* sale message is re-run automatically
([`agent.continue_after_confirmation`](../src/agent.py#L433), guarded against
cascading re-entries by a `_chained` flag).

---

## 4. The 17 tools

Defined as OpenAI-compatible function schemas in
[`config.TOOLS`](../src/config.py#L166); descriptions are loaded from
[`prompts/tool_descriptions.md`](../prompts/tool_descriptions.md); handlers live
in [`src/tools.py`](../src/tools.py); persistence in [`src/db.py`](../src/db.py).

### Customers

- `search_customer(name_fragment)` → fuzzy matches, or `selection_required`
- `create_customer(shop_name, owner_name, owner_phone, address, [credit_limit])` *(write)*
- `get_customer(customer_id)`
- `query_customers([name_fragment, min_balance, max_balance, sort_by, limit])`

### Sales

- `save_sale(customer_id, qty_kg, rate_per_kg, sale_date, payment_status, [payment_mode, notes], original_message)` *(write)*
- `query_sales([customer_id, date_from, date_to, limit])`

### Credit & balances

- `get_customer_balance(customer_id)`
- `get_all_balances([sort_by, limit])`
- `query_credit_ledger([customer_id, transaction_type, date_from, date_to, limit])`
- `record_payment(customer_id, amount, payment_date, [payment_mode, notes], original_message)` *(write)*

### Production

- `save_production(prod_date, total_produced_kg, total_packets, [notes], original_message)` *(write)*
- `query_production([date_from, date_to, limit])`

### Cash flow

- `save_cash_flow(flow_date, flow_type, category, description, amount, party, [payment_mode, notes], original_message)` *(write)*
- `query_cash_flow([date_from, date_to, flow_type, category, limit])`
- `get_cash_position([date_from, date_to])`

### Deletion

- `delete_record(table, record_id, [reason])` *(write — soft delete only)*

Rules enforced in code: numeric fields are JSON numbers (not strings); the
trusted `user_id` is injected by the agent and always overrides any LLM-supplied
value ([`agent.py`](../src/agent.py#L250)); every write carries `original_message`;
`customer_id` must come from `search_customer`, never be invented.

---

## 5. LLM providers & fallback

[`providers.call_llm`](../src/providers.py#L157) implements a transparent fallback
chain. **The chain the code actually runs is Gemini → Groq → Cerebras:**

- **Gemini** (`gemini-2.0-flash`) is tried **first when `GOOGLE_AI_STUDIO_KEY` is
  set**; on rate-limit or API error it falls through.
- **Groq** (`llama-3.3-70b-versatile`, the default `GROQ_MODEL`) is the workhorse,
  called via `asyncio.to_thread` (the SDK is sync) with tenacity retries on
  *transient* per-minute rate limits (per-day limits are not retried —
  [`_is_retryable_rate_limit`](../src/providers.py#L129)).
- **Cerebras** (`qwen-3-235b-a22b-instruct-2507`) is tried **last**, only if
  `CEREBRAS_API_KEY` is set and Groq has failed.

All three use the OpenAI-compatible `tools` / `tool_choice="auto"` interface.
Per-call token usage is printed as colored terminal bars (display only — the
daily limits are **not** enforced).

---

## 6. Data model (summary)

Full DDL: [`database/schema.sql`](../database/schema.sql) — **7 tables, 2 views,
2 triggers**.

**Tables:** `users`, `customers`, `sales`, `credit_ledger`, `production_log`,
`cash_flow`, `audit_log`.

**Views (both exclude soft-deleted rows):**
- `customer_balance` — `SUM(debit − credit)` per customer → `outstanding_balance`.
- `cash_position` — totals of cash in/out and net.

**Auto cash flow (triggers).** The user never logs cash-in manually for sales or
payments — Postgres does it:
- `auto_cash_flow_paid_sale` — after a **paid** sale is inserted, creates a
  `cash_flow` IN row (`category='sale_cash'`).
- `auto_cash_flow_payment` — after a `payment_received` credit-ledger row,
  creates a `cash_flow` IN row (`category='payment_received'`).

**Computed, never stored.** `sales.total_bill` is a generated column
(`quantity_kg * rate_per_kg`); balances are computed live by the
`customer_balance` view. Nothing caches a running balance, so a soft-delete
recomputes correctly.

---

## 7. Safety model

**Soft deletes.** `sales`, `credit_ledger`, `production_log`, `cash_flow`, and
`customers` carry `is_deleted / deleted_at / deleted_by`. "Deleting" is an
`UPDATE` ([`db.soft_delete`](../src/db.py#L544)); the views auto-exclude the row,
so balances update without losing data. Code never issues a hard
`.delete()`. Recover by setting `is_deleted = FALSE` in Supabase.

**Audit trail.** Every write inserts an `audit_log` row
([`db._insert_audit`](../src/db.py#L55)) capturing `action_type`,
`table_affected`, `record_id`, `user_id`, the raw `original_message`, and the
`extracted_data` JSON. You can trace any record back to exactly what the user said
and what the LLM parsed.

**Retried DB calls.** Every `db.py` write/read is wrapped with tenacity
([`db._retry`](../src/db.py#L76)): 3 attempts, exponential backoff, retrying only
transient `ConnectionError / TimeoutError / OSError`. Logical errors bubble up.

**Secret-safe logging.** `main.py` silences `httpx`, `httpcore`, and `openai`
loggers to WARNING because their INFO logs include the bot token in request URLs.
Never re-enable them at INFO in production, and never log the raw token or full
request URLs. See [DEPLOYMENT.md](DEPLOYMENT.md#secret-safety).

---

## 8. Sessions, context & limits

- **History cache** ([`session.py`](../src/session.py)): a thread-safe TTL cache,
  `SESSION_TTL_SECONDS` (default 3600s) and `SESSION_MAX_USERS` (default 10000) —
  bounded memory, no leaks.
- **Context window:** `CONTEXT_WINDOW` (default 10) messages; history is compacted
  past `HISTORY_COMPACT_THRESHOLD`, and tool results stored back into history are
  capped at `TOOL_RESULT_HISTORY_MAX_CHARS` (the full result is always shown to the
  LLM in-flight — only the persisted copy is trimmed).
- **Rate limiting:** per-user sliding window, `RATE_LIMIT_MESSAGES` per
  `RATE_LIMIT_WINDOW_SECONDS` (default 20 / 60s).
- **Loop bound:** `MAX_ITERATIONS` (default 8) tool-calling rounds per turn.

All of these are env-overridable; defaults are in [`src/config.py`](../src/config.py).

---

## 9. History-shape invariant

Groq/OpenAI tool-calling requires every `assistant` message that contains
`tool_calls` to be immediately followed by a matching `role: "tool"` message per
call. `agent.py` preserves this pairing when it appends to and persists history;
breaking it produces 400s from the provider. Keep it intact when editing the loop.
