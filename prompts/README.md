# Prompts Directory

Externalized prompts loaded by `config.py` and `render.py` at startup (cached at import).

## Files

- **system_prompt.md** — Main system prompt for the LLM agent
- **tool_descriptions.md** — All tool descriptions in one file (format: `## tool_name` followed by description)
- **ui_strings/** — Confirmation card templates and selection prompt templates

## Editing

Edit `.md` files directly. Python code loads them on import (the bot needs to be restarted for changes to take effect).

## Loading mechanism

- **system_prompt.md**: Loaded by `config.get_system_prompt()` at [config.py](../src/config.py#L96)
- **tool_descriptions.md**: Loaded by `config._load_tool_descriptions()` at [config.py](../src/config.py) (cached dict)
- **ui_strings/*.md**: Loaded by `render.py` functions at [render.py](../src/render.py)

## Tool descriptions sizing

All 10 tool descriptions combined are ~780 bytes. Each description can be up to **500-1000 characters** (reasonable for LLM context). Current average: ~78 chars per tool.
