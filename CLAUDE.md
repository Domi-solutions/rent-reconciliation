# Domi — Property Intelligence Platform

## AI Re-entry Overview (for agents)

**Domi** is a property intelligence platform for Kenya (and East Africa) that acts as an invisible, intelligent layer across residential properties. It handles rent reconciliation, financial reporting, maintenance, and communication for landlords, property managers, caretakers, and tenants. The product name "Domi" is derived from *domus* (Latin: home) — warm, neutral, and non-intrusive so it can sit on top of any property brand.

**Current codebase name:** `rent-reconciliation` (repo/deploy name unchanged — Domi is the product brand)

**Status:** Core engine, dashboards (The Pulse), monthly reports (The Ledger), tenant/owner/caretaker portals, SMS delivery (Africa's Talking sandbox), and named caretaker accounts are all complete and in production. The AI coordinator layer, conversational inbound parsing, and WhatsApp delivery are planned next.

**Canonical docs — read in this order:**
- `CLAUDE.md` (this file) — technical reference: schema, routes, auth models, patterns, conventions
- `ROADMAP.md` — product vision, phase checklist, what's done vs not
- `CURSOR_PLAN.md` — current work focus, next steps, implementation notes
- `README.md` — how to run and deploy

**Read both `CLAUDE.md` and `CURSOR_PLAN.md` before editing any code.**

**Next steps (in order):**
1. Build inbound webhook foundation (`POST /inbound/sms`, `POST /inbound/whatsapp`) and async processing loop
2. Add `tenants.language_preference` column + language preference prompt on first contact
3. Build admin task feed on dashboard (extend dashboard route + `dashboard.html`, base.html untouched)
4. Wire WhatsApp delivery after SMS flows are stable

---

## Vision: Property Intelligence Platform

Domi is a **financial and operational intelligence layer** on property data. It does not report what an agency did — it surfaces what the data shows, routes it to the right person at the right time, and captures signals that would otherwise be lost in WhatsApp conversations and people's heads.

The product is **pull → push**: it does not wait for users to come to it. Lazy users are the expected baseline. The system delivers value to them regardless.

**Two data layers:**
- **Financial (structured):** charges, payments, bank statements, allocations, balances
- **Qualitative (unstructured → parsed):** tenant feedback, caretaker notes, owner instructions, free-text via WhatsApp/SMS

Both layers together produce intelligence no other property tool captures.

### Design Rule: Data-Descriptive Language

Every user-facing string uses data-descriptive voice, not agency voice:
- "KES 312,000 verified against bank records" — NOT "We collected KES 312,000"
- "Unit A7 — KES 42,000 outstanding, 2 months" — NOT "We are following up on Unit A7"
- "Unit B3 — vacant 14 days, now occupied" — NOT "We filled the vacancy in 14 days"
- "Unit C4 water charge: KES 4,200 — 43% above 3-month average" — NOT "We noticed water usage increased"

The app makes no claims about actions taken. It reports what the data knows. This applies to all dashboards, reports, automated messages, WhatsApp briefings, and agent-generated content.

### Three Audiences (Over Time)

1. **Landlord** (NOW): What's happening with my asset — live financial state, period reports
2. **Agency** (FUTURE): Where are we performing well/poorly across properties
3. **Client-facing** (FUTURE): Same data, tone may shift for external presentation

### Product Layers

1. **The Pulse** (Real-time Dashboard) — "What is the current financial state of my asset?" ✅ COMPLETE

2. **The Ledger** (Monthly Report) — "How did the numbers move this period?" ✅ COMPLETE

3. **The Signal** (Weekly Digest) — "What changed?" Auto-delivered via WhatsApp/SMS
   - Payment velocity (verified income in last 7 days)
   - Arrears state changes (only units that got better/worse vs last snapshot)
   - Claim aging alerts (pending 5+ days)
   - Occupancy change events
   - Brevity IS the signal — stable = silence

4. **The Investment View** (Yearly Report) — "Is this property performing as an asset?"
   - Annual collection rate + month-by-month trend
   - Arrears trajectory over 12 months
   - Tenant reliability scoring (payment behavior profiles)
   - Total vacancy cost / Revenue composition
   - Year-over-year comparison (requires 2+ years of data)

5. **The Coordinator** (AI Agent Layer) — "What needs to happen right now?"
   - Task prompts to humans for physical inputs (bank statement, water readings)
   - Anomaly detection (water spikes, arrears thresholds, vacancy duration, collection pace)
   - Follow-up nudges (units with no activity, aging maintenance issues)
   - Automated execution of routine operations (charge generation, snapshots, reminders on real schedule)
   - Owner engagement loop: briefing + response invited + instruction recorded

6. **The Conversation** (Inbound Free-Text Layer) — "What are users telling us?"
   - WhatsApp/SMS inbound channel for all user roles
   - LLM-based intent classification → structured actions
   - Tenant check-ins: periodic sentiment collection, aggregated and surfaced
   - Caretaker notes logged via natural language
   - Owner instructions parsed and routed
   - Qualitative data layer built alongside financial data

7. **The Voice** (Real Estate Newsletter) — "What is the market telling us?"
   - AI-written weekly real estate newsletter targeting landlords and building owners
   - Proprietary data from Domi platform (anonymized aggregates) + Kenya market data
   - Hedge fund style analysis, not market commentary
   - Distribution: web (SEO/LLM indexing) + LinkedIn + email list
   - Separate codebase; connects via read-only internal API from this system

**Phase status:** Phases 0–2 complete. Phase 3 (Signal + Inbound Foundation) in progress. Phases 4–8 planned. Build sequence: 3 → 4 (Conversation/All Roles) → 5 (Coordinator/Admin Intelligence) → 6 (Owner AI) → 7 (Investment View, deferred) → 8 (Voice). See `ROADMAP.md` for full checklist.

---

## Tech Stack

- Python 3.13, Flask 3.0+, SQLite (PostgreSQL migration path at ~10 properties)
- Templates: Jinja2 + Bootstrap 5
- Parsers: pdfplumber (PDF), pandas + openpyxl (Excel)
- No ORM — raw SQL with parameterized queries
- APScheduler — background job scheduler (embedded in Flask, replaces dashboard-load trigger)
- Anthropic Claude API — LLM inference for inbound message parsing (via `src/agent/llm.py` wrapper)
- Africa's Talking — SMS delivery (live) + WhatsApp Business API (pending approval)

## Project Structure

```
rent-reconciliation/
├── app.py                    # Main Flask app, admin routes, blueprint registration, APScheduler init
├── src/
│   ├── agent/                # AI coordinator layer (Phase 5/6) — NEW
│   │   ├── __init__.py
│   │   ├── coordinator.py    # Orchestrates all scheduled agent jobs
│   │   ├── detector.py       # Anomaly detection + task checker + follow-up nudges
│   │   ├── briefings.py      # Digest + briefing generators (weekly, daily, monthly)
│   │   ├── inbound.py        # Inbound message parsing, intent classification, action handlers
│   │   ├── responder.py      # Response message generation
│   │   ├── router.py         # Delivery abstraction: portal / SMS / WhatsApp / email
│   │   ├── llm.py            # Thin LLM wrapper — never import Anthropic SDK directly elsewhere
│   │   └── state.py          # Conversation session state (inbound_sessions table)
│   ├── parsers/
│   │   ├── router.py         # Input auto-detection & routing
│   │   ├── pdf_parser.py     # Bank statement parsing
│   │   ├── sms_parser.py     # M-Pesa SMS parsing (also exports parse_mpesa_message)
│   │   ├── excel_parser.py   # Tenant Excel import
│   │   └── water_parser.py   # Water readings Excel parser
│   ├── routes/
│   │   ├── test_routes.py    # /test/* - parser & CRUD testing
│   │   ├── viewer_routes.py  # /view/* - owner view-only portal
│   │   ├── tenant_routes.py  # /tenant/<token> - tenant portal (token auth)
│   │   ├── messaging_routes.py  # /messages/* - admin messaging
│   │   ├── report_routes.py  # /reports/* - admin report generation & viewer
│   │   ├── caretaker_routes.py  # /caretaker/* - caretaker live portal
│   │   └── agent_routes.py   # /agent/* - agent preview + simulator + manual triggers (NEW)
│   ├── reports/
│   │   └── landlord_report.py   # Report generation + enrich_report_data()
│   ├── messaging/
│   │   ├── delivery.py       # SMS delivery via Africa's Talking (send_sms, phone normalizer)
│   │   ├── owner_notify.py   # notify_property_owners() — SMS + portal inbox storage
│   │   └── reminders.py      # Automatic due-date reminder generation
│   ├── database/
│   │   ├── db.py             # Connection, generate_id(), all migrations
│   │   └── schema.sql        # Tables
│   └── reconciliation/
│       ├── matcher.py        # Match claims to transactions
│       └── state_machine.py  # Payment lifecycle
├── templates/
│   ├── base.html             # Admin base (sidebar nav, property selector)
│   ├── agent/                # Agent admin pages (simulator, digest preview, briefing preview) NEW
│   ├── viewer/               # Owner portal (base_viewer, dashboard, arrears, payments,
│   │                         #   report_detail, reports, activity, notifications,
│   │                         #   message_detail, maintenance)
│   ├── tenant/               # Tenant portal (base_tenant, portal, charges, payments,
│   │                         #   messages, maintenance, invalid_token)
│   ├── messaging/            # Admin messaging (dashboard, broadcast, templates,
│   │                         #   edit_template, reminders, schedules)
│   ├── reports/              # Admin report pages (history, preview, caretaker_preview)
│   ├── caretaker/            # Caretaker live portal (login, base_caretaker, dashboard,
│   │                         #   arrears, tenants, messages, issues, log_payment)
│   ├── owners.html           # Owners management page
│   └── caretakers.html       # Caretakers management page (named accounts, password, property)
├── scripts/
│   ├── run_dev.sh            # Start local dev server on :5001 (uses data/dev.db, no passwords)
│   ├── download_prod_db.sh   # Pull production DB → backups/ + data/dev.db + data/rent.db
│   └── reset_dev_db.sh       # Reset data/dev.db from latest backup
└── data/
    ├── rent.db               # Default DB (used by flask run directly)
    └── dev.db                # Dev DB (used by run_dev.sh via DATABASE_PATH env var)
```

## Database Tables

### Existing Tables
- `properties` - Rental properties
- `units` - Units with `monthly_rent`, `service_charge`, `apartment_size`, `status` (occupied/vacant/office)
- `tenants` - Linked to units; `access_token` (TEXT, unique) for shareable portal link; NULL = no link
- `bank_statements` - Uploaded PDFs
- `bank_transactions` - Parsed from PDFs
- `payment_claims` - SMS-based claims (pending verification)
- `payments` - Verified payments (reduces balance via allocations)
- `rent_charges` - Monthly charges with `charge_type` ('rent' | 'service' | 'water'), `period` (YYYY-MM or 'ARREARS'), `due_date` (DATE; for reminders; default 5th of next month when generating), UNIQUE(unit_id, period, charge_type)
- `payment_allocations` - FIFO allocation records linking payments to specific charges (payment_id → charge_id, amount). Foreign key ON DELETE CASCADE on payment_id.
- `audit_log` - All actions logged (including exports, tenant_token_generated, tenant_token_revoked, broadcast_sent, reminder_sent)
- `messages` - In-app messages to tenants (reminder | broadcast | notice); per-tenant rows; `batch_id` groups broadcasts; `read_at` for read tracking; `template_body` stores the original unsubstituted template for broadcasts (so owner inbox can show template rather than one tenant's personalized content)
- `message_templates` - Subject/body templates; `property_id` NULL = system default; UNIQUE(property_id, template_key); keys e.g. rent_due_10d, rent_due_5d, rent_due_today, water_cutoff, custom_broadcast
- `reminder_settings` - Per-property: template_key, days_before_due, enabled; UNIQUE(property_id, template_key). Handles legacy hardcoded keys (rent_due_10d, rent_due_5d, etc.); kept intact alongside reminder_schedules.
- `reminder_schedules` - Flexible per-property schedules: id, property_id, label, template_key, days_before_due, send_to ('all' | 'arrears'), enabled. Fires at most once per day when dashboard is loaded; additive to reminder_settings.
- `property_owners` - M:M junction: property_id, owner_id. Determines which properties an owner can see in the viewer; backfilled from properties.owner_id.
- `properties.rent_due_day` - Integer 0–31; 0 = last day of month; used when generating charges and for reminder due-date logic.
- `maintenance_issues` - Logged issues against a property/unit; `source` ('tenant' | 'caretaker'), `raised_by_tenant_id` when tenant-sourced, `category`, `status` ('open' | 'resolved'), `resolved_at`, `resolved_note`
- `owner_messages` - Notifications + broadcast copies stored in owner portal inbox. Columns: id, property_id, owner_id, subject, body, template_body (unsubstituted for broadcasts), message_type ('notification' | 'broadcast' | 'reminder'), channel, recipient_count, sent_by (caretaker's actual name, 'Admin', or 'System'), read_at, created_at. Separate from `messages` table (which requires tenant_id NOT NULL). Populated by `notify_property_owners()`.
- `caretakers` - Named caretaker accounts per property. Columns: id, property_id (FK), name, phone, password_hash, created_at. Migration: `migrate_add_caretakers()`. Name doubles as login username. One caretaker per property (can be expanded). Managed at `/caretakers` (admin).

### New Tables (Agent Layer — Phases 3–6)
- `balance_snapshots` — Daily per-unit balance snapshots. Schema: id, property_id, unit_id, snapshot_date (YYYY-MM-DD), balance, total_charged, total_paid, created_at. UNIQUE(unit_id, snapshot_date). Inserted idempotently by scheduler. **Prerequisite for weekly digest and anomaly detection.**
- `inbound_messages` — Every inbound message from any channel lands here first. Schema: id, property_id, sender_phone, sender_role ('tenant'|'caretaker'|'owner'|'admin'|'unknown'), sender_entity_id, raw_body, channel ('sms'|'whatsapp'), received_at, processed_at, classified_intent, confidence (REAL), action_taken, response_sent. Processing is always async — webhook writes here, worker reads and processes.
- `inbound_sessions` — Conversation state within a 24-hour window. Schema: phone, property_id, last_intent, awaiting_confirmation (TEXT), context_json (TEXT), expires_at. Used so "yes"/"no"/"skip" replies can be resolved against the last prompt.
- `checkin_responses` — Tenant check-in responses. Schema: id, tenant_id, unit_id, property_id, period (YYYY-MM), numeric_response (1|2|3), free_text, classified_category, received_at. Aggregated monthly into sentiment briefings.
- `property_info` — Local amenities per property. Schema: id, property_id, category (pharmacy/grocery/wifi/hospital/gas/etc.), name, details. Admin-managed at onboarding or anytime. Queried when tenant asks Domi about nearby services.
- `caretaker_request_routing` — Per-property config for caretaker escalation routing. Schema: property_id (PK), notify_admin (bool), notify_owner (bool). Owner report always captures all caretaker requests regardless of this config.

### New Columns (additive migrations)
- `tenants.language_preference` — `'en'` | `'sw'` | NULL. NULL triggers language prompt on first inbound contact from that tenant. All Domi responses use the stored preference going forward.
- `tenants.flagged` — boolean, default false. Set when payment claim is rejected (M-Pesa reference absent from bank statement). Flagged tenants' future claims don't get pending-state treatment. Cleared manually by admin only.
- `tenants.flagged_reason` — TEXT, nullable. Set alongside `flagged`.
- `tenants.flagged_at` — TIMESTAMP, nullable. Set alongside `flagged`.
- `maintenance_issues.priority` — `'urgent'` | `'routine'`, default `'routine'`. Urgent issues forwarded to caretaker immediately (24/7); routine issues queue for morning briefing.

## Key Patterns

**ID Generation:**

```python
from src.database.db import generate_id
id = generate_id('PROP')   # → 'PROP-A1B2C3D4'
id = generate_id('TENANT') # → 'TENANT-A1B2C3D4'
```

**Database Access:**

```python
from src.database.db import get_connection
with get_connection() as conn:
    conn.execute("INSERT INTO ...", (val1, val2))
    # Auto-commits on success, rolls back on error
```

**Input Router:**

```python
from src.parsers.router import parse_input
result = parse_input(data)  # Auto-detects SMS/PDF/Excel
```

**Payment Allocation (FIFO):**

```python
from src.database.db import allocate_payment
with get_connection() as conn:
    allocations = allocate_payment(conn, payment_id, unit_id, amount)
    # Returns list of allocation dicts (charge_id, charge_type, period, allocated, charge_settled)
    # Automatically creates payment_allocations records, oldest charges first
```

**Migrations:**

```python
from src.database.db import migrate_add_charge_type, migrate_add_apartment_size, migrate_add_payment_allocations, migrate_allocate_existing_payments
# Called automatically at startup in app.py
# All migrations are idempotent (safe to call multiple times)
```

**LLM Wrapper (never import Anthropic SDK directly outside this module):**

```python
from src.agent.llm import call_llm
result = call_llm(prompt, model='fast')   # 'fast' = haiku, 'smart' = sonnet
# Abstracts provider — swap models/providers without touching agent logic
```

**Delivery Router (agent output — never call delivery directly from agent logic):**

```python
from src.agent.router import route_message
route_message({
    'recipient_phone': '+254712345678',
    'recipient_role': 'caretaker',
    'property_id': 'PROP-45ED445A',
    'message_type': 'daily_briefing',
    'body': 'Morning James...'
})
# Router decides channel: portal / SMS / WhatsApp based on config
# WhatsApp adapter is a stub until credentials are live
```

**Intent Classification Pattern:**

```python
from src.agent.inbound import classify_intent
result = classify_intent(raw_text, sender_role='caretaker')
# Returns: {'intent': 'maintenance_report', 'confidence': 0.94, 'extracted': {...}}
# Rule: confidence >= 0.85 → act + confirm. Below → ask before acting.
# Full intent set in ROADMAP.md Phase 4 checklist.
```

## User Roles

- **Agency Admin**: Full CRUD via main routes (/, /onboard, /units, etc.); Messages dropdown for broadcasts, templates, reminders; manages owners (`/owners`) and caretakers (`/caretakers`)
- **Property Owner**: View-only via `/view/*` (no admin nav; share-link or password-protected); Reports tab shows saved monthly reports; Messages tab shows owner inbox with `sent_by` attribution
- **Caretaker**: Live operational view via `/caretaker/<property_id>` (named account: login with name + password); arrears follow-up, tenant directory, occupancy, log payments, broadcast messages
- **Tenant**: Read-only portal via `/tenant/<token>` (no login; token in URL; generate/revoke from admin Tenants page)

## Owner Viewer (/view/*)

- **URLs:** `/view/` = property list; `/view/<property_id>` = dashboard; `/view/<property_id>/arrears`, `/payments`, `/reports`, `/reports/<report_id>`, `/maintenance`, `/notifications`.
- **Auth:** Shared password (`VIEWER_PASSWORD`); login at `/view/login`.
- **Ownership:** Property list filtered to `property_owners` for `session['owner_id']`. Any `/view/<property_id>/...` route checks `(property_id, owner_id)` in `property_owners`; returns 403 if not found.
- **Templates:** All viewer pages extend `templates/viewer/base_viewer.html`; pass `active_tab` for pill nav.
- **Reports tab:** Uses `enrich_report_data()` to back-fill fields for old saved reports before rendering.
- **Messages tab (notifications):** Marks all as read on page load. **Query must SELECT all columns** (`id, subject, body, template_body, message_type, channel, recipient_count, sent_by, read_at, created_at`) — omitting any silently hides data. Broadcasts show `template_body` (unsubstituted). `sent_by` badge: blue=Admin, amber=Caretaker, gray=System.
- **Link sharing:** One owner can have multiple properties via `property_owners` M:M; copy URL from Owners page.

## Caretaker Portal (/caretaker/*)

- **URLs:** `/caretaker/<property_id>` = dashboard; `/arrears`, `/tenants`, `/issues`, `POST /issues/new`, `POST /issues/<id>/resolve`, `GET/POST /log-payment`.
- **Auth (priority order):** 1) DB accounts in `caretakers` table (name+password, `before_request` verifies `caretaker_id` still exists — deletion revokes immediately); 2) `CARETAKER_PASSWORD` env var (shared password); 3) open in dev. Login at `/caretaker/login`.
- **`sent_by` attribution:** Uses `session.get('caretaker_name', 'Caretaker')` so owner inbox shows actual caretaker name.
- **Data:** Occupancy KPIs, vacant units, top arrears with KES amounts (KES visible to caretakers). Log Payment parses SMS via `sms_parser.parse_mpesa_message`, notifies owners on submission.
- **Blueprint:** `caretaker_bp` in `src/routes/caretaker_routes.py`. Paths starting with `/caretaker` are exempt from admin auth.

## Caretaker Management (Admin — /caretakers)

- **Routes:** `GET /caretakers`; `POST /caretakers/new`; `POST /caretakers/<id>/edit`; `POST /caretakers/<id>/set-password`; `POST /caretakers/<id>/delete` (immediately revokes all active sessions via `before_request` DB check).
- **Replace workflow:** Create new → brief them → delete old. Deletion is instant.

## Monthly Reports Admin (/reports/*)

- **Routes:** `GET /reports`; `GET/POST /reports/generate`; `GET /reports/<id>`; `GET /reports/<id>/caretaker`.
- **Report module:** `src/reports/landlord_report.py` — `generate_report()` saves JSON to `landlord_reports`; always call `enrich_report_data()` before rendering (back-fills fields for old reports).
- **Collection metric:** `vs_expected_income_pct = total_verified / expected_monthly_income * 100` — NOT verified ÷ period charges (misleadingly low mid-month).
- **PDF export:** `window.print()` — no server-side PDF generation.

## Charge Types & Monthly Workflow

Monthly charges have THREE components per tenant:
1. **Rent** - Fixed, stored in `units.monthly_rent`, generated via `/charges/generate`
2. **Service Charge** - Fixed, stored in `units.service_charge`, generated via `/charges/generate` (only if > 0)
3. **Water Charge** - Variable, uploaded via Excel at `/charges/water` each month

**Monthly Workflow:**
1. Admin uploads water readings Excel → creates `rent_charges` with `charge_type='water'`
2. Admin generates rent + service → creates separate `rent_charges` records (`charge_type='rent'` and `charge_type='service'`)
3. Tenants pay via M-Pesa throughout the month
4. Admin uploads bank statement PDF → auto-verifies claims
5. Each verified payment auto-allocates FIFO across outstanding charges (oldest first, regardless of type)
6. Admin exports reports for landlord/audit

**Payment Allocation:**
- FIFO (First In, First Out): Payments allocate to oldest charges first, regardless of charge type
- Allocation happens automatically when payments are verified (`verify_payments()`) or manually assigned (`assign_payment()`)
- Each allocation creates a `payment_allocations` record linking payment → charge with amount
- Overpayments (payment exceeds all charges) show as "Overpayment / Credit" in Export 3
- If a payment is deleted, its allocations are automatically deleted (ON DELETE CASCADE)

## Current Status

Phases 0–2 complete. See `ROADMAP.md` for full checklist. Notable incomplete item: admin route authentication (no login required currently).

## Agent System (Phase 5 — The Coordinator)

The agent layer is **channel-agnostic and additive**. It never modifies existing routes. It writes to existing tables (audit_log, messages, owner_messages) and new tables (balance_snapshots, inbound_messages, inbound_sessions).

### What the Agent Owns (runs without human input)
- Daily balance snapshots (scheduler tick)
- Monthly charge generation (if not done by day 3)
- Weekly digest computation and delivery (Monday morning)
- Caretaker morning briefing: routine issues + nudges batched; urgent issues (`maintenance_issues.priority='urgent'`) forwarded immediately 24/7
- Monthly tenant check-in outbound: 1–3 rating + optional free text; results aggregated into sentiment briefing
- Payment rejection notification: fires when bank statement processed + claim has no matching reference
- Reminder sending (replaces fragile dashboard-load trigger)
- Anomaly detection: water charge >30% above 3-month average (configurable), vacancy duration, arrears threshold crossings, collection pace

### What the Agent Flags (human decides)
- Missing bank statement, water charges, claims aging >7 days, unassigned transactions
- Units with no payment/claim by day 15 → physical follow-up nudge to caretaker
- Arrears threshold crossings: nudge to caretaker + owner report (threshold and nudge frequency configurable per property)
- Water anomaly: caretaker must acknowledge; non-response logged for owner report
- Tenant departure (unit goes vacant): caretaker must comment
- Caretaker escalation requests: routed per `caretaker_request_routing` config; always in owner report
- Caretaker acknowledgment failures (24h window): logged for owner report
- Low-confidence inbound parses: ask before acting
- Owner instructions from inbound replies: logged + routed to caretaker

### Language
All Domi outbound messages support English and Kiswahili. Tenant language preference stored in `tenants.language_preference`. NULL = not yet set; triggers "English or Kiswahili?" prompt on first inbound contact. All responses thereafter use the stored preference.

### Tenant Flagging
Triggered when a payment claim's M-Pesa reference is absent from the bank statement (confirmed falsification, not a timing lag). Flagged state: `tenants.flagged = true`. Flagged tenants' future claims don't receive pending-state treatment (still logged, but not treated as likely-real). Admin clears manually via admin portal. All flag events logged.

### Admin Task Feed
Domi task feed embedded on the main admin dashboard (Option A: extend dashboard route + `dashboard.html`; `base.html` untouched). Pending tasks, anomalies, caretaker requests, flagged items, and unresolved issues surface here. Admin's primary UI — first thing seen on login, returned to after each completed task.

### Agent Admin Routes (/agent/*)
- `GET /agent/simulator` — message simulator: type as any user, see intent classification + action + response
- `GET /agent/digest/preview/<property_id>` — this week's digest as it would be sent
- `GET /agent/briefing/caretaker/<property_id>/preview` — today's caretaker briefing
- `GET /agent/briefing/owner/<property_id>/preview` — owner briefing
- `GET /agent/checklist/<property_id>/preview` — admin task checklist
- `POST /agent/trigger/<job_name>` — manually trigger any scheduled job (dev/testing)

## WhatsApp / Inbound Channel

### Multi-Tenancy: One Number, All Users
One WhatsApp Business number serves all users across all properties. Identity = phone number.

```
Inbound: sender_phone → lookup in tenants/caretakers/owners tables
Result: role + entity_id + property_id → all responses scoped to that context
```

**Lookup priority:** caretakers → owners → tenants → unknown

**Multi-property owners:** if owner has 2+ properties, system prompts "Reply 1 for [Property A], 2 for [Property B]" and caches selection in inbound_sessions for 24 hours.

### Inbound Flow (async — never block on LLM)
```
POST /inbound/sms or /inbound/whatsapp
  → Write to inbound_messages table
  → Return 200 immediately
  → Background worker: classify intent → route to action handler → send response
```

### Outbound: All Proactive Messages Need Pre-Approved Templates
Any message sent to a user who hasn't messaged in the last 24 hours requires a Meta-approved template. Templates are plain text with `{{1}}` variables. All briefings, digests, reminders, charge notifications, and anomaly alerts must be pre-approved before the WhatsApp channel goes live. SMS remains the fallback.

### Channel Configuration
- `AT_USERNAME=sandbox` → SMS sandbox mode; `AT_WHATSAPP_ENABLED=true/false` → toggle WhatsApp
- WhatsApp adapter in `src/agent/router.py` is a stub until credentials are live and templates approved

## Scaling Architecture

- **1–5 properties (now):** SQLite + APScheduler in-process + single gunicorn worker
- **5–15 properties:** SQLite → PostgreSQL (one command), Redis + RQ for jobs, second Fly worker
- **15+ properties:** `src/agent/` extracted to separate Fly app, PgBouncer, dedicated inbound processor

### Architectural Rules (set now, cheap to enforce, expensive to retrofit)
1. **Agent logic never imports from routes.** They communicate via the DB only.
2. **LLM calls are always async.** Never block a web request or webhook handler on inference.
3. **Every agent feature is property-scoped.** All tables have `property_id`. All jobs are parameterized by property. Going from 1 to 20 properties = the job runs 20 times, not a rewrite.
4. **Delivery is abstracted.** `src/agent/router.py` is the only place that knows about channels. Agent code calls `route_message()`, never `send_sms()` directly.
5. **LLM provider is abstracted.** `src/agent/llm.py` is the only place that imports the Anthropic SDK. Swap models or providers by editing one file.

CLAUDE.md is the canonical context for AI agents working on this repo.
See `ROADMAP.md` for product vision, design rules, phase status, and update protocol.

## Multi-Property Support (Admin)

Admin uses **session-based** property selection. The currently selected property is stored in `session['property_id']`.

- **Helper:** `get_current_property(conn)` in `app.py` returns the current property row or `None` (and clears invalid/expired session). All property-scoped admin routes use this instead of `SELECT * FROM properties LIMIT 1`.
- **Routes:** `GET /properties` lists active properties; if only one exists, auto-selects it and redirects to dashboard. `GET /properties/select/<property_id>` sets `session['property_id']` and redirects to dashboard.
- **No property selected:** Visiting any property-scoped route (dashboard, units, tenants, statements, etc.) without a valid session property redirects to `/properties` (or `/setup` if no properties exist).
- **Templates:** `@app.context_processor` injects `current_property` and `property_count` (active properties). Navbar shows current property name and a "Switch Property" link when `property_count > 1`.
- **Single-property UX:** When there is exactly one active property, `/properties` auto-selects it and redirects to dashboard—no selector page is shown.
- **Viewer unchanged:** `/view/*` continues to use URL-based property selection (`/view/<property_id>/...`).
- **Activity/audit:** `activity()` and `export_activity()` are global (audit_log has no property_id); they show all properties’ activity.

## Tenant Portal (/tenant/*)

- **Auth:** Token in URL (`tenants.access_token`); no session. `require_admin_auth` exempts `/tenant` paths.
- **Routes:** `GET /tenant/<token>` = overview; `/charges`, `/payments`, `/messages`, `/maintenance`; `POST /maintenance/new`.
- **Helper:** `_get_tenant_by_token(conn, token)` in `tenant_routes.py` returns (tenant, unit, property) or None.
- **Data-descriptive language** required on all tenant-facing text.

## Messaging (Admin)

- **Scope:** Current property only. `get_current_property(conn)` defined locally in `messaging_routes.py` and `report_routes.py` (not imported from app).
- **Routes:** `GET /messages`; `GET/POST /messages/broadcast`, `/templates`, `/templates/<id>/edit`, `/reminders`, `/schedules`.
- **Broadcast:** One `messages` row per recipient with shared `batch_id`; `template_body` stores unsubstituted template.
- **Reminders:** Two coexisting systems — `reminder_settings` (legacy hardcoded keys) + `reminder_schedules` (flexible per-property). Both run on dashboard load; idempotent per day. Due dates use `properties.rent_due_day` and `rent_charges.due_date`.
- **SMS Delivery:** `send_sms(recipients, message)` in `delivery.py` normalizes Kenyan numbers (07xx → +2547xx). Env: `AT_USERNAME` (default `sandbox`), `AT_API_KEY`, `AT_SENDER_ID`.
- **Owner notifications:** `notify_property_owners(conn, property_id, message, ...)` in `owner_notify.py` — stores in `owner_messages` + SMS to owners with phones. Called from broadcasts, reminders, payment verifications, report generation, caretaker claims.
- **Payment SMS wording:** "payment confirmed" — never expose bank statement mechanics.

## Key Implementation Details

**Payment Allocation Logic:**
- `allocate_payment(conn, payment_id, unit_id, amount)` in `src/database/db.py`
- Queries charges ordered by `created_at ASC` (oldest first)
- Calculates outstanding per charge: `charge.amount - SUM(allocations.amount)`
- Allocates payment amount across charges until exhausted
- Creates `payment_allocations` records for each allocation
- Returns list of allocation dicts (for audit/export purposes)

**Exports:**
- Export 1: Current state by charge type per unit
- Export 2: Audit log with date filter (`?from=YYYY-MM-DD&to=YYYY-MM-DD`)
- Export 3: Payment verification — 3 sheets with full allocation trail; most complex
- All exports use `openpyxl`, log to `audit_log` automatically

**Templates:** Viewer templates extend `viewer/base_viewer.html`; admin templates extend `base.html`.

## Important Notes for AI Agents

**Database Migrations:** All migrations auto-run at startup in `app.py`. All are idempotent — safe to call multiple times.

**Charge Type Handling:**
- Always specify `charge_type` when creating `rent_charges` ('rent' | 'service' | 'water')
- UNIQUE constraint: `(unit_id, period, charge_type)` — same unit can have all three types per period
- Aggregate all types for totals; filter by type for specific queries

**Payment Allocation:** Happens automatically on verify/assign — do NOT call `allocate_payment()` manually. Allocations cascade-delete when payment is deleted.

**Viewer Routes:** Separate blueprint (`/view/*`) — never modify `base.html` for viewer changes. Uses `unit_balances` VIEW which aggregates all charge types.

**Water Parser:** Reuses `parse_currency()` from `excel_parser.py` — do not duplicate. Skips rows with water_charge = 0.

## Local Development

Run against a safe copy of production data — live system is never touched.

```bash
# First time: pull production DB
./scripts/download_prod_db.sh     # saves to backups/ and copies to data/dev.db

# Start app (no passwords, uses data/dev.db)
./scripts/run_dev.sh              # → http://localhost:5000

# Reset dev DB back to latest production snapshot
./scripts/reset_dev_db.sh
```

Key: `run_dev.sh` sets `DATABASE_PATH` to `data/dev.db` and unsets all password env vars so no auth is required locally.

## Deployment

- **Platform:** Fly.io, Johannesburg region (`jnb`)
- **URL:** https://rent-reconciliation.fly.dev/
- **Config:** `fly.toml` with persistent volume `data_vol` mounted at `/data`
- **Database:** SQLite at `/data/rent.db` (production) or `data/dev.db` (local dev)
- **Workers:** Single gunicorn worker (required for SQLite write safety)
- **Secrets:** `ADMIN_PASSWORD`, `VIEWER_PASSWORD`, `CARETAKER_PASSWORD`, `SECRET_KEY`, `APP_BASE_URL` (e.g. `https://rent-reconciliation.fly.dev`) set via `fly secrets set`. `APP_BASE_URL` is used for SMS links (e.g. tenant portal, reminders) when there is no request context. SMS secrets: `AT_USERNAME` (Africa's Talking username; use `sandbox` for sandbox mode), `AT_API_KEY`, `AT_SENDER_ID`.
- **Deploy:** `fly deploy` from project root (uses Dockerfile)
- **Auto-stop:** Machine auto-stops when idle, wakes on request
- **Fly CLI:** `/Users/lincksmorara/.fly/bin/flyctl` (not in PATH by default; `export PATH="$HOME/.fly/bin:$PATH"`)

## Business Context

- Property management agency in Kenya (2-3 person team, no formal brand yet)
- Currently managing 1 property: "Mowin Apartments" (44 units, property ID: PROP-45ED445A)
- Target: grow to 3-5 properties near-term
- Revenue model: not yet locked down (likely 8-10% of verified rent collected)
- Tenants pay via M-Pesa (Kenya's mobile money). Bank statements confirm payments.
- Communication: mix of WhatsApp, calls, SMS (no structured system yet)
- Social media: zero presence currently
- The custom tech is a genuine differentiator vs. other property managers in Kenya
- WhatsApp Business API: application process should be started for future Phase 3 (weekly digest delivery)

## Agent Coordination Rules

> **Multiple AI agents may work on this project simultaneously (Claude Code, Cursor, ChatGPT, etc.).**
> Follow these rules to avoid conflicts and keep docs current.

### Before Starting Work
1. Read `CLAUDE.md` (this file) for technical patterns, conventions, and code structure
2. Read `ROADMAP.md` for product vision, design rules, and what's been built
3. Check which phases/features are already marked complete — don't rebuild what exists

### While Working
4. **Data-descriptive language only** — see `ROADMAP.md` language table. No agency voice anywhere.
5. **Additive migrations only** — use `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ADD COLUMN`. Never drop or recreate existing tables with data.
6. **Follow existing patterns** — `generate_id()`, `get_connection()`, raw SQL, Blueprint structure
7. **Don't modify files another agent might be editing** — if unsure, ask the user

### After Completing Work
8. **Update `ROADMAP.md`** — mark completed items as `[x]`, add any new items
9. **Update `CLAUDE.md`** — if you added new files, routes, tables, or blueprints, document them here
10. **Update project structure** in `CLAUDE.md` if new directories were created
11. **Test before declaring done** — run the app locally (`./venv/bin/python app.py`) and verify your changes work

### Parallel Work Safety
- **Safe to work on simultaneously:** Different templates, different route files, different modules
- **Coordinate before touching:** `app.py` (shared registration), `db.py` (shared migrations), `schema.sql`, `base_viewer.html`
- **Never modify without reading first:** Any file another agent may have recently changed
