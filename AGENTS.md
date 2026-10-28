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

**Who:** Claude — finished Codex's plain-language money labels across the tenant portal (Codex's handoff kept below).
**Date:** 2026-10-28 · **Deployment:** local preview only; no deployment.

### Phone menu, Bills page buttons, green payments (Claude, 2026-10-28)

- Tenant, owner and caretaker headers: on phones the tab row is a ☰ menu button labelled with the open page (`partials/nav_toggle.html` + `partials/nav_scroll.html`; styles in interface.css). Wider screens keep the tab row.
- Bills page: Pay this bill / Ask about this under each unpaid bill; on phones each bill stacks. Removed a first-version rule in style.css that hid every `.viewer-table` on phones (tenant Bills + Payments, owner Cash book were blank on phones).
- Statement payment rows are green end to end ("Payment received").
- Local preview: `ui-review` now runs `flask run --debug --no-debugger` with `SCHEDULER_ENABLED=false` — code reloads by itself; don't restart it (a restart sends the open tab to /demo).

### "Pay this bill" (Claude, 2026-10-28)

- Each unpaid bill (Still owed list on overview/statement/Pay, and each statement bill row) has **Pay this bill** beside **Ask about this**. The Pay page then shows "Paying for <bill>", fills in its amount, and ties the M-Pesa prompt or a pasted code to it; that bill is settled first, anything extra oldest first. **Make a payment** stays generic (oldest first). Paybill payments made from the M-Pesa menu can't carry a bill (the account number is the house); a pasted code can. 6 tests.

### Repairs folded into Messages (Claude, 2026-10-28)

- Tenant Repairs tab removed (it repeated the repair conversations). Messages shows each repair as Open / Fixed; pre-conversation repair requests under "Earlier repair requests"; old Repairs links redirect to Messages. The older repair form now texts staff like a new repair. Caretaker Issues tab and admin side unchanged.

### Message ticks + clickable card header (Claude, 2026-10-28)

- Every chat (tenant↔management, caretaker repairs, management↔caretaker, owner read-only views) shows grey ✓✓ when sent and blue ✓✓ once the other side has opened it. Uses the existing read timestamps; no new tables. Tests: `tests/test_message_ticks.py`.
- Tenant overview: the "Bills & payments" card header is the link to the statement; the button at the bottom of the card is gone.
- Staff texted when a tenant writes (LINCKS, 2026-10-28, revised same day): **no tenant choice** — repairs → management's setting on the Tenant inbox page (default the caretaker); money + "something else" → management only (private; e.g. a complaint about the caretaker); phones per management account on /team; one text until staff reply; **24-hour safety net** texts management about an unanswered caretaker-only repair (`repair_escalation_job`). `src/messaging/staff_alerts.py`, `migrate_add_staff_alerts` + `migrate_add_alert_routing`, tests `tests/test_staff_alerts.py`. Live SMS still off, so nothing reaches a phone yet.

### Tenant Messages page, calmer (Claude, 2026-10-28)

- Order: notices from management (latest 3, older ones folded), then "Your conversations" (whole rows tappable), then one outgoing-chat-style bubble **Write to management** that opens the three topics. The permanence note is one small line inside it. Styles in `static/tenant.css`.

### Pasted M-Pesa codes can't be used to take a neighbour's payment (Claude, 2026-10-28)

- Gap found by LINCKS's question about the Pay page: a tenant pasting someone else's M-Pesa code (seen on their phone) was credited with it if the bank line was still unassigned, and a code already on another account showed "Confirmed ✓". Now `claim_owner.sender_is_tenant()` must pass (name / phone / past sender) or the claim is held for management with a reason the tenant and staff both see. See CLAUDE.md Database rules. 3 tests; they fail without the fix.

### "Still owed" opens into its bills (Claude, 2026-10-28)

- Tapping **Still owed** (tenant overview, statement, Pay) lists the exact unpaid bills, oldest first, ending in a **Make a payment** button (Pay page `#pay-now`: the M-Pesa form, or the paybill steps when STK is off): which bill, KES left on it, due date, **Late** once past due, and how much is already paid. Built by `tenant_documents.unpaid_bills(rows, balance)` from the statement rows; rendered by `templates/tenant/partials/owed_breakdown.html`; styles in `static/tenant.css`. If a confirmed payment is not yet set against a bill, the list shows it as a separate line so the total still matches. Tests: `tests/test_unpaid_bills.py`.
- Demo: second returning tenant who owes — David Kiprop, B2, `0700000108` / PIN `1234` (`LOGINS['tenant_owing']`, on the /demo page). Added to the local `/tmp/domi-ui-review.db` too (backup `/tmp/domi-ui-review.before-owing-login.db`).
- Water line now: "Water you used in September 2026, applied to your October 2026 bill"; Bills help says the same.

### Plain money words, finished (Claude, 2026-10-28)

- Branch `ui-review-fixes`, local only, nothing deployed. Same words everywhere a tenant sees money: **Still owed** (red), **Extra paid** (green), **Nothing owed** at zero; "bills" not "charges"; "confirmed" not "verified/matched to the bank record".
- Invoice (owed/extra paid from before the month; end-of-month line), receipt ("This payment covered", "Extra paid — kept for your next bills", "Your account today: …"), Pay (account box + payment statuses "Confirmed ✓" / "Not confirmed yet"), Payments, Bills page (tab renamed from Charges), tenancies dashboard, dispute box labels (stored subject values unchanged), and tenant guides EN + Kiswahili. `period_label('ARREARS')` → "Earlier unpaid bills" (tenant-side only; admin/owner `charge_label` unchanged). Amounts use `money_amount`.
- No arithmetic, allocation or stored value changed. 63 tests OK; pages rendered for an owing, an extra-paid and a zero-balance tenant (copy DB) and checked at 375px.
- Preview: `.claude/launch.json` → `ui-review` (port 5051, `/tmp/domi-ui-review.db`, SMS/outreach/scheduler off). Restarted so Python changes load; same logins.

### Handoff to Claude — user requested stopping here

- LINCKS asked to save and continue with Claude. No further feature work should be assumed complete beyond this saved state. Checkout: `/Users/lincksmorara/Desktop/Projects/rent-reconciliation`, branch `ui-review-fixes`.
- Latest request: remove finance jargon. **Still owed** in red, **Extra paid** in green, **Nothing owed** at zero. Text labels remain beside colours. This is implemented in the compact statement, tenant overview and account coverage partial. Internal numeric fields/CSS class `credit` remain unchanged.
- **Remaining for Claude:** apply the same terms/colours to tenant invoices, receipts, Pay and tenancies dashboard; these still contain older credit/balance labels. Update remaining tenant help wording as needed. Do not alter ledger arithmetic, allocation or recorded amounts. Native print dialog was not exercised; review the print expansion/restore handler.
- Compact rows and water/payment disclosure were browser-verified before the final label change, including keyboard operation, invoice/receipt access and 390px overflow. Tenant integration tests and rendered-page checks are recorded below. No real SMS sent; no deployment.
- Local preview is still running at http://127.0.0.1:5051 using `/tmp/domi-ui-review.db`; scheduler, SMS and outreach off. SMS remains blocked by local disabled/sandbox configuration, not a claimed fixed handset flow.

### Compact statement this session

- LINCKS requested date, bill/payment name, account movement and balance after at first glance, with the rest tap-to-expand. `tenant/statement.html` now uses native details/summary rows: bills add (+), payments subtract (−), extra paid labelled explicitly; money still owed is red, extra paid green and zero says Nothing owed. Readings, due dates, paid/remaining amounts, payment references/allocations, receipts and Ask about this are inside each row.
- Account coverage and monthly invoices share a collapsed control. The statement help box starts closed via an opt-in flag in the shared guide partial; other pages keep existing defaults. Updated English/Kiswahili statement guidance.
- Print expands all entry details for printing and restores the previous open/closed state afterwards. Amounts, ledger order and allocations remain unchanged.
- Validation: existing 8 tenant integration tests pass; browser checks confirm water hidden initially, tap-to-open water, keyboard payment expansion, receipt/allocation and invoice access, and no horizontal page overflow at 390px. All 156 templates compile and 90 page-render smoke checks pass.
- Local-only, same demo database/SMS-off configuration. Next: Claude continues the plain-language money labels with LINCKS. No deployment authorised.

### Latest wording approval

- LINCKS approved “Bills for July–October 2026”, “Payments confirmed”, “Amount still owed”, with “Latest confirmed payment” below the totals. Applied that wording to the tenant overview and simplified the statement coverage label. Single-month and cross-year ranges retain explicit years; brought-forward balances remain separately identified. Amounts and payment allocation are unchanged.
- Validated the existing coverage integration check and the rendered tenant preview. SMS delivery remains pending live provider credentials as recorded below.

### Tenant feedback addressed this session

- Kept the restored visual direction and existing review fixes (`7270f53`) on branch `ui-review-fixes`. Added tenant-only `static/tenant.css`: prominent labelled notice cards, green accent, preview text and read links above the overview balance; full notices retain anchors on Messages. Only the 30 rendered notices are marked read, rather than every historical notice.
- Balance overview and statement now name the first/latest recorded charge months and latest recorded payment date via `account_coverage()`. Whole-shilling amounts stay compact; cents are retained when present. Pending payment wording no longer exposes internal matching mechanics.
- `src/utils/water_breakdown.py` reads tenant/property/unit/billing-period-scoped meter evidence. Statement, invoice and Charges show previous/new readings, usage, snapshotted rate and calculation. Missing, ambiguous or inconsistent readings are explicitly unavailable; no values inferred from a bill/current tariff. Balances/allocations unchanged.
- Added fictional meter evidence to the Kijani demo seed (KES 100/unit) and enriched the existing `/tmp/domi-ui-review.db` without resetting accounts or changing billed amounts. Before-sample backup: `/tmp/domi-ui-before-meter-examples.db`. Never use this demo helper on real properties.
- SMS: LINCKS confirmed the SMS never arrived. Local outbox proves `AT_SMS_ENABLED=false` simulated the attempt; local .env has Africa's Talking sandbox credentials and no TextSMS credentials. No real SMS/provider call was made. Demo code page now says delivery is unavailable when appropriate, preserves failed status during resend throttling, and clears stale codes when changing phone identity. Code verification/PIN setup passes offline integration tests. **Actual handset delivery remains pending live provider configuration; do not claim it is fixed.**
- Validation: 63 unittest tests pass (8 new tenant tests); 156 Jinja templates compile, 90 rendered GET smoke checks pass. Browser verified tenant overview/notices, statement and invoice, including 390px layout and correct calculation. Server restarted with the same demo DB, SMS/outreach/scheduler off.
- Next: LINCKS reviews this focused tenant pass; configure a live SMS provider before a handset-delivery test. No deployment authorised.

### Latest user decision and restored UI

- LINCKS rejected the MEDVi-inspired refinement (`afbb3bc`) and requested the design immediately before it. Reverted that commit: UI files match `186e518` exactly. Retained the earlier readable typography, visible edit/click controls and distinct conversation surfaces. No deployment or data changes.
- Next: let LINCKS review this restored baseline before making further visual changes.

### Restored cross-page UI baseline

- LINCKS authorised extending the Units trial across this checkout, then user review and Claude feedback via the hub. Checkout is `/Users/lincksmorara/Desktop/Projects/rent-reconciliation`, current branch `ui-review-fixes` (the primary checkout, not a newly created managed worktree).
- `theme.css` promotes system typography, 500 body weight, restrained corners and quieter shadows while preserving forest/sage/emerald/paper and semantic status colours. Secondary ink/borders have stronger contrast. Removed Google font dependency.
- New `interface.css` is loaded last by all 20 document roots: admin, tenant, caretaker, owner/viewer, platform, auth, demo and error pages. Local hardcoded shapes/typography were aligned; icons share `partials/icons.html`. Units-specific layout stays in `units.css` (`units-screen`).
- Clearer actions: underlined content links, bounded controls, focus rings, linked expense/message subjects instead of invisible clickable rows, explicit outbox expansion button, accessible help-language controls. Existing forms/routes preserved.
- Latest user feedback: messages and textboxes blended into surrounding content. New `chat.css` gives all conversation views a muted green canvas, white incoming and light green outgoing bubbles; labelled reply/photo controls and tinted message inputs. Includes admin/tenant/caretaker threads and read-only owner views. Caretaker report helper still works with `reply-body`.
- Validation: all 152 Jinja templates compile; 90 rendered GET pages pass smoke checks (plus tenant conversation template fixture; Sarah's demo conversation has no activated tenant login); existing unittest suite passes 55 tests. Representative browser checks cover admin/Units, payments/expenses, auth, tenant messages, caretaker dashboard/management and owner portfolio/property view, including 390px checks. No real messages were sent by Codex; SMS/outreach off.
- Local preview remains http://127.0.0.1:5051/ (role chooser `/demo`), using `/tmp/domi-ui-review.db`. Credentials in CLAUDE.md. Local platform password is `demo`. Scheduler off; template auto-reload explicitly enabled on Jinja environment. Server left running for review.
- Hub thread **#63** records this Domi work and will carry the Claude review handoff. The hub's attached code snapshot is stale and belongs to Second Mind: do not use its snapshot or factory for this Domi review. Claude should inspect this local path, compare from `d94703d` (rollout) or `82728c7` (includes Units), and report UX/regression findings without deploying.
- Next: user feedback and Claude review, then refine. Operational priorities and reconciliation blockers below remain outstanding.

### Previous implementation handoff

Previously recorded deployment: v92 (all of `main` to `caf64c6`); GitHub up to `dae0094`, with later v99+ work noted below.

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

### Also shipped 2026-10-27 (live v99+)

Follow-up lists, receipts with balance, admin↔caretaker chat, new look (theme.css), domi.co.ke
(APP_BASE_URL), money out (expenses + cash book; no management fee in system).

### Demo site — https://domi-demo.fly.dev (Fly app `domi-demo`, fly.demo.toml)

Made-up Kijani Court (src/demo/seed.py). /demo start page lists logins; Reset button rebuilds.
Deploy: `fly deploy -c fly.demo.toml -a domi-demo --ha=false`. DEMO_MODE refuses production.

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
