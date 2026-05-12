# AGENTS.md — AI Agent Entry Point

> **Start here.** Read this file first, every session, regardless of which AI tool you are.
> After completing work, overwrite the "Last Session" section before handing off.

---

## How to use this file

1. Read this file top to bottom (~2 min)
2. Read `CURSOR_PATTERNS.md` — failure log, read before writing any code
3. Read `.agent/schema.yaml` for precise table/column lookups
4. Read `.agent/routes.yaml` for the full route registry
5. Read `CLAUDE.md` for technical depth (patterns, conventions, business rules)
6. Start work from "Pick up next" below

---

## Project in one paragraph

Domi is a **property fintech platform** for Kenya. Tenants pay rent via M-Pesa STK Push or card directly through Domi. Domi holds the funds and disburses to landlords net of management fee. The fee is embedded in the disbursement spread — not a visible line item. This model makes Domi infrastructure (low churn, high switching cost) rather than software (easy to cancel). Stack: Flask 3 + SQLite + Bootstrap 5, deployed on Fly.io (Johannesburg). SMS via Africa's Talking (sandbox). 1 property live: Mowin Apartments, 44 units.

**Hard rule:** data-descriptive language only — "KES 312,000 verified against bank records", never "We collected KES 312,000". No exceptions. See `ROADMAP.md` for the full table.

---

## Last Session

**Who:** Claude Code (payments feed, demo data, org-scoped statements, parse error logging, parser registry)
**Date:** 2026-05-12

### What was completed this session

**Payments feed UX:**
- "Collected This Month" card on dashboard is now clickable → `/review?tab=confirmed`
- Confirmed tab redesigned as scrolling M-Pesa-style feed (amount bold, tenant/unit/ref/badge per row)
- "Manual" badge renamed to "Bank"

**Demo data:**
- `scripts/seed_demo_payments.py` — 132 rent charges, 12 statements, 56 payments, realistic scenarios (clean payers, late, missed month, partial, catchup, underpay) across Agency Alfa and Agency Beta

**Org-scoped bank statements:**
- `bank_statements` now has `org_id` and `bank_format` columns (migration: `migrate_add_org_scoped_statements`)
- Upload/reparse/verify/review routes all query by `org_id`, not `property_id`
- One statement upload covers all properties in the org

**Parse error logging (real, not stub):**
- `statement_parse_errors` table — every parse failure logged with org_id, statement_id, filename, file_path, bank_format, error_type, error_message, raw_text, page_number
- Parse Errors tab in review.html shows real data from DB
- Platform dashboard: 4th stat card (7d parse error count, amber)
- `GET /platform/parse-errors` — all errors across all orgs, org filter, View PDF button
- `GET /platform/statements/<id>/pdf` — serves stored PDF via `send_file()`
- `templates/platform/parse_errors.html` — new full-page table

**Multi-bank parser registry:**
- `src/parsers/banks/registry.py` — `BANK_DISPLAY_NAMES`, `bank_display_name()`, `SUPPORTED_FORMATS`
- `detect_bank_statement_format()` now returns `'unknown'` instead of silently falling back to `'cooperative'`
- Unknown format → `format_unknown` parse error logged → statement marked `parse_failed`
- Adding a new bank = one file in `src/parsers/banks/` + one entry in `registry.py` + one detection branch in `pdf_parser.py`

**Bug fix:**
- Fixed `AttributeError: 'sqlite3.Row' object has no attribute 'get'` — 6 locations in `app.py`; `.get()` replaced with bracket access + conditional throughout

### Pick up next

1. **Full end-to-end test run:** upload a real bank statement for one of the demo properties, verify claims auto-match, check parse errors surface correctly, confirm payments show in the feed.
2. **Phase 4 (The Conversation):** LLM intent classifier, tenant/caretaker/owner inbound handlers, bilingual responses — see `ROADMAP.md`
3. **Phase 5 (The Coordinator):** admin task feed, anomaly detection, caretaker morning briefing
4. **Phase H:** WhatsApp live channel (gated on Meta approval — apply now)
5. When ready to flip AT_USERNAME to live: read `memory/project_go_live_messaging_checklist.md` first

### Key files changed this session
- `app.py` — upload_statement, manage_statements, reparse_statement, verify_payments, review, dashboard routes
- `src/database/db.py` — migrate_add_org_scoped_statements, migrate_add_statement_parse_errors
- `src/parsers/pdf_parser.py` — detect_bank_statement_format returns 'unknown'; parse_bank_statement fails fast on unknown
- `src/parsers/banks/__init__.py`, `src/parsers/banks/registry.py` — new bank format registry
- `src/routes/platform_routes.py` — parse_errors_view, download_statement_pdf, dashboard parse error count
- `templates/platform/parse_errors.html` — new
- `templates/platform/dashboard.html` — 4-card layout with parse errors
- `templates/platform/base_platform.html` — Parse Errors nav link
- `templates/review.html` — confirmed tab feed UX, real parse errors tab
- `templates/statements.html` — bank format badge, parse error count badge, parse_failed status
- `scripts/seed_demo_payments.py` — new demo data seeder

---

## Key file map

| File | Purpose | When to read |
|---|---|---|
| `AGENTS.md` | This file — entry point + latest handoff | Every session, first |
| `CURSOR_PATTERNS.md` | Failure log — known mistakes from prior builds | Before writing any code |
| `.agent/schema.yaml` | Ground truth for DB tables and columns | Before writing any query or migration |
| `.agent/routes.yaml` | Ground truth for all routes and blueprints | Before adding any route |
| `.agent/jobs.yaml` | Ground truth for all scheduled jobs | Before adding any job |
| `.agent/intents.yaml` | Intent classification set | Before adding any intent handler |
| `.agent/env.yaml` | All environment variables | Before using any config value |
| `CLAUDE.md` | Full technical reference — patterns, rules, architecture | For context and conventions |
| `ROADMAP.md` | Product vision, phase checklist, language rules | For product decisions |
| `CURSOR_PLAN.md` | Active build plan — what to build and how | The primary build guide |
| `README.md` | How to run and deploy | Operational reference |

---

## Quick reference

```python
# DB access
from src.database.db import get_connection, generate_id
with get_connection() as conn:
    conn.execute("INSERT INTO ...", (val1, val2))

# FIFO payment allocation
from src.database.db import allocate_payment
with get_connection() as conn:
    allocate_payment(conn, payment_id, unit_id, amount)

# SMS — always wrap, never block main flow
from src.messaging.delivery import send_sms
try:
    send_sms([{'phone': '+254700000000'}], "message")
except Exception:
    pass

# Owner notification
from src.messaging.owner_notify import notify_property_owners
notify_property_owners(conn, property_id, message, sent_by='Admin')

# Agent delivery (never call send_sms from agent logic)
from src.agent.router import route_message
route_message({'recipient_phone': ..., 'recipient_role': ..., 'property_id': ..., 'message_type': ..., 'body': ...})

# LLM (never import anthropic SDK outside llm.py)
from src.agent.llm import call_llm
result = call_llm(prompt, model='fast')  # 'fast'=haiku, 'smart'=sonnet
```

```bash
# Local dev (no passwords, uses data/dev.db)
./scripts/run_dev.sh        # → http://localhost:5001

# Deploy
export PATH="$HOME/.fly/bin:$PATH" && fly deploy

# Verify no import errors
./venv/bin/python -c "from app import app; print('OK')"

# Pull production DB for local dev
./scripts/download_prod_db.sh
```

---

## Protocol

- **Update this file** at the end of every session — overwrite "Last Session" with what you did and what's next
- **Commit after every session** — `git add . && git commit -m "describe work done"`. The git log is used for diagnosis.
- **Never use agency voice** in any user-facing string
- **Additive migrations only** — `CREATE TABLE IF NOT EXISTS`, never drop/recreate tables with data
- **Raw SQL only** — no ORM
- **Update YAML registries** when adding tables, routes, or jobs — an unregistered component is invisible
