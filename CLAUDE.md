# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Domi — Property Intelligence Platform

## AI Re-entry Overview

**Domi** is a property intelligence platform for Kenya — an invisible layer across residential properties handling rent reconciliation, reporting, maintenance, and communication for landlords, managers, caretakers, and tenants. Name: *domus* (Latin: home).

**Codebase name:** `rent-reconciliation` (repo/deploy name unchanged)

**Strategic direction (locked 2026-05-04):** Property fintech. Tenants pay via M-Pesa STK Push or card through Domi. Domi holds and disburses to landlords net of management fee. See `ROADMAP.md` "Layer F" for spec.

**Canonical docs — read in this order:**
- `.agent/schema.yaml` — ground truth for all DB tables/columns
- `.agent/routes.yaml` — ground truth for all routes/blueprints
- `.agent/jobs.yaml` — ground truth for all scheduled jobs
- `.agent/env.yaml` — all environment variables
- `CLAUDE.md` (this file) — technical patterns, conventions, business rules
- `ROADMAP.md` — product vision, phase checklist, language rules
- `CURSOR_PLAN.md` — current work focus, next steps, implementation notes
- `CURSOR_PATTERNS.md` — failure log; **read before writing any code**
- `README.md` — how to run and deploy

**Next steps (in order):**
1. Full end-to-end test + demo run: fresh property → bank statement workflow → SMS sandbox → M-Pesa STK Push sandbox → disbursement sandbox
2. Phase 4 (The Conversation): LLM intent classifier, tenant/caretaker/owner inbound handlers, bilingual responses — see `ROADMAP.md` Phase 4 checklist
3. Phase 5 (The Coordinator): admin task feed, anomaly detection, caretaker morning briefing — see `ROADMAP.md` Phase 5 checklist
4. Phase H: WhatsApp live channel (gated on Meta approval via Africa's Talking — apply now)
5. Flip AT_USERNAME from `sandbox` → live, set Daraja/Pesapal to production when credentials arrive

**Phases 0–3 and Phase G are COMPLETE. Do not re-implement anything in those phases.**

---

## Development Commands

```bash
# Start dev server (port 5001, data/dev.db, no passwords)
./scripts/run_dev.sh

# Pull production DB from Fly.io to local
./scripts/download_prod_db.sh

# Reset dev DB from latest prod snapshot
./scripts/reset_dev_db.sh

# Verify app imports cleanly (run after any structural change)
./venv/bin/python -c "from app import app; print('OK')"

# Deploy to Fly.io
export PATH="$HOME/.fly/bin:$PATH"
fly deploy
```

**Env vars for local SMS sandbox testing:**
```bash
AT_USERNAME=sandbox AT_API_KEY=<your-sandbox-key> ./scripts/run_dev.sh
```

**Env vars for local M-Pesa (Daraja) sandbox testing:**
```bash
DARAJA_ENV=sandbox DARAJA_CONSUMER_KEY=... DARAJA_CONSUMER_SECRET=... ./scripts/run_dev.sh
```

There are no automated tests. Verification is manual: run the dev server, walk through the workflow, inspect DB state.

---

## Vision

Domi is a **financial and operational intelligence layer** — pull → push model. Lazy users are the expected baseline; the system delivers value regardless. Two data layers: financial (structured) + qualitative (unstructured → parsed via LLM). Full product layer specs and phase status are in `ROADMAP.md`.

**Data-descriptive language (hard rule):** Every user-facing string reports what the data knows — never what the agency did. "KES 312,000 verified against bank records" not "We collected KES 312,000". Full rule table in `ROADMAP.md`.

---

## Tech Stack

- Python 3.13, Flask 3.0+, SQLite (PostgreSQL migration path at ~10 properties)
- Jinja2 + Bootstrap 5; pdfplumber (PDF), pandas + openpyxl (Excel)
- No ORM — raw SQL with parameterized queries
- APScheduler — background scheduler (embedded in Flask)
- Anthropic Claude API — LLM inference via `src/agent/llm.py` wrapper only
- Africa's Talking — SMS (live) + WhatsApp Business API (pending approval)

---

## Project Structure

```
rent-reconciliation/
├── app.py                    # Flask app, admin routes, blueprint registration, APScheduler init
├── src/
│   ├── agent/
│   │   ├── coordinator.py    # Orchestrates all scheduled jobs
│   │   ├── detector.py       # Anomaly detection + task checker + nudges
│   │   ├── briefings.py      # Digest + briefing generators
│   │   ├── inbound.py        # Inbound parsing, intent classification, action handlers
│   │   ├── responder.py      # Response message generation
│   │   ├── router.py         # Delivery abstraction: portal / SMS / WhatsApp / email
│   │   ├── llm.py            # LLM wrapper — only file that imports Anthropic SDK
│   │   └── state.py          # Conversation session state (inbound_sessions)
│   ├── parsers/
│   │   ├── router.py         # Input auto-detection & routing
│   │   ├── pdf_parser.py     # Bank statement parsing (detect_bank_statement_format → 'cooperative'|'tabular_kes'|'unknown')
│   │   ├── sms_parser.py     # M-Pesa SMS parsing (exports parse_mpesa_message)
│   │   ├── excel_parser.py   # Tenant Excel import (exports parse_currency)
│   │   ├── water_parser.py   # Water readings Excel parser (reuses parse_currency — do not duplicate)
│   │   └── banks/
│   │       ├── __init__.py   # Empty
│   │       └── registry.py   # BANK_DISPLAY_NAMES, bank_display_name(), SUPPORTED_FORMATS — add new bank here
│   ├── routes/
│   │   ├── viewer_routes.py  # /view/*
│   │   ├── tenant_routes.py  # /tenant/<token>
│   │   ├── messaging_routes.py  # /messages/*
│   │   ├── report_routes.py  # /reports/*
│   │   ├── caretaker_routes.py  # /caretaker/*
│   │   ├── agent_routes.py   # /agent/*
│   │   ├── payment_routes.py # /inbound/payment/* (Daraja + Pesapal callbacks)
│   │   ├── inbound_routes.py # /inbound/sms, /inbound/whatsapp (Africa's Talking + WhatsApp webhooks)
│   │   └── test_routes.py    # /test/*
│   ├── reports/
│   │   └── landlord_report.py   # generate_report() + enrich_report_data()
│   ├── messaging/
│   │   ├── delivery.py       # send_sms(), phone normalizer (07xx → +2547xx)
│   │   ├── owner_notify.py   # notify_property_owners()
│   │   └── reminders.py      # Due-date reminder generation
│   ├── payments/             # Phase G — planned
│   │   ├── daraja.py         # STK Push (initiate), B2C (disburse to landlord)
│   │   ├── pesapal.py        # Card checkout integration
│   │   └── disbursements.py  # calculate_disbursement(), execute_disbursement()
│   ├── database/
│   │   ├── db.py             # get_connection(), generate_id(), all migrations
│   │   └── schema.sql
│   └── reconciliation/
│       ├── matcher.py        # Match claims to transactions
│       └── state_machine.py  # Payment lifecycle
├── templates/
│   ├── base.html             # Admin base (sidebar, property selector) — never modify for viewer changes
│   ├── agent/                # Simulator, digest preview, briefing preview
│   ├── viewer/               # base_viewer.html + dashboard, arrears, payments, reports, maintenance, notifications
│   ├── tenant/               # base_tenant.html + portal, charges, payments, messages, maintenance
│   ├── messaging/            # broadcast, templates, reminders, schedules
│   ├── reports/              # history, preview, caretaker_preview
│   ├── caretaker/            # login, base_caretaker, dashboard, arrears, tenants, messages, issues, log_payment
│   ├── platform/             # base_platform.html, login.html, dashboard.html, errors.html, parse_errors.html
│   ├── owners.html
│   └── caretakers.html
├── scripts/
│   ├── run_dev.sh            # Dev server :5001, data/dev.db, no passwords
│   ├── download_prod_db.sh   # Pull prod DB → backups/ + data/dev.db
│   ├── reset_dev_db.sh       # Reset dev.db from latest backup
│   ├── seed_phase6.py        # Seeds 2 orgs, 4 properties, owners, tenants (Phase 6)
│   └── seed_demo_payments.py # Seeds 132 rent charges, 12 statements, 56 payments with realistic scenarios
└── data/
    ├── rent.db               # Default DB
    └── dev.db                # Dev DB (set via DATABASE_PATH env var)
```

---

## Database — Critical Rules

Full schema in `.agent/schema.yaml`. Rules that have tripped agents:

- `rent_charges` UNIQUE(unit_id, period, charge_type) — always specify `charge_type` ('rent'|'service'|'water'); a unit can have all three per period
- `payment_allocations` — ON DELETE CASCADE on `payment_id`; never delete allocations manually
- `messages` requires `tenant_id NOT NULL` — owner inbox uses `owner_messages` (separate table)
- `owner_messages` query must SELECT all columns — omitting any silently hides data in the viewer
- `properties.rent_due_day` — 0 = last day of month; drives charge generation and reminder due-date logic
- `balance_snapshots` UNIQUE(unit_id, snapshot_date) — insert idempotently
- `inbound_sessions` keyed on (phone, property_id) — 24h TTL; resolves "yes"/"no"/"skip" replies
- `bank_statements` is **org-scoped** (as of 2026-05-12): new uploads set `org_id`, `property_id` is legacy/nullable. Always query by `WHERE org_id = ?` for new code. `bank_format` is stored at upload time (`cooperative`|`tabular_kes`|`unknown`).
- `statement_parse_errors` — every parse failure is persisted here. columns: id, org_id, statement_id (nullable), filename, file_path, bank_format, error_type (`transaction_row`|`validation`|`format_unknown`|`fatal`), error_message, raw_text, page_number, txn_index, created_at. Surfaced in admin review (Parse Errors tab) and platform dashboard (7-day count card).

---

## Key Patterns

**ID generation:**
```python
from src.database.db import generate_id
id = generate_id('PROP')   # → 'PROP-A1B2C3D4'
```

**DB access (auto-commit/rollback):**
```python
from src.database.db import get_connection
with get_connection() as conn:
    conn.execute("INSERT INTO ...", (val1, val2))
```

**Payment allocation — called automatically on verify/assign; do NOT call manually:**
```python
from src.database.db import allocate_payment
allocations = allocate_payment(conn, payment_id, unit_id, amount)
# Returns: [{charge_id, charge_type, period, allocated, charge_settled}, ...]
```

**LLM wrapper — never import Anthropic SDK outside this module:**
```python
from src.agent.llm import call_llm
result = call_llm(prompt, model='fast')   # 'fast' = haiku, 'smart' = sonnet
```

**Delivery router — agent code never calls send_sms() directly:**
```python
from src.agent.router import route_message
route_message({'recipient_phone': '+254712345678', 'recipient_role': 'caretaker',
               'property_id': 'PROP-45ED445A', 'message_type': 'daily_briefing', 'body': '...'})
```

**Intent classification:**
```python
from src.agent.inbound import classify_intent
result = classify_intent(raw_text, sender_role='caretaker')
# Returns: {'intent': 'maintenance_report', 'confidence': 0.94, 'extracted': {...}}
# confidence >= 0.85 → act + confirm; below → ask before acting
```

**Input router:**
```python
from src.parsers.router import parse_input
result = parse_input(data)  # Auto-detects SMS/PDF/Excel
```

**Bank format registry — always use for display names and format lists:**
```python
from src.parsers.banks.registry import bank_display_name, SUPPORTED_FORMATS
label = bank_display_name('cooperative')  # → 'Co-operative Bank'
label = bank_display_name('unknown')      # → 'Unknown format'
# To add a new bank: add entry to BANK_DISPLAY_NAMES in registry.py,
# write parser in src/parsers/banks/<bank>.py,
# add detection branch in detect_bank_statement_format() in pdf_parser.py
```

**`detect_bank_statement_format` returns `'unknown'` for unrecognised PDFs** — never silently falls back to `'cooperative'`. An `'unknown'` format is logged as a `format_unknown` parse error and the upload is marked `parse_failed`.

**Migrations — all idempotent, all auto-run at startup in app.py:**
```python
from src.database.db import migrate_add_charge_type  # etc.
# Use CREATE TABLE IF NOT EXISTS and ALTER TABLE ADD COLUMN — never drop/recreate
```

---

## User Roles

- **Agency Admin**: Full CRUD, main routes. Manages owners (`/owners`), caretakers (`/caretakers`)
- **Property Owner**: View-only `/view/*`; shared password (`VIEWER_PASSWORD`); Reports + owner inbox
- **Caretaker**: `/caretaker/<property_id>`; named account (name+password) or `CARETAKER_PASSWORD` fallback
- **Tenant**: Read-only `/tenant/<token>`; token in URL, no session

---

## Portal-Specific Rules

### Owner Viewer (/view/*)
- Auth: `VIEWER_PASSWORD` → `session['owner_id']`; property list filtered via `property_owners` M:M
- Ownership check on every `/view/<property_id>/...` route — returns 403 if not in `property_owners`
- Templates extend `viewer/base_viewer.html`; pass `active_tab`
- Notifications page: SELECT all columns from `owner_messages` — omitting any silently hides data
- Broadcasts show `template_body` (unsubstituted). `sent_by` badge: blue=Admin, amber=Caretaker, gray=System
- Reports tab: always call `enrich_report_data()` before rendering (back-fills fields on old reports)

### Caretaker Portal (/caretaker/*)
- Auth priority: 1) `caretakers` table (name+password); 2) `CARETAKER_PASSWORD` env var; 3) open in dev
- `before_request` verifies `caretaker_id` still exists in DB — deletion revokes immediately
- `sent_by` attribution: `session.get('caretaker_name', 'Caretaker')` for owner inbox
- Log Payment: parses SMS via `sms_parser.parse_mpesa_message`, notifies owners on submission
- Paths starting with `/caretaker` exempt from admin auth

### Caretaker Management (/caretakers)
- Replace workflow: create new → brief them → delete old. Deletion immediately revokes sessions.

### Tenant Portal (/tenant/*)
- Auth: token in URL (`tenants.access_token`); no session; `/tenant` paths exempt from admin auth
- `_get_tenant_by_token(conn, token)` in `tenant_routes.py` → (tenant, unit, property) or None

### Monthly Reports (/reports/*)
- Always call `enrich_report_data()` before rendering — back-fills fields on old saved reports
- Collection metric: `total_verified / expected_monthly_income * 100` — NOT verified ÷ period charges (misleadingly low mid-month)
- PDF export: `window.print()` — no server-side generation

---

## Multi-Property Support (Admin)

Selected property in `session['property_id']`. `get_current_property(conn)` in `app.py` returns row or None (clears invalid session). All property-scoped routes use this helper — never `SELECT * FROM properties LIMIT 1`. Single-property UX: `/properties` auto-selects and redirects to dashboard. `activity()` is global (no `property_id` on `audit_log`).

`get_current_property(conn)` is also defined locally in `messaging_routes.py` and `report_routes.py` — not imported from `app.py`.

---

## Charge Types & Monthly Workflow

Three charge types per tenant per period: **rent** (fixed, `units.monthly_rent`), **service** (fixed, `units.service_charge`, only if > 0), **water** (variable, Excel upload).

Workflow: upload water → generate rent+service → tenants pay M-Pesa → upload bank statement → claims auto-verified → FIFO allocation → export reports.

FIFO: oldest charges first regardless of type. Overpayments show as "Overpayment / Credit" in Export 3. Allocations cascade-delete with payment. Allocation happens automatically in `verify_payments()` and `assign_payment()` — never call `allocate_payment()` manually.

---

## Messaging (Admin)

- Scope: current property only
- Broadcast: one `messages` row per recipient, shared `batch_id`; `template_body` stores unsubstituted template
- Two coexisting reminder systems: `reminder_settings` (legacy hardcoded keys) + `reminder_schedules` (flexible per-property). Both idempotent per day. Due dates use `properties.rent_due_day` + `rent_charges.due_date`
- `notify_property_owners()` in `owner_notify.py` — stores in `owner_messages` + SMS to owners with phones. Called from broadcasts, reminders, payment verifications, report generation, caretaker claims
- Payment SMS wording: "payment confirmed" — never mention bank statements

---

## Exports

- Export 1: Current state by charge type per unit
- Export 2: Audit log with date filter (`?from=YYYY-MM-DD&to=YYYY-MM-DD`)
- Export 3: Payment verification — 3 sheets with full allocation trail (most complex)
- All use `openpyxl`, log to `audit_log` automatically

---

## Agent System (The Coordinator)

Channel-agnostic and additive. Never modifies existing routes. Writes to existing tables (audit_log, messages, owner_messages) and new agent tables.

**Agent owns (runs without human input):**
- Daily balance snapshots; monthly charge generation (if not done by day 3)
- Weekly digest (Monday morning); caretaker morning briefing (routine issues batched; urgent forwarded immediately 24/7)
- Monthly tenant check-ins (1–3 rating + free text); payment rejection notifications; reminder sending
- Anomaly detection: water >30% above 3-month avg (configurable), vacancy duration, arrears thresholds, collection pace

**Agent flags (human decides):**
- Missing bank statement, water charges, claims aging >7 days, unassigned transactions
- No payment/claim by day 15 → nudge caretaker; arrears threshold crossings → nudge + owner report
- Water anomaly → caretaker must acknowledge (non-response logged); unit goes vacant → caretaker must comment
- Caretaker escalation requests (always in owner report); 24h acknowledgment failures (logged)
- Low-confidence inbound parses (<0.85); owner instructions from inbound replies

**Language:** All outbound supports English + Kiswahili. `tenants.language_preference` NULL triggers "English or Kiswahili?" on first inbound contact.

**Tenant flagging:** Triggered when M-Pesa reference absent from bank statement. `tenants.flagged = true` — future claims not given pending-state treatment. Admin clears manually.

**Admin task feed:** Embedded in main dashboard (`dashboard.html`); `base.html` untouched. First thing seen on login.

**Agent admin routes:**
- `GET /agent/simulator` — intent classification + response preview
- `GET /agent/digest/preview/<property_id>`, `/briefing/caretaker/<property_id>/preview`, `/briefing/owner/<property_id>/preview`, `/checklist/<property_id>/preview`
- `POST /agent/trigger/<job_name>` — manually trigger any scheduled job

---

## WhatsApp / Inbound Channel

One WhatsApp number serves all users across all properties. Identity = phone number.

Lookup priority: caretakers → owners → tenants → unknown. Multi-property owners: prompt "Reply 1 for [A], 2 for [B]", cache in `inbound_sessions` for 24h.

**Inbound flow (async — never block on LLM):**
```
POST /inbound/sms or /inbound/whatsapp
  → Write to inbound_messages, return 200 immediately
  → Background: classify intent → action handler → send response
```

Outbound proactive messages require Meta-approved templates (plain text, `{{1}}` variables). WhatsApp adapter in `router.py` is a stub until credentials are live. SMS is the fallback.

---

## Fintech Architecture (Payment Rail — Phase G)

**Strategic model:** Money-in-transit. Tenants pay via Domi Paybill. Domi holds and disburses net of management fee (default 8%, `properties.management_fee_rate`).

**`src/payments/`:** `daraja.py` (STK Push + B2C), `pesapal.py` (card), `disbursements.py`. Payment modules never import from `src/agent/` or route files — write to DB and return.

**`src/routes/payment_routes.py`** — `url_prefix='/inbound/payment'`. Routes exempt from admin auth. Always write-and-return-200; never block on processing.

**New tables** (full schema in `.agent/schema.yaml`):
- `payment_transactions` — raw Daraja/Pesapal callbacks; `external_reference` UNIQUE (dedup key); FK to `payments.id` set when processed
- `disbursements` — landlord payouts: status lifecycle pending → processing → completed/failed

**FIFO transparency (required, non-negotiable):** Confirmation SMS + portal must show allocation:
"KES 10,000 confirmed — applied: KES 5,000 to Oct service charge, KES 5,000 to Nov rent."
Data lives in `payment_allocations`.

Payment flow steps: see `CURSOR_PLAN.md` Phase G. Env vars: see `.agent/env.yaml`.

---

## Scaling Architecture

- **Now (1–5 props):** SQLite + APScheduler in-process + single gunicorn worker
- **5–15 props:** PostgreSQL + Redis/RQ + second Fly worker
- **15+ props:** `src/agent/` extracted to separate Fly app + PgBouncer + dedicated inbound processor

**Architectural rules (cheap now, expensive to retrofit):**
1. Agent logic never imports from routes — communicate via DB only
2. LLM calls are always async — never block a web request or webhook on inference
3. Every agent feature is property-scoped — all tables have `property_id`; all jobs parameterized by property
4. Delivery is abstracted — `src/agent/router.py` is the only place that knows about channels
5. LLM provider is abstracted — `src/agent/llm.py` is the only file that imports the Anthropic SDK

---

## Local Development + Deployment

See `README.md` for all commands and Fly.io first-time setup.

Key gotcha: Fly CLI at `/Users/lincksmorara/.fly/bin/flyctl` — not in PATH by default. Run `export PATH="$HOME/.fly/bin:$PATH"`. Single gunicorn worker required for SQLite write safety.

---

## Business Context

- Kenya property management agency (2-3 person team). One property: "Mowin Apartments" (44 units, `PROP-45ED445A`)
- Target: 3-5 properties near-term. Management fee: ~8% (embedded in disbursement spread, not a visible line item)
- Tenants pay M-Pesa; bank statements confirm. Custom tech is a genuine differentiator in the Kenya market

---

## Agent Coordination Rules

Multiple AI agents may work simultaneously (Claude Code, Cursor, etc.).

**Before starting:** Read canonical docs in the order listed at the top of this file. Don't rebuild what's already marked complete in `ROADMAP.md`.

**Before touching shared files** (`app.py`, `db.py`, `schema.sql`, `base_viewer.html`): check for recent changes.

**While working:**
- Data-descriptive language only — no agency voice anywhere (see `ROADMAP.md` rule table)
- Additive migrations only — `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN`; never drop/recreate
- Follow existing patterns: `generate_id()`, `get_connection()`, raw SQL, Blueprint structure

**After completing work:**
- Update `ROADMAP.md` — mark completed items `[x]`, add new items
- Update `CLAUDE.md` — add new files, routes, tables, blueprints
- Test locally: `./venv/bin/python app.py`
