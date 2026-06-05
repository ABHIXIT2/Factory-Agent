-- Factory Agent PostgreSQL Schema (Supabase)
-- Paste all SQL into Supabase SQL editor and run

-- ============================================================
-- TABLES
-- ============================================================

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
    shop_name TEXT NOT NULL CHECK (length(trim(shop_name)) > 0),
    shop_name_normalized TEXT NOT NULL,
    owner_name TEXT NOT NULL CHECK (length(trim(owner_name)) > 0),
    owner_phone TEXT NOT NULL CHECK (length(trim(owner_phone)) > 0),
    address TEXT NOT NULL CHECK (length(trim(address)) > 0),
    credit_limit NUMERIC(12,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by BIGINT REFERENCES users(user_id),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES users(user_id)
);

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
    original_message TEXT NOT NULL CHECK (length(trim(original_message)) > 0),
    confirmed_at TIMESTAMPTZ,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
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
    payment_mode TEXT CHECK (payment_mode IN ('cash', 'online')),
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL CHECK (length(trim(original_message)) > 0),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES users(user_id)
);

-- Production Log
CREATE TABLE production_log (
    id SERIAL PRIMARY KEY,
    prod_date DATE NOT NULL,
    total_produced_kg NUMERIC(10,2) NOT NULL,
    total_packets INTEGER NOT NULL,
    batch_notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL CHECK (length(trim(original_message)) > 0),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES users(user_id)
);

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
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    amount NUMERIC(12,2) NOT NULL,
    party TEXT NOT NULL CHECK (length(trim(party)) > 0),
    payment_mode TEXT CHECK (payment_mode IN ('cash', 'online')),
    notes TEXT,
    recorded_at TIMESTAMPTZ DEFAULT NOW(),
    recorded_by BIGINT REFERENCES users(user_id),
    original_message TEXT NOT NULL CHECK (length(trim(original_message)) > 0),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES users(user_id)
);

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

-- ============================================================
-- FIX 1: ROW LEVEL SECURITY
-- Blocks all anon/authenticated access. Your bot uses the
-- service_role key which bypasses RLS, so it is unaffected.
-- ============================================================

ALTER TABLE users          ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers      ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales          ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_ledger  ENABLE ROW LEVEL SECURITY;
ALTER TABLE production_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE cash_flow      ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log      ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- FIX 2: INDEXES
-- (a) Keep existing indexes for date/name lookups
-- (b) Add missing indexes on all foreign key columns
-- ============================================================

-- Existing indexes (kept)
CREATE INDEX idx_customers_normalized   ON customers(shop_name_normalized);
CREATE INDEX idx_credit_ledger_customer ON credit_ledger(customer_id);
CREATE INDEX idx_production_log_date    ON production_log(prod_date);
CREATE INDEX idx_cash_flow_date         ON cash_flow(flow_date);
CREATE INDEX idx_audit_log_record       ON audit_log(table_affected, record_id);

-- customers: missing FK indexes
CREATE INDEX idx_customers_created_by  ON customers(created_by);
CREATE INDEX idx_customers_deleted_by  ON customers(deleted_by);

-- sales: missing FK indexes
CREATE INDEX idx_sales_customer        ON sales(customer_id);
CREATE INDEX idx_sales_recorded_by     ON sales(recorded_by);
CREATE INDEX idx_sales_deleted_by      ON sales(deleted_by);

-- credit_ledger: missing FK indexes
CREATE INDEX idx_credit_ledger_sale        ON credit_ledger(sale_id);
CREATE INDEX idx_credit_ledger_recorded_by ON credit_ledger(recorded_by);
CREATE INDEX idx_credit_ledger_deleted_by  ON credit_ledger(deleted_by);

-- production_log: missing FK indexes
CREATE INDEX idx_production_log_recorded_by ON production_log(recorded_by);
CREATE INDEX idx_production_log_deleted_by  ON production_log(deleted_by);

-- cash_flow: missing FK indexes
CREATE INDEX idx_cash_flow_recorded_by ON cash_flow(recorded_by);
CREATE INDEX idx_cash_flow_deleted_by  ON cash_flow(deleted_by);

-- audit_log: missing FK index
CREATE INDEX idx_audit_log_user ON audit_log(user_id);

-- ============================================================
-- FIX 3: VIEWS WITH SECURITY INVOKER
-- Recreated with security_invoker=true so they respect RLS
-- on the underlying tables instead of bypassing it.
-- ============================================================

CREATE VIEW customer_balance
WITH (security_invoker = true)
AS
SELECT
    c.id,
    c.shop_name,
    c.credit_limit,
    COALESCE(SUM(cl.debit_amount - cl.credit_amount), 0) AS outstanding_balance
FROM customers c
LEFT JOIN credit_ledger cl ON c.id = cl.customer_id AND cl.is_deleted = FALSE
WHERE c.is_deleted = FALSE
GROUP BY c.id, c.shop_name, c.credit_limit;

CREATE VIEW cash_position
WITH (security_invoker = true)
AS
SELECT
    SUM(CASE WHEN flow_type = 'in'  THEN amount ELSE 0 END) AS total_in,
    SUM(CASE WHEN flow_type = 'out' THEN amount ELSE 0 END) AS total_out,
    SUM(CASE WHEN flow_type = 'in'  THEN amount ELSE -amount END) AS net_cash
FROM cash_flow
WHERE is_deleted = FALSE;

-- ============================================================
-- FIX 4: TRIGGER FUNCTIONS WITH PINNED SEARCH PATH
-- SET search_path = public prevents schema injection attacks
-- where a fake "customers" table in another schema could be
-- substituted for the real one.
-- ============================================================

CREATE OR REPLACE FUNCTION create_cash_flow_from_paid_sale()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    INSERT INTO cash_flow (
        flow_date, flow_type, category, description,
        amount, party, payment_mode, recorded_by,
        original_message, is_deleted
    )
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
$$;

CREATE OR REPLACE FUNCTION create_cash_flow_from_payment()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    INSERT INTO cash_flow (
        flow_date, flow_type, category, description,
        amount, party, payment_mode, recorded_by,
        original_message, is_deleted
    )
    VALUES (
        NEW.transaction_date,
        'in',
        'payment_received',
        'Payment from ' || (SELECT shop_name FROM customers WHERE id = NEW.customer_id),
        NEW.credit_amount,
        (SELECT shop_name FROM customers WHERE id = NEW.customer_id),
        NEW.payment_mode,
        NEW.recorded_by,
        'Auto from credit_ledger_id=' || NEW.id,
        FALSE
    );
    RETURN NEW;
END;
$$;

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
