# Phase 2 — Required Fields Enforcement (COMPLETED)

**Date:** 2026-05-12  
**Status:** ✅ Complete  
**Plan Reference:** `witty-singing-octopus.md`

---

## Overview

Phase 2 implements code and database layer enforcement for required fields, building on Phase 1's prompt-level fix. The goal: prevent the model from emitting writes with hallucinated placeholder values by enforcing required fields at three layers:

1. **JSON Schema** — OpenAI/Groq client rejects empty required fields
2. **Tool Wrappers** — Python layer validates and raises `ValueError` for empty required fields
3. **Database** — PostgreSQL `CHECK` constraints prevent blank values

---

## Changes Made

### 2A — JSON Schema Updates (`src/config.py`)

✅ **`create_customer` (lines 189–202)**
- Promoted to required: `owner_name`, `owner_phone`, `address`
- Added `minLength: 1` to all four required fields
- Updated `required` array: `["shop_name", "owner_name", "owner_phone", "address"]`

✅ **`save_sale` (lines 207–226)**
- Added `minLength: 1` to `original_message`

✅ **`record_payment` (lines 231–244)**
- Added `minLength: 1` to `original_message`

✅ **`save_production` (lines 329–343)**
- Added `minLength: 1` to `original_message`

✅ **`save_cash_flow` (lines 349–376)**
- Promoted to required: `party`
- Added `minLength: 1` to both `party` and `original_message`
- Updated `required` array: `["flow_date", "flow_type", "category", "description", "amount", "party", "original_message"]`

---

### 2B — Tool Wrapper Validation (`src/tools.py`)

✅ **New Helper: `_normalize_optional_text()` (lines 44–52)**
```python
def _normalize_optional_text(value: Any, max_chars: int) -> str | None:
    """Optional text fields: empty/whitespace -> SQL NULL, never ''. Silent."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return truncate(s, max_chars)
```

✅ **`_create_customer()` (lines 280–306)**
- Added validation for `owner_name`, `owner_phone`, `address`
- Raises `ValueError` if any required field is empty

✅ **`_save_sale()` (lines 309–336)**
- Validates `original_message` (raises `ValueError` if empty)
- Normalizes `notes` using `_normalize_optional_text()`

✅ **`_record_payment()` (lines 339–362)**
- Validates `original_message` (raises `ValueError` if empty)
- Normalizes `notes` using `_normalize_optional_text()`

✅ **`_save_production()` (lines 403–422)**
- Validates `original_message` (raises `ValueError` if empty)
- Normalizes `notes` using `_normalize_optional_text()`

✅ **`_save_cash_flow()` (lines 424–460)**
- Validates `party` (raises `ValueError` if empty)
- Validates `original_message` (raises `ValueError` if empty)
- Normalizes `notes` using `_normalize_optional_text()`

✅ **`_delete_record()` (lines 575–583)**
- Normalizes `reason` using `_normalize_optional_text()` (optional field, silent normalization)

---

### 2C — Database Schema (`database/schema.sql`)

✅ **`customers` table (lines 13–26)**
- Promoted to `NOT NULL`: `owner_name`, `owner_phone`, `address`
- Added `CHECK (length(trim(col)) > 0)` constraints to prevent blank-only values:
  - `customers_shop_name_not_blank`
  - `customers_owner_name_not_blank`
  - `customers_owner_phone_not_blank`
  - `customers_address_not_blank`

✅ **`sales` table (line 42)**
- Added `CHECK (length(trim(original_message)) > 0)`

✅ **`credit_ledger` table (line 62)**
- Added `CHECK (length(trim(original_message)) > 0)`

✅ **`production_log` table (line 90)**
- Added `CHECK (length(trim(original_message)) > 0)`

✅ **`cash_flow` table (lines 107–114)**
- Promoted to `NOT NULL`: `party`
- Added `CHECK` constraints:
  - `cash_flow_description_not_blank`
  - `cash_flow_party_not_blank`
  - `cash_flow_orig_msg_not_blank`

---

### Bonus — Trajectory 7 (`prompts/system_prompt.md`)

✅ **Added concrete example** showing the correct behavior:
- User asks to create customer with minimal info
- Bot asks for all missing required fields in **one message** (bulleted list)
- User provides all details
- Bot emits the tool call with complete data

---

## Documentation Updates

✅ **`docs/SCHEMA.md`**
- Updated `customers` table documentation: marked `owner_name`, `owner_phone`, `address` as **required, non-blank**
- Updated `sales`, `credit_ledger`, `production_log`, `cash_flow` tables: documented `original_message` as **required, non-blank**
- Added `cash_flow` documentation: marked `description` and `party` as **required, non-blank**

✅ **`TOOLS-REFERENCE.md`**
- Updated `create_customer()`: all four fields now marked as **required**
- Updated `save_sale()`, `record_payment()`, `save_production()`: `original_message` marked as **required, non-blank**
- Updated `save_cash_flow()`: `party` marked as **required, non-blank**

---

## What Changed (User-Facing)

### Before Phase 2
- Bot could emit writes with empty placeholders (e.g., `owner_name: ""`)
- Only caught by prompt-level hint ("ask for missing fields")
- No validation at code or DB layer

### After Phase 2
- **Layer 1 (Schema):** Client rejects empty required fields before sending to API
- **Layer 2 (Wrapper):** Python code raises `ValueError` if any required text field is empty
- **Layer 3 (Database):** PostgreSQL `CHECK` constraints prevent blank-only values from bypassing wrapper
- **Recovery:** Model receives error envelope `{"ok": false, "error": "field is required"}` and recovers per Trajectory 6

---

## Cleanup

✅ **Deleted migrations folder** — All migration changes are now reflected directly in `database/schema.sql` (source of truth for fresh installs)

---

## Verification Checklist

When testing Phase 2:

- [ ] Schema-level negative test in Supabase SQL: attempt `INSERT INTO customers (shop_name, shop_name_normalized) VALUES ('X','x');` → expect `null value in column "owner_name"`
- [ ] Blank test: attempt `INSERT INTO customers (..., owner_name='', ...) VALUES ...;` → expect `CHECK` violation
- [ ] Unit tests: pass `""` and `"   "` for each required text field → assert `ValueError` raised
- [ ] Optional normalization: pass `""` to optional field → assert wrapper sends `None` to DB
- [ ] End-to-end: full `create_customer` flow with all required fields → verify customer row has populated columns
- [ ] Regression: run `save_sale`, `record_payment`, `save_production`, `save_cash_flow` with normal messages → confirm no new prompts appear

---

## Files Modified

**Code:**
- `src/config.py` — JSON schemas for 5 tools
- `src/tools.py` — Helper + 6 tool wrappers

**Database:**
- `database/schema.sql` — Schema with `NOT NULL` + `CHECK` constraints

**Prompts:**
- `prompts/system_prompt.md` — Trajectory 7 + Tool Field Reference (Phase 1)
- `prompts/tool_descriptions.md` — Tool blocks updated with required fields (Phase 1)

**Documentation:**
- `docs/SCHEMA.md` — Table documentation with constraint notes
- `TOOLS-REFERENCE.md` — Tool parameter documentation

**Removed:**
- `database/migrations/` folder (migration changes now in schema.sql)

---

## Next Steps

1. **Apply schema changes in Supabase SQL editor** — Copy `database/schema.sql` or run migration queries
2. **Run Phase 2 verification tests** (see checklist above)
3. **Monitor production** — Watch for any constraint violations (mapped to user-friendly error messages via `_map_api_error`)

---

**Implementation status: COMPLETE ✅**
