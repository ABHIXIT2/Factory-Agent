-- Factory Agent PostgreSQL Schema (Supabase)
-- Paste all SQL into Supabase SQL editor and run

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
    created_by BIGINT REFERENCES users(user_id),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at TIMESTAMPTZ,
    deleted_by BIGINT REFERENCES users(user_id)
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
WHERE c.is_deleted = FALSE
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
FROM cash_flow
WHERE is_deleted = FALSE;

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

-- Helper function for Trigger 1: Paid sale → cash_flow IN
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

-- Helper function for Trigger 2: Payment received → cash_flow IN
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
