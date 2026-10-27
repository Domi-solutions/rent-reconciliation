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

**Who:** Claude (Codex/ChatGPT no longer in use — Claude builds and reviews)
**Date:** 2026-10-27 · **Deployed:** v92 (all of `main` to `caf64c6`). GitHub has up to `dae0094`.

### Product direction agreed with stakeholders

- **Reactive, not proactive:** nothing texts on its own except payment receipts.
- **Paybill:** the law firm is applying for its own M-Pesa Paybill; account no. = house no.
- **Lease (Mowin):** rent due on/before the **5th of the same month, in advance**; water with rent;
  one-month deposit. Charges now fall due that way (`src/utils/due_dates.py`).
- **Onboarding is repeatable:** a lease opens a clean account; old debt moves only as an evidenced
  "brought forward". Owners see all conversations. Move-out: one month's notice, walk-outs handled.
- **No WhatsApp group** (owner declined, 2026-10-27). SMS + portal only.

### Shipped this session (v92)

- Production detection via `src/utils/runtime.is_production()` (Fly sets FLASK_ENV, not ENVIRONMENT —
  M-Pesa callbacks were accepting untokened posts; `/test` routes were live). Verified on prod.
- `AUTOMATIC_ALERTS_ENABLED` (default off) holds system-initiated alerts; `delete_payment` POST-only.
- **Lease intake** `/leases` (`src/leases/intake.py`) — see CLAUDE.md.
- Family Bank `*OPENING BALANCE*` parser fix; due dates per lease; `scripts/sms_test.py`.
- Prod secrets: `AT_SMS_ENABLED=false` (set), outreach + automatic alerts unset. SMS provider still AT sandbox.

### Reconciliation (in progress — nothing applied to prod yet)

- `scripts/reconcile_2026_10.py review|apply` — adds Family Bank May–Jun + 25 Sep–14 Oct, National
  Bank Mowin lines checked by eye (CSVs; scans were unreliable — totals off by 415k/1.45M), and
  May–Oct rent+service (504 charges, KES 4.13M). Review sheet sent to user
  (`data/output/reconciliation_review_2026-10.xlsx`, gitignored).
- **Blocked on:** Family Bank 1 Jul – 24 Sep statement (without it balances overstate by ~3 months),
  and the user's decisions on yellow rows (NCBA→4F?, GEOFFREY→4B?, Joel Mukwekwe Mutisya, etc.).
- Apply on prod after review: copy PDFs/CSVs/sheet to /data, run `apply` dry then `--execute` via
  `fly ssh console` (backup first). Note: the review sheet was built with next-month due dates;
  regenerate it before applying (charges now due the same month).

### Next (agreed order)

1. TextSMS: user runs `scripts/sms_test.py` locally; then set SMS_PROVIDER/TEXTSMS_* on Fly, flip
   AT_SMS_ENABLED=true, one-phone prod test.
2. Upload safety: statement upload supersedes ANY overlapping active statement org-wide (all banks) —
   scope to same bank/account and full overlap; add totals check for scanned uploads.
3. ~~Receipt SMS with remaining balance; broadcast to portal + SMS~~ (done, `66bc9e0`).
4. ~~">1 month behind" lists (admin + caretaker)~~ (done). 5. ~~Admin↔caretaker chat~~ (done). 6. Move-out (tenant
   notice + left-without-notice). 7. Owner comms + per-unit performance. 8. KCB parser (needs sample).

### Still open

Eight tenants without portal access; two unattributed ledger rows (6C water 1,200 Apr; 6A payment
3,600 Feb). No CSRF protection. `verify_payments` sends on GET. Daraja secrets on prod are sandbox —
tenant Pay button may show; harmless until tenants are invited (outreach off).

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
