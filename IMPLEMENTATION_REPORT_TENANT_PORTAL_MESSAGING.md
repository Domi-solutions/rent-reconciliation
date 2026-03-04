# Implementation Report: Tenant Portal & Messaging System

This report describes the implementation completed for the **Tenant Portal** and **Messaging System** in the rent-reconciliation app. You can hand this to Claude (or another agent) for context on what was built and where everything lives.

---

## Summary

Two interconnected features were implemented:

1. **Tenant Portal** — Read-only view for tenants at `/tenant/<token>`. No login; the URL token is the credential. Tenants see balance summary, charges by period (with paid/unpaid), payment history with allocation trail, and an message inbox. All text uses data-descriptive language per project rules.

2. **Messaging System** — Admin-side: broadcast messages to tenants, manage message templates, and configure automatic rent-due reminders. Reminders are generated when an admin loads the dashboard; they are idempotent per property per day per reminder type. Messages appear in the tenant portal inbox.

---

## Project Root

All paths below are relative to **`rent-reconciliation/`** (the app root).

---

## 1. Database Changes

### 1.1 Migrations (`src/database/db.py`)

- **`migrate_add_tenant_access_token()`**
  - Adds `access_token TEXT` to `tenants` if missing.
  - Creates unique index `idx_tenant_access_token` on `tenants(access_token)`.
  - Idempotent.

- **`migrate_add_messaging()`**
  - Creates tables: `messages`, `message_templates`, `reminder_settings`.
  - **messages:** `id`, `property_id`, `tenant_id`, `batch_id`, `subject`, `body`, `message_type` ('reminder'|'broadcast'|'notice'), `delivery_channel` ('portal'), `delivery_status` ('delivered'), `read_at`, `created_at`. Indexes on `property_id`, `tenant_id`, `batch_id`.
  - **message_templates:** `id`, `property_id` (NULL = system default), `template_key`, `subject`, `body`, `enabled`, `created_at`, `updated_at`. UNIQUE(property_id, template_key).
  - **reminder_settings:** `id`, `property_id`, `template_key`, `days_before_due`, `enabled`, `created_at`. UNIQUE(property_id, template_key).
  - Seeds five system default templates (property_id NULL): `rent_due_10d`, `rent_due_5d`, `rent_due_today`, `water_cutoff`, `custom_broadcast`. Placeholders in subject/body: `{tenant_name}`, `{unit_number}`, `{balance}`, `{month}`, `{amount}`, `{due_date}`.
  - Idempotent; skips seed if system templates already exist.

- **`migrate_set_rent_charge_due_dates()`**
  - Backfills `due_date` on existing `rent_charges` where `period` is `YYYY-MM`: sets due_date to **5th of the next month** (e.g. period 2026-01 → 2026-02-05).
  - Idempotent (only updates rows where `due_date IS NULL`).

### 1.2 Schema (`src/database/schema.sql`)

- **tenants:** Added column `access_token TEXT`. The unique index on `access_token` is **not** in schema (to avoid errors on existing DBs); it is created only in the migration.
- **New tables:** `messages`, `message_templates`, `reminder_settings` appended so fresh installs get them.

### 1.3 Charge generation and due_date

- In **`app.py`**, `generate_charges()` now computes **due_date = 5th of next month** from the period (e.g. 2026-02 → 2026-03-05) and includes it in `INSERT` for both rent and service charges.
- Water charges are unchanged (no due_date set in this implementation).

---

## 2. Tenant Portal

### 2.1 Blueprint and routes (`src/routes/tenant_routes.py`)

- **Blueprint:** `tenant_bp`, prefix `/tenant`.
- **Helper:** `_get_tenant_by_token(conn, token)` — returns `(tenant, unit, property)` dicts or `None` if token invalid/revoked or tenant has no unit.
- **Routes:**
  - `GET /tenant/<token>` — Portal overview: balance (total_charged, total_paid, balance from `unit_balances`), recent messages.
  - `GET /tenant/<token>/charges` — Charges grouped by period; each charge shows amount, paid, outstanding, due_date (same pattern as Export 3 allocation logic).
  - `GET /tenant/<token>/payments` — Payment list with M-Pesa ref and allocation breakdown (payment_allocations JOIN rent_charges).
  - `GET /tenant/<token>/messages` — Full inbox; **marks all unread messages as read** (sets `read_at`) on this request.

### 2.2 Templates (`templates/tenant/`)

- **base_tenant.html** — Mobile-friendly base: property name, unit number, tenant name; pill nav for Overview, Charges, Payments, Messages; no admin/viewer nav.
- **portal.html** — Balance card (data-descriptive: "KES X charged...", "KES X settled...", "KES X outstanding" or "All charges settled — KES 0 outstanding"); recent messages list with link to full inbox.
- **charges.html** — Charges grouped by period; table: type, amount, paid, outstanding, due date.
- **payments.html** — One card per payment: date, amount, M-Pesa ref, allocation list (amount → charge type + period).
- **messages.html** — List of messages with subject, date, type, body; read vs unread styling.
- **invalid_token.html** — Standalone page (no base): "This link has expired or is invalid."

### 2.3 Auth bypass

- In **`app.py`**, `require_admin_auth()` now exempts any path that **starts with `/tenant`**, so tenant portal is accessible without admin login.

---

## 3. Admin Token Management & Tenant List

### 3.1 Routes in `app.py`

- **`POST /tenants/<tenant_id>/generate-token`**  
  - Requires current property. Generates `secrets.token_urlsafe(32)`, updates `tenants.access_token`, logs to `audit_log`: action `tenant_token_generated`, entity_type `tenant`, details `Tenant: {name} | Unit: {unit_number} | Portal link generated`. Redirects to `manage_tenants` with success flash.

- **`POST /tenants/<tenant_id>/revoke-token`**  
  - Sets `tenants.access_token = NULL`. Logs: action `tenant_token_revoked`, details `Tenant: {name} | Unit: {unit_number} | Portal link revoked`. Redirects to `manage_tenants`.

### 3.2 Tenants list (`templates/tenants.html`)

- New column **"Portal link"**.
  - If tenant has `access_token`: **"Copy link"** button; URL = `request.host_url.rstrip('/') + '/tenant/' + token`; JavaScript copies to clipboard and shows "Copied" briefly.
  - If no token: **"Generate link"** button (POST form to `generate_tenant_token`).
- Shown for **all tenants** (any status), as requested.

---

## 4. Messaging Blueprint (Admin)

### 4.1 Blueprint and routes (`src/routes/messaging_routes.py`)

- **Blueprint:** `messaging_bp`, prefix `/messages`.
- All routes use **current property** only (`get_current_property(conn)`); no property switcher in Messages.
- **Routes:**
  - **`GET /messages`** — Dashboard: total message count, unread count, reminder-enabled count, recent messages table (subject, type, tenant, date). Links to Broadcast, Templates, Reminders.
  - **`GET/POST /messages/broadcast`** — Select recipients (checkboxes; "Select all" / "Clear"); optional template dropdown to pre-fill subject/body; subject (required) and body. POST: creates one `messages` row per selected tenant with same `batch_id` (generate_id('BATCH')); message_type `broadcast`; substitutes `{tenant_name}`, `{unit_number}`, etc. Logs `broadcast_sent`, entity_id = batch_id, details = `Subject: {subject} | Recipients: {count} tenants`.
  - **`GET /messages/templates`** — Lists all templates (system + property overrides) with key, scope, subject snippet, enabled, Edit link.
  - **`GET/POST /messages/templates/<id>/edit`** — Edit subject, body, enabled; POST updates and redirects to templates list.
  - **`GET/POST /messages/reminders`** — For current property, shows four reminder types: `rent_due_10d`, `rent_due_5d`, `rent_due_today`, `water_cutoff`. Each has days_before_due (number input) and enabled (checkbox). POST creates or updates `reminder_settings` rows; then redirects back to reminders.

### 4.2 Templates (`templates/messaging/`)

- **dashboard.html** — Cards for total messages, unread, reminders enabled; "Send broadcast" button; recent messages table; links to Templates and Reminders.
- **broadcast.html** — Recipient checkboxes (all tenants for property); template dropdown (fills subject/body via JS); subject and body fields; Submit/Cancel.
- **templates.html** — Table of templates with Edit button.
- **edit_template.html** — Form: subject, body, enabled checkbox.
- **reminders.html** — Table: template_key, days_before_due input, enabled checkbox; Save / Back.

### 4.3 Nav (`templates/base.html`)

- New **"Messages"** dropdown (same style as Charges/Exports) with: Dashboard, Broadcast, Templates, Reminders. Links use `url_for('messaging.dashboard')`, etc.

---

## 5. Automatic Reminders

### 5.1 Module (`src/messaging/reminders.py`)

- **`generate_due_reminders(conn, property_id)`**
  - For each **enabled** row in `reminder_settings` for that property:
    - Resolves template (property override or system default).
    - **Idempotency:** If a message with `batch_id = 'reminder-{today}-{template_key}'` already exists for this property, skip this template_key for today.
    - Finds charges whose `due_date` equals **today + days_before_due** (e.g. 0 = due today, 10 = 10 days before due). Uses SQLite `date(?, '+' || ? || ' days')`.
    - For each such charge, finds **tenants** that: `tenants.unit_id IS NOT NULL`, `tenants.status = 'active'`, and `unit_balances.balance > 0` for that unit. Deduplicates by tenant (one message per tenant per reminder type per day).
    - Inserts one `messages` row per tenant: subject/body from template with variables substituted (`tenant_name`, `unit_number`, `balance`, `month`, `amount`, `due_date`); message_type `reminder`; batch_id = `reminder-{date}-{template_key}`.

### 5.2 Trigger

- In **`app.py`**, inside the **`dashboard()`** route, immediately after `property_id = property_row['id']`, **`generate_due_reminders(conn, property_id)`** is called. So every time an admin loads the dashboard, reminders are run (idempotent so no duplicate messages per day per type).

---

## 6. App Wiring (`app.py`)

- **Imports:** `secrets`; new migrations; `tenant_bp`, `messaging_bp`; `generate_due_reminders`.
- **After existing migrations:** Call `migrate_add_tenant_access_token()`, `migrate_add_messaging()`, `migrate_set_rent_charge_due_dates()`.
- **Blueprint registration:** `app.register_blueprint(tenant_bp)`, `app.register_blueprint(messaging_bp)`.
- **Auth:** In `require_admin_auth()`, add exemption for `request.path.startswith('/tenant')`.
- **Activity export:** In the action_labels dict used for export/activity, added: `tenant_token_generated`, `tenant_token_revoked`, `broadcast_sent`.

---

## 7. Package

- **`src/messaging/__init__.py`** — Empty package init (or minimal comment).

---

## 8. Documentation Updates

- **CLAUDE.md**
  - Project structure: added `tenant_routes.py`, `messaging_routes.py`, `messaging/reminders.py`; templates `tenant/`, `messaging/`.
  - Database tables: `tenants.access_token`; `rent_charges.due_date`; new tables `messages`, `message_templates`, `reminder_settings`.
  - User roles: added Tenant (portal via token).
  - New sections: **Tenant Portal (/tenant/*)** (auth, bypass, routes, helper, data-descriptive language) and **Messaging (Admin)** (scope, routes, broadcast, reminders, due_date behavior).

- **ROADMAP.md**
  - Infrastructure: checkboxes for `tenants.access_token`, messaging tables, `due_date` and backfill.
  - New subsection **Tenant Portal & Messaging** with checkboxes for portal, messaging UI, and automatic reminders.

---

## 9. Verification Checklist (for you or Claude)

1. **Migrations:** Run app once; confirm migrations run (console messages) and no errors; confirm `tenants.access_token` exists and `messages`, `message_templates`, `reminder_settings` exist with system templates seeded.
2. **Tenant portal:** Generate a token for a tenant from Tenants page; open `/tenant/<token>`; check Overview, Charges, Payments, Messages tabs with real data; try invalid/revoked token → invalid_token page.
3. **Copy link:** Tenant with token shows "Copy link"; click and paste in new tab → same portal.
4. **Broadcast:** Send a broadcast from Messages → Broadcast; select one or more tenants; send; confirm messages appear in tenant portal Messages tab.
5. **Reminders:** In Messages → Reminders, enable one type (e.g. rent_due_today with 0 days); ensure there is a charge with due_date = today (or backfill/create charge with due_date = today); load admin dashboard; confirm reminder message appears in tenant portal.
6. **Due date:** Generate charges for a new period; confirm `rent_charges.due_date` is 5th of next month; confirm backfill migration set due_date on existing YYYY-MM charges.
7. **Deploy:** If applicable, run `fly deploy` and re-test portal and messaging on production URL.

---

## 10. File List (quick reference)

**New files:**

- `src/routes/tenant_routes.py`
- `src/routes/messaging_routes.py`
- `src/messaging/__init__.py`
- `src/messaging/reminders.py`
- `templates/tenant/base_tenant.html`
- `templates/tenant/portal.html`
- `templates/tenant/charges.html`
- `templates/tenant/payments.html`
- `templates/tenant/messages.html`
- `templates/tenant/invalid_token.html`
- `templates/messaging/dashboard.html`
- `templates/messaging/broadcast.html`
- `templates/messaging/templates.html`
- `templates/messaging/edit_template.html`
- `templates/messaging/reminders.html`
- `IMPLEMENTATION_REPORT_TENANT_PORTAL_MESSAGING.md` (this file)

**Modified files:**

- `src/database/db.py` — migrations: access_token, messaging tables, due_date backfill
- `src/database/schema.sql` — tenants.access_token; new messaging tables; no index on access_token in schema (migration only)
- `app.py` — migrations, blueprints, auth bypass, token routes, generate_charges due_date, dashboard reminder call, activity labels
- `templates/tenants.html` — Portal link column, Copy/Generate buttons, JS for clipboard
- `templates/base.html` — Messages dropdown
- `CLAUDE.md` — structure, tables, Tenant Portal and Messaging sections
- `ROADMAP.md` — infrastructure and Tenant Portal & Messaging subsection

---

End of report.
