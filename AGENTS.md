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

**Who:** Codex (tenant payment review and fixes)
**Date:** 2026-10-20

### What changed

- Fetched Claude's current `origin/claude/tenant-portal-payments-mto5t5` branch at `bb8d14e`.
  Earlier local ref `2efefcd` was stale; the current branch does include M-Pesa hardening,
  invitations, guides, and caretaker restrictions. Work is isolated on
  `codex/tenant-payment-fixes` in `/private/tmp/domi-review.jLTqNd/checkout`.
- Fixed an STK callback race: Safaricom may call back before the initiating request has
  returned its checkout ID. Early callbacks are held as `awaiting_request`, then linked
  to the tenant and verified by the existing worker.
- Scoped C2B payments to the organization identified by the callback's
  `BusinessShortCode` and the configured property paybill. Unknown payments can only
  be assigned by an admin of that organization; ambiguous paybills stay unassigned.
- Added `payment_transactions.org_id` through an additive migration, updated the
  schema registry, and added four focused regression tests. All pass. App import and
  migrations also passed on a fresh copy of the 2026-10-20 production database.

### Readiness findings

- Production `AT_USERNAME` is `sandbox`. Real tenant SMS delivery is not ready.
  No SMS was sent and no Fly secret was changed.
- User prefers a cheaper SMS provider. TextSMS is implemented in this branch and
  advertises KES 0.30 per SMS; live credentials and a one-phone delivery test
  are still needed. Do not request API keys in chat.
- Mowin's owners have not decided rent and water due dates. User confirmed this
  remains in progress; tenant invitations must wait.
- The copied database has 42 active tenants. All have phone numbers and short
  portal codes; none has set a PIN. Ledger dry run found no active balance changes
  or cross-tenant allocations, but two unattributed rows: unit 6C water charge
  KES 1,200 (2026-04), and unit 6A payment KES 3,600 (2026-02-27). The latter
  links to bank record UBRFV83Z7X, sender TEDDY WAMBUA SIKUKU, but no tenant
  profile matches that name; the 6C charge has no tenant or matching water reading.
- Production backup used for testing:
  `backups/prod_2026-10-20_190452.db`. No merge, push, deploy, production DB write,
  or ledger `--execute` was performed.

### Pick up next

1. Integrate `codex/tenant-payment-fixes` with current `main` when appropriate;
   neither branch has been deployed with these changes.
2. Identify the tenants behind the two unattributed ledger rows from source records
   outside the current tenant table. Do not assign by unit number alone.
3. User can create a TextSMS account. Obtain partner ID and API key through that
   account, securely configure them, and prove delivery to the user's phone before
   sending invitations. Resolve owners' rent and water due-date decision first.
4. Keep Daraja live money disabled until the law-firm paybill arrangement is approved,
   credentials and callback URLs are set, and real-money tests are reconciled.
5. The claimed 26 M-Pesa checks and 116-page crawl from Claude's transcript were
   not committed as runnable suites; rerun relevant live-branch checks before release.

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
