# Error Handling Classification & Output System

**Status:** Designed (awaiting implementation)  
**Phase:** Phase 7 Extended (Post-refactoring)  
**Issue:** Database constraint errors (numeric overflow, FK violations) currently return generic "internal error — please retry" message instead of actionable context

---

## Context

The system swallows all database constraint errors with a generic message, making them useless to both the LLM and the end customer. Additionally:
- `log_utils.log_agent_error` is **called but not defined** in logger.py (live AttributeError bug)
- `DatabaseError` class in db.py is **defined but never used** (dead code)

This plan implements a **standard, generic error classification system** that:
1. Maps PostgreSQL error codes to structured context
2. Passes actionable detail to the LLM so it can suggest what to do
3. Logs full error details for the operator using existing logging patterns
4. Tracks unhandled error types in a markdown file (not a DB table, to avoid overflow)

---

## Design Principles

- **Validation errors** (invalid input) → kept as-is, caught as `ValueError`
- **Database constraint errors** (overflow, FK, duplicate, etc.) → catch `APIError` specifically, map code to context, pass to LLM
- **Connection errors** → already retried 3x by tenacity, then mapped to user message
- **Unknown errors** → log with full details, track in markdown, return generic message
- **Logging** → follow existing `src/logger.py` pattern (named loggers, structured fields, `redact_secrets`)
- **Storage** → markdown file only (not DB) to avoid table bloat

---

## Implementation Plan

### 1. Fix `src/logger.py` (no dependencies)

**Add two missing functions:**

- `log_db_error(tool_name, user_id, exc, args=None)` — logs a Supabase `APIError` with full fields (code, message, details, hint) at ERROR level using the existing `src.error` named logger. Redacts sensitive args.

- `log_agent_error(user_id, error_type, context=None)` — wrapper for agent-level errors (max iterations, loop failures). Fixes the live `AttributeError` bug at `agent.py:345`.

**File:** `src/logger.py`  
**Changes:** Add ~30 lines in the Error Tracking section (after existing `log_error` function)

---

### 2. Update `src/tools.py` (main implementation)

**Add error mapping infrastructure:**

1. **Import:** `from postgrest.exceptions import APIError as _APIError`

2. **Module-level constants:**
   ```python
   PG_ERROR_MAP = {
       "22003": "numeric_overflow",
       "23503": "customer_not_found",
       "23505": "duplicate_record",
       "23514": "invalid_value",
       "42501": "permission_denied",
   }
   
   _UNHANDLED_LOG_PATH = pathlib.Path(__file__).parent.parent / "errors" / "unhandled_errors.md"
   _seen_unhandled: set[str] = set()
   _unhandled_lock = threading.Lock()
   ```

3. **Update `_err()` helper to accept optional `detail` kwarg:**
   ```python
   def _err(message: str, detail: str | None = None) -> str:
       payload = {"ok": False, "error": message}
       if detail:
           payload["detail"] = detail
       return json.dumps(payload)
   ```

4. **Add `_map_api_error(exc, tool_name, args)` function** — reads `exc.code`, looks up in `PG_ERROR_MAP`, constructs context string with field limits and attempted values, returns JSON via `_err(..., detail=...)`

5. **Add dedup + tracking functions:**
   - `_load_seen_unhandled()` — run at module load to parse existing markdown file
   - `_track_unhandled_error(tool_name, exc)` — check dedup set; if new, append to markdown
   - `_append_unhandled_entry(tool_name, exc, key)` — create `errors/` dir if missing, append formatted entry

6. **Update `execute_tool()` exception handlers:**
   ```python
   except ValueError as exc:
       logger.info("Tool %s rejected input: %s", tool_name, exc)
       return _err(str(exc))
   except _APIError as exc:
       log_utils.log_db_error(tool_name, tool_input.get("user_id"), exc, tool_input)
       return _map_api_error(exc, tool_name, tool_input)
   except Exception as exc:
       exc_text = f"{type(exc).__name__}: {exc}"
       logger.warning("Tool %s failed: %s", tool_name, redact_secrets(exc_text))
       return _err("internal error — please retry")
   ```

**File:** `src/tools.py`  
**Changes:** ~150 lines (imports, constants, 4 helper functions, updated exception chain)  
**Dependencies:** `pathlib`, `threading`, `postgrest.exceptions.APIError`, `src.logger.log_db_error`

---

### 3. Clean up `src/db.py`

**Remove dead `DatabaseError` class** (lines 26-27). Nothing in the codebase imports or raises it.

**File:** `src/db.py`  
**Changes:** Delete 2 lines only

---

### 4. Optional: Improve confirmation error messages in `src/render.py`

**One-line change in `_render_closing` (line ~247):**
```python
# Before:
err = parsed.get("error") or "kuch gadbad"

# After:
err = parsed.get("detail") or parsed.get("error") or "kuch gadbad"
```

This shows the rich `detail` field in confirmation templates, not just the error code.

**File:** `src/render.py`  
**Changes:** 1 line (optional)

---

## How It Works (End-to-End Example)

### Numeric Overflow

**User:** "20000kg @ ₹1,000,000/kg" (total = ₹20 trillion, exceeds max ₹9.99 trillion)

**Flow:**
1. `save_sale` validation passes (both numbers are valid floats)
2. DB insert fails with `APIError` code `22003` (numeric_value_out_of_range)
3. `execute_tool` catches `APIError`, calls `_map_api_error`
4. `_map_api_error` computes: attempted total = ₹20T, max = ₹9.99T, returns:
   ```json
   {
     "ok": false,
     "error": "numeric_overflow",
     "detail": "total_bill exceeds max ₹9,999,999,999.99 (attempted ₹20,000,000,000,000 = 20000 kg × ₹1,000,000/kg). Reduce qty or rate."
   }
   ```
5. `log_db_error` logs: `ERROR — DB ERROR — Tool: save_sale | User: 5859709950 | PG code: 22003 | message: numeric field overflow | args: {...}`
6. LLM receives the tool result with the `detail` field
7. LLM composes: "❌ Qty × rate = ₹20 trillion, but system limit is ₹9.99 trillion. Please reduce either quantity or rate."
8. Confirmation template shows: `"❌ Save nahi ho paya: numeric_overflow"` (or with the one-line render.py change: shows the full `detail` field)

---

## Error Categories Supported

| Category | Code(s) | Trigger | What LLM Sees |
|----------|---------|---------|---------------|
| Numeric overflow | `22003` | qty × rate exceeds NUMERIC(12,2) limit | error code + attempted value + max limit |
| Customer not found | `23503` | FK violation on customer_id | error code + the bad customer_id |
| Duplicate entry | `23505` | Unique constraint | error code + field name |
| Invalid enum/check | `23514` | cash_flow.category bad value | error code + field + constraint |
| Permission denied | `42501` | RLS policy blocked | error code + "operation not allowed" |
| DB connection error | `08*` or ConnectionError | Network/auth failure | error code + "retry after moment" |
| Unknown DB error | Any unmapped code | Something unexpected | error code + raw message + logged to markdown |

---

## Files Modified

| File | Changes | Lines |
|------|---------|-------|
| `src/logger.py` | Add `log_db_error`, `log_agent_error` | +30 |
| `src/tools.py` | Add error mapping, update exception chain, add tracking | +150 |
| `src/db.py` | Remove `DatabaseError` class | -2 |
| `src/render.py` | Optional: use `detail` in templates | ±1 |

**New directory:** `errors/` (created on first unhandled error)  
**New file:** `errors/unhandled_errors.md` (created on first unhandled error)

---

## Verification (How to Test)

1. **Numeric overflow:** Run the sale command with large qty and rate
   - Expected: User sees clear message about exceeding limit with the computed total
   - Check logs: `ERROR — DB ERROR — Tool: save_sale | PG code: 22003 | ...`

2. **Customer not found:** Try to record payment for non-existent customer_id
   - Expected: User sees "customer_id X does not exist"
   - Check logs: `ERROR — DB ERROR — PG code: 23503 | ...`

3. **Max iterations:** Run agent loop without giving it a finishable task (keep asking it new questions)
   - Expected: After 5 iterations, user sees "⏱️ Bahut steps ho gaye..."
   - Check logs: No `AttributeError` from `log_agent_error` (bug is fixed)

4. **Unknown DB error:** (Requires manual setup) Deliberately insert a bad enum value to trigger `23514` or similar
   - Expected: Error logged to `errors/unhandled_errors.md` as a new entry
   - On restart: Entry is not duplicated (dedup works)

5. **Normal success path:** Save a normal sale, payment, production
   - Expected: No change in behavior — these still work as before
   - User sees the same confirmation + closing messages

---

## Notes

- **No changes to `agent.py`, `bot.py`** — the infrastructure already passes tool results to the LLM; we just make the results richer
- **PostgreSQL error codes reference:** [PostgreSQL error codes](https://www.postgresql.org/docs/current/errcodes-appendix.html)
- **Fly.io ephemeral containers:** The `errors/unhandled_errors.md` file will be lost on restart. This is acceptable — the file is a developer tracker, not an audit log. If needed in the future, add the `errors/` directory to a fly.io volume.
- **Thread safety:** File appends are protected by a lock; concurrent tool calls cannot corrupt the markdown file
