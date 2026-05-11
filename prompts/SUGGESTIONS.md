# Prompt Suggestions & Improvements

## Issues Fixed (Phase 1 - Code Refactoring)

### 1. Query Result Rendering

**Status**: ✅ FIXED

**Problem**: `_render_query_result()` in `src/render.py` was looking for generic "rows" or "results" keys, but tools return tool-specific keys:

- `query_sales` → `sales`
- `query_customers` → `customers`
- `query_production` → `production`
- `query_cash_flow` → `cash_flows`
- `query_credit_ledger` → `ledger`

**Solution**: Added field mapping to extract the correct key based on tool name, then render via template path `(queries, tool_name, list)`.

**Impact**: Multi-row query results now render consistently with Jinja2 templates instead of falling back to LLM prose.

---

## Issues Identified (Require Prompt Changes)

### 2. Tool Selection: `search_customer` vs `query_customers`

**Problem**: The system has two tools for customer queries:

- `search_customer`: Fuzzy match by name fragment → returns selection UI if multiple ambiguous matches
- `query_customers`: List all customers (with optional filters)

When user asks for a "list of customers", the LLM sometimes calls `search_customer` instead of `query_customers`, triggering the selection UI instead of showing formatted results.

**Root Cause**: System prompt doesn't distinguish when to use each tool.

**Suggested Fix** (in `prompts/system_prompt.md`):

```markdown
## Tool Selection Rules

- **search_customer**: Use when user names a specific customer (e.g., "Sharma", "Gupta")
  - Results in fuzzy match with selection UI if multiple close matches

- **query_customers**: Use when user asks for lists/reports/filters (e.g., "show all customers", "balances", "customers with credit > 50k")
  - Results in formatted multi-row template output, NO selection UI
```

**When to apply**: Any request mentioning:

- "list", "show all", "saari", "sab", "kitne"
- Filter operations: "credit > X", "created after", "outstanding"

---

### 3. Markdown Formatting in Templates

**Problem**: Telegram markdown formatting not consistent with platform standards.

**Current state**: Templates use Telegram's native single-asterisk formatting:

- `*bold text*` (Telegram bold)
- `` `code` `` (inline code)
- No double-asterisk support

**Potential Issue**: Some message rendering systems expect Markdown double-asterisks `**bold**` or convert Telegram → Markdown → Telegram, causing display issues.

**Suggested Fix** (in templates):

1. Audit which rendering system is used (Telegram native vs Markdown conversion layer)
2. If Markdown conversion is happening, convert templates to:
   - Bold: `**text**` (double asterisk)
   - Code: `` `text` `` (backticks, no change needed)
   - Italic: `_text_` (underscores, not needed currently)
3. Verify Telegram API documentation for markdown flavor:
   - [Formatting Options](https://core.telegram.org/bots/api#formatting-options)
   - Supports: `*bold*`, `_italic_`, `` `code` ``, code block with triple backticks

**Current verdict**: Templates are correct for Telegram native format. If display issues persist, check:

- Bot message rendering pipeline (src/bot.py)
- Telegram API client version
- Message text_markup or parse_mode setting

---

### 4. List Formatting Examples

**Example from user**:

```text
*   **Shri Krishna Traders** (id: 16): 400.0 kg @ ₹10,000,000.0/kg = ₹4,000,000,000.00 (Paid Online) on 2026-07-08
```

This uses `*   ` (asterisk-space) for bullets and `**bold**` which is NOT Telegram format.

**Suggested Fix** (in system prompt):

```markdown
## List Formatting

Default to prose. Use bullets ONLY for 3+ items.

Format for multi-row results:

- **Don't use**: Markdown double-asterisk `**text**` or bullet-spacing `*   `
- **Do use**: Telegram native format:
  - `*bold*` (single asterisk)
  - `* item` (bullet: asterisk-space)
  - `` `code` `` (backticks)

Example (3+ sales):

📊 *5 sales:*
* 2026-07-08 — *Shri Krishna Traders* (id: `16`): 400.0 kg @ ₹10,000,000.0/kg = *₹4,000,000,000.00* (Paid Online)
* 2026-05-10 — *Shereen Traders* (id: `21`): 49.0 kg @ ₹100.0/kg = *₹4,900.00* (Paid Online)
```

---

## Code Quality Improvements (Implemented)

### Type Hints

- `src/render.py`: Added type hints to `_render_query_result()` signature
- All existing type hints preserved

### Documentation

- Added inline comments in `_render_query_result()` explaining field mapping
- Logged query template misses at debug level (line 163)

### Error Handling

- Query rendering falls back gracefully to LLM if template not found
- Exception logging includes tool name context

---

## Testing Status

**Test Results**: 112/112 passed (excludes 1 pre-existing failure in pending.py)

Tests covering query rendering:

- ✅ `test_render.py`: Confirmation + closing message tests
- ✅ `test_tools.py`: Tool execution and validation
- ✅ `test_agent.py`: Agent loop (except 1 pre-existing failure)

**Not yet tested**: Query rendering with actual template output (manual smoke test needed)

---

## Remaining Tasks for Complete Fix

### Phase 2 (Prompt Changes - User to implement)

1. **System prompt**: Add tool selection rules for `search_customer` vs `query_customers`
2. **System prompt**: Clarify list formatting (Telegram native format, 3+ items rule)
3. **Tool descriptions**: Emphasize when to use `query_customers` for "list" requests

### Phase 3 (If display issues persist)

1. Check `src/bot.py` for message rendering (parse_mode setting)
2. Verify Telegram API client version matches supported markdown flavor
3. Consider adding format_markdown helper if custom processing is needed

---

## Related Files Modified

- `src/render.py:143-167`: `_render_query_result()` function (field mapping added)

## Related Files to Review

- `prompts/system_prompt.md`: Tool selection rules
- `src/bot.py`: Message rendering pipeline
- `prompts/ui_strings/messages.yaml`: Template correctness verification (already done)
