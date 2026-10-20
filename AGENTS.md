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

**Who:** Codex (tenant onboarding safety and local main integration)
**Date:** 2026-10-20

### What changed

- Integrated the tenant branch (including STK callback-race and C2B
  organization-boundary fixes from `852d69e`) with the four newer local `main`
  bank-reconciliation commits. The combined parser retains both the broader
  M-Pesa reference pattern and the new payer-name recovery logic.
- Tested first-time tenant sign-in, OTP/PIN setup, statements, invoices, receipts,
  payment-status access control, repair conversations, and logout/sign-in against a
  disposable copy of the 2026-10-20 production backup: 22 checks passed. A broader
  crawl rendered 371 pages across all 42 active tenants with zero failures, after
  temporary portal tokens were added only in that copied database.
- Found 8 of 42 active Mowin tenants lacked `access_token` despite having short
  codes and phones. Their short links returned 500 because `app.py` called
  `abort(404)` without importing it. All eight were created with the legacy
  tenant batch on 2026-05-19; no generate/revoke audit events exist for them.
  The exact reason for each missing token is not proven. Do not bulk-enable them.
- Fixed the missing Flask import, made the invitation preview show blocked
  tenant rows and prevent sending to them even if their IDs are forged in a POST.
- Removed automatic portal-token generation from five payment-assignment paths.
  Payment confirmation SMS still sends, but includes a portal link only if access
  was already enabled. This preserves intentional revocation.
- Added focused onboarding/security regression tests. The combined tree passed
  10 unittest cases and 9 parser regressions. Copied-database smoke checks passed
  the 22-step tenant journey, the invitation preview with 8 blocked rows, and
  a 371-page crawl across 42 tenants. See Session 26 in `CURSOR_PATTERNS.md`.

### Release status and next steps

1. Review the eight tenant records with Mowin before deliberately generating any
   missing portal tokens. The copied backup is evidence, not a live-state guarantee.
2. Resolve owner-approved rent and water due dates, tenant opening balances, and
   payment instructions before any invitation.
3. Finish TextSMS account setup and a one-phone delivery test. Current production
   Africa's Talking username is `sandbox`; no real SMS was sent here.
4. The combined work is now on local `main` but has not been pushed or deployed.
   Review it before release. No production DB write, live token generation,
   real SMS, or M-Pesa transaction occurred this session. The pre-existing
   untracked `.agents/` directory in the main checkout was left untouched.
5. Keep Daraja live money disabled until the law-firm paybill arrangement,
   credentials, callbacks, and real-money reconciliation are approved.
6. The previous ledger dry run found two unattributed rows: 6C water KES 1,200
   (2026-04) and 6A payment KES 3,600 (2026-02-27). Neither was reassigned.

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
