# Cursor Patterns — Known Failure Log

> **For Cursor:** Read this file before every build session. These are documented mistakes made in previous builds on this codebase. Each entry includes what went wrong, what the correct behavior is, and why it matters. This file is updated after each Claude review session.
>
> **For Claude:** After reviewing Cursor's work, add new entries here. Be specific — include the file, the wrong pattern, the correct pattern. Vague entries ("be more careful") have no value.

---

## How to Use This File

**Cursor:** Before writing any code, scan this file. If you are about to do something listed under "What Cursor Did Wrong" — stop and do the correct version instead.

**Claude:** After each review session, add entries in this format:
```
### [short title]
**File(s):** where it happened
**What Cursor did:** exact description of the wrong thing
**What it should do:** exact correct approach
**Why:** consequence of the wrong approach
**Spotted:** YYYY-MM-DD
```

---

## Session 1 — Inferred from codebase documentation (2026-04-08)

*These entries are inferred from "NOT this, do THIS" patterns in CLAUDE.md and ROADMAP.md. They indicate bugs caught and documented after a Cursor build. No commit hash available — pre-logging-system.*

---

### Incomplete SELECT on tables with columns added via migration
**File(s):** Any route querying `owner_messages` or other tables with migrated columns
**Root cause:** Cursor reads the original schema (or an early version) and writes queries against that snapshot. It doesn't re-check the current migration state in `db.py` to see if new columns were added later. This means any column added after the initial schema definition gets silently dropped from queries.
**What Cursor did:** `SELECT id, subject, body, message_type, read_at FROM owner_messages` — omitting `template_body`, `sent_by`, `channel`, `recipient_count`.
**What it should do:** Before writing any query against an existing table, check the latest migration for that table in `db.py` to confirm the full column set. For `owner_messages`, always select all: `id, subject, body, template_body, message_type, channel, recipient_count, sent_by, read_at, created_at`.
**Why it matters:** SQLite doesn't error when a template variable references a missing column — it silently renders blank. The bug is invisible until someone looks at the UI and notices missing data.
**Spotted:** 2026-04-08 (Session 1)

---

### Wrong denominator in collection rate calculation
**File(s):** `src/reports/landlord_report.py`
**Root cause:** Cursor chose the most "obvious" denominator — charges generated in the period — without understanding that this produces a meaningless number mid-month (when not all charges are generated yet). The correct denominator is the property's expected monthly income, which is stable and comparable across time.
**What Cursor did:** `collection_rate = total_verified / total_period_charges`
**What it should do:** `vs_expected_income_pct = total_verified / expected_monthly_income * 100`. Expected monthly income = sum of all active units' `monthly_rent + service_charge`.
**Why it matters:** Mid-month the period charges may be incomplete, making the rate appear artificially low. The metric must measure performance against a consistent baseline, not a moving one.
**Spotted:** 2026-04-08 (Session 1)

---

### Importing shared helpers from app.py into blueprints
**File(s):** `src/routes/messaging_routes.py`, `src/routes/report_routes.py`
**Root cause:** Cursor sees a helper defined in `app.py` and assumes it can be imported from there like any module. It doesn't account for the circular dependency: blueprints are registered inside `app.py`, so importing from `app.py` inside a blueprint creates a loop that crashes on startup.
**What Cursor did:** `from app import get_current_property` inside a blueprint file.
**What it should do:** Any helper needed in multiple blueprints must either be defined locally in each blueprint, or moved to a shared utility module (e.g. `src/utils.py`) that has no Flask app imports. `get_current_property(conn)` is defined locally in each blueprint file that needs it.
**Why it matters:** Circular import crashes app startup with no useful error message. Hard to debug.
**Spotted:** 2026-04-08 (Session 1)

---

### Agency voice in user-facing strings
**File(s):** Templates, report generators, SMS message text, briefings
**Root cause:** Cursor defaults to natural English phrasing which is inherently first-person ("we did X"). Domi has a hard product rule against this — it's not just style, it's a core design constraint that must be actively applied.
**What Cursor did:** "We collected KES 312,000" / "We are following up on Unit A7" / "We filled the vacancy."
**What it should do:** Every user-facing string must report what the data shows, not what an agent did. "KES 312,000 verified against bank records." "Unit A7 — KES 42,000 outstanding, 2 months." "Unit B3 — vacant 14 days, now occupied." Apply this to every string before finishing a feature.
**Why it matters:** Hard product rule. Domi is a data reporter, not an agency. The distinction is intentional and must be consistent everywhere.
**Spotted:** 2026-04-08 (Session 1)

---

### Exposing internal reconciliation mechanics in tenant/caretaker messages
**File(s):** Any SMS, WhatsApp, or portal message to tenants or caretakers
**Root cause:** Cursor describes what the system did technically, which seems informative. But tenants and caretakers should not know how the verification pipeline works — only that the outcome happened.
**What Cursor did:** "Your payment has been verified against the bank statement." / "Bank transaction matched."
**What it should do:** "Payment confirmed." Nothing more. Internal mechanics (bank statements, reconciliation, transaction matching) are never surfaced to tenants or caretakers.
**Why it matters:** Exposing the pipeline invites gaming ("what if I send before the bank statement?") and reveals system internals unnecessarily.
**Spotted:** 2026-04-08 (Session 1)

---

### Using base.html for non-admin portal templates
**File(s):** `templates/` — viewer, caretaker, and tenant template files
**Root cause:** Cursor sees `base.html` as the project's base template and extends it everywhere. It doesn't recognize that the project has separate base templates per portal, each with different nav/auth context.
**What Cursor did:** Extended `base.html` in viewer, caretaker, or tenant templates.
**What it should do:** Each portal has its own base: viewer → `viewer/base_viewer.html`, caretaker → `caretaker/base_caretaker.html`, tenant → `tenant/base_tenant.html`. `base.html` is admin-only. Never modify `base.html` for non-admin work.
**Why it matters:** `base.html` renders the full admin sidebar. Wrong audience sees admin navigation. Portal authentication is also bypassed visually.
**Spotted:** 2026-04-08 (Session 1)

---

### Non-idempotent database migrations
**File(s):** `src/database/db.py`
**Root cause:** Cursor writes migrations the way you'd write them once — `CREATE TABLE`, `ALTER TABLE ADD COLUMN` — without thinking about what happens when the function runs again. But migrations run on every app startup, so they must be safe to call repeatedly.
**What Cursor did:** `CREATE TABLE owner_messages (...)` or `ALTER TABLE tenants ADD COLUMN flagged` without guards.
**What it should do:** `CREATE TABLE IF NOT EXISTS` for new tables. For new columns: query `PRAGMA table_info(table_name)` first, check if the column exists, skip the `ALTER TABLE` if it does. Every migration function must be safe to call 100 times.
**Why it matters:** Non-idempotent migrations crash the app on second startup with "table already exists" or "duplicate column" errors. They also block Fly.io deployments.
**Spotted:** 2026-04-08 (Session 1)

---

### Using ORM patterns in a raw SQL codebase
**File(s):** Any database interaction
**Root cause:** Cursor defaults to SQLAlchemy or ORM-style patterns because they're common in Flask tutorials and projects. This codebase deliberately avoids ORMs.
**What Cursor did:** Introduced `db = SQLAlchemy(app)`, model classes, or `db.session.query(...)`.
**What it should do:** Raw SQL only, always parameterized. `with get_connection() as conn: conn.execute("SELECT ...", (val,))`. `get_connection()` from `src/database/db.py` handles commit/rollback automatically.
**Why it matters:** Adding an ORM creates a parallel system that conflicts with the existing migrations, `unit_balances` VIEW, and connection handling. It also introduces security risk if parameterization is missed.
**Spotted:** 2026-04-08 (Session 1)

---

### Calling send_sms directly from agent modules
**File(s):** `src/agent/coordinator.py`, `src/agent/detector.py`, `src/agent/briefings.py`, `src/agent/inbound.py`
**Root cause:** Cursor sees `send_sms` as the obvious way to send a message and imports it directly. It doesn't recognize the delivery abstraction layer that keeps agent logic channel-agnostic.
**What Cursor did:** `from src.messaging.delivery import send_sms` inside agent module; called directly.
**What it should do:** `from src.agent.router import route_message`. Pass a message dict. The router decides the channel. Agent code never touches delivery directly.
**Why it matters:** When WhatsApp goes live, only `router.py` changes. If agent modules call `send_sms` directly, every agent file needs updating. The abstraction is the whole point.
**Spotted:** 2026-04-08 (Session 1)

---

### Importing Anthropic SDK outside llm.py
**File(s):** Any file other than `src/agent/llm.py`
**Root cause:** Same mental model as the delivery abstraction — Cursor reaches for the SDK directly without recognizing the wrapper module that exists to isolate the dependency.
**What Cursor did:** `import anthropic` or `from anthropic import Anthropic` in coordinator, inbound, or route files.
**What it should do:** `from src.agent.llm import call_llm`. The SDK is only imported inside `src/agent/llm.py`. Everything else goes through `call_llm(prompt, model='fast'|'smart')`.
**Why it matters:** Provider swap (e.g. moving to Gemini or a fine-tuned model) means editing one file. Direct SDK imports scatter the dependency everywhere.
**Spotted:** 2026-04-08 (Session 1)

---

### Blocking webhook handlers with synchronous LLM or heavy processing
**File(s):** `POST /inbound/sms`, `POST /inbound/whatsapp`
**Root cause:** Cursor implements the "full flow" in one place — receive message, classify intent, act, respond — because that's the logical sequence. It doesn't account for the timeout constraints of webhook endpoints.
**What Cursor did:** Called `classify_intent()` or `call_llm()` synchronously inside the webhook handler, then returned the response.
**What it should do:** Webhook handler does exactly two things: write the raw message to `inbound_messages`, return 200. The background scheduler job reads unprocessed messages and handles classification + action + response asynchronously.
**Why it matters:** LLM calls take 1-5 seconds. Africa's Talking and WhatsApp webhooks have short timeout windows (~5s). Timeout = AT marks delivery failed = retries = duplicate messages processed multiple times.
**Spotted:** 2026-04-08 (Session 1)

---

## Session 2 — 2026-05-12

---

### sqlite3.Row does not support .get() — bracket access only
**File(s):** `app.py` (dashboard route, verify_payments, review route — 6 locations)
**Root cause:** Cursor treats `sqlite3.Row` as a dict because it has dict-like syntax. It reaches for `.get('col', default)` for safe access (a standard dict pattern). But `sqlite3.Row` only supports bracket access — `.get()` raises `AttributeError` at runtime.
**What Cursor did:** `property_row.get('organization_id')`, `claim.get('mpesa_ref')`, `row.get('rent_due_day', 5)`.
**What it should do:** Bracket access with explicit None check: `property_row['organization_id'] if property_row['organization_id'] else None`. For defaults: `row['rent_due_day'] if row['rent_due_day'] is not None else 5`. Every `sqlite3.Row` access must use brackets, never `.get()`.
**Why it matters:** The error only surfaces at runtime when the specific code path executes. It silently works if the column is always populated but crashes the moment a nullable column is NULL and `.get()` is called for the default.
**Spotted:** 2026-05-12 (Session 2)

---

### bank_statements queries must use org_id, not property_id
**File(s):** `app.py` — `manage_statements()`, `upload_statement()`, `verify_payments()`, `review()`, `reparse_statement()`
**Root cause:** Cursor sees `bank_statements.property_id` in the original schema and writes `WHERE property_id = ?`. After the 2026-05-12 migration, statements are org-scoped: new uploads set `org_id`, `property_id` is legacy/nullable. Querying by `property_id` misses statements uploaded under other properties in the same org.
**What Cursor did:** `SELECT * FROM bank_statements WHERE property_id = ?`.
**What it should do:** `SELECT * FROM bank_statements WHERE org_id = ?` (using `session.get('org_id')` or `property_row['organization_id']`). Fall back to `property_id` only for very old legacy rows if needed. For new code: always `org_id`.
**Why it matters:** One bank statement can cover multiple properties in the same org. A query scoped to a single property_id will silently miss cross-property transactions and produce wrong verification results.
**Spotted:** 2026-05-12 (Session 2)

---

### Silent format fallback in detect_bank_statement_format() was removed — treat unknown formats explicitly
**File(s):** `src/parsers/pdf_parser.py`
**Root cause:** The original `detect_bank_statement_format()` returned `'cooperative'` for any PDF it couldn't identify. Cursor (and previous code) relied on this implicit fallback. After 2026-05-12, it returns `'unknown'` and `parse_bank_statement()` immediately fails fast with a `format_unknown` parse error rather than producing garbled transactions.
**What Cursor did:** Assumed every PDF produces at least some transactions; checked only for `parse_warnings` to detect partial failures.
**What it should do:** Always check the returned `bank_format` from parsing. If `'unknown'`, the statement will have `status='parse_failed'` and zero transactions — handle this explicitly. Never assume a successful return from `parse_bank_statement()` means valid transactions were extracted.
**Why it matters:** An unknown PDF silently parsed as Co-op format produces hundreds of garbage transactions that pollute the unassigned transaction list and confuse verification.
**Spotted:** 2026-05-12 (Session 2)

---

## Session 3 — 2026-05-13

---

### Jinja2 url_for() does not support ** dict unpacking
**File(s):** `templates/activity.html` (and any template with conditional extra params in url_for)
**Root cause:** Cursor writes Python-style dynamic keyword passing (`url_for('route', **{...})`) because it works in regular Python. Jinja2's `url_for` filter does not support `**` unpacking — it raises a `TemplateSyntaxError` at render time.
**What Cursor did:** `{{ url_for('activity', action=r['action'], **({'from': from_date} if from_date else {})) }}`
**What it should do:** Build the URL in two steps using string concatenation: `{{ url_for('activity', action=r['action']) }}{% if from_date %}&from={{ from_date }}{% endif %}{% if to_date %}&to={{ to_date }}{% endif %}`. Conditional query params must be appended as literal strings, not passed as Python dict unpacking.
**Why it matters:** The error surfaces immediately at page render and blocks the entire template. Any template with dynamic optional URL params will fail silently during development if `**` unpacking is used.
**Spotted:** 2026-05-13 (Session 3)

---

### Bootstrap form-select arrow overlap from custom padding shorthand
**File(s):** `templates/units.html` (status select in editable unit table)
**Root cause:** Bootstrap 5's `form-select` class sets `padding-right: 3rem` to reserve space for the dropdown chevron icon. Cursor applies a compact padding shorthand `padding: 3px 8px` which overrides all four sides including the right, collapsing the icon space and pushing the arrow on top of the text.
**What Cursor did:** `style="padding: 3px 8px"` on a `<select class="form-select">`.
**What it should do:** Always preserve Bootstrap's right-padding when adding custom padding to `form-select`. Use four-value shorthand that keeps right padding large: `style="padding: 3px 2rem 3px 8px"`. Never use shorthand that collapses all four sides on a Bootstrap component with a built-in icon.
**Why it matters:** The chevron renders inside the text content area, making the control look broken and text unreadable for multi-character values.
**Spotted:** 2026-05-13 (Session 3)

---

## Session 4 — 2026-05-17

---

### COUNT(*) via fetchone()[0] can return None — always guard with `or 0`
**File(s):** `app.py` — `tools_index()` route (unassigned transaction count)
**Root cause:** Cursor assumes `COUNT(*)` always returns an integer. But when combined with a complex JOIN and no matching rows, SQLite can return `None` through `fetchone()[0]` — particularly when the result set is empty after filtering. This only surfaces at runtime on a fresh or empty DB.
**What Cursor did:** `workflow['unassigned'] = conn.execute("SELECT COUNT(*) FROM ...").fetchone()[0]` — crashes with `TypeError` when the result is `None`, or passes `None` to template comparisons.
**What it should do:** Always guard aggregate queries with `or 0`: `conn.execute("SELECT COUNT(*) FROM ...").fetchone()[0] or 0`. Same applies to `MAX()`, `SUM()`, `MIN()` — all can return `None` on empty sets.
**Why it matters:** The error only surfaces in production or on first-time setups where the table is empty. Masked during development if the DB always has data.
**Spotted:** 2026-05-17 (Session 4)

---

### Disbursements must not write directly to owners.payout_mpesa — it is owner-set via portal only
**File(s):** `src/payments/disbursements.py`, any admin route touching `owners` table
**Root cause:** Cursor sees `owners.payout_mpesa` as just another column and writes to it from whatever route is most convenient (e.g. an admin form). It doesn't recognise the security constraint that makes this column owner-write-only.
**What Cursor did:** Added an admin form field to set `payout_mpesa` directly, or set it during owner creation.
**What it should do:** `owners.payout_mpesa` is set ONLY via `POST /view/<property_id>/payout/request-otp` + `confirm-otp` in `viewer_routes.py`. No admin route, no onboarding form, no migration seed should write to this column. Disbursements read it via `_get_confirmed_payout_owner()` — which also enforces the 48-hour hold. If no confirmed owner exists, the function raises `ValueError` + critical platform alert. Never bypass this check.
**Why it matters:** The OTP + 48h hold is the only guard against an admin or rogue AI agent redirecting landlord disbursements to an attacker-controlled M-Pesa number. Bypassing it removes the entire security layer.
**Spotted:** 2026-05-17 (Session 4)

---

### Monthly workflow status uses timestamp prefix comparison — do not query separate status flags
**File(s):** `app.py` — `tools_index()`, `templates/tools_index.html`
**Root cause:** Cursor would build a separate `workflow_status` table or add boolean columns to track monthly steps. This creates a maintenance burden and can get out of sync with actual data.
**What Cursor did:** N/A — documented preemptively as the pattern was established deliberately in Session 9.
**What it should do:** Each workflow step maps to a specific DB timestamp signal. The `period` is the current `YYYY-MM`. A step is "done this month" if its timestamp starts with the current period. In Jinja2: `{% set done = s and s[:7] == period %}`. Steps: water → `MAX(created_at) FROM rent_charges WHERE charge_type='water'`; charges → `MAX(created_at) FROM rent_charges WHERE charge_type='rent'`; statement → `MAX(uploaded_at) FROM bank_statements WHERE org_id=?`; verify → `MAX(payment_date) FROM payments WHERE property_id=?`; export → `MAX(timestamp) FROM audit_log WHERE action LIKE 'export_%'`. No separate status table needed — the data IS the status.
**Why it matters:** A status table diverges from actual data (e.g. charges generated but then deleted would still show as done). Querying the source tables ensures accuracy.
**Spotted:** 2026-05-17 (Session 4)

---

## Session 5 — 2026-05-19

---

### payment.property_id must be derived from the unit, not the session's selected property
**File(s):** `app.py` — `statement_auto_assign()`, `statement_assign_payment()`, `statement_correct_payment()`
**Root cause:** Cursor sees `get_current_property(conn)` at the top of every admin route and reaches for `property_row['id']` as the natural source of `property_id` when creating a payment. In a single-property org this always happens to be correct. In a multi-property org, the session's selected property may differ from the unit's actual property — for example, an admin selects Property A but assigns a credit to a unit in Property B via the org-wide statement view.
**What Cursor did:** `pay_property_id = property_row['id'] if property_row else None` — uses the session's selected property, not the target unit's property.
**What it should do:** Always derive `property_id` from the unit being assigned to: `conn.execute("SELECT p.id FROM units u JOIN properties p ON u.property_id = p.id WHERE u.id = ? LIMIT 1", (unit_id,)).fetchone()`. The session property is irrelevant — the unit's property is the authoritative source.
**Why it matters:** Payments with the wrong `property_id` produce incorrect reconciliation per-property, skew financial reports, and break collection rate calculations for the affected property.
**Spotted:** 2026-05-19 (Session 5)

---

### Family Bank and National Bank are distinct formats from Co-operative and KCB — check headers before returning format
**File(s):** `src/parsers/pdf_parser.py` — `detect_bank_statement_format()`
**Root cause:** Cursor reads the existing detector logic and sees that `segment_transactions()` (cooperative date pattern) matches successfully → returns `'cooperative'`. It doesn't check whether the statement is actually Co-op or Family Bank. Similarly it sees tabular detection → returns `'tabular_kes'` without distinguishing National Bank.
**What Cursor did:** Returned `'cooperative'` for any statement with `DD-MMM-` date format; returned `'tabular_kes'` for any tabular-layout statement regardless of bank.
**What it should do:** After the segmentation check succeeds, check bank-specific header strings BEFORE committing to the format label. For the cooperative parser group: check `PARTICULARS IN OUT` → `family_bank`, else → `cooperative`. For the tabular parser group: check `Transaction Date Value Date Reference Transaction Details` → `national_bank`, else → `tabular_kes`. Detection order matters — check from most-specific to least-specific.
**Why it matters:** Mislabeled statements confuse admins, produce wrong badge colors, and mislead debugging of parser issues. The format stored in `bank_statements.bank_format` is the permanent record — getting it wrong at upload time means every subsequent view shows the wrong bank.
**Spotted:** 2026-05-19 (Session 5)

---

## Session 6 — 2026-05-19

---

### Template suggestion branches must be kept in sync across all render paths in the same template
**File(s):** `templates/review.html`
**Root cause:** The same template had two separate `{% if t.suggested_unit_number %}...{% endif %}` blocks — one for grouped transactions and one for single transactions. Cursor added the `{% elif t.unit_hint %}` fallback branch to the grouped block but not the single block. Because both blocks look visually similar, this asymmetry is invisible in code review without side-by-side comparison.
**What Cursor did:** Single-transaction block went directly to `<span class="text-muted">None</span>` when `suggested_unit_number` was falsy, skipping the unit_hint fallback entirely.
**What it should do:** Any template that renders the same data structure in multiple table/card layouts must have identical conditional branch structure in every layout. Before adding or changing a suggestion/badge branch, search the template for all other places the same field is rendered and apply the same change.
**Why it matters:** Two render paths showing different information for the same underlying data is an information asymmetry bug — users see "None" in one view and "Hint: M10" in another, creating confusion and eroding trust in the data.
**Spotted:** 2026-05-19 (Session 6)

---

### Inline business logic in routes creates silent divergence — use canonical helpers
**File(s):** `app.py` (3 locations), `src/routes/viewer_routes.py`, `src/routes/caretaker_routes.py`, `src/routes/owner_routes.py`
**Root cause:** Cursor writes self-contained route functions and repeats the computation inline rather than checking whether a shared utility already exists. Each inline copy diverges subtly: different variable names, different edge-case handling (e.g. `monthly_rent > 0` vs no check), different query structure (3 separate `COUNT(*)` queries vs one grouped query). The divergence is invisible until a bug appears in one copy but not the others.
**What Cursor did:** Inline phone normalization (`if phone.startswith('07'): phone_norm = '+254' + phone[1:]`) in 3 separate route functions; inline occupancy counts as 3 separate `COUNT(*)` queries in 4 route files; inline `ceil(balance / monthly_rent)` in 3 arrears routes.
**What it should do:** Before writing any route-level computation, check `src/utils/` for an existing canonical function. If it doesn't exist but the same logic appears in more than one route, create a utility function first. The canonical files are: `src/utils/phone.py` (phone normalization), `src/utils/metrics.py` (occupancy, income, months-behind), `src/reconciliation/matcher.py` (unit suggestion enrichment). Never define these inline.
**Why it matters:** A bug fixed in one inline copy is not fixed in the others. A business rule change (e.g. phone format or occupancy definition) requires updating N files instead of 1. The divergence only surfaces in production, often on edge cases.
**Spotted:** 2026-05-19 (Session 6)

---

## Session 7 — 2026-05-20

---

### verify_payments only checked `pending` claims — flagged claims were invisible to it
**File(s):** `app.py` — `verify_payments()`
**Root cause:** Cursor wrote the query to fetch pending claims as `WHERE pc.status = 'pending'`. This is the natural filter for "claims awaiting verification." But it means flagged claims — previously marked `ref_not_found` because the ref was absent from an earlier statement — can never be automatically cleared when the ref appears in a subsequent statement. The flag becomes permanent even when the payment is later confirmed.
**What Cursor did:** `WHERE p.organization_id = ? AND pc.status = 'pending'` — flagged claims never evaluated against new statements.
**What it should do:** After the pending-claims loop, run a second loop against `WHERE pc.status = 'flagged' AND pc.flag_reason = 'ref_not_found'` against the same `bank_by_ref` dict. If a match is found: create payment, allocate, set `status = 'verified'`, unflag tenant (if no other open flags), write audit_log + platform_shadow_log, SMS caretaker + tenant.
**Why it matters:** A real payment that initially landed in the wrong statement period (e.g. due to bank processing lag) would stay flagged forever — the tenant is permanently marked as a fraud risk even after the bank confirms the payment.
**Spotted:** 2026-05-20 (Session 7)

---

### At-submission fraud check skipped when mpesa_period is NULL
**File(s):** `src/routes/caretaker_routes.py` — `log_payment()`
**Root cause:** Cursor anchored the fraud check on `mpesa_period` — it only ran `if mpesa_period and reference`. The M-Pesa SMS parser extracts `mpesa_period` from the message timestamp, but some messages (minimal refs, forwarded or truncated messages) have no parseable timestamp. Cursor didn't account for this — the check would silently skip, leaving the claim as pending with no cross-reference against bank data.
**What Cursor did:** `if mpesa_period and reference: ...` — full check block skipped when timestamp is absent.
**What it should do:** Gate on `reference` alone, not `mpesa_period`. When `mpesa_period` is set, scope the bank data lookup to that specific period. When `mpesa_period` is NULL, fall back to scanning ALL org bank statements. In both cases, if bank data exists and the ref is absent → flag immediately.
**Why it matters:** A caretaker submitting a bare reference code (no M-Pesa message body) would bypass the check entirely, defeating the fraud detection for exactly the case where a fraudster would try to game it.
**Spotted:** 2026-05-20 (Session 7)

---

### At-submission check only flagged missing refs — did not auto-verify found refs
**File(s):** `src/routes/caretaker_routes.py` — `log_payment()`
**Root cause:** Cursor implemented the check as a pure fraud detector: "if ref missing → flag, else do nothing." It didn't consider the symmetric case: if the ref IS found in bank data, the claim should be verified immediately — not left as pending waiting for the next admin verify run.
**What Cursor did:** `if not _ref_in_stmt: flag the claim` — no action when ref was found.
**What it should do:** Three-branch outcome: (1) ref found + bank transaction has no existing payment → create payment, FIFO allocate, set `status='verified'` immediately; (2) ref found + payment already assigned (admin manually assigned it) → link `claim_id` onto existing payment, set `status='verified'`; (3) ref absent from bank data → flag. Flash message reflects actual outcome: verified / flagged / logged-pending.
**Why it matters:** Without auto-verify, a caretaker logging a real payment after the admin has already uploaded and verified the statement would see their claim stuck as "Pending" — and the bank transaction would remain as an unassigned credit in the admin review page. Two things that belong together, sitting apart, requiring manual reconciliation.
**Spotted:** 2026-05-20 (Session 7)

---

## Session 8 — 2026-05-25

---

### Calling notify_property_owners() or send_sms() synchronously from a route handler blocks the HTTP request
**File(s):** `app.py` — `delete_property()` → `src/platform/guardian.py` → `src/messaging/owner_notify.py`
**Root cause:** Cursor treats `notify_property_owners()` as a fire-and-forget helper that "just sends a notification." It doesn't trace through the call stack to see that `owner_notify.py` calls `send_sms()` (blocking) internally. In a Flask route handler, any blocking I/O holds the HTTP connection open until the call completes. Africa's Talking sandbox is slow (5–30s per SMS). The property deletion route sent SMS to every owner synchronously, so the browser spun indefinitely.
**What Cursor did:** Called `notify_owner_change(conn, property_id, ...)` inside a POST handler. The chain: `notify_owner_change` → `notify_property_owners` → `send_sms` (blocking). The HTTP response was never returned until all SMS calls finished.
**What it should do:** `notify_property_owners()` uses `send_sms_async()` internally — this is now the default. Never call `send_sms()` (blocking) from a route handler except for security-critical flows (payout OTP, fraud alert). For everything else — confirmations, notifications, broadcast receipts, deletion alerts — use `send_sms_async()`. If adding a new notification call to a route, check whether the function being called eventually calls `send_sms()` or `send_sms_async()`. Only `send_sms_async()` is safe in a request context.
**Why it matters:** A hanging POST handler looks like a frozen browser to the user and creates duplicate submission attempts. On Fly.io, gunicorn has a worker timeout — a long-blocking request kills the worker and returns a 502 to the browser, losing the operation entirely.
**Spotted:** 2026-05-25 (Session 8)

---

### organizations table column names differ from the obvious defaults — check schema.yaml before writing queries
**File(s):** `src/routes/platform_routes.py`, any route touching the `organizations` table
**Root cause:** Cursor looks at the table name and guesses column names from convention (`email`, `password_hash`, `status`). The actual `organizations` table uses non-default names that reflect their specific roles.
**What Cursor did:** Wrote `WHERE email = ?`, `UPDATE organizations SET password_hash = ?`, or `WHERE status = 'active'`.
**What it should do:** Check `.agent/schema.yaml` for the `organizations` table before writing any query. The actual column names are: `contact_email` (not `email`), `admin_password_hash` (not `password_hash`), `is_active` (not `status`). The login query is `WHERE LOWER(contact_email) = ? AND is_active = 1`. The password check uses `admin_password_hash`.
**Why it matters:** Wrong column names produce a silent wrong-result query in SQLite (the column evaluates to NULL, so `WHERE email = ?` matches nothing — the user can never log in). This is harder to debug than a crash.
**Spotted:** 2026-05-25 (Session 8)

---

## Session 9 — 2026-05-25

---

### Broadcast SMS sends one shared body — misses per-tenant variable substitution
**File(s):** `src/routes/messaging_routes.py` — broadcast POST handler
**Root cause:** Cursor builds the SMS body once (using any tenant or the first recipient as template context) and sends the same string to all recipients. It treats the broadcast as a bulk message. But the substitution variables like `{tenant_name}` and `{balance}` are per-tenant — each recipient needs a personalised body.
**What Cursor did:** Built `sms_body = _substitute(template, vars_for_first_tenant)` outside the per-tenant loop, then called `send_sms_async(all_phones, sms_body)` once.
**What it should do:** Inside the per-tenant loop, call `_build_variables(conn, tenant, prop, base_url)` and `_substitute(template, vars)` per tenant, then `send_sms_async([tenant_phone], personalised_body)`. Each tenant gets their own balance, name, due date, and portal link.
**Why it matters:** Every tenant receives the first tenant's name and balance. "Hi Grace, you owe KES 45,000" delivered to 44 tenants is a data exposure and trust violation.
**Spotted:** 2026-05-25 (Session 9)

---

### Inline substitution variable dicts diverge between broadcast and thread compose
**File(s):** `src/routes/messaging_routes.py` — `broadcast()` and `thread_message()` POST handlers
**Root cause:** Cursor builds the substitution dict inline in each handler because it seems simpler to read. But when a new variable is added (e.g. `{portal_link}`), it must be added to every inline dict — and Cursor only finds one of them, leaving the others stale.
**What Cursor did:** Two separate `vars = {'tenant_name': ..., 'balance': ...}` dicts in two different POST handlers.
**What it should do:** Use `_build_variables(conn, tenant, prop, base_url=None)` — the single canonical function in `messaging_routes.py`. Never build a substitution dict inline in a route handler. Adding a variable means updating one function, not hunting down all call sites.
**Why it matters:** A variable like `{portal_link}` that works in broadcast but silently outputs `{portal_link}` in thread messages erodes trust. It's the kind of bug that appears "intermittently" (works in broadcast test, fails in thread test) and is hard to trace.
**Spotted:** 2026-05-25 (Session 9)

---

### `messaging_routes.py` recent-messages query missing `tenant_id` — click-through broken
**File(s):** `src/routes/messaging_routes.py` — messaging dashboard SELECT
**Root cause:** Cursor writes the query against the visible template output. The messaging dashboard shows subject, tenant name, body preview, and timestamp — but the click-through to the thread requires `tenant_id` in the row dict. Cursor didn't notice the template also used `tenant_id` for the link `href`, because that part of the template is not visually apparent from the query output.
**What Cursor did:** `SELECT id, tenant_name, subject, body, created_at FROM messages` — omitting `tenant_id`.
**What it should do:** When writing any SELECT that feeds a list template with row-level action links, check the template for all fields referenced in `href`, `url_for()`, data attributes, and hidden form fields — not just the visible text columns. For messages, the minimum is `id, tenant_id, tenant_name, subject, body, created_at`.
**Why it matters:** The template renders without error (Jinja silently returns empty string for missing dict keys) but the href evaluates to `/messages/thread/None`, which 404s when clicked. The bug appears only when the user tries to open a thread.
**Spotted:** 2026-05-25 (Session 9)

---

### Tenant self-report flow missing deduplication — duplicate claims on resubmit
**File(s):** `src/routes/tenant_routes.py` — `report_payment()` POST handler
**Root cause:** Cursor implements the happy path (parse SMS, create claim) without considering what happens when a tenant submits the same M-Pesa reference twice. The `payment_claims` table has a UNIQUE constraint on `(property_id, mpesa_ref)`, but Cursor catches the exception and surfaces a generic error — not the "you already submitted this" message the tenant needs.
**What Cursor did:** `conn.execute("INSERT INTO payment_claims ...")` — caught `IntegrityError` as a generic failure, returned "Something went wrong."
**What it should do:** Before INSERT, check `SELECT id, status FROM payment_claims WHERE property_id=? AND mpesa_ref=?`. If a row exists: redirect back with a flash message "You already submitted this reference — status: [pending/verified/flagged]." Never rely on the database unique constraint as the primary deduplication path in a user-facing route.
**Why it matters:** A tenant who submits twice (e.g. after page refresh) sees "Something went wrong" on the second attempt and may assume neither submission was received. The data-descriptive principle requires showing exactly what happened to their claim.
**Spotted:** 2026-05-25 (Session 9)

---

## Session 24 — 2026-05-26

---

### `assign_group` validates units against session property instead of org — cross-property assigns silently fail
**File(s):** `app.py` — `assign_group()` route
**Root cause:** The group-units `<select>` in the statement review page is populated org-wide (all properties in the org). Cursor wrote the server-side validation as `WHERE id = ? AND property_id = ?`, using the session's selected property. Any unit that belongs to a sibling property passes the dropdown but fails the validation, returning "Selected unit not found for this property." Cursor assumed the dropdown scope matched the session scope.
**What Cursor did:** `unit = conn.execute("SELECT id, unit_number FROM units WHERE id = ? AND property_id = ?", (unit_id, property_row['id'])).fetchone()` — always scoped to session property.
**What it should do:** When an `org_id` is available, validate via org join: `SELECT u.id, u.unit_number, u.property_id FROM units u JOIN properties p ON u.property_id = p.id WHERE u.id = ? AND p.organization_id = ?`. Use `unit['property_id']` (the unit's actual property) for the payment INSERT — never `property_row['id']` (the session's selected property).
**Why it matters:** In multi-property orgs the caretaker or admin regularly assigns credits from an org-wide bank statement to units on any property. The mismatched scope silently blocks every cross-property assign.
**Spotted:** 2026-05-26 (Session 24)

---

### SMS parser silently fails on National Bank message format — amount and date return None
**File(s):** `src/parsers/sms_parser.py` — `extract_amount_multiformat()`, `extract_timestamp_multiformat()`
**Root cause:** National Bank confirmation SMS uses two non-standard formats: (1) `Ksh. 13300.00` — with a period after `Ksh`; (2) `28/08/25 10:58:21` — 24-hour timestamp with no AM/PM and no "at" keyword. Cursor modelled patterns on the standard Kenya M-Pesa format (`Ksh 13,300.00` and `on DD/MM/YY at H:MM AM/PM`). The patterns didn't include the literal dot or the 24-hour no-AM/PM case, so both returned `None` silently — leaving `claimed_amount=0` and `mpesa_period=NULL` on all National Bank claims.
**What Cursor did:** `r'(?:KES|Ksh)\s*(\d+\.\d{2})'` — no `\.?` for optional period. No pattern for `DD/MM/YY HH:MM:SS` without AM/PM.
**What it should do:** Pattern 1 and 2 in `extract_amount_multiformat()` must include `\.?` after `Ksh` to handle the period: `r'(?:KES|Ksh)\.?\s*...'`. In `extract_timestamp_multiformat()`, add a `p0b` pattern between `p0` and `p1`: `r'(\d{1,2})/(\d{1,2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})(?!\s*(AM|PM))'` with a negative lookahead to avoid matching the AM/PM-bearing patterns. Parse `day/month/yr` (not month/day).
**Why it matters:** Claims submitted from National Bank messages all showed `KES 0 · no date` in the statement view. The `mpesa_period=NULL` also prevented period-scoped bank matching, meaning every National Bank claim was left permanently pending even after the correct statement was uploaded.
**Spotted:** 2026-05-26 (Session 24)

---

## How to Add New Entries (for Claude)

**When to add:** After testing reveals a broken or missing integration — Claude diagnoses the root cause, then documents it here.

**Quality standard for entries:** Capture the underlying mistake, not just the surface symptom. A good entry explains *why Cursor made the mistake* and *what mental model Cursor needs to have* to avoid it in similar situations going forward — not just "do X instead of Y."

Bad entry: "Don't use SELECT * on owner_messages."
Good entry: "Cursor writes queries against the schema it last read, not accounting for columns added in later migrations. Before writing any query against an existing table, re-read the current migration in db.py to confirm the full column list."

**Format:**
```
### [short title]
**File(s):** where it happened
**Root cause:** why Cursor made this mistake (the underlying mental model failure)
**What Cursor did:** exact description of the wrong pattern
**What it should do:** exact correct approach — specific enough that the same instruction applies to similar cases
**Why it matters:** consequence of the wrong approach
**Spotted:** YYYY-MM-DD (session number)
```

**Process:**
1. User tests Cursor's build → finds issues → brings to Claude
2. Claude diagnoses root cause (not just symptoms)
3. Claude adds entry — specific, generalizable, root-cause-focused
4. Each session gets a new block with the date and commit hash for reference

**Git log usage:** Cursor commits after every build. Claude can reference the commit hash in entries to pinpoint exactly what Cursor changed. When diagnosing issues, run `git log` and `git diff` to see what Cursor actually built.

The goal: after 3-4 build cycles, this file becomes precise enough that Cursor makes zero repeated mistakes.

---

## Performance Backlog

Known bottlenecks that are acceptable at current scale (1 property, ~50 units) but should be addressed as the platform grows. Reviewed 2026-05-20.

### DONE — strftime() on bank_transactions.txn_date (fixed 2026-05-20)
Range queries (`bt.txn_date >= ? AND bt.txn_date < ?`) now replace all `strftime('%Y-%m', bt.txn_date) = ?` calls. Index `idx_bank_txn_date` added. Affects: `caretaker_routes.py log_payment()`, `app.py tools_index()`.

### PENDING — verify_payments() loads all pending/flagged claims into memory
**Threshold:** ~200+ units across multiple properties.
**Fix:** Process in batches of 500 using `LIMIT/OFFSET`, or add `AND pc.id > ? ORDER BY pc.id` cursor pagination. Both loops in `verify_payments()` need the same treatment.

### PENDING — enrich_with_suggestions() Tier 1 does one query per row with a unit hint
**File:** `src/reconciliation/matcher.py`
**Fix:** At function entry, load all units for the org into a dict keyed by normalised unit number (`UPPER(TRIM(unit_number))`). Replace `_lookup_hint(hint)` with a dict lookup. One query instead of N.

### PENDING — PDF parsing blocks the request thread
**File:** `app.py` `/statements/upload` route.
**Threshold:** Scanned PDFs via Claude vision take 5–15s. Digital PDFs via pdfplumber take ~1–3s.
**Fix:** Write the file, set statement status to `parsing`, return immediately with a "Processing…" flash. Parse in a background thread (same pattern as `send_sms_async`). Poll for completion or refresh on the statement list.

### PENDING — payments(payment_date) has no index; strftime filter on it is a full scan
**File:** `app.py tools_index()` — `strftime('%Y-%m', payment_date)=?`
**Fix:** Add `CREATE INDEX IF NOT EXISTS idx_payment_date ON payments(payment_date)` and rewrite to range query. Low priority — payments table stays small relative to bank_transactions.

### FUTURE — APScheduler + SQLite write contention at 2+ gunicorn workers
WAL mode is already set (`PRAGMA journal_mode = WAL` in `get_connection()`). As long as there is a single gunicorn worker this is safe. Adding a second worker risks write contention from scheduled jobs overlapping with web requests. Fix at that point: extract scheduler to a dedicated Fly process or use Redis/RQ.
