# Factory Agent — Fly.io Deployment

Deploy the bot to the cloud (free, always-on, no device needed).

---

## Prerequisites

- Bot tested locally (see `docs/SETUP.md`)
- Fly CLI installed: [fly.io/docs/getting-started/installing-flyctl](https://fly.io/docs/getting-started/installing-flyctl/)
- Fly account created: [fly.io](https://fly.io) (sign up is free)

---

## Step 1: Authenticate with Fly

```bash
fly auth login
```

This opens a browser. Sign in to your Fly account. ✅

---

## Step 2: Prepare Deployment Files

The project already has:

- `deploy/Dockerfile` — containerizes the bot
- `fly.toml` — Fly configuration (at root)

Verify they exist and look correct:

```bash
cat deploy/Dockerfile
cat fly.toml
```

---

## Step 3: Launch the App

```bash
fly launch
```

This:
1. Asks for an app name (e.g., `factory-agent-yourname`)
2. Chooses a region (pick `bom` for Mumbai if available)
3. Creates the Fly app
4. Does NOT deploy yet ✅

---

## Step 4: Set Environment Secrets

```bash
fly secrets set \
  GROQ_API_KEY="gsk_xxxxxxx..." \
  TELEGRAM_BOT_TOKEN="123456789:ABCDEFG..." \
  SUPABASE_URL="https://your-project.supabase.co" \
  SUPABASE_KEY="eyJhbGciOi..."
```

Replace the values with your actual secrets from `.env`.

Verify they're set:
```bash
fly secrets list
```

You should see all 4 secrets listed (values hidden). ✅

---

## Step 5: Deploy

```bash
fly deploy
```

This:
1. Builds the Docker image
2. Pushes to Fly
3. Starts the bot on their servers

Expected output ends with:
```
Visit your app at https://factory-agent-yourname.fly.dev/
✨ Deployed successfully!
```

Your bot is now **live 24/7**. 🚀

---

## Step 6: Verify Deployment

Check logs:
```bash
fly logs
```

You should see:
```
2026-05-01T10:30:46Z app[...] INFO 🏭 Starting Factory Agent...
2026-05-01T10:30:47Z app[...] INFO ✅ Connected to Supabase
2026-05-01T10:30:47Z app[...] INFO ✅ Bot configured. Starting polling...
```

---

## Step 7: Test from Phone

1. Open Telegram
2. Send your bot a message
3. Bot responds in 1-2 seconds (cloud processing)

---

## Monitoring

View live logs:
```bash
fly logs -f
```

Check status:
```bash
fly status
```

---

## Updates

After making code changes locally:

1. Commit to git (optional but good practice)
2. Deploy:
   ```bash
   fly deploy
   ```

Fly will rebuild and restart the bot (~2 min).

---

## Costs

**Free forever:**
- 3 shared-cpu-1x 256MB VMs
- Unlimited HTTP/HTTPS
- Persistent storage (1GB per app)

Your bot uses <100MB, so it's well within free tier. 💰

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `Error: Could not find app` | Run `fly launch` first |
| Secrets not loading | Run `fly secrets list` to verify; redeploy with `fly deploy` |
| Bot not responding | Check `fly logs` for errors; ensure Telegram token is correct |
| Out of memory | Unlikely; free tier gives 256MB — bot uses ~50MB |

---

## Rollback

If deployment breaks:
```bash
fly releases
fly rollback <version_number>
```

Your previous version comes back online. ✅
