# Implementation Plan: SMS Engagement & Platform Improvements

## Project Re-entry Overview (for AI agents)

**Last updated**: 2026-03-09

- **Project name**: Rent Reconciliation — financial intelligence layer for rental properties in Kenya.
- **Stack**: Python 3.13, Flask 3.0+, SQLite (no ORM), Jinja2 + Bootstrap 5. Deployed on Fly.io (Johannesburg).
- **Source of truth docs** — read ALL of these before touching code:
  - `CLAUDE.md`: canonical technical reference — schema, all routes, patterns, conventions, auth models, messaging system. Maintained by Claude.
  - `ROADMAP.md`: product vision, phase checklist, business context. Updated by all agents.
  - `CURSOR_PLAN.md` (this file): work-in-progress plans and next steps. Maintained by Cursor.
  - `README.md`: high-level overview and how to run/deploy.

---

## Current State (as of last update)

### What is fully built and in production

**Core engine (Phase 0):** PDF bank statement parser, M-Pesa SMS parser, Excel import, FIFO payment allocation, multi-property support, admin + viewer auth, exports, deployed to Fly.io.

**Dashboard — The Pulse (Phase 1):** Collection gap, three-state payment visibility (verified/claimed/no activity), vacancy cost per unit, arrears concentration, maintenance tab.

**Monthly Reports — The Ledger (Phase 2):** Report generator (`src/reports/landlord_report.py`), admin generate/preview routes, owner Reports tab, caretaker report view, PDF export via print.

**Tenant portal (`/tenant/<token>`):** Balance, charges, payments, messages, maintenance. Token-based, no login.

**Owner portal (`/view/*`):** Dashboard, arrears, payments, reports, maintenance, messages inbox. Password + token auth. Messages tab shows all owner notifications with `sent_by` badge (blue=Admin, amber=caretaker name, gray=System).

**Caretaker portal (`/caretaker/*`):** Named account auth (name + password per caretaker; falls back to `CARETAKER_PASSWORD` env var if no DB accounts). Overview, arrears, tenants, issues, log-payment tabs. Session security: account deletion immediately revokes active sessions via per-request DB check.

**Caretaker management (`/caretakers`):** Admin CRUD for named caretaker accounts. Each caretaker tied to one property. Sidebar nav item.

**Messaging system:**
- Admin broadcasts: one row per recipient, `batch_id` groups them, `template_body` stores unsubstituted template.
- Caretaker broadcasts: same, `sent_by` = caretaker's actual name from session.
- Automatic reminders: two coexisting systems — `reminder_settings` (legacy) and `reminder_schedules` (flexible per-property, label + template + days + send_to).
- Both fire idempotently on dashboard load.

**SMS delivery (`src/messaging/delivery.py`):** Africa's Talking integration. `send_sms(recipients, message)`. Kenyan number normalisation (07xx → +2547xx). Currently in **sandbox mode** (`AT_USERNAME=sandbox`).

**Owner notifications (`src/messaging/owner_notify.py`):** `notify_property_owners(conn, property_id, message, portal_subject, portal_body, template_body, channel, recipient_count, message_type, sent_by)`. Stores in `owner_messages` table AND sends SMS to owners with phones. Called from: broadcasts, reminders, payment confirmations, report generation, caretaker payment claims.

**Payment SMS:** "payment confirmed" wording — no bank statement mechanics visible to tenants or caretakers.

---

## What is NOT built yet

### Priority 1 — SMS production go-live
Switch Africa's Talking from sandbox to live credentials:
```bash
fly secrets set AT_USERNAME=<live_username> AT_API_KEY=<live_key> AT_SENDER_ID=<sender_id>
```
Requires deciding on sender ID / shortcode in Africa's Talking account settings.

### Priority 2 — Balance snapshot mechanism
Needed for Phase 3 weekly digest (comparing arrears week-over-week).

**Schema:** New table `balance_snapshots`:
```sql
CREATE TABLE IF NOT EXISTS balance_snapshots (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL REFERENCES properties(id),
    unit_id TEXT NOT NULL REFERENCES units(id),
    snapshot_date TEXT NOT NULL,       -- YYYY-MM-DD
    balance REAL NOT NULL,
    total_charged REAL NOT NULL,
    total_paid REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(unit_id, snapshot_date)
);
```
**Trigger:** Run daily (or on dashboard load, idempotent per day) — for each unit in property, insert today's balance from `unit_balances` VIEW using `INSERT OR IGNORE`.

**Use:** Weekly digest compares `snapshot_date = date('now')` vs `snapshot_date = date('now', '-7 days')` to find units that improved or worsened.

### Priority 3 — Weekly Digest ("The Signal" — Phase 3)
Answers: "What changed this week?"

**Delivery:** Email or WhatsApp (email is simpler to start). Not SMS — digest is too long for SMS.

**Content (compute from DB):**
1. **Payment velocity** — verified payments in last 7 days (count + KES total)
2. **Arrears state changes** — units that got better (paid down) or worse (no payment) vs. last snapshot. Silent on stable units.
3. **Claim aging** — claims pending > 5 days (count + list)
4. **Occupancy changes** — unit status changes in last 7 days

**Implementation approach:**
- New module `src/reports/weekly_digest.py` — `generate_weekly_digest(conn, property_id)` returns a dict with the above sections
- New route `GET /reports/weekly-digest` (admin preview + manual send)
- Delivery: email via SMTP or Resend (`EMAIL_FROM`, `SMTP_*` or `RESEND_API_KEY` env vars)
- Scheduling: APScheduler or Fly.io cron — weekly on Monday morning

### Priority 4 — Minor UI improvement (no schema changes)

**Days-to-due countdown on ADMIN dashboard**
The caretaker dashboard already has `next_due_date`, `days_to_due`, and `recent_reminder`. Verify whether `app.py`'s `dashboard()` route and `templates/dashboard.html` have the same banner. If not, add the same query + coloured countdown banner.

---

## Codebase Quick Reference

- **No ORM** — raw SQL with parameterized queries only
- **ID generation**: `generate_id('PREFIX')` from `src/database/db.py` → `'PREFIX-A1B2C3D4'`
- **DB access**: `with get_connection() as conn:` — auto-commits on success, rolls back on error
- **Migrations**: idempotent functions in `src/database/db.py`, imported and called at startup in `app.py`. All new migrations must follow this pattern. Never drop or recreate tables with data — additive only.
- **SMS delivery**: `from src.messaging.delivery import send_sms` — `send_sms(recipients, message)` where `recipients` is `[{'phone': '...'}]`. Returns `(sent, failed, errors)`. **Always wrap in `try/except Exception: pass` — SMS must never block main flows.**
- **Owner notifications**: `from src.messaging.owner_notify import notify_property_owners` — call after any significant event. Pass `sent_by='Admin'`, `sent_by='System'`, or `session.get('caretaker_name', 'Caretaker')`.
- **Blueprints**: `tenant_bp` (/tenant/*), `messaging_bp` (/messages/*), `report_bp` (/reports/*), `viewer_bp` (/view/*), `caretaker_bp` (/caretaker/*)
- **Jinja2 comments**: `{# comment #}` — never use `{{/* */}}`
- **SMS wording rule**: never expose bank statement or reconciliation mechanics. Use "payment confirmed", not "received and verified against bank records".
- **Data-descriptive language**: no agency voice anywhere. "KES 312,000 verified against bank records" not "We collected KES 312,000". See `ROADMAP.md` for full table.

### Key schema facts

- `caretakers`: id, property_id, name (= login username), phone, password_hash, created_at
- `owners`: id, name, phone, email, password_hash, access_token, created_at
- `property_owners`: M:M junction — property_id, owner_id. Use for all owner↔property queries.
- `owner_messages`: id, property_id, owner_id, subject, body, template_body, message_type, channel, recipient_count, sent_by, read_at, created_at. **When querying for the notifications page, SELECT all these columns — omitting any silently hides data in the template.**
- `messages`: per-tenant in-app messages. tenant_id NOT NULL. batch_id groups broadcasts. template_body stores unsubstituted template.
- `reminder_schedules`: id, property_id, label, template_key, days_before_due, send_to ('all'|'arrears'), enabled
- `unit_balances` VIEW: unit_id, property_id, unit_number, monthly_rent, tenant_id, tenant_name, tenant_phone, total_charged, total_paid, balance
- `tenants`: id, property_id, unit_id, name, phone, email, move_in_date, status, access_token
- System template keys: `rent_due_10d`, `rent_due_5d`, `rent_due_today`, `water_cutoff`, `custom_broadcast`, `rent_due_reminder`

---

## Completed Work (formerly in this plan)

Everything in the original Groups 1–4 of this file is **done**. Do not re-implement:

- ✅ Tenant list sort + arrears filter — done (route + template both complete)
- ✅ Days-to-due countdown — on caretaker dashboard; check admin dashboard (Priority 4.2)
- ✅ `property_owners` M:M junction table + all owner↔property query updates
- ✅ `owner_notify.py` — more advanced version built (portal inbox + SMS)
- ✅ Owner SMS on charges generated, report generated, broadcast sent, reminder sent
- ✅ Payment verified → SMS to tenant with portal link
- ✅ In-app message → SMS ping to tenant
- ✅ `rent_due_day` per-property setting + `generate_charges` respects it
- ✅ `reminder_schedules` table + flexible reminder system
- ✅ Reminder schedules admin UI at `/messages/schedules`
- ✅ Named caretaker accounts + admin management at `/caretakers`
- ✅ Caretaker session security (per-request DB verification)
- ✅ `owner_messages` table + owner portal inbox at `/view/<property_id>/notifications`
- ✅ `sent_by` attribution (actual caretaker name / Admin / System) with colored badge

---

## After completing new work

1. Run `./venv/bin/python -c "from app import app; print('OK')"` — verify no import errors.
2. Test locally: `./scripts/run_dev.sh` → http://localhost:5001
3. Update `ROADMAP.md` — mark completed items `[x]`, add new items.
4. Update `CLAUDE.md` — add new tables, routes, patterns.
5. Update `CURSOR_PLAN.md` (this file) — move completed items to "Completed Work", update re-entry overview.
6. Deploy: `export PATH="$HOME/.fly/bin:$PATH" && fly deploy`
