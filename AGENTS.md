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

**Who:** Claude Code (payout security, sidebar restructure, onboarding UX, codebase cleanup)
**Date:** 2026-05-17

### What was completed this session

**Payout account security (beneficiary fraud prevention):**
- `migrate_add_payout_fields` — 5 new columns on `owners`: `payout_mpesa`, `payout_confirmed`, `payout_active_at`, `payout_otp`, `payout_otp_expires_at`
- `payout_mpesa` is owner-write-only via portal OTP flow — admin has zero write path
- `POST /view/<property_id>/payout/request-otp` + `confirm-otp` — OTP via SMS → 48h hold before disbursements activate
- `_get_confirmed_payout_owner()` in `disbursements.py` — hard-blocks disbursements until confirmed + hold passed; raises ValueError + critical platform alert if none found
- `templates/viewer/wallet.html` — full payout account UI (4 states: unset/pending_otp/hold/active)

**Admin sidebar restructure:**
- New order: Overview → Units → Payments → Messages → Bank Statements → Water Charges → Reports → Activity → (Monthly) Monthly Workflow → (Setup) Caretakers → Owners
- Water Charges added as dedicated sidebar item
- Monthly Workflow elevated from Tools footer into labeled Monthly section

**Monthly Workflow status indicators:**
- `tools_index()` route now queries DB per-step completion timestamps
- Each of 5 steps shows green (done this month) or red (needs doing); Step 4 Verify Payments was missing and is now added
- Unassigned credit count shown on Step 4 with direct link

**New user onboarding:**
- Setup checklist card on dashboard (3 steps: add owners, add caretaker, run workflow); auto-dismisses when all done
- Actionable empty states on: Payments confirmed tab, Bank Statements page, Unreported credits tab

**Codebase cleanup:**
- `src/utils/phone.py` created — `normalize_to_e164()` + `normalize_to_daraja()`, single source of truth
- Removed 4 duplicate `_normalize_phone` definitions from viewer_routes, tenant_routes, owner_routes, delivery.py
- `daraja.py` now imports `normalize_to_daraja` from utils instead of defining its own
- Deleted `src/validation/` (orphaned module — `validate_balance_checksum` was already in pdf_parser.py)
- `test_routes.py` CRUD routes gated behind `ENVIRONMENT != production` in app.py
- Moved screenshots to `docs/screenshots/`, bank PDF to `data/input/`, DB backups to `backups/`
- Archived `scripts/start_tunnel.sh` to `scripts/archived/` (replaced by Fly.io)
- `src/routes/owner_routes.py` documented in `.agent/routes.yaml`

### Pick up next

1. **Full end-to-end test run:** fresh property → bank statement workflow → SMS sandbox → M-Pesa STK Push sandbox → disbursement sandbox
2. **Phase 4 (The Conversation):** LLM intent classifier, tenant/caretaker/owner inbound handlers, bilingual responses — see `ROADMAP.md`
3. **Phase 5 (The Coordinator):** admin task feed, anomaly detection, caretaker morning briefing
4. **Phase H:** WhatsApp live channel (gated on Meta approval — apply now)
5. When ready to flip AT_USERNAME to live: read `memory/project_go_live_messaging_checklist.md` first

### Key files changed this session
- `src/utils/phone.py` — new canonical phone normalizer (normalize_to_e164, normalize_to_daraja)
- `src/routes/viewer_routes.py`, `tenant_routes.py`, `owner_routes.py` — removed local normalizers, import from utils
- `src/messaging/delivery.py` — removed local normalizer, import from utils
- `src/payments/daraja.py` — removed local normalize_phone, import from utils
- `src/database/db.py` — migrate_add_payout_fields
- `src/routes/viewer_routes.py` — payout OTP routes, property_wallet updated
- `src/payments/disbursements.py` — _get_confirmed_payout_owner guard
- `app.py` — tools_index() rewritten; test_bp gated; dashboard setup checklist
- `templates/viewer/wallet.html` — payout account management UI
- `templates/base.html` — sidebar restructure + active_nav update
- `templates/tools_index.html` — 5-step workflow with green/red status
- `templates/dashboard.html` — setup checklist card
- `templates/review.html`, `statements.html` — actionable empty states
- `.agent/schema.yaml` — payout columns on owners table
- `.agent/routes.yaml` — owner blueprint added, payout routes added
- `ROADMAP.md` — payout security + admin UX sections marked complete
- `CURSOR_PATTERNS.md` — Session 4 entries (COUNT None, payout ownership, workflow status pattern)

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
