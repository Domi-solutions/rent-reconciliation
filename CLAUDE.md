# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Domi — Property Intelligence Platform

## AI Re-entry Overview

**Domi** is a Kenya property intelligence platform — rent reconciliation, reporting, maintenance, and communication for landlords, managers, caretakers, and tenants. Deployed as `rent-reconciliation` on Fly.io. Name: *domus* (Latin: home).

**Strategic direction (locked 2026-05-04):** Property fintech. Tenants pay via M-Pesa STK Push or card through Domi. Domi holds and disburses to landlords net of management fee. See `ROADMAP.md` "Layer F" for spec.

**Canonical docs — read in this order:**
- `.agent/schema.yaml` — ground truth for all DB tables/columns
- `.agent/routes.yaml` — ground truth for all routes/blueprints
- `.agent/jobs.yaml` — ground truth for all scheduled jobs
- `.agent/env.yaml` — all environment variables
- `CLAUDE.md` (this file) — technical patterns, conventions, business rules
- `ROADMAP.md` — product vision, phase checklist, language rules, next steps
- `CURSOR_PLAN.md` — current work focus, next steps, implementation notes
- `CURSOR_PATTERNS.md` — failure log; **read before writing any code**
- `README.md` — how to run and deploy

**Phases 0–3 and Phase G are COMPLETE. See `ROADMAP.md` for what's next. Do not re-implement anything in those phases.**

---

## Development Commands

```bash
./scripts/run_dev.sh                                          # dev server :5001, data/dev.db, no passwords
./scripts/download_prod_db.sh                                 # pull prod DB → backups/ + data/dev.db
./scripts/reset_dev_db.sh                                     # reset dev.db from latest backup
./venv/bin/python -c "from app import app; print('OK')"       # verify imports (run after structural changes)
export PATH="$HOME/.fly/bin:$PATH" && fly deploy              # deploy to Fly.io
```

Sandbox testing: `AT_USERNAME=sandbox AT_API_KEY=<key> ./scripts/run_dev.sh` | `DARAJA_ENV=sandbox DARAJA_CONSUMER_KEY=... DARAJA_CONSUMER_SECRET=... ./scripts/run_dev.sh`

There are no automated tests. Verification is manual: run the dev server, walk through the workflow, inspect DB state.

---

## Tech Stack

- Python 3.13, Flask 3.0+, SQLite (PostgreSQL migration path at ~10 properties)
- Jinja2 + Bootstrap 5; pdfplumber (PDF), pandas + openpyxl (Excel)
- No ORM — raw SQL with parameterized queries
- APScheduler — background scheduler (embedded in Flask)
- Anthropic Claude API — LLM inference via `src/agent/llm.py` wrapper only
- Africa's Talking — SMS (live) + WhatsApp Business API (pending approval)

---

## Key Source Files

```
app.py                         # Flask app, admin routes, blueprint registration, APScheduler init
src/platform/guardian.py       # platform_log(), raise_alert(), notify_owner_change()
src/agent/coordinator.py       # orchestrates all scheduled jobs
src/agent/detector.py          # anomaly detection + task checker + nudges
src/agent/briefings.py         # digest + briefing generators
src/agent/inbound.py           # inbound parsing, intent classification, action handlers
src/agent/llm.py               # ONLY file that imports Anthropic SDK; call_llm(prompt, model='fast'|'smart')
src/agent/router.py            # ONLY place that knows about SMS/WhatsApp/email channels; route_message()
src/parsers/pdf_parser.py      # detect_bank_statement_format() → 'cooperative'|'tabular_kes'|'scanned'|'unknown'
src/parsers/sms_parser.py      # parse_mpesa_message()
src/parsers/router.py          # parse_input() — auto-detects SMS/PDF/Excel
src/parsers/excel_parser.py    # parse_currency() — reused by water_parser; do not duplicate
src/parsers/water_parser.py    # water readings Excel; non-numeric values ("Vacant","N/A","-","nil",blank) skipped with warning
src/parsers/banks/registry.py  # bank_display_name(), SUPPORTED_FORMATS — add new banks here
src/database/db.py             # get_connection(), generate_id(), allocate_payment(), all migrations
src/utils/phone.py             # normalize_to_e164(), normalize_to_daraja() — ONLY phone normalization
src/utils/metrics.py           # get_property_occupancy(), get_expected_monthly_income(), get_months_behind()
src/utils/statement.py         # get_tenant_statement(), quick_verify_ref()
src/reconciliation/matcher.py  # enrich_with_suggestions() — 3-tier unit suggestion
src/reconciliation/state_machine.py  # payment lifecycle
src/reports/landlord_report.py # generate_report(), enrich_report_data()
src/messaging/delivery.py      # send_sms(), send_sms_async() — logs to platform_outbox via _log_sms()
src/messaging/outbox.py        # log_outbox() — every outbound message to platform_outbox
src/messaging/owner_notify.py  # notify_property_owners()
src/payments/daraja.py         # STK Push (initiate), B2C (disburse)
src/payments/pesapal.py        # card checkout
src/payments/disbursements.py  # _get_confirmed_payout_owner() — hard blocks unsafe disbursements
src/routes/viewer_routes.py, tenant_routes.py, messaging_routes.py, report_routes.py
src/routes/caretaker_routes.py, agent_routes.py, payment_routes.py, inbound_routes.py
```

Messaging helpers in `messaging_routes.py` (do not duplicate inline):
- `_next_due_date(rent_due_day)` — returns next rent due date (date object) from `properties.rent_due_day`
- `_build_variables(conn, tenant, prop, base_url=None)` — single source of truth for broadcast/thread substitution; returns dict with `{tenant_name}`, `{balance}`, `{unit}`, `{due_date}`, `{caretaker_phone}`, `{portal_link}`, `{property_name}`

Templates: `base.html` (admin base), `viewer/base_viewer.html` + `viewer/wallet.html`, `caretaker/`, `tenant/`, `platform/`, `messaging/`, `tenant_statement.html`, `move_out.html`, `owners.html`, `caretakers.html`

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
- `bank_statements` is **org-scoped**: always query `WHERE org_id = ?`. `property_id` nullable (NULL = org-wide); upload never forces it. Statements list shows per-statement coverage badge. `bank_format` values: `cooperative`|`family_bank`|`national_bank`|`tabular_kes`|`scanned`|`unknown`. `scanned` = Haiku vision; balance validation skipped.
- `water_uploads` UNIQUE(property_id, charge_period). `charge_period` = reading_period + 1 month (billing in arrears). Rate snapshotted from `properties.water_rate` at upload.
- `water_readings` — one row per unit per upload. `previous_reading` auto-populated from last `current_reading`. `amount` = units_consumed × rate → written to `rent_charges` as `charge_type='water'`.
- `property_owners.is_primary` — auto-set to 1 on the first owner assigned to a property. Admin cannot remove a primary owner via the UI; must contact platform to transfer. `remove_property_from_owner` route blocks with 400 if `is_primary=1`. Existing rows migrated by `migrate_add_primary_owner` using `MIN(rowid)` per property.
- `payment_claims.status`: `pending`|`verified`|`flagged` (never `rejected`). `flagged` is permanent; resolution via `caretaker_confirmed`/`admin_cleared` columns. At-submission outcomes: (1) ref found + no payment → create + verify immediately; (2) ref found + payment exists → link + verify; (3) ref absent → flag. Stays `pending` only if no bank data yet. `verify_payments` also re-checks flagged claims — if a new statement contains the ref: status → `verified`, payment created, tenant unflagged (if no other open flags).
- `payment_claims` three-layer resolution: `status='flagged'` never changes; `caretaker_confirmed=1` + `caretaker_note` (caretaker POV); `admin_cleared=1` + `admin_note` (admin POV); platform always sees raw flagged state. All changes written to `audit_log` + `platform_shadow_log`.
- `tenants.flagged=1` — set automatically when a claim is flagged; future claims from this tenant get no pending-state treatment. Admin clears manually only.
- `payments.notes` — admin annotation on any confirmed payment. `payments.caretaker_note` — caretaker annotation, written via caretaker portal, visible to admin and platform. Both write to `audit_log`; `caretaker_note` also writes to `platform_shadow_log`. Amount mismatch (bank ≠ claimed) writes `payment_amount_mismatch` to both `audit_log` and `platform_alerts`.
- `bank_transactions.ignored` — `1` means the transaction is skipped in the workflow unassigned count (so Step 4 can go green) but the row is NOT deleted and remains assignable. Set via `POST /payments/ignore/<txn_id>`; cleared via `POST /payments/unignore/<txn_id>`. Visible in the Ignored tab on the review page. Any route that counts unassigned credits MUST add `AND (bt.ignored IS NULL OR bt.ignored = 0)`.
- `platform_outbox` — every outbound message logged here regardless of channel or delivery status. Written by `log_outbox()` in `src/messaging/outbox.py`. Status values: `sent` | `failed` | `simulated` (simulated = no API configured, code visible for testing). Never used for business logic — audit/debug only. Filters: channel, status. Surfaced at `/platform/outbox`.
- `statement_parse_errors` — every parse failure persisted here. `error_type`: `transaction_row`|`validation`|`format_unknown`|`fatal`. Surfaced in admin review (Parse Errors tab) and platform dashboard (7-day count card). Full schema in `.agent/schema.yaml`.
- `platform_shadow_log` — agency-uneditable record of sensitive actions. Written by `src/platform/guardian.py`. Never query or display in any org-admin route. Platform only.
- `tenant_disputes` — concerns submitted by tenants directly to Domi. Written via `POST /tenant/<token>/dispute`. Platform resolves; agency cannot see.
- `platform_alerts` — anomaly alerts raised on sensitive actions (owner removed = critical; rent changed >10% = warning/critical). Platform dismisses; agency cannot see.
- `owners.payout_mpesa` — **admin has zero write path to this field**. It is set exclusively by the owner via portal OTP flow (`POST /view/<property_id>/payout/request-otp` → `confirm-otp`). Disbursements are hard-blocked until `payout_confirmed=1` AND `payout_active_at <= now()` (48-hour hold after OTP confirmation). See `_get_confirmed_payout_owner()` in `disbursements.py`.
- `units.status` values: `occupied` | `vacant` | `owner_use` | `short_term`. `office` was renamed to `owner_use` via `migrate_rename_office_to_owner_use`. `occupied` is **only** set by the move-in flow (`add_tenant` route) — never via direct field edit or `set_unit_status`. `owner_use` and `short_term` are excluded from rentable count and charge generation. Status cannot be changed via UI when an active tenant exists.
- `tenants.status` values: `active` | `departed` | `inactive`. `departed` = moved out with remaining debt, portal access preserved so tenant can view balance and pay. `inactive` = settled or written-off, access_token cleared. `departed` tenants appear in caretaker log-payment "Departed tenant" mode for post-departure bank matching.
- `tenants.deposit_paid` — recorded at move-in (via `add_tenant` route). Pre-fills `deposit_held` field on the move-out form. Added by `migrate_add_deposit_paid`.
- `tenants.move_in_notes` — optional admin notes recorded at move-in. Added by `migrate_add_deposit_paid` (same migration).
- `tenants.short_code` — 6-char alphanumeric code for short portal links. `GET /t/<code>` redirects to `/tenant/<access_token>`. Generated at move-in; backfilled for existing tenants at migration. Added by `migrate_add_tenant_short_code`. All SMS payment/portal links should use `/t/<short_code>` not the full access_token URL.
- `payment_claims.source` values: `web`|`caretaker`|`tenant`|`whatsapp`|`sms`. `tenant` = self-reported via tenant portal `/tenant/<token>/report-payment`.
- `tenant_departures` — one row per move-out. Columns: id, tenant_id, unit_id, property_id, departure_date, balance_at_departure, deposit_held, deposit_applied, deposit_refunded, remaining_debt, debt_status ('none'/'active'/'written_off'), write_off_note, admin_notes, created_at. Added by `migrate_add_tenant_departures`.
- `payment_claims.departed_tenant_id` — FK to `tenants.id`; set when caretaker submits a claim in "Departed tenant" mode. Used to link post-departure payments to the correct former tenant. Added by `migrate_add_tenant_departures`.
- `landlord_reports.needs_refresh`/`refresh_reason`/`refreshed_at` — staleness tracking. Reports < 3 months regenerate on every view; ≥3 months served from stored JSON. `_flag_stale_reports()` in `app.py` sets `needs_refresh=1` after upload/verify.
- `unit_balances` VIEW — charges and payments scoped to current active tenant's `move_in_date` so a new tenant starts with a clean balance (no inherited history from previous tenant).
- `bank_transactions` — has index `idx_bank_txn_date` on `(txn_date)` added by `migrate_add_bank_txn_date_index` to avoid strftime full-table scans on period queries.

---

## Key Patterns

**Phone normalization — single source of truth, never define inline:**
```python
from src.utils.phone import normalize_to_e164 as _normalize_phone
phone = _normalize_phone('0712345678')  # → '+254712345678'; returns None for unrecognised input
```

**Property metrics — single source of truth, never inline in routes:**
```python
from src.utils.metrics import get_property_occupancy, get_expected_monthly_income, get_months_behind
occ = get_property_occupancy(conn, property_id)
# → {'total', 'occupied', 'vacant', 'owner_use', 'short_term', 'rentable', 'occupancy_rate', 'office'}
# 'office' is a backward-compat alias for owner_use; rentable = total - owner_use - short_term
income = get_expected_monthly_income(conn, property_id)  # → float KES
months = get_months_behind(balance, monthly_rent)         # → int, 0 if monthly_rent == 0
```

**Unit suggestion enrichment — single source of truth for both statement_detail and review routes:**
```python
from src.reconciliation.matcher import enrich_with_suggestions
enrich_with_suggestions(rows, conn, property_id, org_id=None)
# Modifies dicts in-place. Sets: suggested_unit_id, suggested_unit_number, suggestion_source,
# suggested_tenant_name (name_match only).
# Tier 1: unit_hint exact match (org-scoped).
# Tier 2: sender name token overlap >= 2 tokens against active tenant names.
# Tier 3: sender_history — sender has previously paid for a unit (bank_txn→payment link);
#          tokens overlap >= 2; highest-frequency unit wins. Badge shown as "Past payer".
```

**Tenant statement builder:**
```python
from src.utils.statement import get_tenant_statement, quick_verify_ref
tenant, ledger, open_claims = get_tenant_statement(conn, tenant_id, property_id)
# tenant: dict with unit_number, monthly_rent; ledger: sorted rows with running_balance;
# open_claims: pending/flagged claims enriched with source_label, checked_statements list.

result = quick_verify_ref(conn, text, unit_id, org_id)
# text: M-Pesa SMS or bare ref code. Searches bank_transactions org-scoped.
# result['status']: 'found'|'already_this_unit'|'already_other_unit'|'not_found'|'parse_error'
# When 'found': result['txn_id'] is the bank_transactions.id to pass to quick-assign route.
```

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

**Payment allocation — called automatically on verify/assign AND at-submission auto-verify in caretaker `log_payment`; do NOT call manually anywhere else:**
```python
from src.database.db import allocate_payment
allocations = allocate_payment(conn, payment_id, unit_id, amount)
# Returns: [{charge_id, charge_type, period, allocated, charge_settled}, ...]
```

**SMS — async for confirmations, sync for security-critical alerts:**
```python
from src.messaging.delivery import send_sms, send_sms_async
send_sms_async(recipients, message)   # non-blocking daemon thread; confirmations, notifications
send_sms(recipients, message)         # blocking; payout OTP, fraud alert only
```

**LLM wrapper — never import Anthropic SDK outside this module:**
```python
from src.agent.llm import call_llm
result = call_llm(prompt, model='fast')   # 'fast' = haiku, 'smart' = sonnet
```

**Bank format registry — always use for display names and format lists:**
```python
from src.parsers.banks.registry import bank_display_name, SUPPORTED_FORMATS
label = bank_display_name('cooperative')  # → 'Co-operative Bank'
label = bank_display_name('scanned')      # → 'Scanned PDF (AI)'
# To add a new bank: add to BANK_DISPLAY_NAMES, write src/parsers/banks/<bank>.py,
# add detection branch in detect_bank_statement_format() in pdf_parser.py
```

**Scanned PDF parsing — LLM vision fallback:** Triggered when pdfplumber extracts zero text. Requires `ANTHROPIC_API_KEY`; Claude Haiku, 2 pages/chunk; balance validation bypassed; ~$0.05–$0.20/upload.

**Platform guardian — call from any route that performs a sensitive agency action:**
```python
from src.platform.guardian import platform_log, raise_alert, notify_owner_change
platform_log(conn, 'action_name', 'entity_type', entity_id, 'details', org_id=org_id, property_id=pid)
raise_alert(conn, 'alert_type', 'details', org_id=org_id, property_id=pid, severity='critical')
notify_owner_change(conn, property_id, 'Subject line', 'Body text')
```
Sensitive actions that must call guardian: unit field edit (rent/service change >10%), owner removed from property, tenant moved out, payment reversed.

**`detect_bank_statement_format` returns `'unknown'` for unrecognised PDFs** — never silently falls back to `'cooperative'`. An `'unknown'` format is logged as a `format_unknown` parse error and the upload is marked `parse_failed`.

**Migrations — all idempotent, all auto-run at startup in app.py:**
```python
from src.database.db import migrate_add_charge_type  # etc.
# Use CREATE TABLE IF NOT EXISTS and ALTER TABLE ADD COLUMN — never drop/recreate
```

Migration call order: append-only, never reorder. See `app.py` startup for full list. Latest: `migrate_rename_office_to_owner_use`.

---

## User Roles

- **Agency Admin**: Full CRUD, main routes. Manages owners (`/owners`), caretakers (`/caretakers`)
- **Property Owner**: Primary login at `/login` (email + password) or direct token link `/owner/l/<token>` (password only). Person account via `persons` table. Also has legacy view-only `/view/*` access bridged from person session.
- **Caretaker**: `/caretaker/<property_id>`; named account (name+password) or `CARETAKER_PASSWORD` fallback
- **Tenant**: Read-only `/tenant/<token>`; token in URL, no session

---

## Portal-Specific Rules

### Owner Login (/login + /owner/*)
- `/login` handles: org admin (`organizations`), owner person (`persons`), and master `ADMIN_PASSWORD`
- Owner login sets `session['person_id']` + `session['person_role']='owner'`; bridges to viewer via `session['owner_id']`
- Token link `/owner/l/<token>` (password-only form): three states: invalid, not_activated, ready
- Activation: admin sets password → email OTP → `verify_email` clears OTP + logs in. OTP logged to `platform_outbox` (status='simulated' if SMTP unconfigured)
- `persons.email` is **immutable** once `password_hash` is set. Enforced in `edit_owner()`.

### Owner Viewer (/view/*)
- Auth: bridged from person session (preferred) or legacy `VIEWER_PASSWORD` → `session['owner_id']`; property list filtered via `property_owners` M:M
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
- **One report per period** — both generation routes upsert on `(property_id, period_start, period_end)`; regenerating never creates a duplicate row
- **Report ordering** — history list ordered by `period_end DESC`; most recent period always first
- **Arrears are period-scoped** — `generate_landlord_report()` arrears query filters `rent_charges WHERE period <= period_end_month` and `payments WHERE payment_date <= period_end`; historical reports show arrears as of that period's close, not today
- **Live/Stale badges** — history list shows green "Live" badge (< 3 months, auto-regenerates on view) and amber "Stale — refresh needed" (frozen period with new bank data since last generation)

---

## Admin Sidebar

`active_nav` is derived from `request.endpoint` via a chained Jinja2 ternary at the top of `base.html`. When adding a new route that should highlight a sidebar item, add the endpoint to the correct `active_nav` branch. Never modify `base.html` for viewer-side changes.

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
- `notify_property_owners()` in `owner_notify.py` — stores in `owner_messages` + SMS to owners with phones. Uses `send_sms_async()` internally — never blocks the HTTP request. Called from broadcasts, reminders, payment verifications, report generation, caretaker claims, property deletion notifications
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

**Agent owns (runs without human input):** daily balance snapshots; monthly charge generation (if not done by day 3); weekly digest (Monday morning); caretaker morning briefing (routine batched, urgent forwarded 24/7); monthly tenant check-ins; payment rejection notifications; reminder sending; anomaly detection (water >30% above 3-month avg, vacancy duration, arrears thresholds, collection pace).

**Agent flags (human decides):** missing bank statement or water charges; claims aging >7 days; unassigned transactions; no payment/claim by day 15 (nudge caretaker); arrears threshold crossings; water anomalies (caretaker must acknowledge); caretaker escalation requests; low-confidence inbound parses (<0.85); owner instructions from inbound replies.

**Language:** All outbound supports English + Kiswahili. `tenants.language_preference` NULL triggers "English or Kiswahili?" on first inbound contact.

**Tenant flagging:** `tenants.flagged = true` when M-Pesa ref absent from bank statement. Admin clears manually only.

**Admin task feed:** Embedded in `dashboard.html` (first thing seen on login); `base.html` untouched.

**Agent admin routes:** `GET /agent/simulator`, `/agent/digest/preview/<pid>`, `/briefing/caretaker/<pid>/preview`, `/briefing/owner/<pid>/preview`, `/checklist/<pid>/preview`; `POST /agent/trigger/<job_name>`.

---

## WhatsApp / Inbound Channel

One WhatsApp number serves all users across all properties. Identity = phone number. Lookup priority: caretakers → owners → tenants → unknown. Multi-property owners: prompt "Reply 1 for [A], 2 for [B]", cache in `inbound_sessions` for 24h.

**Inbound flow (async — never block on LLM):** Write to `inbound_messages`, return 200 immediately; background thread classifies → handles → responds. WhatsApp adapter in `router.py` is a stub; SMS is the fallback. Outbound proactive messages require Meta-approved templates.

---

## Fintech Architecture (Payment Rail — Phase G)

**Strategic model:** Money-in-transit. Tenants pay via Domi Paybill. Domi holds and disburses net of management fee (default 8%, `properties.management_fee_rate`).

Payment modules (`src/payments/`) never import from `src/agent/` or route files — write to DB and return. `payment_routes.py` routes exempt from admin auth; always write-and-return-200.

**Payout security model (beneficiary substitution fraud prevention):**
- `owners.phone` = contact phone (admin-writable)
- `owners.payout_mpesa` = disbursement destination — **owner-write-only, never admin-writable**
- Owner sets `payout_mpesa` via portal OTP flow: OTP → SMS to submitted number (proves SIM ownership) → confirms → 48-hour hold
- `_get_confirmed_payout_owner(conn, property_id)` enforces `payout_confirmed=1` AND `payout_active_at <= now()`; if no owner passes → `ValueError` + critical platform alert → disbursement blocked
- On payout number change: SMS warning sent to previous number

**FIFO transparency (required):** Confirmation SMS + portal must show allocation detail. "KES 10,000 confirmed — applied: KES 5,000 to Oct service charge, KES 5,000 to Nov rent." Data in `payment_allocations`.

Tables: `payment_transactions` (raw callbacks; `external_reference` UNIQUE dedup; FK to `payments.id`), `disbursements` (pending → processing → completed/failed).

---

## Architectural Rules (cheap now, expensive to retrofit)

1. Agent logic never imports from routes — communicate via DB only
2. LLM calls are always async — never block a web request or webhook on inference
3. Every agent feature is property-scoped — all tables have `property_id`; all jobs parameterized by property
4. Delivery is abstracted — `src/agent/router.py` is the only place that knows about channels
5. LLM provider is abstracted — `src/agent/llm.py` is the only file that imports the Anthropic SDK

Fly CLI at `/Users/lincksmorara/.fly/bin/flyctl` — not in PATH by default. Single gunicorn worker required for SQLite write safety.

---

## Business Context

- **First customer:** Mowin Apartments (44 units, Athi River). Used to build and validate the product.
- **Two fees — never confuse:** `properties.management_fee_rate` (agency's fee, PMO-visible) vs `properties.platform_fee_rate` (Domi's fee, platform-only, **never** appears in org admin routes or templates)
- **Data-descriptive language (hard rule):** Every user-facing string reports what the data knows — never what the agency did. "KES 312,000 verified against bank records" not "We collected KES 312,000". Full rule table in `ROADMAP.md`.
- We are the platform. `/platform/*` is Domi's control room. Bank statements are transitional — once payment rail is live, statement upload workflow becomes legacy-only.

---

## Security Architecture

**Full threat model and agent registry:** `.agent/security.yaml` — read before touching any code that affects owners, property_owners, payments, management_fee_rate, or payout_mpesa.
**Owner talking points and technical notes:** `SECURITY.md`
**Platform UI:** `/platform/trust` — trust dashboard with implementation status badges.

### Core principle
Agency admin and property owner are different parties with potentially conflicting interests. Defenses give owners an independent, platform-delivered channel to observe anything admin does that affects them.

### Separation of control (never violate)
- `owners.phone` — admin-writable; any change SMSes the old number (D1)
- `owners.payout_mpesa` — owner-write-only ONLY; no admin write path; never appears in admin SELECT queries (D7)
- `persons.email` — immutable once `password_hash` is set; admin cannot change email of an activated owner account (D2 partial)
- `property_owners.is_primary` — first owner assigned is auto-marked primary; admin cannot remove primary owner; requires platform intervention to transfer

### Implemented defenses (Sprint 1 — 2026-05-19 / Sprint 1.5 — 2026-05-20)
- **D1**: `POST /owners/<id>/edit` — phone change SMSes old number + platform_log
- **D2 (partial)**: `persons.email` immutable after activation; enforced in `edit_owner()`; full password-lock planned Sprint 2
- **D3**: All property_owners writes (assign/remove/delete) SMS affected owners + raise critical alert; primary owner removal blocked entirely
- **D4**: `_get_confirmed_payout_owner()` in `disbursements.py` — hard blocks if >1 confirmed payout owner
- **D7**: `manage_owners` query never SELECTs `payout_mpesa`; disbursements log last 4 digits only
- **D9**: `delete_payment` + `statement_correct_payment` — SMS active tenant on unit when payment reversed

**Planned (Sprint 2+):** D2-full (password-change lock post-activation), D5 (management_fee guard), D6 (`/view/*/activity` from shadow_log), D8 (rent drift in detector.py).

### Sensitive actions that MUST call platform_log()
owner phone change, owner removed from property, owner added to property, owner deleted, payment reversed, payment corrected, unit rent/service changed >10%, management_fee_rate changed.

---

## Agent Coordination Rules

**Before starting:** Read canonical docs in the order above. Don't rebuild what's complete in `ROADMAP.md`.

**Before touching shared files** (`app.py`, `db.py`, `schema.sql`, `base_viewer.html`): check for recent changes.

**While working:** Data-descriptive language only — no agency voice anywhere. Additive migrations only (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN`; never drop/recreate). Follow existing patterns: `generate_id()`, `get_connection()`, raw SQL, Blueprint structure.

**After completing work:** Update `ROADMAP.md` (mark completed items `[x]`) and `CLAUDE.md` (add new files, routes, tables, blueprints). Test: `./venv/bin/python app.py`.
