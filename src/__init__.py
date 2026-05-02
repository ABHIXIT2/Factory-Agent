"""
Factory Agent — application package.

Layered architecture (top imports bottom; no cycles):

    +------------------+
    |     bot.py       |  Telegram handlers & callbacks (I/O edge)
    +--------+---------+
             |
    +--------v---------+
    |    agent.py      |  LLM loop, sessions, rate limit, confirm-before-write
    +--+------------+--+
       |            |
       |   +--------v---------+
       |   |   pending.py     |  TTL store of pending write actions
       |   +------------------+
       |
    +--v---------------+
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

Read order for new contributors: config -> utils -> db -> tools -> pending
-> agent -> bot -> main. See CODE_TOUR.md for a walkthrough.
"""
