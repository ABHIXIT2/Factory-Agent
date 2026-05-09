"""
Factory Agent — application package.

Layered architecture (top imports bottom; no cycles):

    +------------------+
    |     bot.py       |  Telegram handlers & callbacks (I/O edge)
    +--------+---------+
             |
    +--------v---------+
    |    agent.py      |  LLM loop, confirm-before-write, tool dispatch
    +--+---+-----------+
       |   |
       |   +--+--------v---------+
       |      |  selection.py    |  TTL store for customer selection UI
       |      +--+---------------+
       |         |
       |   +-----v---------+
       |   |  pending.py   |  TTL store of pending write actions
       |   +-----+---------+
       |         |
       |   +-----v-----------+
       |   | token_store.py  |  Base TTL store (used by pending/selection)
       |   +-----------------+
       |
       +---+--------+
           |        |
    +------v--+  +--v----------+
    |render.py|  | session.py  |  Formatters + history cache, rate limiting
    +----------+  +-----+-------+
                        |
    +---+--------+------v--------+
    |   |        |               |
    |   |  +-----v---------+     |
    |   |  |  providers.py |     |  LLM clients (Groq + Google)
    |   |  +-----+---------+     |
    |   |        |               |
    +---v-+---+--v---------------+
    |    tools.py      |  Validates LLM args, dispatches to db
    +--------+---------+
             |
    +--------v---------+
    |     db.py        |  Supabase client + retried CRUD + soft delete
    +--------+---------+
             |
    +--------v---------+
    |   config.py      |  Env validation + tool schemas + system prompt
    +------------------+
             |
    +--------v---------+
    |    utils.py      |  Pure helpers: validators, date parsing, formatting
    +------------------+

Read order for new contributors: config -> utils -> db -> tools -> providers ->
pending -> session -> agent -> bot -> main.
"""
