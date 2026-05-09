# UI Strings

Externalized text shown above confirmation buttons.

## Files

- **confirmation_card.md** — Preamble shown above confirmation cards before ✅/❌ buttons. Loaded once at import by `render._load_confirmation_card_text()` ([src/render.py](../../src/render.py)). Bot restart required after edits.

## Hardcoded (NOT externalized)

The following UI strings are currently **inlined in Python code**, not in this folder:

- Multi-customer selection prompt — hardcoded in [src/agent.py](../../src/agent.py) (`agent_loop`, around the `selection_required` branch). Devanagari/English variants are picked by `detect_user_lang`.
- Per-tool closing messages (e.g. "✅ Sale saved…") — hardcoded in [src/render.py](../../src/render.py) (`_close_save_sale`, `_close_record_payment`, etc.).

Move them here only when you also wire a loader; orphan `.md` files are misleading.
