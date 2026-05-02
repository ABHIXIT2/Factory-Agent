# Code Tour — How to Read This Codebase

Walk this in order. Each step takes 5–10 minutes. By the end you'll know
exactly what happens between a Telegram message and a `sales` row in the DB.

---

## 0. The 30-second mental model

```
Telegram message
   │
   ▼
bot.handle_text_message ─► agent.agent_loop ─► [LLM ↔ tools] ─► AgentResult
                                  │
                       AgentResult.confirmation?
                          │              │
                          │ yes          │ no
                          ▼              ▼
                  send buttons    reply text directly
                          │
                  user taps ✅/❌
                          │
                          ▼
              bot.confirmation_callback
                          │
                          ▼
       agent.continue_after_confirmation  (executes staged tool, asks LLM for closing line)
                          │
                          ▼
                   reply to user
```

**The single invariant**: read tools (search/balance/query) execute inline; the
moment the LLM emits a write tool (`save_*`, `record_payment`, `create_customer`),
the loop **defers** all tool calls in that response and returns a confirmation.
No DB write happens without a button press.

---

## 1. Read in dependency order

The package has zero import cycles. Read bottom-up:

| Step | File | What you'll learn |
|------|------|-------------------|
| 1 | [src/config.py](src/config.py) | Every env var the app uses, every tool schema the LLM sees, the system prompt |
| 2 | [src/utils.py](src/utils.py) | Pure validators (`validate_iso_date`, `validate_positive_number`, `sanitize_name_fragment`) — these guard every DB write |
| 3 | [src/db.py](src/db.py) | Supabase wrapper. Each function = one logical write. Retries on transient errors. Soft delete lives here |
| 4 | [src/tools.py](src/tools.py) | The bridge: each `_handler` validates LLM args, then calls `asyncio.to_thread(db.X, ...)`. Error envelope is always `{"ok": bool, ...}` |
| 5 | [src/pending.py](src/pending.py) | TTL store keyed by random token; cross-user replay returns `None` and consumes the token |
| 6 | [src/agent.py](src/agent.py) | The brain. Sessions, rate limit, the LLM loop, confirmation staging, `continue_after_confirmation` |
| 7 | [src/bot.py](src/bot.py) | The edge. Telegram commands, text handler, `CallbackQueryHandler` for the buttons |
| 8 | [main.py](main.py) | Entry point — DB ping, build app, `run_polling` with SIGTERM handling |

---

## 2. Trace a real example end-to-end

Pick **"Sharma ko 50kg de do 120 rate cash"** and trace it:

1. **bot.py** `handle_text_message` (line ~88) — receives `update.message.text`, calls `agent_loop`.
2. **agent.py** `agent_loop` — checks rate limit, loads `_sessions[user_id]`, sends `[system_prompt, ...history, new_user_msg]` to Groq.
3. Groq responds with `tool_calls=[search_customer(name_fragment="Sharma")]` (read-only).
4. agent_loop appends the assistant message + executes the tool inline via `tools.execute_tool`.
5. **tools.py** `_search_customer` → `sanitize_name_fragment` → `db.search_customer` → returns JSON `{"ok": true, "results": [{"id": 3, "shop_name": "Sharma Namkeen"}]}`.
6. agent_loop appends the `role: "tool"` result, calls Groq again.
7. Groq now responds with `tool_calls=[save_sale(customer_id=3, qty_kg=50, ...)]` (**write**).
8. agent_loop sees a write tool, **does not execute**. It builds a confirmation summary, stores `PendingAction` in `src/pending.py`, returns `AgentResult(confirmation=Confirmation(token=..., summary=...))`.
9. **bot.py** `_send_agent_result` sees `result.confirmation` is set, attaches the `[✅][❌]` keyboard with `callback_data="cf:y:<token>"`.
10. User taps ✅. **bot.py** `confirmation_callback` parses the token, calls `pending.pop(token, user_id)`.
11. **agent.py** `continue_after_confirmation` runs `execute_tool("save_sale", ...)` (which finally hits **db.py** `save_sale` — INSERT into sales + credit_ledger + audit_log), then asks Groq for a one-line summary like "✅ Sale saved: 50kg @ ₹120 = ₹6,000. Sharma ka baqaya: ₹6,000."
12. Bot replies. Done.

---

## 3. Where to look when X is wrong

| Symptom | First file to open |
|---------|-------------------|
| LLM picked wrong customer | `tools._search_customer`, then `db.search_customer` |
| Tool input rejected | `utils.validate_*` — error message names the field |
| Confirmation not appearing | `agent.WRITE_TOOLS` (is the tool listed?), `agent._build_summary`, `bot._send_agent_result` |
| Button does nothing | `bot.confirmation_callback`, the `pattern=r"^cf:(y\|n):"` registration in `build_app` |
| Bot crashes silently | logs — every catch logs `exc_info=True`. User sees a generic message |
| DB connection drops | `db._retry` policy at top of `db.py` (3 attempts, exponential backoff) |
| Memory growth | `agent._sessions` is a `TTLCache(maxsize=SESSION_MAX_USERS, ttl=SESSION_TTL_SECONDS)` — bounded by config |

---

## 4. Read the tests as executable docs

Tests double as the spec for each layer. Open them alongside the source:

| Test file | What it pins down |
|-----------|-------------------|
| [tests/test_utils.py](tests/test_utils.py) | Validator contract — what's accepted, what's rejected, why |
| [tests/test_tools.py](tests/test_tools.py) | Each tool's input validation + the `{"ok": false, "error": ...}` envelope. Includes the **error-redaction test** proving raw exception text never reaches the user |
| [tests/test_agent.py](tests/test_agent.py) | The full loop with a mocked Groq — read-tool inline execution, write-tool deferral, post-confirm execution, cross-user token replay, rate limiting |

Run them:
```bash
pip install -r requirements-dev.txt
python -m pytest -v
```

40 tests pass in <1 second. If you change a layer, run the matching test file first.

---

## 5. Verify a change

When you edit code, sanity-check with:

```bash
python -m py_compile main.py src/*.py     # syntax
python -m pytest                          # behavior
```

For real Telegram + Groq + Supabase smoke test, fill `.env` and run `python main.py` — it'll polling-fetch updates and you can chat with the bot directly.

---

## 6. The five files that matter most

If you only have time to read five files, read these in order:

1. **`src/agent.py`** — the loop. If you understand `agent_loop` + `continue_after_confirmation`, you understand the whole system.
2. **`src/config.py` `TOOLS`** — the contract between Python and the LLM. Every capability the bot has is a tool here.
3. **`src/tools.py`** — every tool's validation and DB call.
4. **`src/db.py`** — every shape of row this app writes.
5. **`tests/test_agent.py`** — what we promise the loop does, with examples.

Everything else is plumbing.
