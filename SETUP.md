# Labbu ❤️ — Setup Guide

Complete all three steps below before running Labbu.

---

## Step 1: Supabase Setup

1. Go to [supabase.com](https://supabase.com) and create a free account
2. Create a new project (choose region: Mumbai or closest to you)
3. Wait for it to initialize (~2 min)
4. Go to **Settings → API → Keys**
   - Copy the `URL` value
   - Copy the `anon public` key (this is your `SUPABASE_KEY`)
5. Go to **SQL Editor** and run the full schema from `SCHEMA.md`:
   - Paste the entire SQL script into the editor
   - Click ▶️ Run
   - Wait for all tables/views/triggers to be created (~30 seconds)
6. Test the connection: Go back to SQL Editor, run:
   ```sql
   SELECT NOW();
   ```
   You should see the current timestamp. ✅

---

## Step 2: Telegram Bot Token

1. Open Telegram and search for `@BotFather`
2. Send `/newbot`
3. Answer the prompts:
   - Name: "Labbu" (or "Labbu ❤️")
   - Username: "labbu_factory_bot" (must end with `_bot`)
4. BotFather will give you a token:
   ```
   123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij
   ```
   Copy this. ✅

---

## Step 3: Environment Variables

1. In the project root, create a file named `.env` (copy from `.env.example`):
   ```bash
   cp .env.example .env
   ```

2. Open `.env` and fill in:
   ```
   GROQ_API_KEY=<your Groq API key from https://console.groq.com>
   TELEGRAM_BOT_TOKEN=<token from BotFather>
   SUPABASE_URL=<URL from Supabase Settings>
   SUPABASE_KEY=<anon public key from Supabase Settings>
   ```

3. Save and close.

---

## Step 4: Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 5: Run the Bot

```bash
python main.py
```

Expected output:
```
2026-05-01 10:30:45 - __main__ - INFO - 🏭 Starting Factory Agent...
2026-05-01 10:30:46 - src.db - INFO - ✅ Connected to Supabase
2026-05-01 10:30:46 - __main__ - INFO - ✅ Bot configured. Starting polling...
```

The bot is now **listening** for messages on Telegram. 🚀

---

## Step 6: Test Labbu

1. Open Telegram on your phone
2. Search for `@labbu_factory_bot`
3. Tap **Start**
4. Try sending a message:

   ```
   Labbu, Sharma ko 50 kg diya 120 rate pe
   ```

   (Mention Labbu's name to get an extra ❤️)

Expected:
- Bot shows a **confirmation card** with the parsed details
- You tap ✅ or ❌
- If ✅, bot confirms: "✅ Sale saved: Sharma ka baqaya: ₹6,000"
- Go to Supabase → `sales` table → you'll see the new row

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `❌ Missing required environment variables` | Check `.env` file exists and all 4 vars are filled in (no empty lines) |
| `❌ Database connection failed` | Verify Supabase URL and KEY are correct; test in SQL Editor first |
| Bot doesn't respond | Check Telegram username matches what you created in BotFather |
| `groq` import error | Run `pip install groq==0.9.0` specifically |

---

## Next Steps

Once working locally:
- Record a few transactions and verify data appears in Supabase
- Test balance queries: "बकाया देखो" or "Show balances"
- **Then deploy to Fly.io** (see `FLY_DEPLOY.md`)
