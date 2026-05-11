# Query Result List Formatting

**Problem**: LLM renders multi-row query results inconsistently (prose vs bullets, no intro line, cramped field asks).

**Examples of failure**:
- "Shop ka naam kya hai? Aur owner ka naam, phone, address, credit limit?" (5 fields in prose)
- "* Vrindavan Traders (id: `20`)" (inconsistent formatting)

**Root cause**: System prompt rule "one fact per line" lacks concrete examples. LLM guesses format for unknown query shapes.

---

## Solution (token-efficient 2-layer approach)

### Layer 1: System prompt (1 line, ~30 tokens)

**File**: `prompts/system_prompt.md:57`

**Current**:
```
- One concrete fact per line when listing.
```

**Replace with**:
```
- Default to prose. Use bullet list ONLY for 3+ rows (queries) or 3+ missing fields. Format: `* Name (key1: val, key2: val)`.
```

**Why**: Mirrors Claude 4 leaked prompt style (negative + override clause). Stops prose-cramming and specifies format gate.

---

### Layer 2: Templates (deterministic, function-based)

**Files to add/edit**:

1. **`prompts/ui_strings/messages.yaml`** — add `list` variant per tool:
   ```yaml
   query_customers:
     list:
       hi-Hind: |
         {{ count }} customers:
         {% for c in customers %}* *{{ c.shop_name }}* (id: `{{ c.id }}`, baaqi: ₹{{ c.outstanding }})
         {% endfor %}
   ```
   Repeat for: `query_sales`, `query_production`, `query_cash_flow`, `query_credit_ledger` (5 tools × 3 langs).

   Also add `create_customer.missing_fields` template (and other write tools).

2. **`src/render.py`** — add function:
   ```python
   def _render_query_result(tool_name: str, tool_result: str, lang: str) -> str:
       """Render multi-row query results via templates (mirrors _render_closing)."""
       # Parse tool_result JSON, render via Jinja template
   ```

3. **`src/agent.py`** — short-circuit at [agent.py:328-335](src/agent.py#L328-L335):
   ```python
   # After tool execution, before appending to messages:
   if is_read_query_with_rows(tc.function.name) and template_exists(tc.function.name):
       rendered = _render_query_result(tc.function.name, tool_result, user_lang)
       # Return rendered text directly; skip LLM re-composition
   ```

---

## Token impact

| Layer | Sysprompt cost | LLM output cost | Reliability |
|-------|---|---|---|
| Today | 0 | ~80–150/query | Low |
| Layer 1 only | +30 | ~80–150/query | Medium |
| **Both** | **+30** | **~0/query hit** | **High** |

Layer 2 skips LLM entirely for known shapes → saves output tokens.

---

## Status

- [ ] Draft messages.yaml templates (~20 min)
- [ ] Add `_render_query_result()` to render.py (5 min)
- [ ] Hook into agent.py short-circuit (10 min)
- [ ] Edit system prompt (1 min)
- [ ] Test 2–3 queries (manual)

**Estimated effort**: ~40 min coding. Zero risk (backwards-compatible).
