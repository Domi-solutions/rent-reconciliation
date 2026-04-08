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

- **Phase C complete**:
  - Implemented real digest/briefing generators in `src/agent/briefings.py`:
    - `generate_weekly_digest()`
    - `generate_caretaker_briefing()`
    - `generate_owner_briefing()`
    - `generate_admin_checklist()`
  - Added agent preview routes in `src/routes/agent_routes.py`:
    - `GET /agent/digest/preview/<property_id>`
    - `GET /agent/briefing/caretaker/<property_id>/preview`
    - `GET /agent/briefing/owner/<property_id>/preview`
    - `GET /agent/checklist/<property_id>/preview`
    - `POST /agent/trigger/<job_name>`
  - Registered `agent_bp` in `app.py`
  - Added `templates/agent/preview_text.html` for browser preview rendering
  - Updated `weekly_digest_job()` in `src/agent/coordinator.py` to call digest generation
  - Validation completed: app import check passes, no linter errors

### What was confirmed (do not re-implement)

- Existing owner/caretaker/tenant portals remain unchanged by this build
- Agent previews are admin-only routes under `/agent/*` and render via `base.html`

### Pick up next

1. Build inbound tables + webhooks: `inbound_messages`, `inbound_sessions`, `POST /inbound/sms`, `POST /inbound/whatsapp`
2. Add language preference column + first-contact language prompt flow
3. Build inbound intent classifier/actions simulator workflows (Phase D)

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
