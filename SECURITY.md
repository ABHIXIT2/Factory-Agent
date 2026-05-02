# Security Model — Labbu ❤️

## Overview

**Goal**: Prevent accidental data loss while keeping operations simple.

**Three layers of protection:**

---

## Layer 1: Soft Deletes (Database Level)

### Tables with Soft Deletes
- `sales` — Never hard delete, mark `is_deleted = TRUE`
- `credit_ledger` — Never hard delete, mark `is_deleted = TRUE`
- `production_log` — Never hard delete, mark `is_deleted = TRUE`
- `cash_flow` — Never hard delete, mark `is_deleted = TRUE`

### Tables Protected from Deletes
- `users` — Permanent; tracks who used the bot
- `audit_log` — Immutable audit trail; append-only
- `customers` — Master list; never deleted (address/credit_limit can be updated)

### How It Works
```python
# To "delete" a sale, code runs UPDATE (not DELETE):
UPDATE sales 
SET is_deleted = TRUE, deleted_at = NOW(), deleted_by = user_id
WHERE id = 42;

# The record still exists in DB but is_deleted = TRUE
# Views automatically exclude soft-deleted rows:
SELECT * FROM customer_balance;  -- Won't count deleted sales
```

**Result**: You can always recover deleted records. No data is permanently lost unless you physically DELETE from Supabase (which you control).

---

## Layer 2: Audit Trail (Application Level)

Every transaction stores:
- `original_message` — Raw user input (e.g., "Sharma ko 50 kg")
- `extracted_data` (JSONB) — What LLM parsed (customer_id, qty, rate)
- `confirmed_at` — When user tapped ✅
- `recorded_by` — Which user (Telegram ID) made the change

### Audit Log Entries
```json
{
  "action_type": "add_sale",
  "table_affected": "sales",
  "record_id": 42,
  "user_id": 12345,
  "original_message": "Sharma ko 50 kg diya 120 rate pe",
  "extracted_data": {
    "customer_id": 3,
    "qty_kg": 50,
    "rate_per_kg": 120,
    "total_bill": 6000,
    "payment_status": "credited"
  },
  "confirmed_at": "2026-05-02T10:30:15Z"
}
```

**Result**: You can trace every transaction back to the original user message. If something went wrong, you see exactly what was parsed and confirm it was wrong.

---

## Layer 3: Code Review (Development Level)

### Rule 1: No Hard Deletes in Python Code
✅ **Allowed**:
```python
db.table("sales").update({
    "is_deleted": True,
    "deleted_at": datetime.utcnow().isoformat(),
    "deleted_by": user_id
}).eq("id", sale_id).execute()
```

❌ **Not Allowed**:
```python
db.table("sales").delete().eq("id", sale_id).execute()
```

**Enforcement**: Git hooks block commits that contain `DELETE FROM` or `.delete()` calls.

### Rule 2: Audit Every Write
Every `INSERT`, `UPDATE` goes into `audit_log` first.

```python
def save_sale(...):
    # 1. Validate and parse
    # 2. Show confirmation to user
    # 3. User taps ✅
    # 4. INSERT into sales
    sale_id = db.table("sales").insert({...}).execute()
    
    # 5. INSERT into audit_log (always)
    db.table("audit_log").insert({
        "action_type": "add_sale",
        "original_message": original_message,
        "extracted_data": json.dumps({...})
    }).execute()
```

---

## Layer 4: Views Exclude Soft-Deleted Rows

```sql
-- customer_balance view
SELECT c.id, c.shop_name, c.credit_limit,
       SUM(cl.debit_amount - cl.credit_amount) AS outstanding_balance
FROM customers c
LEFT JOIN credit_ledger cl 
  ON c.id = cl.customer_id 
  AND cl.is_deleted = FALSE  -- ← Soft-deleted rows excluded
GROUP BY c.id, c.shop_name, c.credit_limit;
```

**Result**: If you soft-delete a credited sale, the customer's outstanding balance automatically updates (removes that amount).

---

## You (Owner) vs Agent (Bot)

| Operation | You (Owner) | Agent (Bot) | How |
|---|---|---|---|
| SELECT | ✅ | ✅ | Both can read |
| INSERT | ✅ | ✅ | Both can record transactions |
| UPDATE | ✅ | ✅ | Both can fix mistakes (via UPDATE) |
| DELETE (hard) | ⚠️ Possible | ❌ Blocked | Code doesn't use DELETE |
| Recovery | ✅ | — | You can restore soft-deleted rows |

### If You Need to Hard Delete
You can do it manually in Supabase dashboard:
```sql
DELETE FROM sales WHERE id = 42;  -- Permanent (use with caution)
```

But the audit trail still exists in `audit_log`, so you can see what was deleted and why.

---

## What Can Go Wrong & How We Prevent It

| Scenario | Prevention |
|---|---|
| Labbu records wrong customer | Parse → Confirm flow; user corrects before save |
| LLM miscalculates 50kg as 500kg | User sees confirmation card; taps ❌ if wrong |
| Someone accidentally deletes all sales | `is_deleted` flag prevents hard delete; soft delete only |
| No way to recover deleted data | Soft deletes keep records; audit_log shows what happened |
| Unknown who changed what | `recorded_by` and `original_message` in every record |
| Balance calculation wrong | Views recompute live from audit trail (never cached) |

---

## Recovery Process

If a sale was soft-deleted and needs to be restored:

```sql
-- In Supabase SQL Editor, you run:
UPDATE sales 
SET is_deleted = FALSE, deleted_at = NULL, deleted_by = NULL
WHERE id = 42;

-- The sale comes back online
-- customer_balance view auto-updates
-- No audit trail lost (original audit_log entry still exists)
```

---

## Compliance & Auditability

**Full traceability:**
- ✅ Who did it? (`recorded_by` = Telegram user_id)
- ✅ What did they do? (`action_type`, `table_affected`)
- ✅ When? (`confirmed_at` timestamp)
- ✅ What did they say? (`original_message`)
- ✅ What did LLM extract? (`extracted_data` JSON)

**Query the audit trail:**
```sql
SELECT * FROM audit_log WHERE table_affected = 'sales' ORDER BY confirmed_at DESC;
```

---

## Summary

- **Soft deletes** prevent permanent data loss at the database level
- **Audit trail** shows you exactly what happened and why
- **Code review** ensures code never calls hard DELETE
- **Views** automatically exclude soft-deleted rows
- **You (owner)** can hard delete if absolutely necessary (but it's logged)
- **Agent (bot)** can't hard delete anything (code doesn't support it)

**Result**: Safe, traceable, recoverable system. 🔒❤️
