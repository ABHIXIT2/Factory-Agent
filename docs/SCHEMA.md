# Database Schema

PostgreSQL (Supabase) schema for Factory Agent. Copy-paste all SQL into Supabase SQL editor and run.

## Core Principle

**Single source of truth = customer ledger data.** All other tables are either references (sales) or aggregations (views).

---

## Tables

### 1. `users` — Telegram user accounts
```sql
CREATE TABLE users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```
- Tracks who's using the bot
- `user_id` = Telegram user ID (unique, immutable)

---

### 2. `customers` — Master shop list
```sql
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    shop_name TEXT NOT NULL CHECK (length(trim(shop_name)) > 0),
    shop_name_normalized TEXT NOT NULL,
    owner_name TEXT NOT NULL CHECK (length(trim(owner_name)) > 0),
    owner_phone TEXT NOT NULL CHECK (length(trim(owner_phone)) > 0),
    address TEXT NOT NULL CHECK (length(trim(address)) > 0),
    credit_limit NUMERIC(12,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by BIGINT REFERENCES users(user_id)
);

CREATE INDEX idx_customers_normalized ON customers(shop_name_normalized);
```

**Notes:**

- `shop_name` = display name (e.g., "Sharma Namkeen") — **required, non-blank**
- `owner_name` = shop owner's full name — **required, non-blank**
- `owner_phone` = shop owner's phone number — **required, non-blank**
- `address` = shop location — **required, non-blank**
- `shop_name_normalized` = lowercase, stripped (e.g., "sharma namkeen") — used for fuzzy matching
- Every table references `customer_id` (FK), never name string — prevents duplicates
- `created_by` tracks who added the customer
- All text fields have `CHECK (length(trim(col)) > 0)` to prevent blank-only values

---

### 3. `sales` — All sales transactions
```sql
CREATE TABLE sales (
    id SERIAL PRIMARY KEY,
    sale_date DATE NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    quantity_kg NUMERIC(10,2) NOT NULL,
    rate_per_kg NUMERIC(10,2) NOT NULL,
    total_bill NUMERIC(12,2) GENERATED ALWAYS AS (quantity_kg * rate_per_kg) STORED,
    payment_status TEXT NOT NULL CHECK (payment_status IN ('paid', 'credited')),
    payment_mode TEXT CHECK (payment_mode IN ('cash', 'online')),
    notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL CHECK (length(trim(original_message)) > 0),
    confirmed_at TIMESTAMPTZ
);
```

**Notes:**

- One row per sale, regardless of payment type
- `total_bill` = computed column (auto-calculated)
- `payment_status`:
  - `'paid'` = customer paid immediately (cash or online)
  - `'credited'` = customer owes money (will be tracked in `credit_ledger`)
- `payment_mode` is NULL when `payment_status='credited'` (payment mode unknown until later)
- `original_message` = raw user input (audit trail: "50 kg Sharma 120 rate udhaar") — **required, non-blank**
- `confirmed_at` = timestamp when user tapped ✅ (proves user confirmed)

---

### 4. `credit_ledger` — Customer outstanding balances
```sql
CREATE TABLE credit_ledger (
    id SERIAL PRIMARY KEY,
    transaction_date DATE NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    sale_id INTEGER REFERENCES sales(id),
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('sale_credited', 'payment_received')),
    debit_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    credit_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL CHECK (length(trim(original_message)) > 0)
);

CREATE INDEX idx_credit_ledger_customer ON credit_ledger(customer_id);
```

**Two types of rows:**

1. **sale_credited**: When a sale is credited to a customer
   - `sale_id` = FK to the `sales` table
   - `transaction_type` = 'sale_credited'
   - `debit_amount` = total_bill (amount added to outstanding)
   - `credit_amount` = 0

2. **payment_received**: When a customer pays back
   - `sale_id` = NULL (payment is not tied to a specific sale)
   - `transaction_type` = 'payment_received'
   - `debit_amount` = 0
   - `credit_amount` = amount received (reduces outstanding)

**Notes:**

- `original_message` — **required, non-blank** (audit trail: raw user input)

**Atomic operation when sale is credited:**
```python
# Step 1: Insert into sales
sales_id = save_sale(customer_id=X, payment_status='credited', ...)

# Step 2: Insert into credit_ledger
INSERT INTO credit_ledger (
    customer_id=X,
    sale_id=sales_id,
    transaction_type='sale_credited',
    debit_amount=total_bill,
    credit_amount=0
)
# Both happen in one DB transaction
```

---

### 5. `customer_balance` — VIEW (Live outstanding)
```sql
CREATE VIEW customer_balance AS
SELECT
    c.id,
    c.shop_name,
    c.credit_limit,
    COALESCE(SUM(cl.debit_amount - cl.credit_amount), 0) AS outstanding_balance
FROM customers c
LEFT JOIN credit_ledger cl ON c.id = cl.customer_id AND cl.is_deleted = FALSE
WHERE c.is_deleted = FALSE
GROUP BY c.id, c.shop_name, c.credit_limit;
```

**Notes:**
- **Never stored** — computed on-the-fly from `credit_ledger`
- Always current (no sync issues)
- Filters out soft-deleted customers and credit_ledger entries
- Debit (sale_credited) adds to outstanding; Credit (payment) reduces it
- Query this view whenever you need current balance (only shows valid, non-deleted data)

---

### 6. `production_log` — Daily production
```sql
CREATE TABLE production_log (
    id SERIAL PRIMARY KEY,
    prod_date DATE NOT NULL,
    total_produced_kg NUMERIC(10,2) NOT NULL,
    total_packets INTEGER NOT NULL,
    batch_notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL CHECK (length(trim(original_message)) > 0)
);

CREATE INDEX idx_production_log_date ON production_log(prod_date);
```

**Notes:**

- `prod_date` is NOT unique — multiple batches in a day are allowed
- Agent warns if entry exists for this date: "आज पहले भी production दर्ज हो चुका है। फिर भी add करें?"
- User taps ✅ to add another, or ❌ to cancel
- `original_message` — **required, non-blank**

---

### 7. `cash_flow` — All cash movements
```sql
CREATE TABLE cash_flow (
    id SERIAL PRIMARY KEY,
    flow_date DATE NOT NULL,
    flow_type TEXT NOT NULL CHECK (flow_type IN ('in', 'out')),
    category TEXT NOT NULL CHECK (category IN (
        'sale_cash',
        'payment_received',
        'raw_material',
        'labour',
        'utilities',
        'transport',
        'packaging',
        'equipment',
        'loan_in',
        'loan_out',
        'owner_draw',
        'misc_in',
        'misc_out'
    )),
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    amount NUMERIC(12,2) NOT NULL,
    party TEXT NOT NULL CHECK (length(trim(party)) > 0),
    payment_mode TEXT CHECK (payment_mode IN ('cash', 'online')),
    notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL CHECK (length(trim(original_message)) > 0)
);

CREATE INDEX idx_cash_flow_date ON cash_flow(flow_date);
```

**Notes:**

- `flow_type`:
  - `'in'` = money received (sales, loans, investments)
  - `'out'` = money spent (expenses, repayments, withdrawals)
- `amount` = always positive (direction given by `flow_type`)
- `description` — **required, non-blank** (e.g., "Besan from supplier")
- `party` — **required, non-blank** (supplier/customer name for the transaction)
- `original_message` — **required, non-blank** (audit trail)
- Complete financial picture — sum all 'in', sum all 'out', compute net

---

### 8. `cash_position` — VIEW (Net cash)
```sql
CREATE VIEW cash_position AS
SELECT
    SUM(CASE WHEN flow_type = 'in'  THEN amount ELSE 0 END) AS total_in,
    SUM(CASE WHEN flow_type = 'out' THEN amount ELSE 0 END) AS total_out,
    SUM(CASE WHEN flow_type = 'in'  THEN amount ELSE -amount END) AS net_cash
FROM cash_flow;
```

---

### 9. `audit_log` — Complete trail
```sql
CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    action_type TEXT NOT NULL,
    table_affected TEXT NOT NULL,
    record_id INTEGER,
    user_id BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL,
    extracted_data JSONB NOT NULL,
    confirmed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_log_record ON audit_log(table_affected, record_id);
```

**Notes:**
- Every write triggers an audit log entry
- `action_type` = 'add_sale', 'add_payment', 'add_production', etc.
- `original_message` = raw user input (e.g., "sharma ko 50 kilo 120 rate udhaar")
- `extracted_data` = JSON of what LLM parsed (debugging when things go wrong)
- Query this to see the full trail: `SELECT * FROM audit_log WHERE table_affected='sales' AND record_id=42`

---

## Complete SQL to Run

Paste this entire block into Supabase SQL editor:

```sql
-- Users
CREATE TABLE users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Customers
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    shop_name TEXT NOT NULL,
    shop_name_normalized TEXT NOT NULL,
    owner_name TEXT,
    owner_phone TEXT,
    address TEXT,
    credit_limit NUMERIC(12,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by BIGINT REFERENCES users(user_id)
);
CREATE INDEX idx_customers_normalized ON customers(shop_name_normalized);

-- Sales
CREATE TABLE sales (
    id SERIAL PRIMARY KEY,
    sale_date DATE NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    quantity_kg NUMERIC(10,2) NOT NULL,
    rate_per_kg NUMERIC(10,2) NOT NULL,
    total_bill NUMERIC(12,2) GENERATED ALWAYS AS (quantity_kg * rate_per_kg) STORED,
    payment_status TEXT NOT NULL CHECK (payment_status IN ('paid', 'credited')),
    payment_mode TEXT CHECK (payment_mode IN ('cash', 'online')),
    notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL,
    confirmed_at TIMESTAMPTZ,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,  -- Soft delete; never hard DELETE
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES users(user_id)
);

-- Credit Ledger
CREATE TABLE credit_ledger (
    id SERIAL PRIMARY KEY,
    transaction_date DATE NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    sale_id INTEGER REFERENCES sales(id),
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('sale_credited', 'payment_received')),
    debit_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    credit_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES users(user_id)
);
CREATE INDEX idx_credit_ledger_customer ON credit_ledger(customer_id);

-- Customer Balance View (excludes soft-deleted rows)
CREATE VIEW customer_balance AS
SELECT
    c.id,
    c.shop_name,
    c.credit_limit,
    COALESCE(SUM(cl.debit_amount - cl.credit_amount), 0) AS outstanding_balance
FROM customers c
LEFT JOIN credit_ledger cl ON c.id = cl.customer_id AND cl.is_deleted = FALSE
GROUP BY c.id, c.shop_name, c.credit_limit;

-- Production Log
CREATE TABLE production_log (
    id SERIAL PRIMARY KEY,
    prod_date DATE NOT NULL,
    total_produced_kg NUMERIC(10,2) NOT NULL,
    total_packets INTEGER NOT NULL,
    batch_notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES users(user_id)
);
CREATE INDEX idx_production_log_date ON production_log(prod_date);

-- Cash Flow
CREATE TABLE cash_flow (
    id SERIAL PRIMARY KEY,
    flow_date DATE NOT NULL,
    flow_type TEXT NOT NULL CHECK (flow_type IN ('in', 'out')),
    category TEXT NOT NULL CHECK (category IN (
        'sale_cash', 'payment_received', 'raw_material', 'labour', 'utilities',
        'transport', 'packaging', 'equipment', 'loan_in', 'loan_out', 'owner_draw',
        'misc_in', 'misc_out'
    )),
    description TEXT NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    party TEXT,
    payment_mode TEXT CHECK (payment_mode IN ('cash', 'online')),
    notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES users(user_id)
);
CREATE INDEX idx_cash_flow_date ON cash_flow(flow_date);

-- Cash Position View
CREATE VIEW cash_position AS
SELECT
    SUM(CASE WHEN flow_type = 'in'  THEN amount ELSE 0 END) AS total_in,
    SUM(CASE WHEN flow_type = 'out' THEN amount ELSE 0 END) AS total_out,
    SUM(CASE WHEN flow_type = 'in'  THEN amount ELSE -amount END) AS net_cash
FROM cash_flow;

-- Audit Log
CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    action_type TEXT NOT NULL,
    table_affected TEXT NOT NULL,
    record_id INTEGER,
    user_id BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL,
    extracted_data JSONB NOT NULL,
    confirmed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_audit_log_record ON audit_log(table_affected, record_id);

-- TRIGGERS: Auto-generate cash flow entries (user doesn't think about them)

-- Trigger 1: Paid sale → immediate cash_flow IN entry
CREATE TRIGGER auto_cash_flow_paid_sale
AFTER INSERT ON sales
FOR EACH ROW
WHEN (NEW.payment_status = 'paid')
EXECUTE FUNCTION create_cash_flow_from_paid_sale();

-- Trigger 2: Payment received → cash_flow IN entry
CREATE TRIGGER auto_cash_flow_payment
AFTER INSERT ON credit_ledger
FOR EACH ROW
WHEN (NEW.transaction_type = 'payment_received')
EXECUTE FUNCTION create_cash_flow_from_payment();

-- Trigger 3: Expense (cash_flow OUT) is recorded directly by user
-- No trigger needed; user calls save_cash_flow() with flow_type='out'

-- Helper function for Trigger 1
CREATE OR REPLACE FUNCTION create_cash_flow_from_paid_sale()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO cash_flow (flow_date, flow_type, category, description, amount, party, payment_mode, recorded_by, original_message, is_deleted)
    VALUES (
        NEW.sale_date,
        'in',
        'sale_cash',
        'Sale to ' || (SELECT shop_name FROM customers WHERE id = NEW.customer_id),
        NEW.total_bill,
        (SELECT shop_name FROM customers WHERE id = NEW.customer_id),
        NEW.payment_mode,
        NEW.recorded_by,
        'Auto from sale_id=' || NEW.id,
        FALSE
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Helper function for Trigger 2
CREATE OR REPLACE FUNCTION create_cash_flow_from_payment()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO cash_flow (flow_date, flow_type, category, description, amount, party, recorded_by, original_message, is_deleted)
    VALUES (
        NEW.transaction_date,
        'in',
        'payment_received',
        'Payment from ' || (SELECT shop_name FROM customers WHERE id = NEW.customer_id),
        NEW.credit_amount,
        (SELECT shop_name FROM customers WHERE id = NEW.customer_id),
        NEW.recorded_by,
        'Auto from credit_ledger_id=' || NEW.id,
        FALSE
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

---

## Key Points

1. **No running_balance stored** — Computed live from `credit_ledger` via `customer_balance` view. Always correct, zero maintenance.

2. **Every credited sale creates 2 rows**:
   - `sales` row with `payment_status='credited'`
   - `credit_ledger` row with `sale_id=FK` (links them)

3. **Cash flow = complete picture** — 'sale_cash' is recorded here, and ALSO in `sales` table. Dual-entry for full visibility.

4. **Audit trail on everything** — `original_message` + `confirmed_at` means you can always trace back to what the user said and when they confirmed.

5. **Soft deletes only** — No `DELETE` statements ever. Records are marked `is_deleted=TRUE` with timestamp and who deleted. This prevents accidental data loss and preserves full history. Views automatically exclude soft-deleted rows.

6. **Customer balance view excludes deleted rows** — If a sale entry is marked deleted, it does not count toward outstanding balance. The financial view always reflects current valid data only.

7. **Normalize customer names** — `shop_name_normalized` enables fuzzy matching without creating duplicates.

8. **Cash flow is automatic** — PostgreSQL triggers ensure:
   - When a sale is recorded as 'paid' → cash_flow 'in' entry created automatically
   - When a payment is recorded → cash_flow 'in' entry created automatically
   - User never needs to manually track cash flow; the ledger is always consistent
   - This prevents divergence between sales/credit ledger and cash position
