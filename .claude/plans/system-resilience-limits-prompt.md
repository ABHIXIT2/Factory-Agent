# System Resilience, Limits & Prompt Gaps

**Problem**: "System feels very low" — 45s rate-limit freezes, sparse query responses, aggressive history compaction.

**Root causes**:
1. Groq 429 rate-limits cause visible 45s delays (SDK retry); no fallback to Google
2. 512-token limit silently truncates complex responses (no `finish_reason` check)
3. History compaction at 6 messages eats context too fast
4. `MAX_ITERATIONS=5` insufficient for multi-step flows (Trajectory 3 needs 10)
5. System prompt has no error-handling guidance for `ok=false` tool results
6. "1–3 lines" rule prevents listing queries from showing all relevant fields

---

## Solution: Five grouped changes

### Group A — Resilience (highest impact, lowest risk)

#### A1. Add retry-with-backoff to Groq rate limits
**File**: `src/providers.py:108-128`

**Change**: Wrap Groq call in `tenacity` (already a dependency) with 2 retries on `RateLimitError`, exponential backoff (1s → 4s).

**Before**:
```python
try:
    response = await asyncio.to_thread(
        _client.chat.completions.create,
        ...
    )
except groq_sdk.RateLimitError:
    raise  # ❌ Fatal — no retry
```

**After**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type(groq_sdk.RateLimitError),
)
async def _call_groq_with_retry(...):
    response = await asyncio.to_thread(
        _client.chat.completions.create,
        ...
    )
    return response
```

**Impact**: No more visible 45s freezes. User sees retry transparently (1–4s backoff, usually succeeds on retry 2–3).

---

#### A2. Detect `finish_reason="length"` truncation
**File**: `src/providers.py:130-141`

**Change**: Log warning when response is truncated by max_tokens.

**After `usage` block (line 141)**:
```python
finish_reason = getattr(response.choices[0], "finish_reason", None)
if finish_reason == "length":
    logger.warning(
        "Response truncated by max_tokens=%d for model=%s. "
        "Consider raising GROQ_MAX_TOKENS.",
        GROQ_MAX_TOKENS, chosen_model
    )
```

**Impact**: You'll see in logs how often truncation happens. Informs whether to bump B1 higher.

---

### Group B — Limits (modest increases)

#### B1. Bump `GROQ_MAX_TOKENS` 512 → 1024
**File**: `src/config.py:50`

**Why**: 
- 512 is tight once tool-call batches (4 parallel tools = ~150-200 tokens) + reply text land in same generation
- 1024 doubles headroom without meaningful latency/cost increase
- Eliminates silent-truncation bugs from oversized list queries or Devanagari output
- Model generates only as much as needed, so average cost rises ~15-25%, not 100%

**Before**: `GROQ_MAX_TOKENS: int = int(os.getenv("GROQ_MAX_TOKENS", "512"))`

**After**: `GROQ_MAX_TOKENS: int = int(os.getenv("GROQ_MAX_TOKENS", "1024"))`

---

#### B2. Bump `HISTORY_COMPACT_THRESHOLD` 6 → 10
**File**: `src/config.py:68`

**Why**: 
- 6 messages triggers compaction too early (search → create → sale → payment = already at threshold)
- 10 keeps an extra round-trip of context before summarizing away
- Prevents "wait, didn't the agent already know this customer?" moments

**Before**: `HISTORY_COMPACT_THRESHOLD: int = int(os.getenv("HISTORY_COMPACT_THRESHOLD", "6"))`

**After**: `HISTORY_COMPACT_THRESHOLD: int = int(os.getenv("HISTORY_COMPACT_THRESHOLD", "10"))`

---

#### B3. Bump `MAX_ITERATIONS` 5 → 8
**File**: `src/config.py:66`

**Why**: 
- Trajectory 3 in system prompt (customer rejection + create + sale) has 10 turns of assistant action
- 5 iterations doesn't cover complex flows; agent hits "max_iterations" error message
- 8 provides headroom without runaway cost

**Before**: `MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "5"))`

**After**: `MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "8"))`

---

#### B4. Rename `CONTEXT_WINDOW` → `SESSION_CACHE_MAX_PER_USER` (clarity only)
**Files**: `src/config.py:67`, `src/session.py:22`

**Why**: 
- Current name is misleading: `CONTEXT_WINDOW` sounds like LLM context limit
- It's actually the TTLCache size (max messages stored per user in memory)
- Real context limiter is `HISTORY_COMPACT_THRESHOLD`
- Rename removes confusion without behavior change

**Before**: `CONTEXT_WINDOW: int = int(os.getenv("CONTEXT_WINDOW", "10"))`

**After**: `SESSION_CACHE_MAX_PER_USER: int = int(os.getenv("SESSION_CACHE_MAX_PER_USER", "10"))`

(Update session.py line 22 to import `SESSION_CACHE_MAX_PER_USER` instead.)

---

### Group C — System Prompt Gaps

**File**: `prompts/system_prompt.md`

#### C1. Add tool-failure handling rule
**Location**: "Hard Rules" section (line 21–31)

**Add after line 31**:
```markdown
- When a tool returns `ok: false`, do NOT silently retry or ignore. Read the `error` field, explain the error in one line to the user, and ask what they'd like to do next. (Example: tool returns `customer_not_found` → "Hm, that customer nahi mila. Doosra naam se search karein, ya naya banayein?")
```

**Why**: Agent currently has no guidance for `ok=false` results. This rule prevents silent failures and dead turns.

---

#### C2. Add Trajectory 6 — error recovery example
**Location**: After Trajectory 5 (line 173)

**Add new section**:
```markdown
## Trajectory 6 — tool error recovery

User: `Sharma ko 50kg 120 rate`

Turn 1 (assistant, plan): "Customer 'Sharma' → search first."
tool_call `search_customer({"name_fragment":"Sharma"})` → 1 match, id=3.

Turn 2 (assistant, plan): "id=3, qty=50, rate=120, status unspecified. Ask."
text `Paid cash ya udhaar?`

User: `udhaar`

Turn 3 (assistant, plan): "id=3, qty=50, rate=120, status=credited. Emit save_sale."
tool_call `save_sale({"customer_id":3,"qty_kg":50,"rate_per_kg":120,"sale_date":"{today_iso}","payment_status":"credited","original_message":"Sharma ko 50kg 120 rate"})`
[Tool result: ok=false, error="customer_not_found", detail="customer id 3 deleted"]

Turn 4 (assistant, text): `Hm, Sharma (id 3) delete ho gaya. Doosra Sharma search karein?`
tool_call `search_customer({"name_fragment":"Sharma"})` → 1 match, id=24, "Sharma Provisions".

Turn 5 (assistant, text): `Sharma Provisions (id=24) mila. 50kg × ₹120 = ₹6,000 udhaar karein?`

User: `haan`

Turn 6 (assistant, plan): "id=24, qty=50, rate=120, status=credited. Emit save_sale."
tool_call `save_sale({"customer_id":24,"qty_kg":50,"rate_per_kg":120,"sale_date":"{today_iso}","payment_status":"credited","original_message":"Sharma ko 50kg 120 rate"})` → ✅

Turn 7 (assistant, text): `✅ Sale saved: 50kg × ₹120 = ₹6,000 udhaar. Sharma Provisions ka baaqi ₹14,800.`
```

---

#### C3. Loosen "1–3 lines" rule for list-type read queries
**Location**: "Output Style" section (line 55–60)

**Modify line 57**:
```markdown
- For read-only queries that return rows (query_customers, query_sales, get_all_balances, query_credit_ledger, query_production, query_cash_flow), list one row per line with shop name and key fields (balance, owner, etc.). Up to 10 rows per reply; summarize if more exist.
```

**Why**: Allows fuller responses for "give me customers 20-22" instead of just shop names. Pairs with B1 (1024 tokens).

---

### Group D — Observability

**File**: `src/providers.py`

#### D1. Add structured logs for retry/fallback events
**Location**: Line 99–141 (call_llm function)

**Add after line 112** (when Google rate-limits and falls back to Groq):
```python
logger.info("Provider fallback: gemini_rate_limit → groq")
```

**Add before line 128** (when Groq is retried):
```python
logger.info("Groq rate_limit → retrying with backoff")
```

**Why**: Lets you measure in production logs how often fallback/retry happens. Validates that A1 is working.

---

### Group E — Tests

**File**: `tests/test_agent.py`

#### E1. Add tests for new retry and error-handling behavior
**Add new test functions**:

```python
@pytest.mark.asyncio
async def test_groq_rate_limit_retries_then_succeeds(monkeypatch):
    """Verify Groq RateLimitError triggers retry (via tenacity) and succeeds on retry 2."""
    # Mock: _client.chat.completions.create raises RateLimitError twice, then returns valid response
    # Verify: call_llm() returns response (not re-raised error)

@pytest.mark.asyncio
async def test_both_providers_exhausted_raises(monkeypatch):
    """When Groq and Google both rate-limit, raise after fallback."""
    # Mock: both providers raise RateLimitError
    # Verify: call_llm() re-raises after exhausting retries

@pytest.mark.asyncio
async def test_truncation_warning_logged(monkeypatch, caplog):
    """When response has finish_reason='length', warning is logged."""
    # Mock: Groq response with finish_reason='length'
    # Verify: WARNING logged with "truncated by max_tokens"

@pytest.mark.asyncio
async def test_tool_error_agent_continues(monkeypatch):
    """When tool returns ok=false, agent continues (doesn't crash, doesn't retry)."""
    # Mock: save_sale returns {"ok": false, "error": "customer_not_found"}
    # Verify: agent appends tool result to history and generates error explanation on next iteration
```

---

## Implementation Order

**Suggested sequence** (minimizes merge conflicts + builds confidence):

1. **A1** — Add retry with backoff (biggest UX win)
2. **A2** — Add truncation logging (observability)
3. **D1** — Add fallback/retry logs (complete observability before bumping limits)
4. **B1, B2, B3** — Bump limits (safe once A1 in place)
5. **B4** — Rename (touches multiple files; do last to minimize churn)
6. **C1, C2, C3** — System prompt (no code risk, but re-read carefully for consistency)
7. **E1** — Add tests (validate A1+C1)

**Estimated effort**:
- A1: 15 min (add decorator, test tenacity import)
- A2: 5 min (add finish_reason check + log line)
- D1: 5 min (add 2 log lines)
- B1–B3: 5 min (edit config.py, no deps)
- B4: 20 min (rename, update session.py, tests)
- C1–C3: 10 min (edit system_prompt.md, proofread)
- E1: 25 min (write 4 async test funcs with mocks)

**Total**: ~85 min coding + testing.

---

## Risk Assessment

- **A1 (retry)**: Low risk. Tenacity is stable; wraps existing call. Worst case: falls back to Google or raises, same as before.
- **A2 (logging)**: Zero risk. Logging only.
- **B1–B3 (limits)**: Very low risk. Pure config; existing code handles higher values. May increase latency/cost slightly (acceptable per analysis).
- **B4 (rename)**: Medium risk (broad change), but backwards-compatible if env var name is also updated.
- **C1–C3 (prompt)**: Low risk (prompt-only), but test edge cases after deployment (verify error recovery actually triggers).
- **E1 (tests)**: Zero risk (tests only).

---

## Success Criteria

- [ ] A1: No more 45s visible freezes. Logs show retry attempts.
- [ ] A2: Truncation warnings appear in logs (or none, if 1024 suffices).
- [ ] B1–B3: Config values changed, tests still pass.
- [ ] C1–C3: Manual test with Telegram: ask "Sharma ke 50kg 120 rate" → agent resolves to Sharma Provisions or asks, doesn't silently fail.
- [ ] E1: All new tests pass, existing tests still pass.
