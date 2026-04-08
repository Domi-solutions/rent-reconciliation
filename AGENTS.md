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

- **Phase A complete**:
  - Added `migrate_add_balance_snapshots()` in `src/database/db.py`
  - Wired migration call in `app.py` startup sequence
  - Added APScheduler in `app.py` with four jobs:
    - `daily_snapshot_job` (1am)
    - `anomaly_check_job` (6am)
    - `morning_briefings_job` (7am)
    - `weekly_digest_job` (Mon 8am)
  - Added safe scheduler startup guard for Flask reloader mode + `atexit` shutdown
- **Phase A module skeleton complete**:
  - Created `src/agent/` with:
    - `__init__.py`
    - `coordinator.py`
    - `detector.py`
    - `briefings.py`
    - `inbound.py`
    - `responder.py`
    - `router.py`
    - `llm.py`
    - `state.py`
- **Phase A delivery router complete**:
  - `route_message()` now writes portal messages to `owner_messages`/`messages`
  - SMS adapter wired via `src.messaging.delivery.send_sms`
  - WhatsApp adapter left as explicit stub
- **Phase B complete** in `src/agent/detector.py`:
  - `check_pending_tasks(conn, property_id)`
  - `detect_anomalies(conn, property_id)`
  - `detect_followups(conn, property_id)`
- Added dependencies: `apscheduler`, `anthropic`
- Updated docs: `ROADMAP.md`, `CLAUDE.md`, `CURSOR_PLAN.md`

### What was confirmed (do not re-implement)

- Existing owner/caretaker/tenant portals remain unchanged by this build
- New `src/agent/*` layer is additive and not yet wired to user-facing routes

### Pick up next

1. Build inbound tables + webhooks: `inbound_messages`, `inbound_sessions`, `POST /inbound/sms`, `POST /inbound/whatsapp`
2. Implement weekly digest generator in `src/agent/briefings.py` and add `/agent/*` preview routes
3. Add language preference column + first-contact language prompt flow

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
