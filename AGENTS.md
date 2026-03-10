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

**Who:** Claude Code
**Date:** 2026-03-09

### What was built

- **Named caretaker accounts** — `caretakers` table, migration in `db.py`, called at startup
- **Caretaker management UI** at `/caretakers` (admin) — create, edit, set password, delete; mirrors owners page; sidebar nav item
- **Caretaker auth upgraded** in `src/routes/caretaker_routes.py`:
  - Three-tier: DB accounts (name+password) → `CARETAKER_PASSWORD` env var → dev open
  - `before_request` verifies `caretaker_id` against DB on every request — deletion immediately revokes sessions
- **Caretaker name in header** — `base_caretaker.html` shows logged-in name next to logout
- **`sent_by` attribution** — caretaker broadcasts now use actual name from session, not generic string
- **Owner inbox bug fixed** — `property_notifications` SELECT was missing `sent_by`, `template_body`, `channel`, `recipient_count`; fixed in `viewer_routes.py`
- **Attribution badges** in `templates/viewer/notifications.html` — blue=Admin, amber=caretaker name, gray=System
- All docs updated: `CLAUDE.md`, `ROADMAP.md`, `CURSOR_PLAN.md`
- **Agent memory system set up** — `AGENTS.md` created as single entry point; `~/Desktop/Projects/projects.md` created as global project registry
- **Project folder moved** — from `~/Desktop/rentalManagement/rent-reconciliation/` to `~/Desktop/Projects/rent-reconciliation/`

### What was confirmed (do not re-implement)

- Tenant sort + arrears filter is **fully built** — `app.py:622–627`, `tenants.html:18–28`
- Old owner_messages records have NULL `sent_by` — unrecoverable, will show no badge (expected)

### Pick up next

1. **Admin dashboard days-to-due countdown** — caretaker dashboard has `next_due_date`/`days_to_due`/`recent_reminder` banner; verify admin `dashboard()` route and `templates/dashboard.html` have the same. Add if missing (no schema changes needed).
2. **`balance_snapshots` table** — daily per-unit balance snapshots, prerequisite for Phase 3. Full schema in `CURSOR_PLAN.md` Priority 2.
3. **Weekly digest** — after balance_snapshots. See `CURSOR_PLAN.md` Priority 3.

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
