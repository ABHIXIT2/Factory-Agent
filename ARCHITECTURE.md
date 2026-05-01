# System Architecture

How the bot works end-to-end: from user message to database record.

---

## System Flow (Data Path)

```
┌─────────────────────────────────────────────────────────────┐
│ User sends message on Telegram                              │
│ "Sharma ko 50 kilo 120 rate pe udhaar"                      │
└──────────────────────┬──────────────────────────────────────┘
                       │
         ┌─────────────▼──────────────┐
         │ bot.py (Telegram Handler)  │
         │ - Receive message          │
         │ - Load user context        │
         │ - Pass to agent loop       │
         └─────────────┬──────────────┘
                       │
         ┌─────────────▼────────────────────────────────────────────┐
         │ agent.py (Groq LLM Loop)                                │
         │                                                         │
         │ 1. Build context:                                       │
         │    - System prompt (Hindi/Hinglish detection)           │
         │    - User message                                       │
         │    - Tool schemas (list of what agent can do)           │
         │    - Conversation history (optional, for multi-turn)    │
         │                                                         │
         │ 2. Call Groq API                                        │
         │    - Send context + tools                              │
         │    - Receive: tool_calls or final_response              │
         │                                                         │
         │ 3. If tool calls:                                       │
         │    - Loop: dispatch → execute → collect results         │
         │    - Pass results back to LLM                           │
         │                                                         │
         │ 4. If final_response:                                   │
         │    - Exit loop                                          │
         │    - Send response to user via bot                      │
         └──────────────────┬─────────────────────────────────────┘
                            │
         ┌──────────────────▼────────────────────────┐
         │ tools.py (Business Logic)                │
         │                                          │
         │ Tool examples:                           │
         │ - search_customer("Sharma")              │
         │ - validate_amount(50)                    │
         │ - parse_date("aaj")                      │
         │ - confirm_and_save_sale(...)             │
         │                                          │
         │ Each tool calls db.py functions          │
         └──────────────────┬──────────────────────┘
                            │
         ┌──────────────────▼────────────────────────┐
         │ db.py (Database Operations)              │
         │                                          │
         │ - Connect to Supabase                    │
         │ - Execute queries                       │
         │ - Return results                        │
         │ - Handle transactions (atomic ops)      │
         └──────────────────┬──────────────────────┘
                            │
         ┌──────────────────▼────────────────────────┐
         │ PostgreSQL (Supabase)                    │
         │                                          │
         │ - Customers table                       │
         │ - Sales table                           │
         │ - Credit ledger                         │
         │ - Cash flow                             │
         │ - Audit log                             │
         └──────────────────────────────────────────┘
```

---

## Example: Adding a Credited Sale (The Full Flow)

### Message
```
User: "Sharma ko 50 kilo 120 rate pe udhaar"
```

### Step 1: Bot Handler (bot.py)
```python
async def handle_message(update, context):
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Pass to agent
    response = await agent_loop(message_text, user_id)
    
    # Send back to user
    await update.message.reply_text(response)
```

### Step 2: Agent Initialization (agent.py)
LLM is called with:
```json
{
  "system_prompt": "You are a factory operations assistant...",
  "messages": [
    {"role": "user", "content": "Sharma ko 50 kilo 120 rate pe udhaar"}
  ],
  "tools": [
    {
      "name": "search_customer",
      "description": "Find customer by name fragment",
      "parameters": {"name": "name_fragment", "type": "string"}
    },
    {
      "name": "create_customer",
      "description": "Create new customer"
    },
    {
      "name": "confirm_and_save_sale",
      "description": "Save a credited sale"
    },
    ...
  ]
}
```

### Step 3: LLM Thinks & Calls Tool
Groq returns:
```json
{
  "thought": "User mentioned Sharma. I need to find the customer first.",
  "action": "search_customer",
  "action_input": {"name_fragment": "Sharma"}
}
```

### Step 4: Tool Execution (tools.py → db.py)
```python
def search_customer(name_fragment):
    results = db.search_customer(name_fragment)
    # Returns: [{id: 3, shop_name: "Sharma Namkeen", score: 0.91}]
    return results
```

Tool calls `db.search_customer()`:
```python
def search_customer(name_fragment):
    sql = """
    SELECT id, shop_name, similarity(shop_name_normalized, %s) AS score
    FROM customers
    WHERE shop_name_normalized LIKE %s
    ORDER BY score DESC
    LIMIT 5
    """
    results = supabase.execute(sql, [name_fragment.lower(), f"%{name_fragment.lower()}%"])
    return results
```

### Step 5: LLM Gets Results, Asks for Confirmation
Groq receives the search results and generates:
```json
{
  "thought": "Found Sharma Namkeen. I should confirm with user before proceeding.",
  "final_response": "क्या यह सही customer है?\n\n🏪 Sharma Namkeen\n\n[✅ हाँ] [❌ नहीं]"
}
```

Bot sends confirmation card with inline buttons.

### Step 6: User Taps ✅
Bot receives callback from the button.

### Step 7: Agent Continues (Multi-Turn)
Context updated with confirmation:
```json
{
  "messages": [
    {"role": "user", "content": "Sharma ko 50 kilo 120 rate pe udhaar"},
    {"role": "assistant", "content": "क्या यह सही customer है? Sharma Namkeen [✅ हाँ] [❌ नहीं]"},
    {"role": "user", "content": "[✅ Confirmed Sharma Namkeen, customer_id=3]"}
  ]
}
```

LLM continues:
```json
{
  "thought": "Good, user confirmed. Now I have: customer_id=3, qty=50kg, rate=120, payment=credited. I should confirm this sale.",
  "action": "confirm_and_save_sale",
  "action_input": {
    "customer_id": 3,
    "qty_kg": 50,
    "rate_per_kg": 120,
    "sale_date": "2026-05-01",
    "payment_status": "credited",
    "original_message": "Sharma ko 50 kilo 120 rate pe udhaar"
  }
}
```

### Step 8: Save to Database (Atomic Transaction)
Tool calls `confirm_and_save_sale()`:

```python
def confirm_and_save_sale(customer_id, qty_kg, rate_per_kg, sale_date, payment_status, original_message):
    """
    Atomic operation: insert into sales, then credit_ledger
    """
    try:
        # Transaction 1: Save sale
        sale_id = db.execute("""
            INSERT INTO sales (customer_id, quantity_kg, rate_per_kg, sale_date, 
                             payment_status, recorded_by, original_message, confirmed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id
        """, [customer_id, qty_kg, rate_per_kg, sale_date, payment_status, user_id, original_message])
        
        # Transaction 2: If credited, add to credit_ledger
        if payment_status == 'credited':
            total_bill = qty_kg * rate_per_kg
            db.execute("""
                INSERT INTO credit_ledger 
                  (customer_id, sale_id, transaction_date, transaction_type, 
                   debit_amount, recorded_by, original_message)
                VALUES (%s, %s, %s, 'sale_credited', %s, %s, %s)
            """, [customer_id, sale_id, sale_date, total_bill, user_id, original_message])
        
        # Transaction 3: Audit log
        db.execute("""
            INSERT INTO audit_log (action_type, table_affected, record_id, user_id, 
                                   original_message, extracted_data)
            VALUES ('add_sale', 'sales', %s, %s, %s, %s)
        """, [sale_id, user_id, original_message, json.dumps({...})])
        
        return {"success": True, "sale_id": sale_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

### Step 9: LLM Gets Result & Sends Final Response
```json
{
  "thought": "Sale saved successfully. Let me inform the user.",
  "final_response": "✅ Sale save ho gayi!\n\n📦 Sharma Namkeen\n50 kg @ ₹120/kg = ₹6,000\n💳 Credited\n\n📊 Sharma ka baqaya: ₹14,800"
}
```

Bot sends this message to user.

### Database State After
```
Sales table:
| id | sale_date | customer_id | qty_kg | rate_per_kg | total_bill | payment_status | ... |
| 42 | 2026-05-01|      3      |   50   |    120      |   6000     | credited       | ... |

Credit ledger:
| id | customer_id | sale_id | transaction_type | debit_amount | credit_amount | ... |
| 87 |      3      |   42    | sale_credited    |     6000     |       0       | ... |

Customer balance (view):
| id | shop_name        | outstanding_balance |
| 3  | Sharma Namkeen   |       14,800        |

Audit log:
| id | action_type | table_affected | record_id | original_message          | extracted_data |
|118 | add_sale    | sales          |    42     | Sharma ko 50 kilo... | {...json...}   |
```

---

## Tool Schemas (What LLM Can Call)

### Customer Management
```python
search_customer(name_fragment: str) → [{id, shop_name, score}, ...]
create_customer(shop_name, owner_name=None, owner_phone=None, address=None, credit_limit=0) → {success, customer_id}
list_customers() → [{id, shop_name, credit_limit}, ...]
```

### Sales
```python
confirm_and_save_sale(customer_id, qty_kg, rate_per_kg, sale_date, payment_status, payment_mode=None, notes=None, original_message) → {success, sale_id}
query_sales(customer_id=None, date_from=None, date_to=None) → [sale_rows]
```

### Credit
```python
get_customer_balance(customer_id) → {shop_name, outstanding_balance, credit_limit}
get_all_balances(sort_by="outstanding_desc") → [{shop_name, outstanding_balance, credit_limit}, ...]
record_payment(customer_id, amount, payment_date, payment_mode, notes=None, original_message) → {success, new_balance}
```

### Production
```python
save_production(prod_date, total_produced_kg, total_packets, notes=None, original_message) → {success, id}
query_production(date_from=None, date_to=None) → [prod_rows]
```

### Cash Flow
```python
save_cash_flow(flow_date, flow_type, category, description, amount, party=None, payment_mode=None, notes=None, original_message) → {success, id}
query_cash_flow(flow_type=None, category=None, date_from=None, date_to=None) → [flow_rows]
get_cash_position(date_from=None, date_to=None) → {total_in, total_out, net_cash}
```

### Validation
```python
validate_field(field_name, value) → {valid, reason}
```

---

## Key Design Patterns

### 1. Parse → Confirm → Write

**Never write without confirmation.**

```python
# Bad (old way):
user_message → LLM parses → DB write → ❌ Silent error if LLM misunderstood

# Good (new way):
user_message → LLM parses → Show confirmation card → User taps ✅ → DB write
```

### 2. Customer by ID with Fuzzy Matching

```python
# Bad:
sales.shop_name = "Sharma Namkeen"  # String reference = duplicates
users.shop_name = "Sharma"           # Same customer, different strings

# Good:
customers.id = 3, shop_name = "Sharma Namkeen"  # FK reference
sales.customer_id = 3                           # Link by ID
search_customer("Sharma") → fuzzy match → confirm → use customer_id=3
```

### 3. Audit Trail on Everything

```python
# Every write includes:
- original_message = raw user input (immutable)
- confirmed_at = timestamp (when user confirmed)
- recorded_by = user_id (who wrote it)
- audit_log row = detailed JSON of what was extracted
```

Debug any record:
```sql
SELECT * FROM audit_log WHERE table_affected='sales' AND record_id=42
→ Shows exactly what user said, what LLM extracted, when it was confirmed
```

### 4. Atomic Multi-Row Transactions

```python
# When saving a credited sale:
BEGIN TRANSACTION
  INSERT INTO sales (...)           → returns sale_id
  INSERT INTO credit_ledger (sale_id=..., ...)
  INSERT INTO audit_log (...)
COMMIT
# Either all succeed or all rollback — never partial writes
```

### 5. No Stored Computed Values (Except Audit Trail)

```python
# Bad:
running_balance = 6000  # In credit_ledger row
# Deletes one row → all subsequent running_balances are wrong

# Good:
View customer_balance:
  SELECT SUM(debit - credit) FROM credit_ledger WHERE customer_id=X
# Always correct, computed on-the-fly
```

---

## Error Handling

### LLM Misparse → User Fixes It

```
User: "50kg Sharma"
LLM parses: qty=50, customer="Sharma"
Agent: "Quantity 50? At what rate (₹/kg)?" 
User: "130"
Agent: "Confirm: 50 kg @ ₹130 = ₹6,500 to Sharma?"
[✅ Confirm] [❌ Edit]
```

### Missing Required Field → Follow-Up

```
User: "Sharma 30kg"
LLM: "At what rate (₹/kg)?"
User: "120"
LLM: "Was this paid now or credited?"
User: "credited"
LLM: [Confirmation card]
```

### Duplicate Detection

```
Agent checks credit_ledger: "Sharma already has ₹6,000 from today at ₹120/kg?"
If yes: "Is this a different sale? Or did I misunderstand? [✅ Different] [❌ Cancel]"
```

---

## Multi-Turn Conversation (Context Management)

Conversation history is stored in memory during a session:
```python
context = {
    'user_id': 12345,
    'messages': [
        {'role': 'user', 'content': 'Sharma ko...'},
        {'role': 'assistant', 'content': 'Confirm karo...'},
        {'role': 'user', 'content': '[✅ Confirmed]'},
        ...
    ],
    'last_customer_id': 3,  # Context for follow-ups
}
```

Groq's context window is 32K tokens — enough for ~50 back-and-forths in a single session.

---

## Deployment Notes

### Local Development
```bash
python main.py
# Runs bot.py + agent.py + db.py
# Connects to Supabase cloud
# Polls Telegram for messages
```

### Fly.io Production
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

Fly.io runs this container 24/7 in Mumbai region (latency < 100ms to Indian servers).

---

## Memory & Context Management (2026 Best Practice)

**Problem:** Long sessions can cause context bloat. Groq has rate limits. The agent needs to stay sharp without loading 100KB of customer data into every prompt.

**Solution: Just-in-time dynamic context loading**

### What's NOT in the system prompt (kept small)
- ❌ Full customer list (could be 1000+ rows)
- ❌ Recent sales history (queried on-demand)
- ❌ Balances (computed via VIEW on-the-fly)
- ❌ Long conversation history (only last N messages)

### What IS in the system prompt (fixed, <500 tokens)
- Role: "You are a factory operations assistant..."
- Tool definitions (names + descriptions only, not full docs)
- Expected input/output format
- Hindi/Hinglish detection logic
- Safety rules: "Ask for confirmation before saving", "Never assume customer names"

### How context is loaded dynamically
```python
def agent_loop(user_message, user_id, context_window=5):
    # 1. Load conversation history (last 5 messages only)
    history = db.get_recent_messages(user_id, limit=context_window)
    
    # 2. System prompt (static, small)
    system_prompt = load_system_prompt()
    
    # 3. Call Groq with minimal context
    response = groq.chat.create(
        model="llama-4-scout",
        messages=[
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": user_message}
        ],
        tools=TOOL_SCHEMAS,  # Just names + descriptions, not full data
        max_tokens=1000,
        temperature=0.3
    )
    
    # 4. If agent needs data (e.g., "show me all customers")
    # It calls the tool; tool fetches FROM DB fresh
    # Tools return only what's needed (top 5 debtors, not all 100)
```

### Why this matters
- **Cost**: Groq charges per input token. Loading 1000 customer rows every message wastes tokens.
- **Latency**: Smaller context = faster API calls.
- **Accuracy**: Fresh data from DB is always correct (no stale context).
- **Rate limits**: Groq free tier: 30 calls/min. Smaller context helps stay under limit.

### Conversation history
- Store messages in-memory during a session (Python dict)
- Optional: persist to DB `message_log` table for multi-session continuity (v2 feature)
- On new session or after 1 hour: start fresh (prevents context rot)

### Future: Memory Compactor (planned, not in v1–v3)

**Problem it solves:** The current approach keeps only the last 5 messages. For long conversations or multi-session continuity, older context is lost. The father might say "wahi wala Sharma" referring to a customer mentioned 20 messages ago — the agent won't remember.

**How it works:**
- After every N messages (e.g., every 10), run a compression pass
- A separate LLM call summarises the conversation so far into a compact "memory block"
  ```
  Memory block example:
  "User has been adding sales today. Confirmed customers: Sharma Namkeen (id=3),
   Gupta Stores (id=7). Last sale: Sharma 50kg ₹120 cash. Pending: none."
  ```
- This memory block is injected at the top of every subsequent prompt (before the last 5 messages)
- Conversation history is replaced by: [memory block] + [last 5 raw messages]
- This gives the agent "long-term session awareness" without blowing up token count

**Why not now:**
- Adds a second LLM call per compression cycle (doubles Groq usage during compression)
- Overkill for v1 where sessions are short
- The simple 5-message window is sufficient for all v1 use cases

**Where to implement (when ready):**
- `src/agent.py` → `compress_history(user_id, messages)` function
- Called automatically when `len(sessions[user_id]) > 10`
- Memory block stored in `sessions[user_id]['memory']`
- Model: use a lighter/cheaper call since summarisation is simpler than reasoning

### When the agent needs context
- **"Who owes me the most?"** → Agent calls `get_all_balances(sort_by="outstanding_desc")` → tool fetches from DB
- **"How much did Sharma buy last month?"** → Agent calls `query_sales(customer_id=3, date_from=..., date_to=...)` → tool fetches
- **"Confirm this: Sharma 50kg @ ₹120?"** → Agent doesn't need context; user confirmation is the source of truth

This is the 2026 pattern: **tiny system prompt + dynamic tool-based context retrieval**. No CLAUDE.md or .md files stored in the agent loop; instead, context is a real-time query to the database.

---

## Testing Checklist

- [ ] `search_customer()` returns fuzzy matches
- [ ] `confirm_and_save_sale()` creates both sales + credit_ledger rows
- [ ] `record_payment()` updates customer balance
- [ ] `customer_balance` view returns correct outstanding
- [ ] Agent asks for missing fields
- [ ] Agent shows confirmation card
- [ ] Audit log records everything
- [ ] Duplicate detection warns user
- [ ] Paid sale automatically creates cash_flow entry
- [ ] Payment received automatically creates cash_flow entry
- [ ] Context window is kept to last 5 messages (not full history)
- [ ] System prompt is <500 tokens
