# Factory Agent — Project Plans

This directory contains all architectural and implementation plans for the Factory Agent project.

## Current Plans

### 1. `tools-standard-crud-coverage.md`
**Status:** Implemented  
**Phase:** Phase 7 (Post-refactoring tooling)  
**Summary:** Standardized CRUD operations for all major entities (customers, sales, payments, production, cash flow, credit ledger). Ensures consistent error handling, field coverage, and LLM-friendly response structures for all tool handlers.

**Key deliverables:**
- 6 new query tools (search, list, filter, history)
- Standardized response envelopes (`{"ok": bool, ...}`)
- Consistent result set limits and pagination
- Updated system prompt with tool guidelines

---

### 2. `error-handling-classification.md`
**Status:** Designed (awaiting implementation)  
**Phase:** Phase 7 Extended (Post-refactoring)  
**Summary:** Maps PostgreSQL error codes to structured context, enabling the LLM to provide actionable error messages instead of generic "internal error" strings. Fixes live bugs and improves observability.

**Key deliverables:**
- Specific handlers for database constraint errors (overflow, FK, duplicate, etc.)
- Structured error context passed to LLM
- Full descriptive logging for operators
- Unhandled error tracking in markdown (not DB)

**Issues fixed:**
- ❌ `log_utils.log_agent_error` called but not defined
- ❌ Generic "internal error" swallows constraint violations
- ❌ Dead `DatabaseError` class

**How to use this plan:**
1. Read the full plan in `error-handling-classification.md`
2. Review and approve the design
3. Follow the 4-step implementation sequence (logger → tools → db → render)
4. Run the verification tests

---

## Planning Process

All plans follow this structure:
1. **Context** — what problem is being solved and why
2. **Design Principles** — what constraints and values guide the solution
3. **Implementation Plan** — step-by-step changes with file paths and line counts
4. **Verification** — how to test the changes work end-to-end

---

## How to Create New Plans

When starting a new architectural task:

1. **Explore the codebase** — understand existing patterns, dependencies, constraints
2. **Design in isolation** — articulate the problem, constraints, and proposed solution
3. **Review dependencies** — what other systems are affected?
4. **Write the plan** — place in this directory, named descriptively, include all sections above
5. **Get approval** — discuss with the team before implementing
6. **Execute and verify** — follow the plan, test thoroughly, document outcomes

---

## Phase History

- **Phase 1-2:** Modernize types & tighten error handling (commit efbf4f4)
- **Phase 3a-c:** Extract providers, session store, render layer (commits 81bfba2, fcea6f5, 7bb68b1)
- **Phase 7:** Complete refactoring — add tools, tests, tooling (commit 7bb68b1)
- **Phase 7 Extended:** Error handling classification (this directory)

---

## Key Directories Referenced

- `src/` — core application code (tools, db, agent, bot)
- `database/schema.sql` — PostgreSQL schema with constraints
- `prompts/` — system prompt and tool descriptions
- `errors/` — will store unhandled error tracking (created by error-handling-classification plan)

---

## Related Files

- `../ARCHITECTURE.md` — system design overview
- `../SCHEMA.md` — database schema documentation
- `../SECURITY.md` — secret management and safety
- `../../IMPLEMENTATION-CHECKLIST.md` — phase completion checklist
- `../../TOOLS-REFERENCE.md` — tool specifications
