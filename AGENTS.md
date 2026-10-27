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

**Who:** Claude (Codex/ChatGPT no longer in use — Claude now builds and reviews)
**Date:** 2026-10-27

### Product direction agreed with stakeholders (see ROADMAP / memory)

- **Reactive, not proactive:** nothing is texted automatically except payment
  receipts; admin and caretaker act on lists (e.g. tenants >1 month behind).
- **Paybill:** the law firm (drew the lease) is applying for its **own M-Pesa
  Paybill**; account number = house number. Portal STK waits for it.
- **Lease (Mowin):** rent due on/before the **5th, in advance**; water with the
  rent; one-month deposit. This settles the due-date go-live blocker.
- **Onboarding must be repeatable:** new lease = clean start; old debt only as
  an evidenced "brought forward" line; profiles can be "lease pending".
- **Owners see all tenant↔management conversations** (no notice to tenants).
- **Move-out:** one month's notice expected; also handle walk-outs.

### What changed (local, on `main`, not deployed)

- Reviewed Codex's 2026-10-20 commits — sound, except: production detection.
  `ENVIRONMENT` is never set on Fly (fly.toml sets `FLASK_ENV`), so the M-Pesa
  "no token → refuse callbacks in production" guard and the dev-only `/test`
  blueprint treated production as dev. New `src/utils/runtime.is_production()`
  used everywhere. Test added.
- New default-off `AUTOMATIC_ALERTS_ENABLED`: holds the Monday owner digest SMS,
  stale-claim SMS, and verify-time not-found / mismatch / flag-cleared SMS.
  Flags, alerts and audit rows still written. Receipts unaffected. 4 tests.
- `delete_payment` is POST-only; the "Undo" flash is a one-button form.
- 31/31 tests pass.

### Build order (agreed)

0. **Make SMS safe and deploy** — code done; deploy + TextSMS switch-over per
   RUNBOOK "Switching production SMS to TextSMS". Awaiting user's TextSMS keys
   and go-ahead. Nov 1 monthly check-in is gated on current main, not on v91.
1. Lease intake + repeatable onboarding (lease-pending profiles, ID no., lease
   start, deposit, evidenced brought-forward balance), then all 44 units.
2. Receipt SMS includes remaining balance; broadcast to portal **and** SMS.
3. ">1 month behind" lists for admin and caretaker.
4. Admin ↔ caretaker chat (caretaker reports).
5. Tenant move-out notice + admin "left without notice".
6. Owner view of conversations + per-unit performance.
7. Portal payments once the law firm's Paybill exists. Note: `resolve_account`
   matches the house number org-wide — two properties with a "6A" under one
   Paybill would be ambiguous; needs a per-property prefix before property #2.

### Still open from Codex's list

Eight tenants without portal access and two unattributed ledger rows (6C water
KES 1,200 2026-04; 6A payment KES 3,600 2026-02-27) — resolve during onboarding,
not by inference. No CSRF protection anywhere (forms are session-cookie only).

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
