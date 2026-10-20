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

**Who:** Codex (credential-free go-live preflight and payment hardening)
**Date:** 2026-10-20

### What changed

- Audited the 2026-10-20 backup read-only, then ran migration and ledger repair on
  a disposable copy only. Its 42 active tenants all had distinct, normalizable
  phones and short codes; 34 had portal tokens, 8 did not. No active unit had
  two active tenants. The backup is not a substitute for a current roll call.
- Ledger dry run found no active balance changes or cross-tenant allocations,
  but one unattributed 6C water charge (KES 1,200, 2026-04) and one unattributed
  6A payment (KES 3,600, 2026-02-27). These were not reassigned.
- On the copy, 11 tenants had both unapplied credit and unpaid charges. Running
  `scripts/tenant_ledger_report.py --execute` on that copy applied KES 121,600
  from 15 payments; no overlap remained. Total tenant balance was unchanged
  (KES 1,259,900). Allocation-over-payment, allocation-over-charge, and
  cross-tenant checks returned zero; SQLite integrity check passed. Nothing
  equivalent was run on production.
- The copied ledger built 42 statements, 126 invoices, and 102 receipts without
  error; invoice arithmetic held. Existing due dates reflect the old policy and
  remain subject to the owners' decision.
- Hardened M-Pesa handling in `src/payments/mpesa.py`: production callbacks need
  a token even if `DARAJA_ENV` is still sandbox; production STK bookings need
  query credentials; STK callback amount must match exactly and include a receipt;
  zero/invalid amounts cannot book; claims resolve only for the paying tenant;
  receipt links are omitted when portal access is disabled.
- Added offline TextSMS and payment regression tests. All 20 unittest cases and
  9 parser regressions passed; no provider network calls were made.

### Release status and next steps

1. Obtain the current Mowin roll; review the eight blocked portal accounts and
   the two unattributed ledger rows against source evidence. Do not bulk-enable
   access or reassign money by inference.
2. Owners must approve rent/water due dates, opening balances, and payment
   instructions before any invitation. Historical invoice due dates may change.
3. Before release, review the payment changes and run a fresh backup-first ledger
   preflight. The copied `--execute` result shows a potential repair, not approval
   to perform it on the live database.
4. Finish TextSMS signup and a one-phone real delivery test. The local mock test
   proves request construction and disabled-mode behavior, not provider delivery.
5. Keep Daraja live money disabled until the law-firm paybill arrangement,
   credentials, callbacks, and real-money reconciliation are approved.
6. Work is local only, on `main` (still unpushed and undeployed). No production
   DB write, live token generation, real SMS, or M-Pesa transaction occurred.
   The pre-existing untracked `.agents/` directory was left untouched.

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
