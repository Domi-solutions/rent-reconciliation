# Domi — Implementation Plan

## Architecture Redesign — Multi-Org / Person Identity Layer
**Decided:** 2026-05-12 | **Status:** IN PROGRESS — schema design complete, code not yet written

### Why this is being built now
Four real properties are ready to enter the system. Two are owned by Owner A (managed by Agency 1), two by Owner B (managed by Agency 2). One tenant rents units in two different properties across both agencies. The current schema has no org layer and no person identity — retrofitting this after data entry would touch every query. Build it first.

### Exact scenario to implement
```
Organization 1 (Agency A): manages Property 1 + Property 2 (both owned by Owner A)
Organization 2 (Agency B): manages Property 3 + Property 4 (both owned by Owner B)
Owner A: holistic dashboard (P1+P2 aggregate) + individual property drilldown
Owner B: holistic dashboard (P3+P4 aggregate) + individual property drilldown
Tenant X: unit in P1 AND unit in P3 — one login, sees both obligations
Platform admin (Domi operator): sees all orgs, all errors, login-as-org
```

### New tables — exact DDL

```sql
-- organizations: one row per agency using Domi
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE,                        -- URL-safe identifier
    admin_password_hash TEXT,                -- bcrypt hash; NULL = dev mode open
    contact_email TEXT,
    contact_phone TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- persons: human identity — shared across roles and properties
CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,                              -- normalized +2547xx
    email TEXT,
    password_hash TEXT,                      -- bcrypt; for owner + tenant credential login
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- platform_errors: surfaced to platform admin without users calling
CREATE TABLE IF NOT EXISTS platform_errors (
    id TEXT PRIMARY KEY,
    error_type TEXT,                         -- '500' | 'warning' | 'payment_failed' etc.
    route TEXT,
    method TEXT,
    org_id TEXT,
    property_id TEXT,
    user_role TEXT,                          -- 'admin' | 'owner' | 'caretaker' | 'tenant' | 'platform'
    message TEXT,
    traceback TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Column additions to existing tables

```sql
-- properties: which org manages this property
ALTER TABLE properties ADD COLUMN organization_id TEXT REFERENCES organizations(id);

-- owners: link to person identity (enables holistic multi-property owner view)
ALTER TABLE owners ADD COLUMN person_id TEXT REFERENCES persons(id);

-- tenants: link to person identity (enables multi-unit holistic tenant view)
ALTER TABLE tenants ADD COLUMN person_id TEXT REFERENCES persons(id);
```

All additions are **nullable** — existing rows are unaffected.

### New auth routes (locked)

| Role | Login route | Session key | Portal |
|---|---|---|---|
| Platform (Domi operator) | `POST /platform/login` | `session['platform_admin']` | `/platform/` |
| Org admin | `POST /login` (existing) — now picks org | `session['org_id']` + `session['admin_authenticated']` | `/` scoped to org |
| Owner | `POST /owner/login` | `session['person_id']` + role='owner' | `/owner/dashboard` + `/owner/<property_id>/` |
| Caretaker | Existing `/caretaker/<property_id>/login` | Unchanged | `/caretaker/<property_id>/` |
| Tenant holistic | `POST /tenant/login` | `session['person_id']` + role='tenant' | `/tenant/dashboard` |
| Tenant single-unit | Token in URL (existing) | None | `/tenant/<token>` |

### Build sequence — 6 phases

#### Phase 1 — Schema Foundations (start here)
- [ ] `migrate_add_organizations()` in `src/database/db.py`
- [ ] `migrate_add_persons()` in `src/database/db.py`
- [ ] `migrate_add_platform_errors()` in `src/database/db.py`
- [ ] Register all three in `app.py` startup after existing migrations
- [ ] Verify: `./venv/bin/python -c "from app import app; print('OK')"`
- [ ] Commit: `git commit -m "Phase 1 complete: organizations, persons, platform_errors schema"`

#### Phase 2 — Platform Admin (`/platform/*`) ✅ COMPLETE
New blueprint: `src/routes/platform_routes.py`, prefix `/platform`
- [x] `GET /platform/login` + `POST /platform/login` — auth via `PLATFORM_ADMIN_PASSWORD` env var
- [x] `GET /platform/logout`
- [x] `before_request` in platform blueprint: check `session['platform_admin']`
- [x] `GET /platform/` — dashboard: org list (name, property count, last activity), system health
- [x] `GET /platform/errors` — `platform_errors` table, most recent first, filterable by type
- [x] `POST /platform/impersonate/<org_id>` — sets `session['org_id']` + `session['admin_authenticated']`, redirects to `/`
- [x] Wire `@app.errorhandler(500)` in `app.py` → write to `platform_errors` table
- [x] Template dir: `templates/platform/` — `base_platform.html`, `login.html`, `dashboard.html`, `errors.html`
- [x] Exempt `/platform/*` from org admin `before_request` check

#### Phase 3 — Org Admin Scoping ✅ COMPLETE
- [x] `GET/POST /org-select` — org picker shown post-login when multiple orgs exist; auto-selects when 0 or 1 org
- [x] `before_request` redirects authenticated users without `org_selection_done` to `/org-select`
- [x] `get_current_property(conn)` in app.py, messaging_routes.py, report_routes.py — filters by `org_id` when set
- [x] Properties page (`/properties`) filters by `session['org_id']`
- [x] `select_property` rejects cross-org property switches
- [x] Creating a property (`/setup`) assigns `organization_id = session.get('org_id')`
- [x] `inject_property_context` scopes property count to current org
- [x] Dev mode (no ADMIN_PASSWORD): org selector skipped entirely (unchanged behaviour)
- [x] Platform impersonate sets `org_selection_done` so operator lands cleanly in the org

#### Phase 4 — Owner Holistic View ✅ COMPLETE
New blueprint: `src/routes/owner_routes.py`, prefix `/owner`
- [x] `GET/POST /owner/login` — phone + password lookup in `persons` table; session['person_id'] + person_role='owner'
- [x] `GET /owner/logout`
- [x] `GET /owner/dashboard` — portfolio aggregate + per-property cards (collection %, arrears, top arrears unit)
- [x] `GET /owner/<property_id>/` — verifies person owns property via persons→owners→property_owners; sets session['owner_id'] and bridges to existing viewer
- [x] `/owner/*` exempted from admin before_request
- [x] `create_owner` route updated: creates persons row when phone provided; domi_password field sets persons.password_hash
- [x] `manage_owners` query includes person_id; owners list shows "Domi Login" badge when person_id linked
- [x] owners.html: Domi Login password field added to create modal

#### Phase 5 — Tenant Holistic View ✅ COMPLETE
- [x] `GET/POST /tenant/login` — phone + password via persons; 0 tenants→error, 1 tenant→token redirect, 2+→holistic dashboard
- [x] `GET /tenant/logout`
- [x] `GET /tenant/dashboard` — all active units for person: property, unit, balance, pending, total paid, last payment, link to full portal
- [x] `POST /tenants/<tenant_id>/link-person` — admin links tenant by phone; creates persons row if not found
- [x] `add_tenant`: auto-links persons row when phone matches on creation
- [x] tenants.html: "Domi Login" column — shows "Linked" badge if person_id set, inline phone-link form if not
- [x] Existing `/tenant/<token>` routes fully unchanged

#### Session 7 — Payments Feed UX, Demo Data, Org-Scoped Statements, Parse Error Logging (2026-05-12) ✅ COMPLETE

**Payments feed UX:**
- [x] "Collected This Month" KPI card on dashboard is now clickable — links to `/review?tab=confirmed`
- [x] Confirmed tab redesigned as M-Pesa-style scrolling feed (card per payment, amount bold, tenant + unit below, ref in monospace, date + source badge on right)
- [x] "Manual" badge renamed to "Bank" (source = manual means bank statement assignment)

**Demo data:**
- [x] `scripts/seed_demo_payments.py` — seeds 132 rent charges (22 units × 3 months: Mar/Apr/May 2025), 12 bank statements, 56 bank transactions, 56 payments, 111 payment allocations across Agency Alfa and Agency Beta properties
- [x] Realistic scenarios: clean payers, late payers (day 8–11), missed months, partial payments (Agnes Auma 15K of 20.5K; Esther Wambua 18K of 23K), catchup overpayment (Grace Njeri 34K for 2 months), underpayment (Eric Onyango 20K of 28.5K), 1 pending claim

**Org-scoped statements (Option 2):**
- [x] `migrate_add_org_scoped_statements()` in `db.py` — adds `org_id TEXT` and `bank_format TEXT` to `bank_statements`; index `idx_stmts_org`
- [x] `upload_statement()` — uses `org_id` from session; stores `bank_format`; keeps file on `parse_failed` (was deleting)
- [x] `manage_statements()` — queries `WHERE bs.org_id = ?`; passes `parse_error_counts` and `bank_label` to template
- [x] `reparse_statement()` — org-aware lookup; clears and re-logs parse errors; updates `bank_format`
- [x] `verify_payments()` — resolves `verify_org_id` from statement's org; queries pending claims across all org properties
- [x] `review()` — all tabs (unreported/unconfirmed/reversals/parse_errors) org-scoped; `group_units` includes `property_name` for cross-property assignment
- [x] Dashboard unassigned count queries — org-scoped when `org_id` is in session

**Parse error logging (real, not stub):**
- [x] `migrate_add_statement_parse_errors()` — creates `statement_parse_errors` table with org_id, statement_id, filename, file_path, bank_format, error_type, error_message, raw_text, page_number, txn_index, created_at
- [x] `upload_statement()` and `reparse_statement()` — write every `PARSE_ERROR`-type transaction and fatal errors to `statement_parse_errors`
- [x] Parse Errors tab in `review.html` — replaced stub with real table: filename, bank badge (color-coded), error_type badge, error_message, collapsible raw text, page number, date
- [x] Platform dashboard — 4th stat card: "Parse errors (7d)" in amber; links to parse errors page
- [x] `GET /platform/parse-errors` — full parse errors across all orgs; org filter buttons with count badges; View PDF button per row
- [x] `GET /platform/statements/<id>/pdf` — serves stored PDF via `send_file()` (platform admin only)
- [x] `templates/platform/parse_errors.html` — new file
- [x] Platform nav — Parse Errors link added

**Multi-bank parser registry:**
- [x] `src/parsers/banks/__init__.py` — empty package marker
- [x] `src/parsers/banks/registry.py` — `BANK_DISPLAY_NAMES`, `bank_display_name()`, `SUPPORTED_FORMATS`
- [x] `detect_bank_statement_format()` — returns `'unknown'` (not `'cooperative'`) for unrecognised PDFs
- [x] `parse_bank_statement()` — explicit `format_unknown` branch; fails fast instead of garbled parse

**Bug fixes:**
- [x] Fixed `AttributeError: 'sqlite3.Row' object has no attribute 'get'` — 6 locations in `app.py`; all `.get()` calls replaced with bracket access + conditional
- [x] `statements.html` — updated to show bank format badge, parse error count badge (linked), `parse_failed` status badge

#### Phase 6 — Data Entry + Test Run
**Seeded (scripts/seed_phase6.py — run against dev.db, verified):**
- [x] 2 organisations: Agency Alfa (agency-alfa), Agency Beta (agency-beta)
- [x] Owner A: Amara Waweru (+254701000001 / ownerA123) — persons + owner linked
- [x] Owner B: Benjamin Omondi (+254701000002 / ownerB123) — persons + owner linked
- [x] Property 1+2 (Agency Alfa, Owner A): Riverside Courts, Garden View Apartments
- [x] Property 3+4 (Agency Beta, Owner B): Parklands Estate, Westlands Flats
- [x] 6 units + 5 tenants per property (1 unit vacant in P2+P4)
- [x] Tenant X: Xenia Kamau (+254701000010 / tenantX123) — unit A6 (Riverside) + unit C6 (Parklands)

**Platform additions (also in this commit):**
- [x] POST /platform/orgs/new — org creation modal on platform dashboard
- [x] POST /platform/orgs/<org_id>/toggle — activate/deactivate org
- [x] Fixed tenant dashboard bug: u.id now selected directly (removed redundant subquery)

**Requires manual run to verify (live app):**
- [ ] Walk bank statement workflow end-to-end (at least one property)
- [ ] Test SMS sandbox (broadcasts, reminders, payment confirmation)
- [ ] Test Daraja sandbox STK Push
- [ ] Test disbursement calculation
- [ ] Verify platform admin sees all orgs + errors at /platform/
- [ ] Verify Owner A holistic view at /owner/dashboard shows P1+P2 aggregate
- [ ] Verify Tenant X holistic view at /tenant/dashboard shows both units

**How to run the full test:**
```bash
# Seed dev DB (already done — re-run resets data)
DATABASE_PATH=data/dev.db ./venv/bin/python scripts/seed_phase6.py

# Start dev server
./scripts/run_dev.sh

# Env vars needed for extended testing:
PLATFORM_ADMIN_PASSWORD=domiplatform ADMIN_PASSWORD=domiadmin ./scripts/run_dev.sh
```

---

## Project Re-entry Overview (for AI agents)

**Last updated:** 2026-05-12
**Product:** Domi — property fintech platform, Kenya. Repo name: `rent-reconciliation` (unchanged).
**Stack:** Python 3.13, Flask 3.0+, SQLite, APScheduler, Jinja2 + Bootstrap 5. Fly.io (Johannesburg).

**Strategic direction (locked 2026-05-04):** Domi is a property fintech, not a property tool. Tenants pay rent via M-Pesa STK Push or card through Domi. Domi holds funds and disburses to landlords net of management fee. Fee embedded in disbursement spread — not a visible line item. This makes Domi infrastructure, not software, which means low churn and high switching cost.

**Phases A–E are COMPLETE and deployed. Do not re-implement anything in those phases.**

**Source of truth — read ALL before touching code:**
- `.agent/schema.yaml` — canonical DB schema (ground truth for tables and columns)
- `.agent/routes.yaml` — canonical route registry (all blueprints and URL prefixes)
- `.agent/jobs.yaml` — canonical scheduler job registry
- `.agent/intents.yaml` — intent classification set
- `.agent/env.yaml` — all environment variables
- `CLAUDE.md` — technical patterns, conventions, business rules
- `ROADMAP.md` — product vision, phase checklist, language rules
- `CURSOR_PATTERNS.md` — **read before writing any code** — logged failure patterns, do not repeat
- `README.md` — how to run and deploy

---

## What Is Fully Built (do not re-implement)

**Core Engine (Phase 0):**
PDF bank statement parser, M-Pesa SMS parser, Excel import, water readings parser, FIFO payment allocation, multi-property support, admin session auth (partial — password checked, no login route yet), exports, deployed to Fly.io.

**Phase 1 — The Pulse:**
Collection gap, three-state payment visibility (verified/claimed/no activity), vacancy cost per unit, arrears concentration, maintenance tab.

**Phase 2 — The Ledger:**
Report generator (`src/reports/landlord_report.py`), admin generate/preview routes, owner Reports tab, caretaker report view, PDF export via print.

**Portals:**
Tenant (`/tenant/<token>`), owner (`/view/*`), caretaker (`/caretaker/*`), caretaker management (`/caretakers`), owner management (`/owners`).

**Messaging:**
Admin broadcasts, caretaker broadcasts, automatic reminders (two coexisting systems), SMS via Africa's Talking (sandbox), owner inbox (`owner_messages`), `sent_by` attribution.

**Agent Infrastructure (Phases A–E — all complete):**
- `balance_snapshots` table + daily snapshot job (A1)
- APScheduler: 5 jobs registered — daily_snapshot (1am), morning_briefings (7am), weekly_digest (Mon 8am), anomaly_check (6am), monthly_checkins (1st 9am) (A2)
- `src/agent/` module: coordinator, detector, briefings, inbound, responder, router, llm, state (A3–A4)
- Weekly digest generator + admin preview routes at `/agent/*/preview` (C1–C5)
- Message simulator at `/agent/simulator` (D5)
- `inbound_messages` + `inbound_sessions` tables (D1)
- LLM wrapper at `src/agent/llm.py` — Anthropic SDK isolated here only (D2)
- Intent classifier stub at `src/agent/inbound.py` (D3)
- `checkin_responses` table + monthly check-in sender + sentiment aggregator (E1–E3)

---

## Prerequisite Sequence (nothing in Phase G starts until all three are clear)

### Prereq 1 — Admin Authentication (build first, ~2 days)

**Why:** Admin routes currently have no login gate. Full CRUD behind an unguarded URL is not acceptable for a system that will handle third-party funds. Fix this before any payment code is written.

**What to build:**
- `GET /login` — render login form
- `POST /login` — check against `ADMIN_PASSWORD` env var, set `session['admin_authenticated'] = True`, redirect to dashboard
- `GET /logout` — clear session, redirect to `/login`
- `before_request` hook in `app.py` — for all routes not in the exempt list, check `session.get('admin_authenticated')`
- Exempt paths: `/login`, `/logout`, `/tenant/*`, `/view/*`, `/caretaker/*`, `/inbound/*`, `/static/*`
- Dev mode: if `ADMIN_PASSWORD` not set (as in `run_dev.sh`), skip auth check entirely — no change to local dev flow

**Pattern to follow:** `src/routes/viewer_routes.py` — identical pattern, same session approach.

**Test:** Set `ADMIN_PASSWORD=test` locally, verify `/` redirects to `/login`. Verify `/tenant/<token>` still works without login. Verify `run_dev.sh` (no ADMIN_PASSWORD) remains open.

### Prereq 2 — Legal Structure Review (external, run in parallel with Prereq 1)

**Question to answer:** Is Domi operating as a merchant (collecting on behalf of client — trust account model) or a payment service provider (CBK-regulated activity)?
- Merchant structure = lower barrier, operates under Africa's Talking Payments license
- PSP license = 6-18 months, capital requirements — not the path for now

**Action:** Engage Kenyan fintech legal counsel or Africa's Talking compliance team. Africa's Talking Payments API may allow Domi to operate under their existing license as a merchant aggregator.

### Prereq 3 — Safaricom Paybill + Daraja Application (run in parallel)

**What to apply for:**
- Paybill number via Safaricom Business or Africa's Talking Payments API
- Daraja credentials: `Consumer Key`, `Consumer Secret`, `Passkey` (STK Push)
- Timeline: 2-4 weeks for approval

**Documents needed:** Business registration cert, KRA PIN, company bank account details, directors' IDs.

**One Paybill, all properties.** Tenant uses unit number as M-Pesa account reference (e.g. pay Paybill 123456, account A7). Routing logic maps account reference to property + unit in DB.

---

## Phase G — The Payment Rail (Fintech Foundation)

**Begins after:** Prereq 1 complete + Daraja approved + legal structure clear.

### G1. New module: `src/payments/`

Create `src/payments/__init__.py`, `src/payments/daraja.py`, `src/payments/pesapal.py`, `src/payments/disbursements.py`.

**Architectural rule:** Payment modules never import from `src/agent/` or route files. They write to DB tables. Agent and route code reads those tables. Same DB-as-interface pattern used throughout.

### G2. Daraja STK Push (`src/payments/daraja.py`)

```python
def stk_push(phone: str, amount: int, account_ref: str, description: str) -> dict:
    """
    Initiates M-Pesa STK Push to tenant phone.
    account_ref = unit number (e.g. 'A7') — maps to unit in DB.
    Returns {'success': bool, 'checkout_request_id': str, 'error': str|None}
    """
```

Env vars needed: `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_PASSKEY`, `DARAJA_SHORTCODE`, `DARAJA_CALLBACK_URL`.

Use Daraja sandbox for all dev/test work (`DARAJA_ENV=sandbox`).

### G3. Payment callback handler (`src/routes/payment_routes.py` — new blueprint)

**Rule R15:** Write-and-return-200. Never process inline.

```
POST /inbound/payment/mpesa  (Daraja callback)
  → parse callback JSON
  → write to payment_transactions table (new — see G4)
  → return '', 200

POST /inbound/payment/pesapal  (Pesapal IPN)
  → same pattern
```

Background worker (add to `coordinator.py`): reads `payment_transactions WHERE processing_status = 'pending'`, runs every 60 seconds.

```
For each pending transaction:
  → look up unit by account_ref (unit_number)
  → create verified payment record in payments table
  → run FIFO allocator (allocate_payment())
  → send SMS confirmation to tenant ("Payment confirmed.")
  → set processing_status = 'completed'
  → on error: set processing_status = 'failed', write error_detail
```

**Deduplication:** Check `external_reference` before inserting. Skip if already in `payment_transactions`. Daraja and Pesapal both retry on timeout.

### G4. New DB tables

**`payment_transactions`** — raw payment callbacks before processing:

```sql
CREATE TABLE IF NOT EXISTS payment_transactions (
    id TEXT PRIMARY KEY,
    property_id TEXT,
    unit_id TEXT,
    tenant_id TEXT,
    source TEXT NOT NULL,            -- 'daraja' | 'pesapal' | 'manual'
    external_reference TEXT UNIQUE,  -- checkout_request_id or Pesapal ref
    phone TEXT,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'KES',
    raw_callback TEXT,               -- full JSON blob from provider
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processing_status TEXT DEFAULT 'pending',  -- 'pending' | 'completed' | 'failed'
    processed_at TIMESTAMP,
    error_detail TEXT,
    payment_id TEXT REFERENCES payments(id)
);
```

**`disbursements`** — landlord payouts:

```sql
CREATE TABLE IF NOT EXISTS disbursements (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL REFERENCES properties(id),
    period TEXT NOT NULL,
    total_collected REAL NOT NULL,
    fee_rate REAL NOT NULL,
    fee_amount REAL NOT NULL,
    net_amount REAL NOT NULL,
    status TEXT DEFAULT 'pending',   -- 'pending' | 'processing' | 'completed' | 'failed'
    method TEXT,                     -- 'mpesa_b2c' | 'bank_transfer'
    recipient_account TEXT,
    daraja_transaction_id TEXT,
    disbursed_at TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Add migrations: `migrate_add_payment_transactions()` and `migrate_add_disbursements()` in `src/database/db.py`. Register in `app.py` startup sequence.

Update `.agent/schema.yaml` after adding.

### G5. Tenant portal: payment UI

Existing tenant portal (`/tenant/<token>`) becomes transaction-capable. Add payment tab.

**Auth upgrade (required before payment tab goes live):**
- Current: token in URL — sufficient for read-only
- Required: token + 4-digit PIN for payment actions only
- New column: `tenants.portal_pin_hash` (TEXT, nullable) — migration required
- PIN is only prompted when accessing the payment tab, not for read-only views
- First visit to payment tab: "Create a 4-digit PIN for payment security" → hash with `bcrypt`, store
- Subsequent visits: "Enter your PIN" → verify before showing payment form

**STK Push flow:**
```
Tenant enters phone (pre-filled from record) + amount
  → POST /tenant/<token>/pay/stk
  → calls stk_push() → returns checkout_request_id
  → frontend polls GET /tenant/<token>/pay/status/<checkout_request_id> every 3s
  → tenant confirms on their phone
  → Daraja fires callback → payment processed in background
  → status poll returns 'completed' → show confirmation
```

**Card flow (Pesapal):**
```
Tenant clicks "Pay by card"
  → redirect to Pesapal hosted checkout with unit reference and amount
  → Pesapal fires IPN to POST /inbound/payment/pesapal
  → tenant redirected back to portal
```

### G6. Disbursement engine (`src/payments/disbursements.py`)

`calculate_disbursement(conn, property_id, period) -> dict`
- Total collected via `payment_transactions` WHERE source IN ('daraja','pesapal') for the period
- Fee deduction: configurable per property (new column: `properties.management_fee_rate`, default 0.08)
- Returns: `{total_collected, fee_rate, fee_amount, net_amount, unit_breakdown}`

`execute_disbursement(conn, property_id, period)` — creates `disbursements` record, triggers B2C payout (Daraja) or logs for manual bank transfer.

Disbursement schedule: 10th of each month. Add job to APScheduler and `jobs.yaml`.

### G7. Landlord disbursement statement

Extend owner report tab (`/view/<property_id>/reports`) to show disbursement history alongside monthly reports. Show: total collected via Domi, fee deducted (rate + KES amount), net disbursed, disbursement date.

### G8. Payment source visibility (admin/caretaker)

Extend arrears and payment history views to show source badge per payment:
- "Paybill" — Daraja STK Push
- "Card" — Pesapal
- "Manual" — existing bank statement reconciliation (legacy, still fully supported)

---

## Legacy Reconciliation Flow — Keep Permanently

The bank statement PDF + SMS claim workflow is NOT removed. It remains for:
- Tenants who pay directly to the owner's bank account
- Transition period while tenants migrate to Paybill
- Any landlord who prefers the manual flow

Both flows (Daraja/Pesapal AND manual) write to the same `payments` table and run through the same FIFO allocator. The `payment_transactions.source` field distinguishes origin. All flows are audited identically in `audit_log`.

---

## Phase H — WhatsApp Channel (after Phase G is stable)

Same as Phase F from the previous plan. All agent logic, detection, briefings, parsing, and action handlers are already built and tested via simulator and SMS. Phase H wires the live WhatsApp channel.

**Prerequisite:** WhatsApp Business API approval from Meta via Africa's Talking. Apply now — 2-6 week lead time. This runs in parallel with all other work.

**H1.** `POST /inbound/sms` — Africa's Talking inbound SMS webhook (write to `inbound_messages`, return 200)
**H2.** `POST /inbound/whatsapp` — same pattern, different AT payload format
**H3.** WhatsApp adapter in `src/agent/router.py` — implement `_send_whatsapp()` stub → real
**H4.** Pre-approve all outbound templates via Africa's Talking dashboard (list in `ROADMAP.md`)

---

## After Completing Any Work

1. `./venv/bin/python -c "from app import app; print('OK')"` — verify no import errors
2. `./scripts/run_dev.sh` → http://localhost:5001 — test locally
3. Update `.agent/schema.yaml` if new tables or columns added
4. Update `.agent/routes.yaml` if new routes or blueprints added
5. Update `.agent/jobs.yaml` if new scheduled jobs added
6. Update `ROADMAP.md` — mark completed items `[x]`
7. Update `CLAUDE.md` — technical additions only (never restructure)
8. Overwrite "Last Session" in `AGENTS.md` with what was built and what's next
9. `git add . && git commit -m "describe work done"`
10. `export PATH="$HOME/.fly/bin:$PATH" && fly deploy`
