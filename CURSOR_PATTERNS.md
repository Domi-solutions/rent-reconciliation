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
