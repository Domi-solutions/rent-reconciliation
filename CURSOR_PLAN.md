# Domi — Implementation Plan

## Project Re-entry Overview (for AI agents)

**Last updated:** 2026-04-08

**Build progress update:** Phase A, B, and C are now implemented in code (`db.py`, `app.py`, `src/agent/*`, `src/routes/agent_routes.py`, `templates/agent/*`). Next step is Phase D.

- **Product name:** Domi (property intelligence platform). Repo/deploy name: `rent-reconciliation` (unchanged).
- **Stack:** Python 3.13, Flask 3.0+, SQLite (no ORM), Jinja2 + Bootstrap 5. Deployed on Fly.io (Johannesburg).
- **Source of truth docs — read ALL of these before touching code:**
  - `CLAUDE.md` — canonical technical reference: schema, all routes, patterns, conventions, auth models, agent architecture
  - `ROADMAP.md` — product vision, all 8 phases, checklist status, business context, WhatsApp strategy
  - `CURSOR_PLAN.md` (this file) — active build sequence, next steps, implementation notes
  - `CURSOR_PATTERNS.md` — **read this before writing any code** — logged mistakes from previous Cursor builds; do not repeat these
  - `README.md` — how to run and deploy

---

## What Is Fully Built (do not re-implement)

**Phase 0 — Core Engine:** PDF bank statement parser, M-Pesa SMS parser, Excel import, water readings parser, FIFO payment allocation, multi-property support, admin + viewer auth, exports, deployed to Fly.io.

**Phase 1 — The Pulse:** Collection gap, three-state payment visibility (verified/claimed/no activity), vacancy cost per unit, arrears concentration, maintenance tab.

**Phase 2 — The Ledger:** Report generator (`src/reports/landlord_report.py`), admin generate/preview routes, owner Reports tab, caretaker report view, PDF export via print.

**Portals:** Tenant portal (`/tenant/<token>`), owner portal (`/view/*`), caretaker portal (`/caretaker/*`), caretaker management (`/caretakers`), owner management (`/owners`).

**Messaging:** Admin broadcasts, caretaker broadcasts, automatic reminders (two coexisting systems: `reminder_settings` + `reminder_schedules`), SMS via Africa's Talking (sandbox), owner notifications inbox (`owner_messages`), `sent_by` attribution.

---

## Active Build Plan

### The Core Architectural Rule (enforce from day one)

```
Agent logic  →  writes message objects to DB / queue
Delivery router  →  reads message objects, sends via correct channel
Web routes  →  never call agent functions directly
Agent  →  never imports from routes
LLM calls  →  always async, never blocking a request or webhook
```

The delivery router (`src/agent/router.py`) is the ONLY place that knows about channels (portal/SMS/WhatsApp). Agent code calls `route_message()`. When WhatsApp goes live, only the router changes — zero agent logic changes.

---

### Phase A — Infrastructure (prerequisite for everything)

**A1. `balance_snapshots` table**

Migration function `migrate_add_balance_snapshots()` in `src/database/db.py`:

```sql
CREATE TABLE IF NOT EXISTS balance_snapshots (
    id TEXT PRIMARY KEY,
    property_id TEXT NOT NULL REFERENCES properties(id),
    unit_id TEXT NOT NULL REFERENCES units(id),
    snapshot_date TEXT NOT NULL,
    balance REAL NOT NULL,
    total_charged REAL NOT NULL,
    total_paid REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(unit_id, snapshot_date)
);
```

Snapshot function: for each unit in property, `INSERT OR IGNORE` today's balance from `unit_balances` VIEW. Called by scheduler daily. Idempotent — safe to call multiple times.

**A2. APScheduler setup**

Add to `app.py` (after blueprint registration):

```python
from apscheduler.schedulers.background import BackgroundScheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=daily_snapshot_job, trigger='cron', hour=1)       # 1am daily
scheduler.add_job(func=morning_briefings_job, trigger='cron', hour=7)    # 7am daily
scheduler.add_job(func=weekly_digest_job, trigger='cron', day_of_week='mon', hour=8)  # Mon 8am
scheduler.add_job(func=anomaly_check_job, trigger='cron', hour=6)        # 6am daily
scheduler.start()
```

All job functions live in `src/agent/coordinator.py`. Scheduler is started once on app startup. Add `atexit.register(lambda: scheduler.shutdown())`.

**A3. `src/agent/` module skeleton**

Create the module with stub implementations. Every file must exist before wiring:
- `src/agent/__init__.py`
- `src/agent/coordinator.py` — job functions called by scheduler
- `src/agent/detector.py` — anomaly + task detection (returns dicts, does not send)
- `src/agent/briefings.py` — digest + briefing text generators (returns strings, does not send)
- `src/agent/inbound.py` — intent classifier + action handlers
- `src/agent/responder.py` — response message generator
- `src/agent/router.py` — delivery abstraction
- `src/agent/llm.py` — LLM wrapper
- `src/agent/state.py` — conversation session state

**A4. Delivery router (`src/agent/router.py`)**

```python
def route_message(msg: dict):
    """
    msg keys: recipient_phone, recipient_role, property_id,
              message_type, body, subject (optional)
    """
    # Portal adapter: always store in owner_messages / messages table
    # SMS adapter: call send_sms() from src.messaging.delivery
    # WhatsApp adapter: STUB — logs "would send via WhatsApp: {body}" until credentials live
```

Test: call `route_message(...)`, verify portal notification appears in the correct inbox.

---

### Phase B — Detection Engine

**B1. Task checker** (`src/agent/detector.py`)

`check_pending_tasks(conn, property_id) -> list[dict]`

Checks (all idempotent — just detection, no side effects):
- Bank statement not uploaded this month
- Water charges not uploaded this month
- Rent/service charges not generated this month
- Payment claims pending > 7 days (count)
- Unassigned bank transactions (count)
- Monthly report not generated for last completed month

Returns list of `{'task': str, 'severity': 'warning'|'urgent', 'detail': str}`.

Test: deliberately skip charge generation, call `check_pending_tasks()`, verify correct flag returned.

**B2. Anomaly detector** (`src/agent/detector.py`)

`detect_anomalies(conn, property_id) -> list[dict]`

Checks:
- Water charge > 30% above unit's 3-month average (requires 3 months of data)
- Vacancy duration flags: 14 days, 30 days, 60 days thresholds
- Arrears threshold crossings: unit moved from N to N+1 months behind (vs last snapshot)
- Collection rate below same-day-of-month from last month (requires `balance_snapshots`)

Returns list of `{'type': str, 'unit_id': str, 'unit_number': str, 'detail': str, 'severity': str}`.

Test: insert water charge 50% above average for a unit, call `detect_anomalies()`, verify flag.

**B3. Follow-up nudge detector** (`src/agent/detector.py`)

`detect_followups(conn, property_id) -> list[dict]`

Checks:
- Units with no payment or claim activity by day 15 of current month (arrears units only)
- Open maintenance issues with no status update in > 7 days
- Follow-up notes (from caretaker inbound) with a due date that has passed

Returns list of nudge dicts.

---

### Phase C — Digests and Briefings

All generators return plain text strings — readable in any channel. No markdown, no formatting that breaks in SMS or WhatsApp.

**C1. Weekly digest** (`src/agent/briefings.py`)

`generate_weekly_digest(conn, property_id) -> str`

Content (all computed from DB + balance_snapshots):
1. Payment velocity: verified payments in last 7 days (count + KES total)
2. Arrears changes: units that got better or worse vs last week's snapshot (stable = silence)
3. Claim aging: claims pending > 5 days
4. Occupancy changes: unit status changes in last 7 days

Format: 6-10 lines maximum. Property name in header. Invite reply.

**C2. Caretaker daily briefing** (`src/agent/briefings.py`)

`generate_caretaker_briefing(conn, property_id, caretaker_name) -> str`

Content: top arrears (unit + name + phone + balance + months), any follow-up nudges due today, new maintenance issues since yesterday, vacancy count. Max 10 lines.

**C3. Owner monthly briefing** (`src/agent/briefings.py`)

`generate_owner_briefing(conn, property_id) -> str`

Content: collection rate, arrears summary, anomaly flags (if any), tenant sentiment summary (if check-ins exist), invite to respond.

**C4. Admin task checklist** (`src/agent/briefings.py`)

`generate_admin_checklist(conn, property_id) -> str`

Content: list of pending tasks with days overdue. Concise, actionable.

**C5. Admin preview routes** (`src/routes/agent_routes.py`)

```
GET /agent/digest/preview/<property_id>       — renders weekly digest as it would be sent
GET /agent/briefing/caretaker/<property_id>/preview
GET /agent/briefing/owner/<property_id>/preview
GET /agent/checklist/<property_id>/preview
POST /agent/trigger/<job_name>               — manually trigger any scheduler job (dev only)
```

These are the primary local testing tools for Phase C. Test all digest content here before wiring delivery.

---

### Phase D — Inbound Parsing

**D1. New tables**

`migrate_add_inbound_messages()` and `migrate_add_inbound_sessions()` in `src/database/db.py`:

```sql
CREATE TABLE IF NOT EXISTS inbound_messages (
    id TEXT PRIMARY KEY,
    property_id TEXT,
    sender_phone TEXT NOT NULL,
    sender_role TEXT,          -- tenant | caretaker | owner | admin | unknown
    sender_entity_id TEXT,
    raw_body TEXT NOT NULL,
    channel TEXT NOT NULL,     -- sms | whatsapp
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    classified_intent TEXT,
    confidence REAL,
    action_taken TEXT,
    response_sent TEXT
);

CREATE TABLE IF NOT EXISTS inbound_sessions (
    phone TEXT PRIMARY KEY,
    property_id TEXT,
    last_intent TEXT,
    awaiting_confirmation TEXT,
    context_json TEXT,
    expires_at TIMESTAMP
);
```

**D2. LLM wrapper** (`src/agent/llm.py`)

```python
def call_llm(prompt: str, model: str = 'fast') -> str:
    """
    model='fast' → claude-haiku-4-5-20251001
    model='smart' → claude-sonnet-4-6
    Never import anthropic SDK outside this file.
    """
```

Env vars: `ANTHROPIC_API_KEY`. Wrap all calls in try/except — LLM failure must never crash a webhook.

**D3. Intent classifier** (`src/agent/inbound.py`)

`classify_intent(raw_text: str, sender_role: str) -> dict`

Returns `{'intent': str, 'confidence': float, 'extracted': dict}`.

Intent types:
| Intent | Example |
|---|---|
| `maintenance_report` | "The tap in B7 is dripping" |
| `maintenance_resolve` | "Fixed the tap in B7" |
| `payment_claim` | forwarded M-Pesa SMS |
| `followup_note` | "A3 guy says paying Friday" |
| `query_balance` | "How much does A3 owe?" |
| `query_arrears` | "Which units haven't paid?" |
| `checkin_reply` | "1" / "2" / "3" |
| `owner_instruction` | "Focus on A3 urgently" |
| `occupancy_update` | "New tenant in F1 on 1st April" |
| `confirmation` | "yes" / "no" / "skip" |
| `unknown` | anything else |

Rule: confidence ≥ 0.85 → execute + confirm. Below 0.85 → ask before acting.

**D4. Action handlers** (`src/agent/inbound.py`)

One handler function per intent. Each handler:
1. Takes classified result + sender context (role, entity_id, property_id)
2. Performs DB action
3. Returns response string

`confirmation` intent: reads `inbound_sessions.awaiting_confirmation` to determine what to execute.

**D5. Message simulator** (`src/routes/agent_routes.py` + `templates/agent/simulator.html`)

`GET /agent/simulator`

Admin page with:
- Property selector
- Role selector (tenant / caretaker / owner / admin)
- Unit selector (shown when role=tenant)
- Message text box
- Submit button

Response panel shows:
- Classified intent + confidence
- Extracted fields
- Action taken (what was written to DB)
- Response that would be sent

This is the primary testing tool for Phase D. Test every inbound scenario here before touching real SMS/WhatsApp.

---

### Phase E — Tenant Check-ins

**E1. `checkin_responses` table**

Migration `migrate_add_checkin_responses()`:

```sql
CREATE TABLE IF NOT EXISTS checkin_responses (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    property_id TEXT NOT NULL,
    period TEXT NOT NULL,          -- YYYY-MM
    numeric_response INTEGER,      -- 1 | 2 | 3
    free_text TEXT,
    classified_category TEXT,      -- maintenance | noise | security | water | general | null
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**E2. Check-in scheduler job** (`src/agent/coordinator.py`)

`send_monthly_checkins_job(property_id)` — runs on 1st of each month. For each active tenant with a phone and a unit: send check-in SMS via existing `send_sms()`. Stores in `messages` table with `message_type='checkin'`. Idempotent per period.

**E3. Response aggregator + sentiment summary** (`src/agent/briefings.py`)

`generate_sentiment_summary(conn, property_id, period) -> str`

Pulls all `checkin_responses` for the period, counts by numeric response, clusters free_text by category (LLM call), formats as a briefing paragraph for owner and caretaker.

---

### Phase F — WhatsApp Channel (add last, minimal new code)

By the time you reach Phase F, all agent logic, detection, briefings, parsing, and action handlers are fully built and tested via the simulator and SMS.

**F1. Africa's Talking inbound SMS webhook**

`POST /inbound/sms` — registered in `app.py`, exempt from admin auth.

Receives AT webhook payload → extracts `from` (phone) and `text` → writes to `inbound_messages` → returns 200 immediately. Background job picks up and processes.

**F2. Africa's Talking inbound WhatsApp webhook**

`POST /inbound/whatsapp` — same pattern. Different AT payload format but same write-to-table pattern.

**F3. WhatsApp adapter in delivery router**

Implement `_send_whatsapp(phone, body, template_id=None)` in `src/agent/router.py`. For template messages: use AT WhatsApp API with pre-approved template ID. For replies within 24-hour window: free-form text.

**F4. Pre-approve all outbound templates**

Submit to Meta via Africa's Talking dashboard before going live:

| Template key | Use | Example |
|---|---|---|
| `domi_charge_notification` | Monthly charges generated | "Hello {1}, {2} charges for Unit {3}: KES {4} due {5}." |
| `domi_payment_confirmed` | Payment verified | "{1} confirmed for Unit {2}. Balance: KES {3}." |
| `domi_reminder_5d` | 5-day rent reminder | "Reminder: KES {1} due in 5 days for Unit {2}. M-Pesa ref: {3}." |
| `domi_weekly_digest` | Owner weekly digest | "{1} — week of {2}: KES {3} verified. {4} units in arrears. Reply for details." |
| `domi_anomaly_alert` | Anomaly flagged | "{1} — {2}: {3}. Worth investigating." |
| `domi_caretaker_briefing` | Daily briefing | "Morning {1}. Arrears today: {2} units, KES {3}. Top: {4}." |
| `domi_checkin` | Tenant monthly check-in | "Quick check-in, Unit {1}: How is everything? Reply 1 (good) 2 (small issue) 3 (urgent)." |
| `domi_maintenance_update` | Issue resolved notice | "Update on your request, Unit {1}: {2}. Note: {3}." |

---

## Local Testing Strategy

**Before WhatsApp, every feature is testable via:**
1. Admin preview routes (`/agent/*/preview`) — verify digest and briefing content
2. Message simulator (`/agent/simulator`) — verify inbound parsing + actions
3. DB inspection — verify correct records written
4. SMS channel — test actual delivery (Africa's Talking sandbox → live)

**Test sequence per phase:**
- Phase A: trigger snapshot job manually, verify `balance_snapshots` rows appear
- Phase B: manipulate DB conditions, call detector, verify correct flags
- Phase C: open preview routes, verify text content is correct and concise
- Phase D: use simulator with every intent type, verify DB writes + response text
- Phase E: manually insert check-in responses, verify sentiment summary
- Phase F: test with a real phone via Africa's Talking SMS first, then WhatsApp

---

## Codebase Quick Reference

- **No ORM** — raw SQL with parameterized queries only
- **ID generation:** `generate_id('PREFIX')` from `src/database/db.py` → `'PREFIX-A1B2C3D4'`
- **DB access:** `with get_connection() as conn:` — auto-commits on success, rolls back on error
- **Migrations:** idempotent functions in `src/database/db.py`, called at startup in `app.py`. All new migrations must follow this pattern. Never drop or recreate tables with data — additive only.
- **SMS delivery:** `from src.messaging.delivery import send_sms` — `send_sms(recipients, message)` where `recipients` is `[{'phone': '...'}]`. Always wrap in `try/except Exception: pass` — SMS must never block main flows.
- **Owner notifications:** `from src.messaging.owner_notify import notify_property_owners`
- **LLM calls:** `from src.agent.llm import call_llm` — never import `anthropic` SDK elsewhere
- **Agent delivery:** `from src.agent.router import route_message` — never call `send_sms` from agent logic directly
- **Blueprints:** `tenant_bp`, `messaging_bp`, `report_bp`, `viewer_bp`, `caretaker_bp`, `agent_bp` (new)
- **Data-descriptive language:** no agency voice. "KES 312,000 verified against bank records" not "We collected KES 312,000"
- **SMS wording rule:** never expose bank statement or reconciliation mechanics. "payment confirmed" not "verified against bank records"

---

## After Completing New Work

1. Run `./venv/bin/python -c "from app import app; print('OK')"` — verify no import errors
2. Test locally: `./scripts/run_dev.sh` → http://localhost:5001
3. Update `ROADMAP.md` — mark completed items `[x]`, add new items
4. Update `CLAUDE.md` — add new tables, routes, patterns
5. Update `CURSOR_PLAN.md` (this file) — move completed items to "What Is Fully Built", update plan
6. **Commit your work** — commit after every build session with a clear message describing what was built. This is required, not optional. The git log is used by Claude to diagnose issues in the next review session.
7. Deploy: `export PATH="$HOME/.fly/bin:$PATH" && fly deploy`
