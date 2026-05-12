# AGENTS.md — AI Agent Entry Point

> **Start here.** Read this file first, every session, regardless of which AI tool you are.
> After completing work, overwrite the "Last Session" section before handing off.

---

## How to use this file

1. Read this file top to bottom (~2 min)
2. Read `CURSOR_PATTERNS.md` — failure log, read before writing any code
3. Read `.agent/schema.yaml` for precise table/column lookups
4. Read `.agent/routes.yaml` for the full route registry
5. Read `CLAUDE.md` for technical depth (patterns, conventions, business rules)
6. Start work from "Pick up next" below

---

## Project in one paragraph

Domi is a **property fintech platform** for Kenya. Tenants pay rent via M-Pesa STK Push or card directly through Domi. Domi holds the funds and disburses to landlords net of management fee. The fee is embedded in the disbursement spread — not a visible line item. This model makes Domi infrastructure (low churn, high switching cost) rather than software (easy to cancel). Stack: Flask 3 + SQLite + Bootstrap 5, deployed on Fly.io (Johannesburg). SMS via Africa's Talking (sandbox). 1 property live: Mowin Apartments, 44 units.

**Hard rule:** data-descriptive language only — "KES 312,000 verified against bank records", never "We collected KES 312,000". No exceptions. See `ROADMAP.md` for the full table.

---

## Last Session

**Who:** Claude Code (architecture redesign — multi-org, multi-owner holistic views, multi-unit tenant identity)
**Date:** 2026-05-12

### What was completed this session

- Wiped `data/dev.db` for a clean test run (backup: `data/dev.db.backup_20260512_160834`)
- Updated `CLAUDE.md`: added development commands section, corrected stale "Next steps"
- Designed the full multi-org / person identity architecture (locked below + full DDL in CURSOR_PLAN.md)
- **No code written yet.** This session was architecture design + planning only.

### What was decided (locked — do not revisit)

**1. Multi-org architecture is required before any data is entered.**
Retrofitting an org layer after 4 properties are live would touch every query in the codebase. Do it now.

**2. Person identity layer is required for multi-unit tenants.**
Tenant X rents a unit in Property 1 (Org 1) and a unit in Property 3 (Org 2). Fix: `persons` table + `person_id` FK on `tenants` and `owners`. All existing FK relationships stay intact — additive only.

**3. The exact real-world scenario to implement (demo + live):**
```
Organization 1 (Agency A) — manages:
  Property 1  ← owned by Owner A
  Property 2  ← owned by Owner A

Organization 2 (Agency B) — manages:
  Property 3  ← owned by Owner B
  Property 4  ← owned by Owner B

Owner A: holistic view across Property 1 + 2, plus individual property drilldown
Owner B: holistic view across Property 3 + 4, plus individual property drilldown

Tenant X: active unit in Property 1 AND active unit in Property 3
          single login → sees both units, both balances, all charges holistically
```

**4. New portals and auth (locked):**
| Role | Login | Portal |
|---|---|---|
| Platform owner (Domi operator) | `PLATFORM_ADMIN_PASSWORD` env var | `/platform/` |
| Org admin | Per-org password in `organizations.admin_password_hash` | `/` scoped by `session['org_id']` |
| Owner | Phone + password via `persons` table | `/owner/dashboard` holistic + `/owner/<property_id>/` individual |
| Caretaker | Existing named accounts | `/caretaker/<property_id>/` — unchanged |
| Tenant | Phone + PIN (holistic) OR token link (single unit) | `/tenant/dashboard` holistic + `/tenant/<token>` individual |

**5. New schema (locked — see CURSOR_PLAN.md for exact DDL):**
- `organizations` — agency identity + per-org admin credentials
- `persons` — human identity shared across roles, orgs, properties
- `platform_errors` — wired to Flask `@app.errorhandler`; platform admin sees all errors without users calling
- `properties.organization_id` — nullable FK to organizations
- `tenants.person_id` — nullable FK to persons
- `owners.person_id` — nullable FK to persons

### Pick up next — Phase 1: Schema Foundations

**Read CURSOR_PLAN.md "Architecture Redesign" section for exact DDL and build sequence.**

Steps in order:
1. Add `migrate_add_organizations()` in `src/database/db.py`
2. Add `migrate_add_persons()` in `src/database/db.py`
3. Add `migrate_add_platform_errors()` in `src/database/db.py`
4. Register all three in `app.py` startup (after existing migrations, in the order above)
5. `./venv/bin/python -c "from app import app; print('OK')"` — must pass
6. `./scripts/run_dev.sh` — app must still function normally

Then Phase 2 (platform admin `/platform/*`), Phase 3 (org scoping), Phase 4 (owner holistic), Phase 5 (tenant holistic), Phase 6 (data entry + test run).

**Commit after each phase:** `git add . && git commit -m "Phase N complete: [description]"`

**Critical rules for this build:**
- All migrations idempotent: `CREATE TABLE IF NOT EXISTS`; check `PRAGMA table_info` before `ALTER TABLE`
- `organization_id`, `person_id` columns are nullable on all tables — never break existing null rows
- Never import from `app.py` inside blueprints — define helpers locally or in a shared util
- Read `CURSOR_PATTERNS.md` before writing any migration or route code

**Note on credentials:** When AT_USERNAME flips from sandbox to live, 5 message types fire to real phones immediately. Read `memory/project_go_live_messaging_checklist.md` before flipping.

---

## Key file map

| File | Purpose | When to read |
|---|---|---|
| `AGENTS.md` | This file — entry point + latest handoff | Every session, first |
| `CURSOR_PATTERNS.md` | Failure log — known mistakes from prior builds | Before writing any code |
| `.agent/schema.yaml` | Ground truth for DB tables and columns | Before writing any query or migration |
| `.agent/routes.yaml` | Ground truth for all routes and blueprints | Before adding any route |
| `.agent/jobs.yaml` | Ground truth for all scheduled jobs | Before adding any job |
| `.agent/intents.yaml` | Intent classification set | Before adding any intent handler |
| `.agent/env.yaml` | All environment variables | Before using any config value |
| `CLAUDE.md` | Full technical reference — patterns, rules, architecture | For context and conventions |
| `ROADMAP.md` | Product vision, phase checklist, language rules | For product decisions |
| `CURSOR_PLAN.md` | Active build plan — what to build and how | The primary build guide |
| `README.md` | How to run and deploy | Operational reference |

---

## Quick reference

```python
# DB access
from src.database.db import get_connection, generate_id
with get_connection() as conn:
    conn.execute("INSERT INTO ...", (val1, val2))

# FIFO payment allocation
from src.database.db import allocate_payment
with get_connection() as conn:
    allocate_payment(conn, payment_id, unit_id, amount)

# SMS — always wrap, never block main flow
from src.messaging.delivery import send_sms
try:
    send_sms([{'phone': '+254700000000'}], "message")
except Exception:
    pass

# Owner notification
from src.messaging.owner_notify import notify_property_owners
notify_property_owners(conn, property_id, message, sent_by='Admin')

# Agent delivery (never call send_sms from agent logic)
from src.agent.router import route_message
route_message({'recipient_phone': ..., 'recipient_role': ..., 'property_id': ..., 'message_type': ..., 'body': ...})

# LLM (never import anthropic SDK outside llm.py)
from src.agent.llm import call_llm
result = call_llm(prompt, model='fast')  # 'fast'=haiku, 'smart'=sonnet
```

```bash
# Local dev (no passwords, uses data/dev.db)
./scripts/run_dev.sh        # → http://localhost:5001

# Deploy
export PATH="$HOME/.fly/bin:$PATH" && fly deploy

# Verify no import errors
./venv/bin/python -c "from app import app; print('OK')"

# Pull production DB for local dev
./scripts/download_prod_db.sh
```

---

## Protocol

- **Update this file** at the end of every session — overwrite "Last Session" with what you did and what's next
- **Commit after every session** — `git add . && git commit -m "describe work done"`. The git log is used for diagnosis.
- **Never use agency voice** in any user-facing string
- **Additive migrations only** — `CREATE TABLE IF NOT EXISTS`, never drop/recreate tables with data
- **Raw SQL only** — no ORM
- **Update YAML registries** when adding tables, routes, or jobs — an unregistered component is invisible
