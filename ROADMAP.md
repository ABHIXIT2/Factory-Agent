# Factory Agent Roadmap

Versioned feature rollout. Each version is a stable, deployable product on its own.

---

## v1 — Core MVP (This Weekend) · **SHIP THIS FIRST**

**Goal:** Working bot by Sunday 11pm. Father records sales, credit, production. Safe, auditable, live on Fly.io.

**Timeline:**
- **Friday 6pm–11pm (5h)**: DB setup, schema, Telegram scaffold, basic agent
- **Saturday 9am–9pm (12h)**: Full CRUD, confirm-before-write, integration tests
- **Sunday 9am–10pm (13h)**: Final testing, deployment, polish
- **Total: 30 hours**

### Core Features

- **Sales ledger** (text input → confirmed → saved)
  - "Sharma ko 50 kilo 120 rate cash" → Confirm → ✅ Saved
  - Query: "Who owes most?", "Today's sales?"

- **Credit ledger** (automatic, tied to sales)
  - "Gupta paid 5000" → Balance updates
  - `/status` → All outstanding balances

- **Production log** (basic daily tracking)
  - "50 kg produced, 100 packets"

- **Cash flow** (automatic + manual)
  - Paid sales → auto cash_flow 'in' (trigger)
  - Payments → auto cash_flow 'in' (trigger)
  - "Raw material 2000 cash" → manual expense entry

- **Parse → Confirm → Write** (core safety pattern)
  - LLM parses → formatted card → user taps ✅/❌ → DB write
  - No silent errors

- **Fuzzy customer matching**
  - "Sharma" → finds "Sharma Namkeen", confirms

- **Inline buttons** (no typing required)
  - [✅ हाँ] [❌ नहीं]
  - [नकद] [उधार] [ऑनलाइन]

- **Audit trail**
  - original_message, timestamp, user_id → full traceability

- **Commands**
  - `/start`, `/help`, `/status`

- **Deployment**
  - Supabase + Fly.io, 24/7 live

### Out of Scope for v1

- ❌ Voice, full Hindi, multi-user, export, alerts, trends

### Done Checklist

- [ ] Supabase + schema + triggers running
- [ ] Bot responds to `/start` `/help` `/status`
- [ ] Record sale → confirm → saved
- [ ] Record payment → balance updates
- [ ] Query balances
- [ ] Audit log working
- [ ] Deployed to Fly.io + tested on phone

### Definition of Done

- [ ] Supabase schema initialized
- [ ] Telegram bot responds to commands
- [ ] Can add a sale via text, get confirmation, data saved
- [ ] Can query outstanding balances
- [ ] Audit log records everything
- [ ] Deployed to Fly.io, working from phone
- [ ] Tested end-to-end with sample data

---

## v2 — Usability & Voice (Day 2)

**Goal:** Father can use it entirely in Hindi/Hinglish. Voice support makes it effortless.

### New Features

- **Full Hindi/Hinglish support**
  - Detect user language (Hinglish, Devanagari, English)
  - System prompt in Hindi
  - Responses in user's language
  - Error messages in Hindi

- **Voice message support** ✨
  - User sends voice note → Groq Whisper transcription
  - Transcription treated as text message
  - Same agent pipeline
  - Massive UX improvement for Hindi speaker

- **Better date handling**
  - "आज" → today
  - "कल" → disambiguate with buttons [कल (Yesterday)] [कल (Tomorrow)]
  - "परसों" → day after tomorrow
  - "इस हफ्ते" → this week
  - "पिछले महीने" → last month

- **Informal customer references**
  - "Sharma wale" → match to "Sharma Namkeen"
  - "Gupta bhai" → match to "Gupta Stores"
  - Fuzzy matching handles variations

- **Better inline button labels**
  - Hindi labels on all buttons
  - Category selection with Hindi text
  - Payment type with Hinglish options

- **Improved formatting**
  - Emojis on all messages
  - Structured tables in Telegram
  - Better visual hierarchy

### Effort

**~2 days**

### Definition of Done

- [ ] Voice message → transcription → works end-to-end
- [ ] System responds in Hindi when user writes Hindi
- [ ] Date disambiguation works for "कल"
- [ ] Fuzzy matching handles informal names

---

## v3 — Intelligence & Automation (Day 3–4)

**Goal:** Actionable insights and automatic summaries. Smart alerts.

### New Features

- **Multi-user support** 🔐
  - Each Telegram user has isolated data
  - Separate credit ledgers per user
  - Settings per user

- **Automated daily summary**
  - Sent at 8 PM automatically: "आज का हिसाब"
  - Total sales, total produced, total expenses
  - Outstanding balances
  - No user action needed

- **Weekly outstanding reminder**
  - Every Monday morning
  - List of shops with pending balances
  - Credit limit breaches highlighted

- **Smart credit alerts**
  - "Gupta का बकाया ₹15,000 हो गया — limit ₹12,000 है"
  - Sent when balance exceeds credit_limit

- **NLP insights** 💡
  - "पिछले महीने सबसे ज़्यादा किसने खरीदा?" → top customer by volume
  - "इस हफ्ते का खर्चा?" → weekly expenses by category
  - "Production vs sales gap?" → trend analysis

- **Trend queries**
  - Sales by week, month, year
  - Production consistency
  - Cash flow trends

### Effort

**~3 days** (background scheduling, multi-user DB isolation, analytics)

### Definition of Done

- [ ] Daily 8 PM summary auto-sends
- [ ] Multi-user data is properly isolated
- [ ] Credit limit alerts trigger
- [ ] Trend queries work (sales by month, etc.)

---

## v4 — Enterprise Features (Day 5)

**Goal:** Formal reporting for accountant/auditor review.

### New Features

- **PDF monthly reports**
  - Formatted Hindi headings
  - Sales summary by shop
  - Customer credit ledger full detail
  - Cash flow statement
  - Net cash position

- **CSV/Excel export**
  - Export any ledger to CSV
  - Open in Excel for analysis
  - Date range filters

- **Report generation**
  - `/report` command → generates PDF
  - Sent directly in Telegram chat
  - Downloadable

- **Admin audit view**
  - Who recorded what and when
  - Filter by user, date, action type
  - Full audit_log query interface

- **Settings per user**
  - Alert threshold (default ₹10,000)
  - Alert frequency (daily, weekly, never)
  - Timezone (default Asia/Kolkata)
  - Date format (default YYYY-MM-DD)

### Effort

**~2 days** (PDF generation, export logic, admin interface)

### Definition of Done

- [ ] `/report` generates PDF monthly summary
- [ ] CSV export works
- [ ] Audit log is queryable via `/audit` command
- [ ] Settings configurable via `/settings` command

---

## Beyond v4 (Ideas for Future)

- **Advanced analytics**
  - Predictive: "Based on trends, you'll sell X kg next month"
  - Variance analysis: "Production is 10% below average"
  - Profitability per customer (if cost data added)

- **Customer master data**
  - Add cost_per_kg field → compute profit margin per sale
  - Add credit_days field → auto-alert overdue payments

- **Email summaries**
  - Daily digest emailed to owner
  - Weekly full report

- **API for external tools**
  - Query balances via REST API
  - Integrate with accounting software
  - Webhook for external notifications

- **Mobile app** (later)
  - Native Android/iOS app
  - Offline support
  - Biometric auth

---

## Timeline

```
Week 1:  v1 complete (Days 1–3) + buffer testing
Week 2:  v2 (voice + Hindi)
Week 3:  v3 setup (multi-user, scheduling)
Week 4:  v3 polish + v4 start
Month 2: v4 complete + production hardening
```

## Success Criteria

- **v1**: Father can use bot to record all 4 ledgers without help
- **v2**: Father prefers voice messages; never types in English
- **v3**: Father trusts the automated summaries; alerts work
- **v4**: Owner can generate formal monthly reports for accountant

---

## Rollback Plan

Each version is independent and deployable. If v2 breaks something from v1:
1. Revert to v1 code
2. Debug v2 in parallel
3. Deploy fixed v2 when ready

No forced upgrades — v1 users can stay on v1 forever.
