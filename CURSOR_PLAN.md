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

#### Session 8 — Admin UX, Platform Guardian, Owner Wallet (2026-05-13) ✅ COMPLETE

**Dashboard UX — clickable KPIs and table rows:**
- "Total Arrears" → dedicated `/arrears` page (unit balances, pending claims, months-behind with `math.ceil`)
- "Expected Income" → `/units`; "Pending Claims" stat + table rows → `/review?tab=unconfirmed`; "Units in Arrears" rows → `/arrears`; "Unassigned Bank Payments" rows → `/review?tab=unreported`
- All table row clicks use `onclick="window.location=..."` with `stopPropagation()` on inner action links

**Activity sidebar:**
- Moved Activity out of Tools section into its own sidebar nav item (clock SVG)
- Filter form: activity type (DISTINCT from audit_log), from date, to date; "Clear filters" button when active
- Fixed Jinja2 `{% endif %}` missing bug and `**` dict unpacking error in `url_for()` calls (use string concatenation instead)

**Combined units + tenants page (complete rewrite of `templates/units.html`):**
- Single table: Unit, Status, Rent, Service, Tenant Name, Phone, Balance, Actions
- Inline editing via `<span class="editable" data-entity="..." data-field="..." data-value="...">` — click → input → Enter/blur → fetch POST → JSON response
- Two JSON endpoints: `POST /units/<unit_id>/field` and `POST /tenants/<tenant_id>/field`
- Status uses `<select class="status-select">` auto-saves on change
- All edits log to `audit_log` + `platform_shadow_log`; rent/service changes >10% raise alert + notify owner
- Fixed Bootstrap select arrow overlap: `padding: 3px 2rem 3px 8px` (preserves Bootstrap's right-padding for arrow icon)
- `/tenants` redirects to `/units`; Tenants removed from sidebar; `active_nav` consolidated

**Platform guardian system (new):**
- `src/platform/__init__.py` (empty) + `src/platform/guardian.py` — `platform_log()`, `raise_alert()`, `notify_owner_change()`
- 3 new DB tables + migrations: `platform_shadow_log`, `tenant_disputes`, `platform_alerts`
- `POST /tenant/<token>/dispute` — tenant raises dispute directly to platform (bypasses agency)
- Tenant portal: "Something looks wrong?" card with subject dropdown + message textarea
- Platform routes: shadow log, disputes (+resolve), alerts (+dismiss), trust score
- Platform nav: 4 new items; Platform dashboard: 2 new KPI cards (Alerts, Disputes); layout 6-card grid
- Owner removal triggers `platform_log()` + `raise_alert()` + direct SMS to owner

**Owner wallet stub:**
- `GET /view/<property_id>/wallet` + `templates/viewer/wallet.html`
- Balance = total payments − fee − disbursed; 3 KPI cards + dashed stub withdraw card + disbursement history table
- Wallet tab added to `base_viewer.html` nav

**Doc updates:** `.agent/schema.yaml` (3 new tables), `.agent/routes.yaml` (3 new sections), `CLAUDE.md` (platform structure, patterns, DB rules), `ROADMAP.md` (new completed sections)

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

#### Session 13 — Scanned PDF Vision Parsing, Multi-Property Statements, Platform Parser Tools (2026-05-19) ✅ COMPLETE

**Scanned PDF support (LLM vision fallback):**
- [x] `src/agent/llm.py` — added `call_llm_vision(pages_b64, prompt, model, max_tokens)` using Anthropic vision API (images + text content block)
- [x] `src/parsers/pdf_parser.py` — `_parse_scanned_statement()`: renders PDF pages to base64 PNG via PyMuPDF (fitz), calls Haiku vision in 2-page chunks, merges JSON results; balance validation bypassed for scanned results (vision can't read running totals); adds advisory warning to result recommending digital statements
- [x] `parse_bank_statement()` checks for zero extracted text → triggers vision fallback if `ANTHROPIC_API_KEY` set; returns clear error if not set
- [x] `bank_format = 'scanned'` stored in DB for vision-parsed statements
- [x] `requirements.txt` — added `pymupdf>=1.24.0` and `python-dotenv>=1.0.0`
- [x] `app.py` — `load_dotenv()` called at startup (via try/import) so API key loads from `.env` without relying on bash source
- [x] Advisory warning flashed on admin upload and shown in platform parser test for scanned PDFs
- [x] Upload spinner + live second counter added to both admin upload page and platform parser test tool

**Multi-property bank statement model:**
- [x] `src/database/db.py` — `migrate_bank_statements_nullable_property()`: recreates `bank_statements` table removing NOT NULL from `property_id` (idempotent; skips if already nullable; uses raw sqlite3 + executescript to bypass get_connection context)
- [x] `app.py` — upload route: removed session property fallback; statements are now truly org-scoped with no forced property tag
- [x] `app.py` — `manage_statements`: added `stmt_coverage` query (COUNT DISTINCT properties with verified payments per statement); passed to template
- [x] `app.py` — `statement_detail`: added `prop_breakdown` dict from matched rows (per-property payment count + KES total); passed to template
- [x] `templates/statements.html` — added "Properties" column with coverage badge (property name / "Multi-property (N)" / "—")
- [x] `templates/statement_detail.html` — added property breakdown strip below summary cards

**Platform parser test tools:**
- [x] `src/routes/platform_routes.py` — `GET/POST /platform/parsers`: calls parsers directly (not via ParseResult wrapper); full result dict passed to template for PDF/SMS/Excel
- [x] `templates/platform/parsers.html` — Bootstrap tab nav; PDF/SMS/Excel forms; results rendered inline; warnings block for scanned PDF advisory
- [x] `templates/platform/base_platform.html` — Parser Tools nav link added

**Water charges robustness (prefix-fallback unit matching):**
- [x] `app.py` upload_water_charges: prefix-fallback matching for unit numbers like "4A" when DB has "4A NBK"; uniqueness check prevents false positives; flash message shows matched units

---

#### Session 14 — PMO Account System, Owner Activation, Platform Outbox (2026-05-20) ✅ COMPLETE

**Unified login (`/login`):**
- [x] `admin_login` route now handles org admin + owner (persons) + master key in one form — priority: org email → person email → ADMIN_PASSWORD
- [x] Owner login always redirects to `/owner/dashboard` (ignores `next` URL, preventing redirect loop back to org admin routes)
- [x] `/owner/login` reduced to a redirect to `/login` (legacy URL preserved)
- [x] `require_owner_auth` redirects to `admin_login` endpoint instead of `owner.login`
- [x] `login.html` label changed from "Work email" to "Email address"

**Token-based owner login (`/owner/l/<token>`):**
- [x] `GET/POST /owner/l/<token>` — password-only login using `owners.access_token`; no email required; greets owner by first name
- [x] `templates/owner/token_login.html` — three states: invalid token, not activated yet (links to activation), ready (password form)
- [x] Owners page shows `/owner/l/<token>` link prominently when account is active; uses `generate_owner_token` route

**Owner activation (email OTP, replaces phone OTP):**
- [x] `src/messaging/email.py` — `send_email()` via SMTP; Mailtrap-compatible; returns `(False, 'SMTP not configured')` gracefully
- [x] `src/messaging/outbox.py` — `log_outbox()` writes every outbound message to `platform_outbox` table regardless of channel
- [x] `_send_email_otp()` helper in `owner_routes.py` — generates 6-digit OTP, stores with 10-min expiry, sends via email, always logs to outbox (status: 'simulated' if SMTP not configured)
- [x] `activate` route: on password set → `_send_email_otp()` → redirect to `/owner/verify-email` (removed phone OTP path)
- [x] `GET/POST /owner/verify-email` — enter 6-digit code; verify; log in; resend option
- [x] `templates/owner/verify_email.html` — Step 2 of 2 progress bar; OTP input; resend link
- [x] `verify_phone` route and template preserved (file exists) but no longer reachable via activation flow
- [x] SMTP env vars documented in `scripts/run_dev.sh` comments

**Platform Outbox:**
- [x] `migrate_add_platform_outbox()` — creates `platform_outbox` table: id, to_name, to_email, to_phone, channel, subject, body, status, error, org_id, property_id, message_type, created_at
- [x] `GET /platform/outbox` — filterable by channel + status; expandable rows showing full message body
- [x] `templates/platform/outbox.html` — table with collapse rows; channel/status badges
- [x] Outbox tab added to `templates/platform/base_platform.html`
- [x] `delivery.py` `send_sms()` — logs every SMS to outbox via `_log_sms()` helper (never blocks delivery on logging failure)
- [x] `guardian.py` `notify_owner_change()` — logs portal notifications to outbox for platform visibility

**Owner security hardening (primary owner + email lock):**
- [x] `migrate_add_primary_owner()` — adds `property_owners.is_primary INTEGER DEFAULT 0`; auto-migrates: first assigned owner per property marked primary
- [x] `assign_property_to_owner` — auto-sets `is_primary=1` if no primary owner exists for the property; prevents double-assign
- [x] `create_owner` inline assignment also sets `is_primary` correctly
- [x] `remove_property_from_owner` — blocks removal if `is_primary=1` (must contact platform to transfer); requires `reason` field; logs reason to audit + shadow log + critical alert; SMS removed owner and remaining owners
- [x] `edit_owner` — email field immutable once `persons.password_hash` is set; name/phone sync to `persons` row; persons.email never touched (login key)
- [x] `owners.html` — property pills show PRIMARY (dark badge) / additional (gray badge); primary owner has 🔒 icon not × button; removal opens modal requiring reason; email field disabled with 🔒 label when account active

**Platform fee:**
- [x] `organizations.platform_fee_rate REAL DEFAULT 0.01` — Domi's 1% take, never shown in org admin routes
- [x] Platform dashboard "Platform Fee" column; create org modal field
- [x] `calculate_disbursement()` deducts both management fee and platform fee; disbursements table stores both

**Org self-service settings:**
- [x] `GET /settings` — email + password change; org name read-only
- [x] `POST /settings/email` + `POST /settings/password`
- [x] `templates/settings.html`; sidebar "Account Settings" link

**Welcome wizard:**
- [x] `/welcome` is a live DB query: `step1_done` (has property), `step2_done` (has owner), `step3_done` (has caretaker), `all_done`
- [x] Step 2 and 3 unlock when step 1 done; celebration screen when all done
- [x] Dashboard redirects to `/welcome` when org has no active properties

**Misc fixes:**
- [x] `require_admin_auth` always enforces auth (removed dev bypass); `ADMIN_PASSWORD=dev` in run_dev.sh as master key
- [x] `onboard_preview` passes `organization_id = session.get('org_id')` to property INSERT (was NULL)
- [x] `migrate_add_owners()` no longer seeds ghost "Property Owner" row on fresh install

---

#### Session 12 — Security Architecture: Threat Model + Sprint 1 Defenses (2026-05-19) ✅ COMPLETE

**Documentation (new files):**
- [x] `.agent/security.yaml` — full threat/defense registry for AI agents; 10 threats, 9 defenses, owner talking points, implementation status per defense
- [x] `SECURITY.md` — owner-facing talking points (Section 1, plain English, use when pitching to landlords) + technical implementation notes (Section 2, for developers/agents)
- [x] `templates/platform/trust.html` — replaced stub with full trust dashboard: agency trust scores, owner guarantees (9 cards with Live/Planned badges), platform oversight monitor, sprint status
- [x] `CLAUDE.md` — Security Architecture section added (separation of control rules, implemented defenses, planned defenses, guardian call requirements)
- [x] `CURSOR_PLAN.md` — security sprint blocks added here

**Sprint 1 code (before disbursements go live):**
- [x] D1 — `POST /owners/<owner_id>/edit` route in `app.py`: updates name/phone/email; if phone changes, SMS old number + platform_log; UI added to `owners.html` (Edit Details section above Set Password)
- [x] D3 (complete) — `remove_property_from_owner`: now also SMSes remaining owners on property when one is removed (was: only SMSed the removed owner)
- [x] D4 — already implemented in `_get_confirmed_payout_owner()` in `disbursements.py` — confirmed complete
- [x] D7 — confirmed: `manage_owners` query does not SELECT `payout_mpesa`; disbursements.py logs last 4 digits only
- [x] D9 — `delete_payment` + `statement_correct_payment` in `app.py`: SMS active tenant on unit when payment reversed or corrected; mpesa_ref looked up from bank_transactions via bank_txn_id; also logs to platform_shadow_log via platform_log()

**Sprint 2 — before onboarding external owners (not yet started):**
- [ ] D2: Lock `set_owner_password` route to owner-initiated only after first login; admin cannot change password once owner has logged in
- [ ] D5: Guard on `properties.management_fee_rate` changes — add when property settings UI is built; fire critical alert + SMS all owners + platform_log
- [ ] D6: `GET /view/<property_id>/activity` — owner activity tab; reads `platform_shadow_log` filtered to property; new template `templates/viewer/activity.html`; add Activity to `base_viewer.html` nav

**Sprint 3 — Phase 5 intelligence:**
- [ ] D8: Extend `src/agent/detector.py` anomaly_check_job — flag if monthly_rent on any unit has decreased >20% over 90 days (catches gradual skimming that per-change >10% threshold misses)

---

#### Session 11 — Water Readings, Reports Portals, Suggestion Refactor, Metrics Consolidation (2026-05-19) ✅ COMPLETE

**Water charges — storage and web view:**
- [x] `migrate_add_water_readings()` in `db.py`: adds `properties.water_rate` (REAL DEFAULT 300), creates `water_uploads` and `water_readings` tables with indexes
- [x] `POST /water-uploads/set-rate` — updates property water rate; audited
- [x] `GET /water-uploads` — admin list with rate card + inline rate edit (JS toggle)
- [x] `GET /water-uploads/<upload_id>` — detail: readings table with prev/current/consumed/amount; anomaly flags (amount > 2× previous); fallback charges_only table for Excel uploads
- [x] Updated `/charges/water` Excel upload to create `water_uploads` + `water_readings` records; redirects to detail instead of dashboard
- [x] Templates: `templates/water_uploads.html`, `templates/water_upload_detail.html`

**Caretaker water entry form:**
- [x] `GET /caretaker/<pid>/water` — list of past batches
- [x] `GET/POST /caretaker/<pid>/water/new` — meter reading form; previous reading auto-populated from last upload; live JS calculation (rate × consumed); validates current >= previous; creates water_upload + readings + rent_charges; prevents duplicate per charge_period
- [x] Templates: `templates/caretaker/water.html`, `templates/caretaker/water_new.html`
- [x] Caretaker nav: "Reports" and "Water" tabs added to `base_caretaker.html`

**Caretaker reports portal:**
- [x] `GET /caretaker/<pid>/reports` — list of landlord_reports; occupancy + arrears badges from JSON blob
- [x] `GET /caretaker/<pid>/reports/<report_id>` — operational report (occupancy, arrears follow-up with phone links, tenant movement, vacant units); no KES amounts; calls `enrich_report_data()`
- [x] Template: `templates/caretaker/report_detail.html`

**Owner report generation from portal:**
- [x] `POST /view/<property_id>/reports/generate` — owner selects month, derives period_start/period_end using `calendar.monthrange`, calls `generate_landlord_report`, inserts to `landlord_reports`; no SMS
- [x] `GET /view/<property_id>/reports` — passes `default_period` (last month) to template; generate form at top
- [x] Removed "Owner View ↗" button from `templates/reports/preview.html`

**Bug fix — suggestion "None" in unreported payments:**
- [x] `templates/review.html` single-transaction section was missing `{% elif t.unit_hint %}` branch; grouped-transaction section had it; fixed to match. Now shows "Hint: M10 — no match" consistently with `statement_detail.html`

**Architectural refactor — shared suggestion logic:**
- [x] `enrich_with_suggestions(rows, conn, property_id, org_id=None)` extracted to `src/reconciliation/matcher.py`
- [x] Both `statement_detail` and `review` unreported-tab inline implementations replaced with calls to canonical function
- [x] Dead `tenant_rows` queries removed from both routes (function runs its own)

**Architectural refactor — canonical utils:**
- [x] `src/utils/metrics.py` created: `get_property_occupancy()`, `get_expected_monthly_income()`, `get_months_behind()`
- [x] Used in `app.py` (admin dashboard + arrears), `viewer_routes.py` (owner dashboard + arrears), `caretaker_routes.py` (via `_occupancy_data` wrapper), `owner_routes.py` (portfolio loop)
- [x] Phone normalization consolidated: 3 inline copies in `app.py` replaced with `_normalize_phone()` from `src/utils/phone.py`; `link_tenant_person` now returns an error on unrecognised format instead of silently storing garbage

---

#### Session 10 — Bank Statement Workflow (2026-05-19) ✅ COMPLETE

- Fixed `manage_statements()` redirect bug (org_id not set on master-key login path) + `property_list()`/`select_property()` now backfill `session['org_id']`
- Added Family Bank format detection (distinct from Co-operative; same parser, different `PARTICULARS IN OUT` header check)
- Added National Bank format detection (distinct from KCB/Tabular; different column header; same tabular parser)
- Fixed `view_statement_pdf` and `reparse_statement` to use `UPLOAD_FOLDER/{id}.pdf` (not stored `file_path` which is a Fly.io absolute path)
- Reparse now updates `period_start`/`period_end` from transactions in the UPDATE
- Built `GET /statements/<statement_id>` — full statement management hub (verified, unmatched, assign, auto-assign, correct, parse errors, other txns) — all actions audited
- Two-tier suggestion engine: Tier 1 = unit_hint narration exact match → Auto-assign; Tier 2 = sender name token overlap ≥2 → name_match badge + pre-filled assign form
- `verify_payments()` now scopes to `stmt.property_id` when set; org-wide otherwise
- Payments tab reorder: Confirmed → Unconfirmed → Unreported → Reversals → Parse Errors
- Statement filename column added to Unreported tab (links to statement detail)
- Multi-property upload tagging: optional property dropdown at upload; supersede logic scoped by tag; statement detail shows Property column + grouped optgroup dropdowns when org has >1 property
- `payment.property_id` now always derived from unit SQL in all 3 assign/correct routes (was using session property)
- Updated `.agent/schema.yaml`, `.agent/routes.yaml`, `ROADMAP.md`, `CURSOR_PLAN.md`, `CURSOR_PATTERNS.md`

---

#### Session 12 — Business Model Alignment, Delete Property, PMO Account System Design (2026-05-19) 🔄 IN PROGRESS

**Business model locked:**
- Go-to-market: PMOs are the buyers, owners are the advocates. Target 1,000 properties at Mowin scale.
- Revenue: `platform_fee_rate` (default 1%) on every disbursement. Silent in the float. Not a subscription.
- Two fees: `management_fee_rate` (agency's, editable by org admin) vs `platform_fee_rate` (Domi's, platform-only, never in org admin routes/templates).
- Bank statements are transitional — customer acquisition bridge while Daraja/Pesapal credentials are pending.
- Informal monthly payment from PMOs during bridge period (not in app).
- `ROADMAP.md`, `CLAUDE.md`, `CURSOR_PLAN.md` updated to reflect all of the above.

**Delete property (robust):**
- [x] `migrate_add_deletion_requested()` — `properties.deletion_requested_at TIMESTAMP` column
- [x] `GET /properties/delete/<property_id>` — confirmation page with impact summary, typed name confirmation, 14-day window explanation
- [x] `POST /properties/delete/<property_id>` — soft delete: `status='deletion_requested'`, SMS owners, critical platform alert, shadow log. Data fully preserved.
- [x] Agency view automatically excludes `deletion_requested` properties (all queries filter `status='active'`)
- [x] Platform `GET /platform/deletions` — table of pending properties with days remaining, Cancel/Transfer/Approve actions
- [x] `POST /platform/properties/<id>/cancel-deletion` — restore to active, SMS owners
- [x] `POST /platform/properties/<id>/transfer` — reassign `organization_id` to new agency, full history intact, SMS owners
- [x] `POST /platform/properties/<id>/approve-deletion` — hard cascade delete, only after 14-day window expires
- [x] Platform nav "Deletions" tab with red badge count
- [x] `run_dev.sh` no longer fails when `data/dev.db` is missing — creates fresh DB instead
- [x] `setup_property` FK bug fixed — stale `org_id` in session now validated before INSERT

**Mowin data recreation (in progress):**
- [x] Prod DB backed up to `backups/prod_2026-05-19_180211.db`
- [x] Fresh `data/dev.db` created (all migrations run clean)
- [ ] Onboard Mowin via `/onboard` (Excel upload) — next step
- [ ] January workflow: generate charges → upload Family Bank + NBK Jan statements → verify → report
- [ ] February: same (rent + service only, no water data)
- [ ] March: upload Feb water readings → generate charges → upload statements → verify → report
- [ ] Rongai: onboard property shell → tag Rongai transactions in statements → import Excel when ready

**Phase I: PMO Account System (next to build):**
- [ ] `organizations.platform_fee_rate REAL DEFAULT 0.01` — migration
- [ ] `persons.activation_token TEXT` — migration (for owner account activation)
- [ ] Org-specific login: `POST /login` checks `organizations.admin_password_hash` by slug/email
- [ ] First-time wizard: `GET /welcome` shown on first login with no properties
- [ ] Platform org creation: add `platform_fee_rate` field to `/platform/orgs/new`
- [ ] `calculate_disbursement()` deducts `platform_fee_rate` after management fee
- [ ] Owner activation: `GET /owner/activate/<token>` — owner sets password via OTP link
- [ ] Owner invite SMS sent when PMO adds owner with phone number

---

## Project Re-entry Overview (for AI agents)

**Last updated:** 2026-05-19
**Product:** Domi — property fintech platform, Kenya. Repo name: `rent-reconciliation` (unchanged).
**Stack:** Python 3.13, Flask 3.0+, SQLite, APScheduler, Jinja2 + Bootstrap 5. Fly.io (Johannesburg).

**Strategic direction (locked 2026-05-19):** Domi is a property fintech platform operated by its founders. PMOs are the buyers; owners are the advocates. Target: 1,000 properties at Mowin scale. Revenue: `platform_fee_rate` (1%) on every landlord disbursement — silent in the float, never visible to PMO or org admin. Bank statements are a transitional customer acquisition tool while Daraja/Pesapal credentials are pending. Two fees that must never be confused: `management_fee_rate` (agency's, PMO-editable) and `platform_fee_rate` (Domi's, platform-only). The payment rail + intelligence layer is the moat — switching requires changing payment instructions for every tenant.

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
