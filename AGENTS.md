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

**Who:** Claude Code (dormancy-readiness audit + hardening — Lincks leaving for a 15-month masters in Arizona, Domi needs to run unattended)
**Date:** 2026-08-05

### Context: why this session happened

Lincks ran a full audit against a "Domi must survive 15 months unattended, one paying landlord
client (Mowin Apartments), problems must reach Lincks proactively" spec. Audit found: zero working
alert channel (SMTP unconfigured despite code existing for it), no dead-man's-switch for scheduled
jobs, backups manual/ad hoc/stale, restart behavior never actually tested, secrets baked into the
Docker image, no runbook. This session closed most of the 🔴 items. Full audit findings live in
this conversation's history, not repeated here — this section is the "what changed" record.

### What was completed this session

**Alerting foundation (was: half-built, uncommitted, undeployed — found and shipped):**
- Discovered `/health` endpoint, global exception handler, `maintainer_digest_job`, and
  `src/messaging/email.py` already existed in the working tree but were never committed or
  deployed (5+ weeks stale on the laptop). Committed, configured Gmail SMTP as a real Fly secret,
  deployed, verified with a real test email delivered.
- `MAINTAINER_EMAIL` = `lincksmorara@gmail.com` — this is where every alert below lands.

**Dead-man's-switch for scheduled jobs (`src/agent/heartbeat.py`, new):**
- Every APScheduler job pings a fixed healthchecks.io URL on success/failure via a scheduler-level
  `EVENT_JOB_EXECUTED`/`EVENT_JOB_ERROR` listener in `app.py` — covers all jobs generically, no
  per-job wiring needed for new jobs beyond adding to `PING_URLS`.
- Job exceptions also trigger an immediate email independent of the heartbeat grace period.
- healthchecks.io project: `lincksmorara@gmail.com`. 9 checks total (8 original jobs +
  `backup_job` added later this session). Verified end-to-end with a real fail/success cycle —
  confirmed alert email received, not just assumed.
- **Known bug found while wiring this, not yet fixed:** the `hour=` values in `app.py`'s
  `scheduler.add_job()` calls run as UTC, not EAT, despite comments (in `app.py` and
  `.agent/jobs.yaml`) claiming EAT. See the CAUTION note at the top of `.agent/jobs.yaml`.

**Automated off-server backups (`src/agent/backup.py`, new):**
- Daily (02:00 UTC) snapshot via `sqlite3`'s own backup API (not a raw file copy — DB is in WAL
  mode under live writes), uploaded to Cloudflare R2 (`domi-backups` bucket), 30-day rolling
  retention. Wired into the same heartbeat system.
- Restore actually tested end-to-end: downloaded a real backup, ran `PRAGMA integrity_check`
  (passed) and verified row counts — not assumed from a successful upload.
- `boto3` added to `requirements.txt` for R2's S3-compatible API.

**Restart/reboot verified with real numbers (not assumed):**
- SIGKILL to gunicorn master: full recovery (new PIDs, all migrations re-run) in ~10s.
- Full `fly machine restart`: VM relaunch → volume remount → gunicorn listening in ~3s, fully
  ready by ~10s. Both fully automatic, no intervention needed.

**Security fix: `.env` was baked into the production Docker image:**
- `.dockerignore` didn't exclude `.env` (or `backups/`) — every image pushed to
  `registry.fly.io/rent-reconciliation` contained all 12 secret keys in plaintext, a wider and
  longer-lived exposure than live env vars (persists in old image layers, not just current state).
- `ANTHROPIC_API_KEY` was the one var that existed *only* in the baked `.env` with no
  corresponding Fly secret — migrated it to a real Fly secret first so this fix didn't silently
  break LLM features, then excluded `.env`/`backups/` and redeployed. Verified `.env` is now
  genuinely absent from the running container.

**`RUNBOOK.md` (new, top-level):**
- What alerts you and how, a safe hotfix deploy procedure (backup-before-deploy is now a documented
  step, not folklore), the verified recovery timings above, and an honest monthly ~20-minute
  checklist that flags what's *not* built yet (reconciliation-mismatch detection, receipt resend,
  landlord troubleshooting guide) instead of implying completeness.

**Git/ops hygiene:**
- Repo had 20 unpushed commits + 5 uncommitted files sitting on the laptop at session start — all
  committed and pushed. Remote moved from `LincksMorara/rent-reconciliation` to the
  `Domi-solutions` org; local remote URL updated to match.

### Pick up next

From the dormancy-readiness audit, still open:

1. **3.4 — Daraja/Safaricom account ownership documentation.** Needs information from Lincks
   (which account, who controls the tied phone/SIM, cert/passkey expiry), not code work.
2. **5.1 — Receipt generation/resend.** No "receipt" concept exists anywhere in the codebase yet —
   owners can see payment status/arrears but can't download or resend a receipt.
3. **5.3 — Landlord-facing "something looks wrong" guide.** The tenant portal already has an
   equivalent pattern (dispute form, dynamic caretaker phone) — the owner/viewer portal doesn't.
4. **Informational-only, no code needed:** 3.1 (Fly billing/domain/SSL renewal dates — needs a
   direct answer, not derivable from the repo), 3.6 (password manager for secrets — currently only
   in Fly's store + the laptop's `.env`), 2.2 (the audit's premise of "two previously discovered
   STK push race conditions" could not be substantiated anywhere in this repo's history — needs
   clarification on what that refers to before writing a regression test).
5. **Deferred (🟡/🟢) from the audit, not urgent:** structured/retained logging (1.7), decimal-safe
   money types + timezone audit (2.7), `KNOWN_ISSUES.md` (6.3), PII redaction in error alerts/logs
   (7.1 — a WhatsApp stub in `src/agent/router.py:68` prints tenant phone numbers to stdout).

### Key files changed this session
- `src/agent/heartbeat.py` — new, dead-man's-switch pings
- `src/agent/backup.py` — new, R2 backup logic
- `src/agent/coordinator.py` — `backup_job()` wrapper added
- `app.py` — `/health`, exception handler, `maintainer_digest_job` + `backup_job` scheduled,
  heartbeat listener registered
- `src/agent/maintainer.py` — already existed uncommitted, now live (digest + error alert emails)
- `.dockerignore` — excludes `.env`, `backups/`
- `.agent/env.yaml`, `.agent/jobs.yaml` — documented all of the above, including the UTC/EAT bug
- `requirements.txt` — added `boto3`
- `RUNBOOK.md` — new
- Fly secrets added/changed: `SMTP_HOST/PORT/USER/PASSWORD/FROM`, `MAINTAINER_EMAIL`,
  `R2_ENDPOINT_URL/ACCESS_KEY_ID/SECRET_ACCESS_KEY/BUCKET_NAME`, `ANTHROPIC_API_KEY` (properly
  migrated from the baked `.env`)

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
