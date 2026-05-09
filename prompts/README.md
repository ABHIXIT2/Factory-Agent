# Prompts Directory

Externalized prompts loaded by `config.py` and `render.py` at startup (cached at import).

## Files

- **system_prompt.md** — Main system prompt for the LLM agent. Templated with `{today_iso}` and `{timezone_name}`, substituted on every turn by `config.get_system_prompt()`.
- **tool_descriptions.md** — All tool descriptions in one file (format: `## tool_name` header, then everything until the next `##` header is the description for that tool — param bullets, enums, examples are all sent to the LLM).
- **ui_strings/** — Confirmation card preamble. See [ui_strings/README.md](ui_strings/README.md) for what is and isn't externalized.

## Editing

Edit `.md` files directly. Python loads them once at import — restart the bot to pick up changes.

## Loading mechanism

- **system_prompt.md**: Loaded by `config._load_system_prompt()` at [config.py](../src/config.py#L100); rendered per turn via `config.get_system_prompt()` at [config.py:113](../src/config.py#L113).
- **tool_descriptions.md**: Loaded by `config._load_tool_descriptions()` at [config.py:120](../src/config.py#L120). Returns `{tool_name: full_block_text}` cached dict.
- **ui_strings/confirmation_card.md**: Loaded by `render._load_confirmation_card_text()` at [render.py:131](../src/render.py#L131).

## Tool descriptions sizing

16 tool blocks, ~5 KB combined, averaging ~300 chars per tool. Each block (header → next header) is sent to the LLM as the OpenAI-compatible `function.description` field. Keep blocks schema-first: type, required fields, enums, one canonical example.
