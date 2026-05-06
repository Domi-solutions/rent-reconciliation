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

**Who:** Claude (Phase G + Phase 3 completion, competitive research, deploy)
**Date:** 2026-05-06

### What was completed

**Phase G — Payment Rail (all items complete):**
- `src/payments/daraja.py` — STK Push initiation + B2C disbursement
- `src/payments/pesapal.py` — card checkout integration
- `src/payments/disbursements.py` — `calculate_disbursement()`, `execute_disbursement()`, `scheduled_disbursement_job()`
- `src/routes/payment_routes.py` — Daraja C2B + Pesapal IPN webhooks (write-and-return-200)
- `src/routes/tenant_routes.py` — PIN-gated pay tab: `GET /tenant/<token>/pay`, PIN create/verify, STK Push, poll
- `templates/tenant/pay.html` — tenant payment UI
- `process_payment_queue` — 60s FIFO processor; FIFO SMS confirmation with allocation breakdown
- `disbursement_job` — 10th of month 9am; pending → processing → completed/failed lifecycle
- Disbursement statement shown in owner viewer payments tab (`viewer/payments.html`)
- Source badges (Admin / Caretaker / System) on owner notifications page

**Phase 3 — Signal + Inbound Foundation (all items complete):**
- `src/routes/inbound_routes.py` — `POST /inbound/sms` + `POST /inbound/whatsapp`; write-and-return-200; background thread dispatch
- `src/agent/inbound.py` — `process_inbound_message()`; intent classifier; action handlers; `classify_intent()`
- `src/agent/state.py` — `inbound_sessions` conversation state (24h TTL)
- `src/agent/responder.py` — response message generator
- `weekly_digest_job()` updated to deliver via `route_message()` to all owners with phones (Monday 8am)
- Payment rejection SMS: fires after `verify_payments()` for claims not matched on bank statement
- `migrate_add_language_preference()` — `tenants.language_preference TEXT` column

**Fixed:** `sqlite3.IntegrityError: FOREIGN KEY constraint failed` in `migrate_add_payment_transactions()` — wrapped table rebuild with `PRAGMA foreign_keys = OFF` / `ON` (orphaned FK refs in dev DB).

**Deployed** to Fly.io (Johannesburg). AT sandbox mode — live credentials pending.

### What was decided (do not revisit)

- Fintech model locked: Domi holds tenant payments, disburses net of 8% management fee. Money-in-transit, not pass-through.
- Inbound processing: `threading.Thread` (fire-and-forget) rather than APScheduler queue job — simpler, no polling needed.
- Competitive landscape: Domi does not compete with Nyumba Zetu (upmarket PM SaaS) — it competes with the profession of property manager, targeting individual landlords who currently use a management company + caretaker.

### Pick up next

**Phase 4 — The Conversation (All Roles).** Full spec in `ROADMAP.md` Phase 4 section.

Infrastructure first (these unblock everything else):
1. Africa's Talking dashboard — point inbound SMS to `https://rent-reconciliation.fly.dev/inbound/sms`
2. Wire `src/agent/inbound.py` intent classifier to actual LLM (`call_llm` in `llm.py`) — currently stubbed
3. Implement tenant intent handlers: `checkin_reply`, `payment_claim`, `query_balance`, `complaint_submission`
4. `tenants.flagged` + `tenants.flagged_reason` + `tenants.flagged_at` columns + migration (falsification detection)
5. `maintenance_issues.priority` column + migration
6. `property_info` table + migration + admin management UI (needed for `local_amenity_query`)

Caretaker and owner handlers follow after tenant handlers are stable.

**Note on credentials:** When AT_USERNAME flips from sandbox to live, 5 message types fire to real phones immediately. Read `memory/project_go_live_messaging_checklist.md` in Claude memory before flipping.

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
