# Tools Reference — Phase 7

## Complete Tool Inventory (17 tools)

All tools with their parameters, return values, and use cases.

---

## 🔍 READ TOOLS (Inline execution, no confirmation needed)

### Customer Management

#### `search_customer(name_fragment: str) → {ok, results, count, selection_required?, customer_options?, not_found?}`

**When to use:** Always first when a customer name is mentioned.

**Returns:**
- `results: [{id, shop_name}]` — matching customers
- `selection_required: true` — if ambiguous matches (shows UI)
- `not_found: true` — if no matches (user can create)

**Example:**
```
User: "Sharma ko sale kar"
→ search_customer(name_fragment="Sharma")
← {ok: true, results: [{id: 3, shop_name: "Sharma Namkeen"}], count: 1}
```

---

#### `get_customer(customer_id: int) → {ok, customer?, not_found?}`

**When to use:** After search confirms one customer, to fetch full profile.

**Returns:**
- `customer: {id, shop_name, owner_name, owner_phone, address, credit_limit, created_at}`
- `not_found: true` — if customer doesn't exist

**Example:**
```
User: (after selecting Sharma) "Uski details dikhao"
→ get_customer(customer_id=3)
← {ok: true, customer: {id: 3, shop_name: "Sharma Namkeen", owner_name: "Sharma", owner_phone: "98765...", ...}}
```

---

#### `query_customers(name_fragment?, min_balance?, max_balance?, sort_by?, limit?) → {ok, customers, count}`

**When to use:** To list/filter customers by balance or name.

**Parameters:**
- `name_fragment` — fuzzy name search (optional)
- `min_balance`, `max_balance` — filter by outstanding (optional)
- `sort_by` — "shop_name" (default), "outstanding_desc", "outstanding_asc"
- `limit` — max results (capped at MAX_CUSTOMERS_RETURNED=50)

**Returns:**
- `customers: [{id, shop_name, credit_limit, outstanding_balance}]`
- `count: int`

**Example:**
```
User: "Jinke upar 5000 se zyada baaki hai?"
→ query_customers(min_balance=5000, sort_by="outstanding_desc", limit=10)
← {ok: true, customers: [{id: 3, shop_name: "Sharma Namkeen", outstanding_balance: 8000}, ...], count: 3}
```

---

### Balance Queries

#### `get_customer_balance(customer_id: int) → {ok, balance?, not_found?}`

**When to use:** Quick check of one customer's balance.

**Returns:**
- `balance: {id, shop_name, credit_limit, outstanding_balance}`
- `not_found: true` — if customer doesn't exist

**Example:**
```
User: "Sharma ka baaki kya hai?"
→ get_customer_balance(customer_id=3)
← {ok: true, balance: {id: 3, shop_name: "Sharma Namkeen", outstanding_balance: 8000, credit_limit: 10000}}
```

---

#### `get_all_balances(sort_by?, limit?) → {ok, balances, count, total_outstanding, formatted_total}`

**When to use:** "Show me all balances", "Top debtors", etc.

**Parameters:**
- `sort_by` — "outstanding_desc" (default, top debtors), "outstanding_asc"
- `limit` — max results (capped at MAX_BALANCES_RETURNED=20)

**Returns:**
- `balances: [{shop_name, outstanding_balance, credit_limit}]`
- `count: int`
- `total_outstanding: float` — sum of all balances
- `formatted_total: string` — formatted currency

**Example:**
```
User: "Sabke balances dikhao"
→ get_all_balances(sort_by="outstanding_desc", limit=20)
← {ok: true, balances: [...], count: 5, total_outstanding: 25000, formatted_total: "₹25,000.00"}
```

---

### Sales History

#### `query_sales(customer_id?, date_from?, date_to?, limit?) → {ok, sales, count}`

**When to use:** "Show sales for this customer", "Sales on this date", etc.

**Parameters:**
- `customer_id` — filter by customer (optional)
- `date_from`, `date_to` — YYYY-MM-DD (optional)
- `limit` — max results (capped at MAX_SALES_RETURNED=20)

**Returns:**
- `sales: [{id, sale_date, customer_id, shop_name, quantity_kg, rate_per_kg, total_bill, payment_status, ...}]`
- `count: int`

**Example:**
```
User: "Sharma ki sales this month?"
→ query_sales(customer_id=3, date_from="2025-05-01", date_to="2025-05-31")
← {ok: true, sales: [{...}, {...}], count: 3}
```

---

### Production Tracking

#### `query_production(date_from?, date_to?, limit?) → {ok, production, count, total_kg}`

**When to use:** "Production last week?", "Today's output?", etc.

**Parameters:**
- `date_from`, `date_to` — YYYY-MM-DD (optional)
- `limit` — max results (capped at MAX_PRODUCTION_RETURNED=20)

**Returns:**
- `production: [{id, prod_date, total_produced_kg, total_packets, batch_notes, ...}]`
- `count: int`
- `total_kg: float` — sum of kg produced in period

**Example:**
```
User: "Is hafte kitna production hua?"
→ query_production(date_from="2025-05-05", date_to="2025-05-09")
← {ok: true, production: [{prod_date: "2025-05-05", total_produced_kg: 500, total_packets: 100}, ...], count: 5, total_kg: 2500}
```

---

### Credit Ledger (NEW — Payment History)

#### `query_credit_ledger(customer_id?, transaction_type?, date_from?, date_to?, limit?) → {ok, ledger, count, total_debit, total_credit}`

**When to use:** "Sharma ke payments?", "Payment history for customer", "When did Gupta last pay?", etc.

**Parameters:**
- `customer_id` — filter by customer (optional)
- `transaction_type` — "sale_credited" (credits added) or "payment_received" (payments made) (optional)
- `date_from`, `date_to` — YYYY-MM-DD (optional)
- `limit` — max results (capped at MAX_LEDGER_RETURNED=30)

**Returns:**
- `ledger: [{id, transaction_date, customer_id, sale_id?, transaction_type, debit_amount, credit_amount, notes, ...}]`
- `count: int`
- `total_debit: float` — sum of credits added
- `total_credit: float` — sum of payments received

**Example:**
```
User: "Sharma ke payments dikhao (last 3 months)"
→ query_credit_ledger(customer_id=3, transaction_type="payment_received", date_from="2025-02-09", date_to="2025-05-09")
← {ok: true, ledger: [{transaction_date: "2025-04-15", credit_amount: 5000}, {...}], count: 8, total_credit: 15000}
```

---

### Cash Flow

#### `query_cash_flow(date_from?, date_to?, flow_type?, category?, limit?) → {ok, cash_flows, count, total_in, total_out}`

**When to use:** "Cash flow last week?", "Where did money go?", "Expenses in May?", etc.

**Parameters:**
- `date_from`, `date_to` — YYYY-MM-DD (optional)
- `flow_type` — "in" or "out" (optional)
- `category` — one of: sale_cash, payment_received, raw_material, labour, utilities, transport, packaging, equipment, loan_in, loan_out, owner_draw, misc_in, misc_out (optional, fuzzy match)
- `limit` — max results (capped at MAX_CASH_FLOW_RETURNED=30)

**Returns:**
- `cash_flows: [{id, flow_date, flow_type, category, description, amount, party, payment_mode, notes, ...}]`
- `count: int`
- `total_in: float` — total inflows
- `total_out: float` — total outflows

**Example:**
```
User: "Aaj ka cash flow?"
→ query_cash_flow(date_from="2025-05-09", date_to="2025-05-09")
← {ok: true, cash_flows: [{flow_type: "in", category: "sale_cash", amount: 10000}, {flow_type: "out", category: "raw_material", amount: 5000}, ...], total_in: 15000, total_out: 5000}
```

---

### Financial Summary

#### `get_cash_position(date_from?, date_to?) → {ok, total_in, total_out, net_cash, formatted_in, formatted_out, formatted_net}`

**When to use:** "Cash position today?", "Net cash this week?", "How much in/out?", etc.

**Parameters:**
- `date_from`, `date_to` — YYYY-MM-DD (optional; if absent, returns all-time)

**Returns:**
- `total_in: float` — total cash received
- `total_out: float` — total cash spent
- `net_cash: float` — net (in - out)
- `formatted_*: string` — currency-formatted versions

**Example:**
```
User: "Week ka cash position?"
→ get_cash_position(date_from="2025-05-05", date_to="2025-05-09")
← {ok: true, total_in: 150000, total_out: 95000, net_cash: 55000, formatted_in: "₹150,000.00", ...}
```

---

## ✏️ WRITE TOOLS (Deferred, require confirmation)

All write tools show **[✅ Confirm][❌ Cancel]** inline buttons before executing.

### Customer Management

#### `create_customer(shop_name, owner_name, owner_phone, address, credit_limit?) → {ok, customer_id, shop_name}`

**Parameters:**

- `shop_name` — **required**, non-blank shop name
- `owner_name` — **required**, shop owner's full name
- `owner_phone` — **required**, owner's phone number
- `address` — **required**, shop location
- `credit_limit` — optional, number (default 0)

**Chaining:** After confirmation, if the original message wasn't a create request, the agent re-runs it (e.g., "Naya customer Patel ko sale kar" → creates Patel → re-runs sale).

---

### Sales

#### `save_sale(customer_id, qty_kg, rate_per_kg, sale_date, payment_status, payment_mode?, notes?, original_message)`

**Parameters:**

- `customer_id` — **required**, from search_customer
- `qty_kg`, `rate_per_kg` — **required**, numbers
- `sale_date` — **required**, YYYY-MM-DD
- `payment_status` — **required**, "paid" or "credited"
- `payment_mode` — optional, "cash" or "online" (only for paid sales)
- `original_message` — **required**, non-blank (audit trail: raw user input)
- `notes` — optional

---

### Payments

#### `record_payment(customer_id, amount, payment_date, payment_mode?, notes?, original_message)`

**Parameters:**

- `customer_id` — **required**, from search_customer
- `amount` — **required**, number (> 0)
- `payment_date` — **required**, YYYY-MM-DD
- `payment_mode` — optional, "cash" or "online"
- `original_message` — **required**, non-blank (audit trail: raw user input)
- `notes` — optional

---

### Production

#### `save_production(prod_date, total_produced_kg, total_packets, notes?, original_message)`

**Parameters:**

- `prod_date` — **required**, YYYY-MM-DD
- `total_produced_kg` — **required**, number (> 0)
- `total_packets` — **required**, integer (>= 0)
- `original_message` — **required**, non-blank (audit trail: raw user input)
- `notes` — optional

---

### Cash Flow

#### `save_cash_flow(flow_date, flow_type, category, description, amount, party, payment_mode?, notes?, original_message)`

**Parameters:**

- `flow_date` — **required**, YYYY-MM-DD
- `flow_type` — **required**, "in" or "out"
- `category` — **required**, one of: sale_cash, payment_received, raw_material, labour, utilities, transport, packaging, equipment, loan_in, loan_out, owner_draw, misc_in, misc_out
- `description` — **required**, non-blank (e.g., "Besan from supplier")
- `amount` — **required**, number (> 0)
- `party` — **required**, non-blank (supplier/customer name for the transaction)
- `original_message` — **required**, non-blank (audit trail: raw user input)
- `payment_mode` — optional, "cash" or "online"
- `notes` — optional

---

### Delete (NEW)

#### `delete_record(table, record_id, reason?) → {ok, success}`

**When to use:** "Wrong entry", "Delete that", "Undo that", etc.

**Parameters:**
- `table` — **required**, one of: "sales", "credit_ledger", "production_log", "cash_flow"
- `record_id` — **required**, integer
- `reason` — optional, explanation for audit log

**Behavior:**
- Soft-deletes: marks `is_deleted=TRUE`
- Audit-logged with reason
- Recoverable: admin can UPDATE is_deleted=FALSE in SQL
- Balances automatically recompute (soft-deleted rows excluded from views)

**Example:**
```
User: "Wo galat sale tha, delete kar do"
→ delete_record(table="sales", record_id=42, reason="User said galat")
← Shows [✅ Confirm Delete][❌ Cancel] buttons
User taps ✅:
← {ok: true, success: true}
Agent: "✅ Sale #42 deleted. Sharma ka baqaya ab ₹2000."
```

---

## 📊 Summary by Entity

| Entity | Tools | Status |
|--------|-------|--------|
| **Customers** | search, create, get, query | ✅ Full CRUD |
| **Sales** | save, query, delete | ✅ Full CRUD (no update; soft-delete) |
| **Payments/Credit** | record, query_credit_ledger, delete | ✅ Full coverage |
| **Production** | save, query, delete | ✅ Full CRUD |
| **Cash Flow** | save, query, delete, get_position | ✅ Full coverage |

---

## 🎯 Common Use Cases

| User Request | Tools Called |
|--------------|--------------|
| "Sharma ko 5kg sale kar" | search_customer → save_sale (deferred) |
| "Sharma ke balance?" | search_customer → get_customer_balance |
| "Top debtors?" | get_all_balances |
| "Jinke 5000+ baaki?" | query_customers(min_balance=5000, sort_by=outstanding_desc) |
| "Sharma ke payment history?" | search_customer → query_credit_ledger(transaction_type=payment_received) |
| "This week production?" | query_production(date_from=..., date_to=...) |
| "Cash flow today?" | query_cash_flow(date_from=today) |
| "Net cash this month?" | get_cash_position(date_from=..., date_to=...) |
| "Wo galat sale tha" | delete_record(table=sales, record_id=...) (deferred) |
| "Naya customer Patel ko sale kar" | create_customer → search_customer → save_sale (chained) |

---

## 🔗 Tool Dependency Graph

```
search_customer
  ├─> get_customer (for full profile)
  ├─> get_customer_balance (for balance check)
  ├─> query_credit_ledger (for payment history)
  ├─> query_sales (for sale history)
  └─> save_sale (create new sale)
  └─> record_payment (record payment)

query_customers (lists without search)

query_production
query_cash_flow
get_cash_position
get_all_balances

delete_record (works on any transactional table)
```

---

## ⚙️ Configuration

All limits are configurable via env vars:

| Var | Default | Purpose |
|-----|---------|---------|
| `MAX_CUSTOMERS_RETURNED` | 50 | Limit for query_customers |
| `MAX_SALES_RETURNED` | 20 | Limit for query_sales |
| `MAX_PRODUCTION_RETURNED` | 20 | Limit for query_production |
| `MAX_CASH_FLOW_RETURNED` | 30 | Limit for query_cash_flow |
| `MAX_LEDGER_RETURNED` | 30 | Limit for query_credit_ledger |
| `MAX_BALANCES_RETURNED` | 20 | Limit for get_all_balances |

Set in `.env` to customize: `MAX_CUSTOMERS_RETURNED=100`
