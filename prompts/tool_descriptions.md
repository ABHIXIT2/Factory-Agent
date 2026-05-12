# Tool Descriptions

Each block is a tool description loaded into the OpenAI-compatible TOOLS schema. Schema-first: type, required fields, enums, one canonical example. All numeric fields are JSON numbers, never strings.

## search_customer

Resolve a customer by partial shop name. Call this before any sale, payment, balance check, or delete that names a customer. If multiple match, the harness shows a selection UI and the next user turn carries the chosen `customer_id`.

- `name_fragment` (string, required) — partial shop name, case-insensitive

Example: `search_customer({"name_fragment":"Sharma"})`

## create_customer

Create a new customer when `search_customer` returned no match and the user confirmed the new name. Returns `customer_id` for use in the next call.

- `shop_name` (string, required)
- `owner_name` (string, required)
- `owner_phone` (string, required) — kept as string
- `address` (string, required)
- `credit_limit` (number, optional, default 0)

Example: `create_customer({"shop_name":"Patel Stores","owner_name":"Ramesh Patel","owner_phone":"9876543210","address":"Sadar Bazar, Indore","credit_limit":10000})`

## save_sale

Record one sale. Requires a `customer_id` from `search_customer`.

- `customer_id` (number, required)
- `qty_kg` (number, required, > 0)
- `rate_per_kg` (number, required, > 0)
- `sale_date` (string, required, `YYYY-MM-DD`)
- `payment_status` (enum, required) — `"paid"` or `"credited"`
- `payment_mode` (enum, optional when `paid`) — `"cash"` or `"online"`. Omit when `credited`.
- `notes` (string, optional)

Example: `save_sale({"customer_id":3,"qty_kg":50,"rate_per_kg":120,"sale_date":"2026-05-09","payment_status":"credited"})`

## record_payment

Record a customer paying down their outstanding balance. Requires `customer_id` from `search_customer`.

- `customer_id` (number, required)
- `amount` (number, required, > 0)
- `payment_date` (string, required, `YYYY-MM-DD`)
- `payment_mode` (enum, required) — `"cash"` or `"online"`
- `notes` (string, optional)

Example: `record_payment({"customer_id":7,"amount":5000,"payment_date":"2026-05-09","payment_mode":"cash"})`

## get_customer

Full profile for one customer (id, shop_name, owner, phone, address, credit_limit, created_at). Use after a search resolves to one id when you need the full record.

- `customer_id` (number, required)

## query_customers

List or filter customers. Supports name fragment, balance range, and sort. Use this for "show me who owes more than X" or "list all Sharmas".

- `name_fragment` (string, optional)
- `min_balance` (number, optional)
- `max_balance` (number, optional)
- `sort_by` (enum, optional, default `"shop_name"`) — `"outstanding_desc"`, `"outstanding_asc"`, `"shop_name"`
- `limit` (number, optional, default 50)

## get_customer_balance

Single customer's outstanding balance and credit limit. Quick check.

- `customer_id` (number, required)

## get_all_balances

Top-N balances sorted by largest or smallest debtor. Use for "who owes the most?". For shop-name sort or filtered lists, use `query_customers`.

- `sort_by` (enum, optional, default `"outstanding_desc"`) — `"outstanding_desc"`, `"outstanding_asc"`
- `limit` (number, optional, default 50)

## save_production

Record one production batch. Multiple batches per day are allowed.

- `prod_date` (string, required, `YYYY-MM-DD`)
- `total_produced_kg` (number, required, > 0)
- `total_packets` (number, required, integer ≥ 0)
- `notes` (string, optional)

Example: `save_production({"prod_date":"2026-05-09","total_produced_kg":500,"total_packets":100})`

## query_production

Production entries over a date range. Returns rows plus `total_kg`.

- `date_from` (string, optional, `YYYY-MM-DD`)
- `date_to` (string, optional, `YYYY-MM-DD`)
- `limit` (number, optional, default 50)

## save_cash_flow

Record income or expense. Sales paid in cash and customer payments are auto-recorded by triggers — call this only for direct expenses or non-sale income (raw materials, labour, loans, owner draw, misc).

- `flow_date` (string, required, `YYYY-MM-DD`)
- `flow_type` (enum, required) — `"in"` or `"out"`
- `category` (enum, required) — one of: `"sale_cash"`, `"payment_received"`, `"raw_material"`, `"labour"`, `"utilities"`, `"transport"`, `"packaging"`, `"equipment"`, `"loan_in"`, `"loan_out"`, `"owner_draw"`, `"misc_in"`, `"misc_out"`
- `description` (string, required)
- `amount` (number, required, > 0)
- `party` (string, required)
- `payment_mode` (enum, optional) — `"cash"` or `"online"`
- `notes` (string, optional)

Example: `save_cash_flow({"flow_date":"2026-05-09","flow_type":"out","category":"raw_material","description":"Besan from supplier","amount":12000,"party":"Raj Suppliers","payment_mode":"cash"})`

## query_cash_flow

Filtered cash flow entries with `total_in`/`total_out`. Use for "kharcha kitna hua", "this week's expenses", "where did money come from".

- `date_from` (string, optional, `YYYY-MM-DD`)
- `date_to` (string, optional, `YYYY-MM-DD`)
- `flow_type` (enum, optional) — `"in"` or `"out"`
- `category` (enum, optional) — same set as `save_cash_flow`
- `limit` (number, optional, default 50)

## query_credit_ledger

The audit trail behind a customer's balance — every `sale_credited` and `payment_received` row. Call this when the user asks "kab paise diye?", "yeh balance kyun hai?", or "Sharma ka history dikhao". Don't infer payment history from `get_customer_balance`; query the ledger.

- `customer_id` (number, optional)
- `transaction_type` (enum, optional) — `"sale_credited"` or `"payment_received"`
- `date_from` (string, optional, `YYYY-MM-DD`)
- `date_to` (string, optional, `YYYY-MM-DD`)
- `limit` (number, optional, default 50)

## query_sales

Sales rows with optional customer and date filter. Returns qty, rate, total, payment status, customer name.

- `customer_id` (number, optional)
- `date_from` (string, optional, `YYYY-MM-DD`)
- `date_to` (string, optional, `YYYY-MM-DD`)
- `limit` (number, optional, default 50)

## delete_record

Soft-delete one row. Marks `is_deleted=TRUE`; views auto-exclude it; data is recoverable. The harness shows `✅ Confirm Delete` / `❌ Cancel` — never render your own. Use when the user says "galat tha", "delete", "undo", or "cancel that entry".

- `table` (enum, required) — `"sales"`, `"credit_ledger"`, `"production_log"`, `"cash_flow"`
- `record_id` (number, required)
- `reason` (string, required in practice) — short specific reason for the audit log (e.g. `"qty wrong: was 5kg, should be 50kg"`). Always provide one.

Example: `delete_record({"table":"sales","record_id":42,"reason":"qty wrong, user correction"})`

## get_cash_position

Total in, total out, net cash — all-time or over a date range.

- `date_from` (string, optional, `YYYY-MM-DD`)
- `date_to` (string, optional, `YYYY-MM-DD`)

Example: `get_cash_position({"date_from":"2026-05-01","date_to":"2026-05-09"})`