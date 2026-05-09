# Secret Safety & Log Protection

## Overview

This document outlines how Factory Agent prevents secret leakage in logs and production deployments.

## Secrets in Scope

- **TELEGRAM_BOT_TOKEN**: Full bot token (e.g., `123456:ABCDefGHIJKlmnOpQrsTUVWxy`)
- **GROQ_API_KEY**: Groq API key
- **SUPABASE_KEY**: Supabase service role key (read/write all tables)
- **SUPABASE_URL**: Supabase project URL (lower-risk but still confidential)

## Risk: Third-Party Logger Leakage

**Problem**: Libraries like `httpx` (HTTP client) and `python-telegram-bot` log at INFO level and include sensitive data:
- httpx logs full request URLs, which include bot tokens in the path: `https://api.telegram.org/bot<TOKEN>/getMe`
- Request/response bodies may contain keys or tokens

**Example Incident**:
```
2026-04-30 10:12:34 - httpx - INFO - GET https://api.telegram.org/bot8650466728:AAFXotFLqoTj5LZstKg4ORI4PUbxJ8/getMe
```

The token is now exposed in stdout, logs saved to file, and CI/CD output.

## Mitigation: Logger Silencing

### main.py

On startup, silence loggers from libraries that include secrets in their output:

```python
# httpx logs full URLs (which contain the bot token) at INFO. Mute it.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
```

This ensures:
- httpx, httpcore, and urllib3 only emit WARN+ (errors, warnings)
- INFO-level debug details (full URLs, request bodies) are suppressed
- Actual connection errors still surface as WARN (traceable without exposing tokens)

### Which Loggers to Silence

| Logger | Risk | Silence Level |
|--------|------|---------------|
| `httpx` | High (URLs contain tokens) | WARNING |
| `httpcore` | High (low-level HTTP details) | WARNING |
| `urllib3` | Medium (connection debug) | WARNING (if used by python-telegram-bot) |
| `groq` (if verbose) | Low (SDK is careful) | Not needed unless DEBUG |
| `supabase` (if verbose) | Low (wrapper is careful) | Not needed unless DEBUG |

### Testing Logger Silencing

```bash
# Run the bot and verify no tokens in stdout
python main.py > test_logs.txt 2>&1

# Search for patterns that indicate token leakage
grep -i "bot[0-9]" test_logs.txt      # Should find nothing
grep "://api\." test_logs.txt          # Should find nothing (or only WARN-level errors)
grep "groq\|supabase" test_logs.txt    # Should find setup/connection logs only
```

## Production Deployment: Fly.io Secrets

### env vars vs Secrets

| Storage | Use For | Exposed In |
|---------|---------|-----------|
| `.env` (local) | Development only | Git (add to `.gitignore`) |
| Fly secrets | Production | Environment, NOT in logs or console output |
| Config files | Non-secret config | Git (version-controlled) |

### Deploying with Fly Secrets

```bash
# Set secrets (never in fly.toml or git)
fly secrets set \
  TELEGRAM_BOT_TOKEN=<new_token_from_@BotFather> \
  GROQ_API_KEY=<key_from_groq_console> \
  SUPABASE_URL=<url> \
  SUPABASE_KEY=<anon_key>

# Verify secrets are set (shows names, not values)
fly secrets list

# Deploy (secrets injected at runtime)
fly deploy
```

### Audit: Check Logs in Fly

```bash
fly logs | grep -i "token\|key\|secret"
```

Should return **nothing** — secrets are only in memory, never logged.

## Code-Level Secret Hygiene

### In logging.py and src/tools.py

- **Never log arguments** to functions that receive secrets
- **Never log exception tracebacks** that include API responses
- **Always redact** raw error messages before returning to users

Example (src/tools.py):
```python
except Exception:
    logger.exception("Tool execution failed")  # Logs full traceback (risky!)
    return json.dumps({"ok": False, "error": "internal error — please retry"})
    # ^ Generic message to user; real error is in logs (protected by logger silencing)
```

Better:
```python
except Exception as e:
    logger.debug("Tool execution failed: %s", type(e).__name__)  # Type only, safe
    return json.dumps({"ok": False, "error": "internal error — please retry"})
```

### In agent.py

- Session history is stored in TTL cache (memory-only, auto-expires)
- User messages with command details ("Sharma ko 50kg send") are stored *only for LLM context*, never raw-logged
- Confirmation tokens are 16-byte random hex, not tied to secrets

## Dependency Updates & Security

### Python-telegram-bot

- **v20.7**: Known to leak in logs (now unsupported)
- **v22.0+**: Improved secret handling; use this or newer

Pin in requirements.txt:
```
python-telegram-bot>=22.0,<23
```

### httpx

- Use a tested version range (e.g., `>=0.27,<0.29`)
- Newer versions may have improved logging

Pin in requirements.txt:
```
httpx>=0.27,<0.29
```

## Checklist: Before Deploying to Production

- [ ] `.env` is in `.gitignore` (never committed)
- [ ] `fly.toml` does NOT contain secrets (config only)
- [ ] All required env vars are set via `fly secrets set`
- [ ] Logger silencing is in place in `main.py`
- [ ] Run `python main.py` locally and inspect logs for tokens
- [ ] `fly logs` after deploy contains no token/key patterns
- [ ] Review `requirements.txt` for outdated versions of logging libraries

## Checklist: After Token Exposure (Like in This Project)

1. **Rotate the token immediately** via @BotFather (old token is compromised)
2. **Update .env** with the new token
3. **Test locally**: `python main.py` should not expose the new token
4. **Deploy to Fly.io**: `fly secrets set TELEGRAM_BOT_TOKEN=<new> && fly deploy`
5. **Verify in Fly logs**: `fly logs | head -100` should show no tokens
6. **Notify any users** who may have seen the old token in shared logs (if applicable)

## Further Reading

- [Telegram Bot Security](https://core.telegram.org/bots/features#botfather)
- [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [12-Factor App: Config](https://12factor.net/config)
- [httpx Logging](https://www.python-httpx.org/)
- [Python Logging Security](https://docs.python.org/3/library/logging.html#logging.LogRecord)
