# AGENTS.md — AI Agent Entry Point

> **Start here.** Read this file first, every session, regardless of which AI tool you are.
> After completing work, update the "Last Session" section below before handing off.

---

## How to use this file

1. Read this file top to bottom (~2 min)
2. Then read `CLAUDE.md` for full technical depth (schema, routes, patterns, auth)
3. Check `ROADMAP.md` if you need product vision or phase status
4. Start work from "Pick up next" below

---

## Project in one paragraph

Financial intelligence layer for a Kenyan property management agency. Flask 3 + SQLite + Bootstrap 5, deployed on Fly.io (Johannesburg). Parses M-Pesa bank statements, matches tenant payment claims, tracks charges (rent/service/water), allocates payments FIFO, and surfaces live financial state to landlords, caretakers, and tenants via separate portals. SMS delivery via Africa's Talking (sandbox now, production pending). 1 property live: Mowin Apartments, 44 units.

**Hard rule:** data-descriptive language only — "KES 312,000 verified against bank records", never "We collected KES 312,000". See `ROADMAP.md` for full table.

---

## Last Session

**Who:** Cursor Agent
**Date:** 2026-04-08

### What was built

- **Phase E complete**:
  - Added E1 migration in `src/database/db.py`:
    - `migrate_add_checkin_responses()`
  - Wired migration in `app.py` startup sequence
  - Added monthly scheduler trigger in `app.py`:
    - `monthly_checkins_job` on day 1 at 9:00
  - Implemented E2 check-in jobs in `src/agent/coordinator.py`:
    - `send_monthly_checkins_job(property_id)`
    - `monthly_checkins_job()`
    - Idempotent per tenant + period
  - Kept agent delivery abstraction intact:
    - check-ins use `route_message()` (no direct `send_sms` import in agent module)
  - Implemented E3 sentiment aggregator in `src/agent/briefings.py`:
    - `generate_sentiment_summary(conn, property_id, period)`
  - Ran smoke test:
    - monthly check-in messages inserted as `message_type='checkin'`
    - sentiment summary generated from `checkin_responses`

### What was confirmed (do not re-implement)

- Existing owner/caretaker/tenant portals remain unchanged by this build
- Agent previews and simulator are admin-only routes under `/agent/*` and render via `base.html`
- Monthly check-ins are now sent once per tenant per period

### Pick up next

1. Build inbound webhooks: `POST /inbound/sms`, `POST /inbound/whatsapp` (write inbound row, return 200)
2. Add language preference column + first-contact language prompt flow
3. Begin Phase F WhatsApp adapter and template-based outbound path

---

## Key file map

| File | Purpose | Maintained by |
|---|---|---|
| `AGENTS.md` | This file — entry point + latest handoff | All agents (update after each session) |
| `CLAUDE.md` | Full technical reference — schema, routes, auth, patterns | Claude |
| `ROADMAP.md` | Product vision, design rules, phase checklist | All agents |
| `CURSOR_PLAN.md` | Work-in-progress plans, implementation notes | Cursor |
| `README.md` | How to run and deploy | Any |

---

## Quick reference (copy these when you need them)

```python
# DB access
from src.database.db import get_connection, generate_id
with get_connection() as conn:
    conn.execute("INSERT INTO ...", (val1, val2))

# SMS — always wrap, never block main flow
from src.messaging.delivery import send_sms
try:
    send_sms([{'phone': '+254700000000'}], "message")
except Exception:
    pass

# Owner notification
from src.messaging.owner_notify import notify_property_owners
notify_property_owners(conn, property_id, message, sent_by='Admin')
```

```bash
# Local dev
./scripts/run_dev.sh        # → http://localhost:5001

# Deploy
export PATH="$HOME/.fly/bin:$PATH" && fly deploy

# Verify no import errors
./venv/bin/python -c "from app import app; print('OK')"
```

---

## Protocol for agents

- **Update this file** at the end of every session — overwrite "Last Session" with what you did and what's next
- **Git backup after every session** — from the project root run: `git add . && git commit -m "describe work done" && git push`. This keeps the owner's code safe and resumable from any device.
- **Never use agency voice** in any user-facing string
- **Additive migrations only** — `CREATE TABLE IF NOT EXISTS`, never drop/recreate tables with data
- **Raw SQL only** — no ORM
