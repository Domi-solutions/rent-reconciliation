# Rent Reconciliation System

## Vision: Financial Intelligence Layer

This app is a **financial intelligence layer** on property data. It does not report what an agency did — it surfaces what the data shows. Think of it as a stock portfolio dashboard for rental property: it doesn't manage the stocks, it tells you exactly what's happening with your money at every level of zoom. **The insight is the product.**

### Design Rule: Data-Descriptive Language

Every user-facing string uses data-descriptive voice, not agency voice:
- "KES 312,000 verified against bank records" — NOT "We collected KES 312,000"
- "Unit A7 — KES 42,000 outstanding, 2 months" — NOT "We are following up on Unit A7"
- "Unit B3 — vacant 14 days, now occupied" — NOT "We filled the vacancy in 14 days"
- "Unit C4 water charge: KES 4,200 — 43% above 3-month average" — NOT "We noticed water usage increased"

The app makes no claims about actions taken. It reports what the data knows. This applies to dashboard cards, report templates, and all future automated messages.

### Three Audiences (Over Time)

1. **Landlord** (NOW): What's happening with my asset — live financial state, period reports
2. **Agency** (FUTURE): Where are we performing well/poorly across properties
3. **Client-facing** (FUTURE): Same data, tone may shift for external presentation

### Product Layers (Roadmap)

1. **The Pulse** (Real-time Dashboard) — "What is the current financial state of my asset?"
   - Net collectible vs. verified collected (shilling gap)
   - Three-state payment visibility: verified / claimed-unverified / no activity
   - Vacancy cost per unit (days × daily rent = foregone income)
   - Arrears concentration (which units hold most of the debt)

2. **The Ledger** (Monthly Report) — "How did the numbers move this period?"
   - Collection rate (verified ÷ charged)
   - Payment timing distribution
   - Charges generated (rent/service/water breakdown)
   - Tenant movement (move-ins/departures)
   - Claim resolution rate
   - Vacancy cost calculation

3. **The Signal** (Weekly Digest) — "What changed?" Delivered via email/WhatsApp.
   - Payment velocity (verified income in last 7 days)
   - Arrears state changes (only units that got better/worse)
   - Claim aging alerts (pending 5+ days)
   - Occupancy change events
   - Brevity IS the signal — if nothing changed, brief message confirms steady state

4. **The Investment View** (Yearly Report) — "Is this property performing as an asset?"
   - Annual collection rate + month-by-month trend
   - Arrears trajectory over 12 months
   - Tenant reliability scoring (payment behavior profiles)
   - Total vacancy cost
   - Revenue composition (rent vs service vs water)
   - Year-over-year comparison (requires 2+ years of data)

### Current Implementation Status

- [x] Phase 0: Core reconciliation engine (parsing, matching, allocation)
- [ ] Phase 1: Dashboard upgrade ("The Pulse") — in progress
- [ ] Phase 2: Monthly report generation ("The Ledger") — in progress
- [ ] Phase 3: Weekly digest ("The Signal") — requires email/WhatsApp delivery
- [ ] Phase 4: Yearly view ("The Investment View") — requires 12+ months of data

---

## Purpose

Flask web app for rental management agencies to:

1. Onboard properties with units/tenants (via Excel upload)
2. Parse bank statements (PDF) to extract M-Pesa transactions
3. Match tenant payment claims (SMS) to bank transactions
4. Track monthly charges (rent + service + water) and outstanding balances
5. Allocate payments FIFO across charges (oldest first, regardless of type)
6. Export reports for accountability (current state, activity logs, payment verification)
7. Provide view-only access for property owners via financial intelligence dashboard

## Tech Stack

- Python 3.13, Flask 3.0+, SQLite
- Templates: Jinja2 + Bootstrap 5
- Parsers: pdfplumber (PDF), pandas + openpyxl (Excel)
- No ORM - raw SQL with parameterized queries

## Project Structure

```
rent-reconciliation/
├── app.py                    # Main Flask app, admin routes
├── src/
│   ├── parsers/
│   │   ├── router.py         # Input auto-detection & routing
│   │   ├── pdf_parser.py     # Bank statement parsing
│   │   ├── sms_parser.py     # M-Pesa SMS parsing
│   │   ├── excel_parser.py   # Tenant Excel import
│   │   └── water_parser.py   # Water readings Excel parser
│   ├── routes/
│   │   ├── test_routes.py    # /test/* - parser & CRUD testing
│   │   ├── viewer_routes.py  # /view/* - stakeholder view-only
│   │   ├── tenant_routes.py  # /tenant/<token> - tenant portal (token auth)
│   │   └── messaging_routes.py  # /messages/* - admin messaging (broadcasts, templates, reminders)
│   ├── messaging/
│   │   └── reminders.py      # Automatic due-date reminder generation
│   ├── database/
│   │   ├── db.py             # Connection, generate_id()
│   │   └── schema.sql        # Tables
│   └── reconciliation/
│       ├── matcher.py        # Match claims to transactions
│       └── state_machine.py  # Payment lifecycle
├── templates/
│   ├── base.html
│   ├── viewer/               # View-only stakeholder templates
│   ├── tenant/               # Tenant portal (base_tenant, portal, charges, payments, messages, invalid_token)
│   ├── messaging/            # Admin messaging (dashboard, broadcast, templates, edit_template, reminders)
│   └── ...
└── data/
    ├── rent.db
    └── statements/
```

## Database Tables

- `properties` - Rental properties
- `units` - Units with `monthly_rent`, `service_charge`, `apartment_size`, `status` (occupied/vacant/office)
- `tenants` - Linked to units; `access_token` (TEXT, unique) for shareable portal link; NULL = no link
- `bank_statements` - Uploaded PDFs
- `bank_transactions` - Parsed from PDFs
- `payment_claims` - SMS-based claims (pending verification)
- `payments` - Verified payments (reduces balance via allocations)
- `rent_charges` - Monthly charges with `charge_type` ('rent' | 'service' | 'water'), `period` (YYYY-MM or 'ARREARS'), `due_date` (DATE; for reminders; default 5th of next month when generating), UNIQUE(unit_id, period, charge_type)
- `payment_allocations` - FIFO allocation records linking payments to specific charges (payment_id → charge_id, amount). Foreign key ON DELETE CASCADE on payment_id.
- `audit_log` - All actions logged (including exports, tenant_token_generated, tenant_token_revoked, broadcast_sent)
- `messages` - In-app messages to tenants (reminder | broadcast | notice); per-tenant rows; `batch_id` groups broadcasts; `read_at` for read tracking
- `message_templates` - Subject/body templates; `property_id` NULL = system default; UNIQUE(property_id, template_key); keys e.g. rent_due_10d, rent_due_5d, rent_due_today, water_cutoff, custom_broadcast
- `reminder_settings` - Per-property: template_key, days_before_due, enabled; UNIQUE(property_id, template_key)

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

## User Roles

- **Agency Admin**: Full CRUD via main routes (/, /onboard, /units, etc.); Messages dropdown for broadcasts, templates, reminders
- **Property Owner**: View-only via `/view/*` (no admin nav; share-link or password-protected)
- **Tenant**: Read-only portal via `/tenant/<token>` (no login; token in URL; generate/revoke from admin Tenants page)

## Owner Viewer (/view/*)

- **URLs:** `/view/` = property list; `/view/<property_id>` = dashboard; `/view/<property_id>/arrears`, `/view/<property_id>/payments`.
- **Templates:** All viewer pages extend `templates/viewer/base_viewer.html` (no admin navbar). Child templates set `active_tab` (overview | arrears | payments) for pill nav; the view passes `active_tab` in context. Do not add a second `container` in child templates—the base provides `<main class="container py-4">`.
- **Auth:** Viewer can be protected by a shared password (env `VIEWER_PASSWORD`); login at `/view/login`, logout at `/view/logout`. If no auth is implemented, access is share-link only.

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

## Database (viewer-relevant)

- **unit_balances** (VIEW): `unit_id`, `property_id`, `unit_number`, `monthly_rent`, tenant fields, `total_charged` (sums ALL charge types), `total_paid` (sums ALL payments), `balance`. Viewer uses it for arrears counts and sums. The view aggregates all charge types automatically.
- **units:** `service_charge`, `apartment_size` (TEXT), `status` (`'occupied'` | `'vacant'` | `'office'`). Rentable units = total − office; occupancy rate = occupied / rentable.
- **rent_charges:** `charge_type` ('rent' | 'service' | 'water'), `period` (YYYY-MM or 'ARREARS'), UNIQUE constraint on (unit_id, period, charge_type). Existing charges from before migration have `charge_type='rent'` (default).
- **payment_allocations:** Links payments to specific charges. Used for Export 3 (payment verification audit trail). Query allocations per payment to see how payment was split across charges.

## Current Status

- [x] PDF parser with balance validation
- [x] SMS parser (M-Pesa formats)
- [x] Excel parser for onboarding
- [x] Water readings Excel parser (`water_parser.py`)
- [x] Input router layer
- [x] Test routes (/test/*)
- [x] Viewer routes (/view/*) — property list, dashboard (occupancy, expected income, arrears), arrears tab (months behind, tel links), payments tab
- [x] Property onboarding flow (includes `apartment_size`, `charge_type` for ARREARS)
- [x] Water charges upload (`/charges/water`)
- [x] Charge generation (rent + service separately, `/charges/generate`)
- [x] Payment allocation engine (FIFO, automatic on payment verification/assignment)
- [x] Export 1: Current State Excel (unit breakdown by charge type)
- [x] Export 2: Activity Log Excel (date-filtered audit trail)
- [x] Export 3: Payment Verification Excel (3-sheet legal audit trail with allocations)
- [x] Viewer auth (shared password via VIEWER_PASSWORD; login/logout routes)
- [x] Database migrations (charge_type, apartment_size, payment_allocations) - auto-run at startup
- [ ] User authentication (admin routes)
- [x] Multi-property support (session-based property selection; selector in nav)

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

- **Auth:** URL contains the credential; no session or password. Token is `tenants.access_token` (generated via admin "Generate link", revoked via "Revoke").
- **Bypass:** `require_admin_auth` exempts paths starting with `/tenant`.
- **Routes:** `GET /tenant/<token>` = overview (balance + recent messages); `GET /tenant/<token>/charges` = charges by period with paid/unpaid; `GET /tenant/<token>/payments` = payments with allocation trail; `GET /tenant/<token>/messages` = inbox (marks unread as read on view).
- **Helper:** `_get_tenant_by_token(conn, token)` in `tenant_routes.py` returns (tenant, unit, property) or None; invalid/revoked token renders `tenant/invalid_token.html`.
- **Data-descriptive language:** All tenant-facing text uses data voice.

## Messaging (Admin)

- **Scope:** Current property only; no property switcher inside Messages.
- **Routes:** `GET /messages`, `GET/POST /messages/broadcast`, `GET /messages/templates`, `GET/POST /messages/templates/<id>/edit`, `GET/POST /messages/reminders`.
- **Broadcast:** One `messages` row per recipient with shared `batch_id`; audit log `broadcast_sent`. Reminders run on dashboard load; idempotent per day per template_key; use `rent_charges.due_date` (5th of next month for new charges).

## Test Endpoints

- `POST /test/parse` - Auto-detect and parse any input
- `POST /test/parse/sms` - Test SMS parser
- `POST /test/parse/excel` - Test Excel parser
- `POST /test/parse/pdf` - Test PDF parser
- `POST /test/detect` - Test type detection only
- `POST /test/crud/property` - Create property (commits to DB, dev only)
- `POST /test/crud/unit` - Create unit
- `POST /test/crud/tenant` - Create tenant
- `POST /test/crud/charge` - Create charge (accepts optional `charge_type` in JSON, defaults to 'rent')
- `GET /test/crud/balance/<unit_id>` - Get unit balance

## Admin Routes

**Property selection:**
- `GET /properties` - List properties to select; auto-selects and redirects if only one exists
- `GET /properties/select/<property_id>` - Set active property in session, redirect to dashboard

**Charges:**
- `GET/POST /charges/water` - Upload water readings Excel (creates `charge_type='water'` records)
- `GET/POST /charges/generate` - Generate rent + service charges for a period (creates separate records)

**Exports:**
- `GET /export/current-state` - Download current state Excel (all units with charge breakdown)
- `GET /export/activity?from=YYYY-MM-DD&to=YYYY-MM-DD` - Download activity log Excel
- `GET /export/payments?from=YYYY-MM-DD&to=YYYY-MM-DD` - Download payment verification Excel (3 sheets)
- `GET /export` - Export landing page with date pickers

**Payment Processing:**
- `POST /verify` - Auto-verify payment claims against bank transactions (calls `allocate_payment()` automatically)
- `GET/POST /assign/<txn_id>` - Manually assign unassigned transaction to unit (calls `allocate_payment()` automatically)

## Key Implementation Details

**Charge Type Migration:**
- Migration recreates `rent_charges` table to add `charge_type` column and change UNIQUE constraint
- Backs up database before migration (`rent.db.backup_before_charge_type`)
- Drops and recreates `unit_balances` VIEW after table recreation
- Existing charges get `charge_type='rent'` (default)

**Payment Allocation Logic:**
- `allocate_payment(conn, payment_id, unit_id, amount)` in `src/database/db.py`
- Queries charges ordered by `created_at ASC` (oldest first)
- Calculates outstanding per charge: `charge.amount - SUM(allocations.amount)`
- Allocates payment amount across charges until exhausted
- Creates `payment_allocations` records for each allocation
- Returns list of allocation dicts (for audit/export purposes)

**Export Queries:**
- Export 1: Groups charges by `charge_type` per unit, calculates due = charged - paid per type
- Export 2: Simple audit_log query with date filter
- Export 3: Joins payments → allocations → charges to show full allocation trail, includes overpayment detection

**Templates:**
- All viewer templates extend `viewer/base_viewer.html` (no admin nav)
- Admin templates extend `base.html` (full nav with Charges dropdown, Exports dropdown)
- `generate_charges.html` shows two-step workflow (water first, then rent+service)
- `dashboard.html` hint updated to mention water charges first

## Important Notes for AI Agents

**Database Migrations:**
- All migrations run automatically at startup in `app.py` (after `init_database()`)
- Migrations are idempotent - safe to call multiple times
- `migrate_add_charge_type()` backs up database before table recreation
- `migrate_allocate_existing_payments()` processes any payments without allocations (idempotent)

**Charge Type Handling:**
- Always specify `charge_type` when creating `rent_charges` records ('rent', 'service', or 'water')
- UNIQUE constraint is `(unit_id, period, charge_type)` - same unit can have all three types for same period
- When querying charges, filter by `charge_type` if you need specific type, or aggregate all types for totals

**Payment Allocation:**
- Allocation happens automatically - do NOT call `allocate_payment()` manually unless creating payments outside normal flow
- If modifying payment amounts or deleting payments, allocations are handled automatically (CASCADE delete)
- Overpayments are tracked but not stored separately - Export 3 calculates unallocated amount dynamically

**Viewer Routes:**
- Viewer routes (`/view/*`) are separate blueprint - do not modify `base.html` for viewer changes
- Viewer uses `unit_balances` VIEW which aggregates all charge types automatically
- Viewer totals remain correct even with multiple charge types (view sums everything)

**Export Generation:**
- All exports use `openpyxl` (already in requirements.txt)
- Exports log to `audit_log` automatically
- Export 3 (payment verification) is the most complex - includes 3 sheets with allocation breakdown
- Date ranges use query params `?from=YYYY-MM-DD&to=YYYY-MM-DD` (defaults to all time if not provided)

**Water Parser:**
- Reuses `parse_currency()` from `excel_parser.py` - do not duplicate this function
- Column normalization handles variations: "House No", "Unit No", "Water Charge", "Water", etc.
- Skips rows with water_charge = 0 (warns but doesn't error)

## Deployment

- **Platform:** Fly.io, Johannesburg region (`jnb`)
- **URL:** https://rent-reconciliation.fly.dev/
- **Config:** `fly.toml` with persistent volume `data_vol` mounted at `/data`
- **Database:** SQLite at `/data/rent.db` (production) or `data/rent.db` (local dev)
- **Workers:** Single gunicorn worker (required for SQLite write safety)
- **Secrets:** `ADMIN_PASSWORD`, `VIEWER_PASSWORD`, `SECRET_KEY` set via `fly secrets set`
- **Deploy:** `fly deploy` from project root (uses Dockerfile)
- **Auto-stop:** Machine auto-stops when idle, wakes on request
- **Fly CLI:** `/Users/lincksmorara/.fly/bin/flyctl` (not in PATH by default)

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

## Feature Phases & Status

See `ROADMAP.md` for the full product vision, detailed phase descriptions, and checklist status.

Summary:
- **Phase 0 (Core Engine):** COMPLETE — parsing, matching, allocation, exports, auth, deployment
- **Phase 1 (The Pulse — Dashboard):** COMPLETE — collection gap, three-state payments, vacancy cost, arrears concentration
- **Phase 2 (The Ledger — Monthly Report):** COMPLETE — report generator, admin routes, viewer Reports tab
- **Phase 3 (The Signal — Weekly Digest):** FUTURE — requires email/WhatsApp delivery
- **Phase 4 (The Investment View — Yearly):** FUTURE — requires 12+ months of data

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
