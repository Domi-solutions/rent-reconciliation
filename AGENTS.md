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

**Who:** Claude (strategy + workspace cleanup)
**Date:** 2026-05-04

### What was decided

- **Strategic pivot:** Domi is now a property fintech (money-in-transit model). Detailed in `CURSOR_PLAN.md` Phase G and `ROADMAP.md` "Payment Rail" section.
- **Tenant experience:** STK Push (phone + amount on portal → M-Pesa prompt) + card (Pesapal). Partial payments are first-class. Legacy bank statement reconciliation kept during transition.
- **Workspace cleaned:** `DEPLOY_GUIDE.md` deleted (outdated tutorial). All docs updated and locked to reflect current state.
- **YAML registries created:** `.agent/schema.yaml`, `.agent/routes.yaml`, `.agent/jobs.yaml`, `.agent/intents.yaml`, `.agent/env.yaml` — use these for precise lookups, not CLAUDE.md prose.

### What was confirmed (do not re-implement)

- Phases A–E (agent infrastructure, balance snapshots, APScheduler, simulator, inbound tables, check-ins) are complete and deployed.
- All portals (tenant, owner, caretaker), messaging, reports — complete.

### Pick up next

**Prereq 1 — Admin auth (do this first, ~2 days):**
- `GET/POST /login` at `app.py` level
- `before_request` hook checking `session['admin_authenticated']`
- Exempt: `/login`, `/logout`, `/tenant/*`, `/view/*`, `/caretaker/*`, `/inbound/*`, `/static/*`
- Dev mode: if `ADMIN_PASSWORD` not set, skip check entirely (no change to `run_dev.sh`)
- Pattern: see viewer auth in `src/routes/viewer_routes.py`

**Prereq 2 — Legal structure (external, run in parallel):**
- Engage Kenyan fintech legal counsel or Africa's Talking compliance
- Question: merchant model vs PSP license under CBK

**Prereq 3 — Paybill + Daraja application (external, run in parallel):**
- Apply via Africa's Talking Payments or Safaricom Business
- Timeline: 2-4 weeks
- One Paybill for all properties, unit number as account reference

**After all three prereqs:** Build Phase G (Payment Rail) — full spec in `CURSOR_PLAN.md`.

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
