# Domi — Product Roadmap

> **For AI agents:** Read this file to understand the product vision, what's built, and what's next.
> After completing work, update the status checkboxes and "Current Implementation Status" section below.
> For technical patterns, database schema, and code conventions, see `CLAUDE.md`.

**Product name:** Domi (derived from *domus*, Latin for home). Warm, neutral, non-intrusive — sits on top of any property brand. "This building runs on Domi."
**Repo/deploy name:** `rent-reconciliation` (unchanged)
**Market:** Kenya → East Africa

---

## Vision

Domi is a **property intelligence platform** — an invisible, intelligent layer across residential properties that handles rent reconciliation, financial reporting, maintenance, and communication. It does not report what an agency did — it surfaces what the data shows, routes it to the right person at the right time, and captures qualitative signals that would otherwise disappear into WhatsApp conversations.

**The insight is the product. The push is the delivery. The conversation is the data.**

### The Core Shift: Pull → Push

The original product required users to come to it. Lazy users (the expected baseline) got zero value. The new model delivers value TO users regardless of their behavior — meeting them in the channel they already use (WhatsApp/SMS) without requiring them to open a dashboard.

A landlord who reads a 30-second WhatsApp digest outperforms one using spreadsheets and gut feel — not because they changed behavior, but because the system does the analytical work automatically.

## Design Rule: Data-Descriptive Language

**HARD RULE — applies to all user-facing strings, templates, reports, and future automated messages.**

Every label, heading, and message uses data-descriptive voice. Never agency voice.

| DO (data voice) | DON'T (agency voice) |
|---|---|
| "KES 312,000 verified against bank records" | "We collected KES 312,000" |
| "Unit A7 — KES 42,000 outstanding, 2 months" | "We are following up on Unit A7" |
| "Unit B3 — vacant 14 days, now occupied" | "We filled the vacancy in 14 days" |
| "Unit C4 water charge: KES 4,200 — 43% above 3-month average" | "We noticed water usage increased" |
| "3 claims pending verification for 5+ days" | "3 unverified payments" |

The app makes no claims about actions taken. It reports what the data knows.

## Go-To-Market (locked 2026-05-19)

**Primary customer: Property Management Organisations (PMOs).** Agents, administrators, firms — people who manage properties on behalf of owners. They sign up, they pay (via transaction fees), they onboard their portfolio.

**Secondary beneficiary: Property Owners.** They don't sign up themselves. They get invited when a PMO adds them. Their experience must be trustworthy and beautiful enough that they mandate Domi to any new manager they hire and recommend it to other owners. Owners are the word-of-mouth engine.

**Sales motion:** Recruit agents/salespeople who bring PMOs onto the platform. Agent earns a share of the transaction fee revenue from every PMO they onboard — forever. This aligns incentives: agent only earns if the PMO stays active.

**Target:** 1,000 properties at Mowin scale (40–120 units, KES 15K–25K avg rent). At 1% platform fee on KES 800K/month per property → KES 8M/month at 1,000 properties.

## Three Audiences

1. **PMO admin** (PRIMARY): Manages day-to-day operations. Uploads statements, generates charges, manages tenants. Domi makes them look professional and saves hours of WhatsApp coordination.
2. **Property Owner** (ADVOCATE): Sees their asset clearly. Trusts the PMO more because Domi gives them an independent view. Tells other owners to use Domi.
3. **Tenant** (END USER): Pays rent, sees their balance and history. No friction, no WhatsApp back-and-forth.

---

## Product Layers

### Layer 0: Core Reconciliation Engine
PDF parsing, M-Pesa SMS matching, FIFO allocation, exports, multi-property, auth. **Status: COMPLETE.**

---

### Layer 1: The Pulse (Real-Time Dashboard)
**Question it answers:** "What is the current financial state of my asset?"

Every number is a live data point derived from the database.

- **Net collectible vs. verified collected** — The gap between what's been charged and what's been confirmed against bank records. Not a percentage — a shilling figure. "KES 47,000 outstanding from charges due."
- **Three-state payment visibility** — verified / claimed-but-unverified / no payment activity. Three states, not two. Most landlord tools collapse this into two. Ours doesn't.
- **Vacancy cost per unit** — "Unit B3 — unoccupied 31 days — KES 15,333 in foregone rent." Surfaces financial bleeding in time. Uses `status_changed_at` column on units table.
- **Arrears concentration** — What portion of total outstanding debt sits in the top 2-3 units. Framed as context: "KES 38,000 of KES 47,000 outstanding sits in 2 units." Not a risk label.

**Status:** COMPLETE — collection gap, three-state payments, vacancy cost, arrears concentration all implemented.

### Layer 2: The Ledger (Monthly Report)
**Question it answers:** "How did the numbers move across this period?"

Period-bounded data only. Does NOT duplicate the live dashboard.

- **Collection rate** — Verified collected ÷ total charges generated. The headline metric.
- **Payments received in period** — Count + total verified amount.
- **Payment timing distribution** — What % of rent was confirmed by the 5th, 10th, 15th, month-end. Behavioral pattern of the tenant base.
- **Charges generated** — Rent, service, water breakdown for the period.
- **Tenant movement** — Who moved in, who moved out during the period.
- **Claim resolution rate** — Of claims submitted: what % verified, what % pending, what % unresolved.
- **Vacancy cost calculation** — For each unit vacant during the month: days × daily rent = foregone income.

**Format:** Admin generates it, stored in DB, landlord views in "Reports" tab.
**Status:** COMPLETE — report generator, admin routes, viewer Reports tab, owner + caretaker report views, PDF export via browser print, collection rate fixed to use vs-expected-income metric.

### Layer 3: The Signal (Weekly Digest)
**Question it answers:** "What changed?"

Delivered automatically via email/WhatsApp. Only surfaces changes. Brevity IS the signal.

- **Payment velocity** — How much verified income entered the system in the last 7 days, and how many transactions.
- **Arrears state changes** — Only flag units that got better or worse since last week. Stable balances = silence.
- **Claim aging alert** — Claims pending verification for 5+ days.
- **Occupancy change events** — Unit status changes are discrete events. Report them if they happened, omit if nothing changed.

**Requires:** Email/WhatsApp delivery infrastructure. WhatsApp Business API application has lead time.
**Requires:** `balance_snapshots` table (daily snapshots) — prerequisite for arrears comparison.
**Status:** IN PROGRESS — `balance_snapshots`, APScheduler, and detector foundations are now in place; digest/inbound wiring is next.

### Layer 4: The Investment View (Yearly Report)
**Question it answers:** "Is this property performing as a financial asset?"

- **Annual collection rate + month-by-month trend** — Is it getting better, worse, or seasonal?
- **Arrears trajectory** — Total outstanding balance at end of each month, plotted over 12 months.
- **Tenant reliability scoring** — Per-tenant behavioral profile: payment timing, arrears history, claim usage. Data-derived, not judgment.
- **Vacancy cost — annual total** — Sum of all foregone income from vacancy across the year.
- **Revenue composition** — Proportion of income from rent vs service vs water.
- **Year-over-year comparison** — Requires 2+ years of data.

**Format:** In-app view + PDF export. This is what a landlord takes to their accountant.
**Status:** FUTURE — requires 12+ months of structured data.

---

### Layer 5: The Coordinator (AI Agent Layer)
**Question it answers:** "What needs to happen right now — and who needs to know?"

The agent monitors system state continuously and routes the right action to the right person without anyone having to remember to check.

**What the agent owns (no human input needed):**
- Daily balance snapshots
- Monthly charge generation (if not done by day 3)
- Weekly digest delivery (Monday morning)
- Caretaker morning briefing (every morning): routine issues + nudges batched; urgent issues forwarded immediately 24/7 regardless of time
- Monthly tenant check-in outbound: 1–3 rating + optional free text; results aggregated into sentiment briefing for caretaker and owner
- Reminder sending (on real schedule, not dashboard-load side-effect)
- Payment rejection notification: when bank statement processed and claim has no matching reference → tenant gets "we could not verify your payment, please contact us"; admin sees unverified claims in portal
- Anomaly detection: water charge spikes (>30% above 3-month avg, threshold configurable), vacancy duration, arrears thresholds, collection pace

**What the agent flags (human decides):**
- Missing bank statement / water charges / report (task prompts to admin)
- Units with no payment or claim activity by day 15 → physical follow-up nudge to caretaker ("call or visit unit X — no payment activity")
- Arrears threshold crossings (nudge to caretaker + surfaced in owner report; threshold and nudge frequency configurable per property)
- Water bill anomaly → caretaker must acknowledge or comment (logged if no response)
- Tenant departure (unit goes vacant) → caretaker must comment for owner report context
- Open maintenance issues aging past 7 days
- Caretaker escalation requests (supply, repair, other) → routed per `caretaker_request_routing` config (admin only / owner only / both); always captured in owner report
- Caretaker acknowledgment failures: if caretaker doesn't respond to a forwarded issue within 24h → logged for owner report
- Low-confidence inbound parses (asks before acting)

**Admin task feed (dashboard integration):**
Domi task feed embedded on admin dashboard alongside existing financial KPIs (Option A: dashboard route extended, base.html untouched). Admin sees pending actions on first load; returns to dashboard after completing each task for "what next." This is the primary UI element for admin day-to-day use.

**Status:** PLANNED — builds on top of Phase 3 infrastructure.

---

### Layer 6: The Conversation (Inbound Free-Text)
**Question it answers:** "What are users actually telling us?"

Every user gets a conversational input channel via WhatsApp/SMS. Natural language in, structured data out. Builds the qualitative data layer no other property tool captures.

**Tenant inbound:**
- M-Pesa SMS forwarded → payment claim created; tenant enters pending state (treated as likely paid, not chased like non-payer). Falsified/rejected reference → tenant flagged; future claims lose pending status. Flag cleared by admin only.
- Check-in replies (1/2/3 + optional free text) → intent classification tags category (maintenance/staff/neighbour/general); no multi-question flows
- Complaints (maintenance, noise, domestic violence, staff) → issue created and categorized; caretaker notified per urgency
- Balance / payment history query → answered from live DB
- Local amenities query ("nearest pharmacy", "wifi provider") → pulled from `property_info` table; available anytime, not just on greeting
- Unknown/ex-tenant numbers → polite decline ("we don't have an active account for this number")
- Greeting → Domi responds with suggested help options
- Language preference: first contact from new tenant triggers "English or Kiswahili?" prompt; preference stored on tenant record; all future messages in their language

**Caretaker inbound:**
- Issue acknowledgment reply → logged against forwarded issue; clears pending acknowledgment flag
- "Fixed the tap in B7" → closes maintenance issue
- "Unit A3 guy says he'll pay Friday" → follow-up note logged against unit with date
- "New tenant in F1 on 1st April" → occupancy update flagged to admin
- Payment logging (M-Pesa ref + unit) → claim created; caretaker source recorded
- Escalation request (supply, repair, anything needed) → routed per property config; always in owner report
- Broadcast to all tenants → sent via Domi channel
- Rent notice to specific tenant → targeted message

**Owner inbound (conversational AI — SQL-backed):**
- Replies to weekly digest → parsed as queries or instructions
- "Follow up on A3 urgently" → logged as owner instruction, routed to caretaker
- Questions answered from live DB data: "how much collected this month?", "which unit has most arrears?", "what maintenance issues are open?" → intent classified → SQL query → LLM formats response in data-descriptive voice
- Full conversation context: owner can think, plan, and analyze their property with Domi as the interface to their own data
- RAG / vector DB for qualitative data (tenant feedback, caretaker notes) added in a later iteration once sufficient data exists

**Confidence rule:** ≥0.85 → act and confirm. Below 0.85 → ask before acting. Never silent failures.

**Status:** PLANNED — built on top of Phase 5 infrastructure. Requires WhatsApp Business API approval (apply now — long lead time).

---

### Layer 7: The Voice (Real Estate Newsletter)
**Question it answers:** "What is the Kenyan property market telling us?"

An AI-written weekly real estate newsletter targeting landlords, building owners, and real estate companies in Kenya. Hedge fund style — proprietary data analysis, not market commentary rehash.

**Unfair advantage:** Anonymized aggregate data from the Domi platform (collection rates, arrears patterns, vacancy trends) combined with public Kenya market data (Kenya Bankers Association, KNBS, county permit data).

**Purpose:** SEO presence on Google + AI search indexing + LinkedIn authority. The flywheel: newsletter builds audience → audience becomes product leads → more properties on Domi → richer data → better newsletter.

**Distribution:** Web (SEO/LLM indexing) + LinkedIn articles + weekly email list.

**Architecture:** Separate codebase. Connects to this system via a single read-only internal API endpoint (`GET /api/v1/aggregate-stats`) that returns anonymized property-level aggregate data. This system is never modified for newsletter purposes beyond that one endpoint.

**Branding:** Newsletter is "Domi Weekly" or "The Domi Brief" — same brand, extended into content.

**Status:** FUTURE — separate repo. Start WhatsApp Business API application immediately (independent of newsletter timeline).

---

## Current Implementation Status

### Infrastructure (Database)
- [x] Core schema: properties, units, tenants, bank_statements, bank_transactions, payment_claims, payments, rent_charges, payment_allocations, audit_log
- [x] `unit_balances` VIEW for live balance calculation
- [x] `status_changed_at` column on units (migration in `db.py`)
- [x] `report_settings` table (migration in `db.py`)
- [x] `landlord_reports` table (migration in `db.py`)
- [x] `tenants.access_token` + messaging tables: `messages`, `message_templates`, `reminder_settings` (migrations in `db.py`)
- [x] `rent_charges.due_date` set on generate; backfill migration
- [x] `messages.template_body` column (migration: `migrate_add_template_body` in `db.py`) — stores unsubstituted template for broadcasts
- [x] `reminder_schedules` table — flexible per-property schedules (label, template_key, days_before_due, send_to)
- [x] `owner_messages` table (migration: `migrate_add_owner_messages` in `db.py`) — owner portal inbox; stores all SMS notifications + broadcasts
- [x] `caretakers` table (migration: `migrate_add_caretakers` in `db.py`) — named caretaker accounts; id, property_id, name, phone, password_hash
- [x] `balance_snapshots` table — daily per-unit balance snapshots; needed for Phase 3 weekly arrears comparison. Schema: id, property_id, unit_id, snapshot_date (YYYY-MM-DD), balance, total_charged, total_paid. UNIQUE(unit_id, snapshot_date). Insert idempotently by scheduler (not dashboard load).
- [x] `inbound_messages` table — all inbound messages from any channel; async processing pipeline
- [x] `inbound_sessions` table — conversation state (24-hour window); resolves "yes"/"no" replies
- [x] `checkin_responses` table — tenant check-in responses; aggregated monthly into sentiment briefings
- [ ] `property_info` table — local amenities per property: id, property_id, category (pharmacy/grocery/wifi/hospital/gas/etc.), name, details. Admin-managed at onboarding or anytime. Queried when tenant asks Domi about local services.
- [ ] `tenants.language_preference` column — `'en'` | `'sw'` | NULL. NULL = not yet set; triggers language prompt on first inbound contact. Stored permanently on tenant record.
- [x] `tenants.flagged` column — boolean, default false. Set when payment claim rejected (M-Pesa reference absent from bank statement). Cleared manually by admin only.
- [ ] `tenants.flagged_reason`, `tenants.flagged_at` — text + timestamp, nullable. Set alongside `flagged`.
- [ ] `maintenance_issues.priority` column — `'urgent'` | `'routine'`, default `'routine'`. Urgent = forward to caretaker immediately (24/7). Routine = next morning briefing.
- [ ] `caretaker_request_routing` table — per-property config: property_id, notify_admin (bool), notify_owner (bool). Governs where caretaker escalation requests are routed. Owner report always captures all requests regardless of routing config.
- [x] APScheduler setup in `app.py` — real job scheduler replacing dashboard-load side-effects

### Phase 0: Core Reconciliation Engine
- [x] PDF bank statement parser
- [x] M-Pesa SMS parser
- [x] Excel tenant/unit import parser
- [x] Water readings Excel parser
- [x] Payment claim → bank match → verified payment workflow
- [x] FIFO payment allocation engine
- [x] Multi-property support
- [x] Admin auth + Viewer auth
- [x] Export reports (current state, activity, payment verification)
- [x] Deployed to Fly.io (Johannesburg)

### Phase 1: The Pulse (Dashboard Upgrade)
- [x] Basic viewer dashboard (occupancy, expected income, arrears count)
- [x] Pending payments shown with "Pending" badge
- [x] Projected arrears (total and per-unit)
- [x] Payment summary stats (Collected/Pending/Total)
- [x] Net collectible gap (shilling figure, not percentage)
- [x] Three-state payment visibility (verified / claimed / no activity counts)
- [x] Vacancy cost per unit (days × daily rent)
- [x] Arrears concentration context
 - [x] Recent maintenance activity surfaced on owner dashboard + dedicated maintenance tab

### Phase 2: The Ledger (Monthly Report)
- [x] Database tables created (report_settings, landlord_reports)
- [x] Reports tab added to viewer nav
- [x] Report generator module (`src/reports/landlord_report.py`)
- [x] Admin report routes (`src/routes/report_routes.py`)
- [x] Admin generate/preview templates
- [x] Viewer report list + detail templates
- [x] Report detail with all 6 sections (collections, occupancy, arrears, tenant movement, claims, charges)
- [x] Caretaker report view (same data, no financial amounts in print version; KES visible in live portal)
- [x] PDF export via browser print (`@media print` hides nav/sidebar)
- [x] Collection metric fixed: uses verified ÷ expected monthly income (not verified ÷ period charges)
- [x] `enrich_report_data()` back-fills new fields for old saved reports

### Tenant Portal & Messaging
- [x] Tenant portal: read-only via `/tenant/<token>` (balance, charges, payments, messages); token generate/revoke from Tenants page; data-descriptive language
- [x] Messaging: admin dashboard, broadcast (one row per recipient, batch_id), templates list/edit, reminder settings (days_before_due per type)
- [x] Automatic reminders: run on dashboard load; idempotent per day per template; only active tenants with unit and balance > 0; due_date = 5th of next month for new charges
- [x] Tenant maintenance logging: tenants can log maintenance issues from their portal and see status updates; caretaker resolution triggers in-app notice
- [x] SMS delivery via Africa's Talking: `src/messaging/delivery.py`; broadcasts + reminders + payment confirmations reach tenant phones; sandbox mode (AT_USERNAME=sandbox) for testing
- [x] Payment SMS wording: "payment confirmed" — bank statement reconciliation mechanics not visible to tenants or caretakers

### Caretaker Portal
- [x] Live operational view at `/caretaker/<property_id>` (separate from owner viewer)
- [x] Auth: named DB accounts (name + password); falls back to `CARETAKER_PASSWORD` env var if no accounts exist; dev mode open if neither
- [x] Session security: `before_request` verifies `caretaker_id` still exists in DB — deleting an account revokes access immediately, even for active browser sessions
- [x] Named caretaker management at `/caretakers` (admin): create, edit, set password, delete; sidebar nav item
- [x] `sent_by` in owner inbox uses actual caretaker name from session (not generic 'Caretaker')
- [x] Caretaker header shows logged-in caretaker's name
- [x] Tabs: Overview, Arrears, Tenants, Issues, Log Payment
- [x] Printable (each tab has Print button, nav hides in `@media print`)
- [x] Maintenance issues board: caretakers can log issues, mark them resolved, and see recent history; tenant-raised issues appear alongside caretaker issues
- [x] Log Payment tab: caretaker submits M-Pesa SMS for a tenant; creates payment_claims record (`source='caretaker'`); notifies property owners
- [x] Payment Activity page (`/caretaker/<id>/payment-activity`): dedicated claim history with mismatch highlighting, caretaker note form, flagged claim links; Log Payment redirects here after submission
- [x] Mismatch notification: when bank amount ≠ claimed amount, caretaker SMSed async; dashboard shows amber alert card until note added
- [x] Flagged claims tab (`/caretaker/<id>/flagged-claims`): caretaker confirms with mandatory note; count badge in nav; three-layer resolution synced with admin view

### Owners & Multi-Property
- [x] `owners` table, token + password portal auth
- [x] One owner can have multiple properties (`properties.owner_id` one-to-many)
- [x] Owners page: shows assigned property chips, copy-link button (clipboard API), assign property on creation
- [x] Owner messages inbox: `/view/<property_id>/notifications`; broadcasts show template body + personalisation note; `sent_by` shown as colored badge (blue=Admin, amber=caretaker name, gray=System); unread highlighted; all marked read on page load
- [x] Owner notifications for all key events: broadcast sent, reminder sent, payment confirmed, report generated, caretaker payment claim submitted
- [x] Bug fixed: `property_notifications` query was missing `sent_by`, `template_body`, `channel`, `recipient_count` columns — now selects all required fields

### Mobile & UX
- [x] Admin sidebar: backdrop overlay on mobile, closes on tap-outside
- [x] Report two-column sections collapse to single column ≤600px
- [x] Report tables scroll horizontally on mobile
- [x] Topbar "Export Data" hidden on mobile

### Developer Tooling
- [x] Git repository initialized, `.gitignore` (excludes DBs, venv, screenshots)
- [x] `scripts/download_prod_db.sh` — pull production DB from Fly.io; copies to both `data/dev.db` AND `data/rent.db` so both `run_dev.sh` and `flask run` use the same data
- [x] `scripts/run_dev.sh` — local dev server on :5001 (uses `data/dev.db`, no passwords)
- [x] `scripts/reset_dev_db.sh` — reset dev DB from latest backup

### Messaging Delivery Integration
**Goal:** Admin and caretaker broadcasts reach tenants on their actual phones — not just the in-app portal.

**Status: SMS delivery COMPLETE (sandbox tested). Production SMS keys pending.**

**Delivery channels:**
1. **SMS** ✅ — Africa's Talking integration live. `delivery_channel = 'sms'` on outbound, `'portal'` for in-app-only. Kenyan number normalization (07xx → +2547xx). Sandbox via `AT_USERNAME=sandbox`.
2. **WhatsApp** (future) — WhatsApp Business API via Meta or BSP. `delivery_channel = 'whatsapp'`. Start API application early — Meta approval has lead time.
3. **Email** (future) — SMTP or Resend. Lower priority.

**Checklist:**
- [x] Africa's Talking account + sandbox API key
- [x] `src/messaging/delivery.py` — SMS send function (Africa's Talking), phone number normalizer
- [x] Admin broadcast: channel selector + SMS delivery wired up
- [x] Caretaker broadcast: same
- [x] Automatic reminders: SMS sent alongside portal message
- [x] Payment confirmation SMS: "payment confirmed" wording (no bank mechanics exposed)
- [x] `send_sms_async()` daemon thread wrapper in `src/messaging/delivery.py` — non-blocking SMS for scheduled jobs and webhooks; used by `detect_stale_claims` and mismatch notifications
- [x] `owner_messages` table + `notify_property_owners()` — owner inbox + SMS notifications
- [x] `messages.template_body` — broadcasts store unsubstituted template for owner inbox display
- [x] `sent_by` attribution in owner inbox — uses actual caretaker name (from session), 'Admin', or 'System'
- [ ] Switch Africa's Talking from sandbox → live credentials in production (`fly secrets set AT_USERNAME=... AT_API_KEY=...`)
- [ ] Decide on sender ID / shortcode for SMS (Africa's Talking account setup)
- [ ] WhatsApp Business API credentials + send function
- [ ] Email delivery (Resend or SMTP) — future

### Phase 3: The Signal + Inbound Foundation
- [x] `balance_snapshots` table + migration (prerequisite for arrears comparison)
- [x] APScheduler setup — real scheduler in `app.py`
- [x] `src/agent/` module skeleton (`coordinator.py`, `detector.py`, `briefings.py`, `router.py`, `llm.py`)
- [x] Delivery router abstraction (`src/agent/router.py`) — portal / SMS adapters wired; WhatsApp stub
- [x] `src/agent/briefings.py` — `generate_weekly_digest(conn, property_id)`: payment velocity (7d), arrears state changes (vs last snapshot), claim aging (5+ days), occupancy changes
- [x] Admin preview route `GET /agent/digest/preview/<property_id>`
- [x] Inbound webhook foundation — `POST /inbound/sms` and `POST /inbound/whatsapp` → write to `inbound_messages`, return 200 immediately; sender resolved by phone; processing dispatched in background thread (`src/routes/inbound_routes.py`)
- [x] `inbound_messages` + `inbound_sessions` tables + migrations
- [x] `tenants.language_preference` column + migration (`migrate_add_language_preference`)
- [x] Payment rejection notification: fires when bank statement processed + claim has no matching reference → SMS to tenant
- [x] Scheduled delivery — weekly digest sent to property owners via SMS every Monday 8am (`weekly_digest_job` updated)

### Phase 4: The Conversation — All Roles
*Pulled forward from Layer 6 — core to the product value proposition.*

**Tenant:**
- [ ] `src/agent/llm.py` — LLM wrapper (Anthropic Claude API)
- [ ] `src/agent/inbound.py` — intent classifier; extended intent set (see below)
- [ ] `src/agent/state.py` — session state management
- [ ] `src/agent/responder.py` — response message generator (bilingual: EN/SW)
- [ ] Language preference prompt on first contact; store to `tenants.language_preference`
- [ ] Payment claim via WhatsApp/SMS → pending state (not treated same as non-payer)
- [x] Falsification detection: claim rejected after bank statement → `tenants.flagged = true`; flagged claims lose pending status; admin clears manually via `/flagged-claims`; three-layer resolution (platform visible, caretaker confirms, admin clears)
- [x] `tenants.flagged` column + migration (`migrate_add_claim_flagging`); `tenants.flagged_reason`/`flagged_at` deferred
- [x] Amount mismatch detection: bank amount ≠ claimed amount → verified at bank amount (source of truth); caretaker SMS notification + admin alert; caretaker adds note via `/caretaker/<id>/payment-activity`; admin annotates via `POST /payments/<id>/note`
- [ ] Complaint submission (maintenance, noise, domestic violence, staff) → `maintenance_issues` record + urgency routing
- [ ] `maintenance_issues.priority` column + migration (`'urgent'` | `'routine'`)
- [ ] Balance / payment history query → answered from live DB
- [ ] Local amenities query → pull from `property_info` table (anytime, not just on greeting)
- [ ] `property_info` table + migration + admin management UI
- [ ] Unknown number → polite decline
- [x] `checkin_responses` table + migration
- [x] Monthly check-in outbound: Domi sends "How is everything? Reply 1/2/3 + optional comment"
- [ ] Check-in response handler: store numeric + free text, classify category via LLM
- [x] Check-in aggregator: monthly sentiment briefing for caretaker and owner

**Caretaker:**
- [ ] Issue acknowledgment reply → logged; clears pending flag; failure within 24h logged for owner report
- [ ] Maintenance issue resolution via message → closes issue in DB
- [ ] Follow-up note logging (unit + comment + implied date)
- [ ] Occupancy update → flagged to admin
- [ ] Payment logging via message (M-Pesa ref + unit assignment)
- [ ] Escalation request → `caretaker_request_routing` config → notify admin/owner/both; always in owner report
- [ ] `caretaker_request_routing` table + migration + admin config UI
- [ ] Broadcast to all tenants via Domi
- [ ] Rent notice to specific tenant

**Owner:**
- [ ] Owner inbound reply handler (digest responses, queries, instructions)
- [ ] Owner instruction → logged + routed to caretaker
- [ ] Owner conversational Q&A: classify intent → SQL query → LLM formats response in data-descriptive voice
- [ ] Initial query set: collections (period/YTD), arrears (by unit/total), maintenance (open issues, aging), occupancy, payment history by unit

**Infrastructure:**
- [x] Message simulator admin page (`GET /agent/simulator`) — test all inbound scenarios as any user role
- [ ] Africa's Talking inbound SMS webhook (`POST /inbound/sms`)
- [ ] WhatsApp Business API credentials + inbound webhook (`POST /inbound/whatsapp`)
- [ ] Pre-approve all outbound WhatsApp templates (list in `CURSOR_PLAN.md`)

**Extended intent set:**
`maintenance_report` | `maintenance_resolve` | `payment_claim` | `followup_note` | `query_balance` | `query_arrears` | `query_collections` | `query_maintenance` | `checkin_reply` | `local_amenity_query` | `owner_instruction` | `escalation_request` | `occupancy_update` | `confirmation` | `greeting` | `unknown`

---

### Phase 5: The Coordinator — Admin Intelligence Layer

- [ ] Task checker — missing bank statement, water charges, charge generation, stale claims
- [ ] Anomaly detector — water charge spikes, vacancy duration, arrears threshold crossings, collection pace
- [ ] Caretaker morning briefing: batches routine issues, nudges, anomalies; urgent issues bypass and go immediately
- [ ] Arrears nudge to caretaker: configurable threshold (default >2 months) + frequency (default every 2 weeks) per property
- [ ] Physical follow-up nudge: no payment/claim by day 15 → caretaker prompt to call/visit unit
- [ ] Water anomaly nudge: caretaker must acknowledge; non-response logged for owner report
- [ ] Tenant departure nudge: caretaker must comment when unit goes vacant
- [ ] Admin task feed on dashboard: extend dashboard route + `dashboard.html` to show Domi pending actions alongside financial KPIs (base.html untouched)
- [ ] Owner monthly briefing with LLM narrative layer: pass structured report JSON to Claude → narrative summary + recommendations in data-descriptive voice
- [ ] Admin preview routes: `GET /agent/digest/preview/<property_id>`, `GET /agent/briefing/caretaker/<property_id>/preview`, `GET /agent/briefing/owner/<property_id>/preview`, `GET /agent/checklist/<property_id>/preview`
- [ ] `POST /agent/trigger/<job_name>` — manual job trigger for dev/testing
- [ ] Agent admin routes blueprint (`src/routes/agent_routes.py`)
- [ ] Wire all briefings to SMS delivery

### Platform Guardian (Session 8 — 2026-05-13) ✅ COMPLETE

- [x] `platform_shadow_log` table — org_id, property_id, action, entity_type, entity_id, details, actor, created_at; agency cannot read or edit
- [x] `tenant_disputes` table — tenant raises concern directly to platform (bypasses agency): tenant_id, property_id, org_id, subject, message, status, resolved_at, resolution_note
- [x] `platform_alerts` table — anomaly alerts: org_id, property_id, alert_type, details, severity, status, dismissed_at
- [x] `src/platform/guardian.py` — `platform_log()`, `raise_alert()`, `notify_owner_change()`
- [x] `POST /tenant/<token>/dispute` — tenant raises dispute; writes to `tenant_disputes`; flash success (bypasses agency)
- [x] Tenant portal: "Something looks wrong?" card with subject dropdown + message textarea
- [x] `GET /platform/shadow-log` — platform-only audit log view; `?org_id=` filter
- [x] `GET /platform/disputes` + `POST /platform/disputes/<id>/resolve` — dispute queue with resolution notes
- [x] `GET /platform/alerts` + `POST /platform/alerts/<id>/dismiss` — anomaly alert queue
- [x] `GET /platform/trust` — per-agency trust score (0–100); computed from open alerts + disputes; color-coded progress bar
- [x] Platform nav: Alerts, Disputes, Shadow Log, Trust Scores tabs added
- [x] Platform dashboard: Alerts + Disputes KPI cards added (red/amber thresholds); layout 4→6 cards
- [x] Rent/service field edits >10% → auto-raise alert + notify owner via `guardian.py`
- [x] Owner removed from property → `platform_log()` + `raise_alert()` + direct owner SMS

---

### Phase 6: Owner Intelligence (SQL-backed Conversational AI)

- [ ] Extend owner Q&A with broader query coverage (payment trends, tenant reliability patterns, vacancy history)
- [ ] Owner can initiate conversation (not just reply to digest) — "ask Domi anything about my property"
- [ ] Response quality tuning: prompt engineering for data-descriptive voice, accurate SQL generation
- [ ] Qualitative data aggregation: surface patterns from `checkin_responses` free text to owner
- [ ] RAG / vector DB for qualitative layer (tenant feedback text, caretaker notes) — added when sufficient data exists; modular, does not replace SQL-backed Q&A

---

### Phase 7: The Investment View (Yearly Report)
*Deferred — requires 12+ months of structured data.*

- [ ] Annual collection rate + month-by-month trend
- [ ] Arrears trajectory over 12 months
- [ ] Tenant reliability scoring (payment timing, arrears history, claim behavior)
- [ ] Revenue composition (rent vs service vs water)
- [ ] Year-over-year comparison
- [ ] PDF export

---

### Admin UX Improvements (Session 8 — 2026-05-13) ✅ COMPLETE

- [x] "Total Arrears" KPI card clickable → dedicated `/arrears` admin page (unit balances + pending claims, months-behind)
- [x] "Expected Income" KPI card clickable → `/units` page
- [x] "Pending Claims" stat clickable → `/review?tab=unconfirmed`
- [x] "Pending Payment Claims" table rows clickable → `/review?tab=unconfirmed`; Assign link uses `stopPropagation()`
- [x] "Units in Arrears" table rows clickable → `/arrears`
- [x] "Unassigned Bank Payments" table rows clickable → `/review?tab=unreported`
- [x] Activity sidebar item: own nav section with type filter + date range filters; filters preserved on row clicks
- [x] Combined units + tenants page: single table with inline-editable fields (rent, service, tenant name, phone, status); all edits logged to `audit_log` and `platform_shadow_log`
- [x] Inline editing via `<span class="editable">` + fetch POST to `/units/<id>/field` and `/tenants/<id>/field` (JSON endpoints)
- [x] Status dropdown fix: `padding-right: 2rem` prevents Bootstrap arrow overlapping text
- [x] `/tenants` now redirects to `/units`; Tenants removed from sidebar

### Owner Wallet + Payout Security (Sessions 8–9 — 2026-05-13 / 2026-05-17) ✅ COMPLETE

- [x] `GET /view/<property_id>/wallet` — balance, fee rate, disbursement history; payout account section with 4 states (unset/pending_otp/hold/active)
- [x] Balance = total verified payments − management fee − total disbursed (computed from existing tables)
- [x] Three KPI cards: available balance, total collected (all-time + this month), management fee % + disbursed total
- [x] Disbursement history table: period, collected, fee, net, method, status badges, date, recipient_account
- [x] Wallet tab added to owner viewer nav (`base_viewer.html`)
- [x] **Beneficiary substitution fraud prevention:** `owners.payout_mpesa` is owner-write-only via portal OTP flow — admin has zero write path
- [x] `migrate_add_payout_fields` — adds `payout_mpesa`, `payout_confirmed`, `payout_active_at`, `payout_otp`, `payout_otp_expires_at` to `owners` table
- [x] `POST /view/<property_id>/payout/request-otp` — owner submits number; OTP generated + sent via SMS; 10-min expiry
- [x] `POST /view/<property_id>/payout/confirm-otp` — verifies OTP; sets confirmed + 48h hold; platform_log + critical alert; warns old number via SMS if changing
- [x] `_get_confirmed_payout_owner(conn, property_id)` in `disbursements.py` — hard-blocks disbursements until `payout_confirmed=1 AND payout_active_at <= now()`; raises `ValueError` + critical platform alert if no eligible owner found
- [x] `execute_disbursement()` sets `recipient_account` from confirmed owner's `payout_mpesa`

### Admin UX & Onboarding (Session 9 — 2026-05-17) ✅ COMPLETE

- [x] Sidebar restructured: daily-use items first (Overview → Units → Payments → Messages → Bank Statements → Water Charges → Reports → Activity), then Monthly section (Monthly Workflow), then Setup section (Caretakers → Owners)
- [x] Water Charges added to sidebar as dedicated nav item (adjacent to Bank Statements — both are upload actions)
- [x] Monthly Workflow elevated from Tools & Settings footer → main Monthly sidebar section
- [x] `tools_index()` route fully implemented: queries DB for per-step last-completion timestamps; returns `workflow` dict + `period` for live status
- [x] Monthly Workflow page: 5 steps (was 4); Step 4 "Verify Payments" added; each step shows green top border if done this month, red if not; unassigned count shown on Step 4 with direct "Assign X" link
- [x] Setup checklist on dashboard: 3-step card (add owners / add caretaker / run workflow); each step shows ✓ when complete; entire card disappears when all 3 done; live DB check on every load
- [x] Actionable empty states: Payments confirmed tab, Bank Statements page, Unreported credits tab — each has explanatory copy + CTA links to next workflow step

### Bank Statement Workflow (Session 10 — 2026-05-19) ✅ COMPLETE

- [x] **Bank statements page redirect fixed** — `manage_statements()` now falls back to `property_row['organization_id']` when `session['org_id']` is missing (master-key login path); `property_list()` and `select_property()` backfill `org_id` into session
- [x] **Family Bank detection** — `detect_bank_statement_format()` checks for `PARTICULARS IN OUT` header to separate Family Bank from Co-operative Bank (both use `DD-MMM-` date format); dispatches to cooperative parser
- [x] **National Bank detection** — detects `Transaction Date Value Date Reference Transaction Details` column header; dispatches to tabular parser with `national_bank` label
- [x] **Bank display names** — `src/parsers/banks/registry.py`: `BANK_DISPLAY_NAMES` dict + `bank_display_name()` with all 5 formats (`cooperative`, `family_bank`, `national_bank`, `tabular_kes`, `unknown`)
- [x] **View PDF route** — `GET /statements/<statement_id>/view` serves stored PDF via `send_file()` using `UPLOAD_FOLDER/{id}.pdf` (not stored `file_path` which is a Fly.io absolute path)
- [x] **Reparse fix** — route uses `UPLOAD_FOLDER/{id}.pdf`, computes `period_start`/`period_end` from transactions and includes both in the UPDATE
- [x] **Statement detail hub** — `GET /statements/<statement_id>` — full management page: 4 summary cards, verified payments table (with Correct button → collapse form), unmatched credits (with suggestion badges + Auto-assign + Assign), parse errors, other transactions collapsed; all actions audited
- [x] **Suggestion engine** — Tier 1: unit_hint from narration (exact org-scoped unit match → Auto-assign button); Tier 2: sender name token overlap ≥2 tokens → name_match badge, pre-fills unit + reason in assign form
- [x] **Verify scoped to statement** — `verify_payments()` reads `stmt.property_id`; if set, filters pending claims to `WHERE p.id = stmt.property_id` only; org-wide otherwise
- [x] **Payments tab reorder** — Confirmed → Unconfirmed → Unreported → Reversals → Parse Errors
- [x] **Statement column in Unreported tab** — both single and group unreported tables show statement filename as first column with link to `statement_detail`
- [x] **Multi-property upload tagging** — property dropdown at upload (appears only when org has >1 property); `tagged_property_id` saved to `bank_statements.property_id`; supersede logic scoped by property tag (tagged statements only supersede same-tag statements; org-wide only supersedes org-wide)
- [x] **Property context in statement detail** — when org has >1 property: Property column in verified/unmatched tables; "Property-tagged" or "Org-wide" badge in header; unit dropdowns use `<optgroup>` headers grouped by property
- [x] **payment.property_id always derived from unit** — `statement_auto_assign()`, `statement_assign_payment()`, `statement_correct_payment()` all derive `property_id` from the target unit via SQL (`SELECT p.id FROM units u JOIN properties p ... WHERE u.id = ?`); never from session's selected property

### Phase I: PMO Account System (COMPLETE — 2026-05-20) ✅

**Platform creates the org:**
- [x] Platform org creation form includes `platform_fee_rate` field (default 0.01)
- [x] `organizations.platform_fee_rate REAL DEFAULT 0.01` — visible only in `/platform/*`

**Org login:**
- [x] `GET/POST /login` unified — handles org admin + owner (person) + ADMIN_PASSWORD master key
- [x] Org identified by `contact_email`; sets `session['org_id']` + `session['admin_authenticated']`
- [x] `ADMIN_PASSWORD=dev` in `run_dev.sh` as master key fallback
- [x] `GET/POST /settings` (email + password change) + `templates/settings.html`

**First-time wizard:**
- [x] `GET /welcome` — live DB query: `step1_done`, `step2_done`, `step3_done`, `all_done`
- [x] Dashboard redirects to `/welcome` when org has no active properties
- [x] Steps unlock progressively; celebration screen when all complete

**`platform_fee_rate` on disbursements:**
- [x] `calculate_disbursement()` deducts platform fee after management fee
- [x] `disbursements` table stores `platform_fee_rate` + `platform_fee_amount`
- [x] Never appears in org admin routes or templates

**Owner account activation:**
- [x] `GET/POST /owner/activate/<token>` — sets password; stores in `persons.password_hash`
- [x] Email OTP step 2: `_send_email_otp()` helper + `/owner/verify-email` route; logged to platform outbox
- [x] `src/messaging/email.py` SMTP delivery (Mailtrap-compatible); simulated if unconfigured
- [x] `persons.activation_token` + OTP columns (`otp_code`, `otp_expires_at`, `phone_verified`)

**Owner login alternatives:**
- [x] Email + password at `/login` (unified)
- [x] `/owner/l/<token>` — password-only login via shareable link using `owners.access_token`

**Owner security hardening:**
- [x] `property_owners.is_primary` — auto-set; primary cannot be removed by admin
- [x] Removal requires written reason; confirmation modal; critical alert + owner SMS
- [x] `edit_owner` email immutable once account activated

**Platform Outbox:**
- [x] `platform_outbox` table + `src/messaging/outbox.py` + `/platform/outbox` route
- [x] All outbound SMS and portal notifications logged regardless of delivery status

### Session 16: Workflow UX, Bug Fixes, Ignore Feature (2026-05-20) ✅

- [x] **`generate_charges` idempotent** — replaced SELECT+INSERT with `INSERT OR IGNORE`; `rowcount` used for counter; re-running for an already-charged period no longer crashes
- [x] **Monthly Workflow period-aware** — `tools_index()` auto-detects earliest YYYY-MM period with charges but no verified payments (not hardcoded to today's month); accepts `?period=` override; prev/next navigation arrows; invalid periods (e.g. "ARREARS") filtered out with `LIKE '20__-__'` guard
- [x] **Workflow step correctness** — Step 3 (bank statement) checks `MAX(bt.txn_date)` scoped to the selected period (not upload date); Step 4 excludes `ignored` transactions; Step 5 checks `landlord_reports` table (not `audit_log export_%`)
- [x] **One-click report generation** — Step 5 "Generate Report" button POSTs to `/reports/generate-for-period`; computes first/last day from YYYY-MM; generates landlord report; redirects to preview; "View Reports" link appears once done
- [x] **org-scoped statement bug** — `auto_assign_payment` and bulk-assign routes were querying `bs.property_id = ?` (fails for org-scoped statements with `property_id = NULL`); both now use `bs.org_id` when org exists
- [x] **`bank_transactions.ignored`** — new flag (`migrate_add_bank_txn_ignored`): `ignored=1` excludes a transaction from the unassigned count without deleting it; row remains assignable; `POST /payments/ignore/<txn_id>` + `POST /payments/unignore/<txn_id>` routes; "Ignored" tab on review page with Assign + Restore buttons; CLAUDE.md rule: always add `AND (bt.ignored IS NULL OR bt.ignored = 0)` to unassigned-credit queries

### Phase 8: The Voice (Newsletter)
- [ ] Apply for WhatsApp Business API via Africa's Talking (do this NOW — long lead time)
- [ ] Design newsletter brand: "Domi Weekly" or "The Domi Brief"
- [ ] Read-only aggregate stats API endpoint (`GET /api/v1/aggregate-stats`) in this codebase
- [ ] Separate newsletter repository
- [ ] AI agent pipeline: data ingestion → analysis → content generation → distribution
- [ ] Web presence (SEO/LLM indexing) + LinkedIn + email list

---

## Layer F: The Payment Rail (Property Fintech — Money in Transit)

**Decision locked: 2026-05-04**

Domi is a **property fintech platform**, not a property management tool. This is a strategic pivot that changes the revenue model, the product stickiness, and the build sequence.

### The Model

```
Tenant pays M-Pesa → Domi Paybill (STK Push or self-initiated)
                   → Card (Pesapal hosted checkout)
Domi holds funds
Domi disburses to landlord net of management fee
Fee is embedded in disbursement spread — not a visible line item
```

**Why this is the right model:**
- Landlord receiving KES 290,000 doesn't feel the same as landlord paying an 8% invoice
- Tenants use the M-Pesa Paybill flow they already know from KPLC and Nairobi Water
- Switching away from Domi requires changing payment instructions for all tenants — very high friction
- Domi becomes infrastructure, not software. Infrastructure doesn't get cancelled.

### Tenant Payment Experience

- Tenant portal gains a payment tab (existing token-based portal, extended — not replaced)
- **STK Push:** tenant enters phone + amount → M-Pesa prompt sent to their phone → they enter PIN → done
- **Card:** Pesapal hosted checkout → redirect back to portal on completion
- **Partial payments:** first-class feature — tenant can pay KES 5,000 of KES 15,000 due; FIFO allocator handles it correctly already
- Payment history shows what each payment was applied to (FIFO transparency)
- SMS + email confirmation on every payment

### Two Fees — Never Confuse Them

| Field | Who sets it | Who benefits | Visible to PMO? |
|---|---|---|---|
| `properties.management_fee_rate` | PMO (their contract with owner) | PMO agency | Yes |
| `properties.platform_fee_rate` | Platform only — never editable by org admin | Domi | No — deducted silently from float |

Disbursement order: gross rent → minus management fee → minus platform fee → owner receives net.

`platform_fee_rate` default: 0.01 (1%). Set by platform at org creation. Never appears in any org admin query or template.

### Bank Statement Reconciliation — Transitional Feature

The PDF bank statement upload + M-Pesa SMS claim workflow is **how we acquire customers while the payment rail is being built.** It is not the end state.

**The bridge strategy:** PMOs onboard now using the bank statement workflow. They build their data, their owners see the value, their tenants learn Domi exists. When the Daraja/Pesapal credentials arrive, we flip the paybill instructions and the payment rail activates — existing customers transition automatically.

Bank statement reconciliation stays in the product permanently for PMOs who have tenants paying directly to the property bank account (legacy arrangements). But it is no longer the primary payment flow once the rail is live.

### Informal Monthly Payment (Bridge Period)

During the customer acquisition phase (before transaction fees are technically live), Domi requests a direct monthly payment from PMOs. This is informal, not reflected in the app, and not enforced via the platform. It funds development. Amount to be discussed with each PMO during onboarding.

Once the payment rail is live and transaction volumes are established, the informal fee is replaced by the automatic `platform_fee_rate` deduction from disbursements.

### Legacy Reconciliation Flow (kept permanently)

Bank statement PDF + SMS claim workflow stays active. Tenants who pay directly to the property's bank account continue to use proof-of-payment. Both flows write to the same `payments` table and run through the same FIFO allocator. Source field (`payment_transactions.source`) distinguishes origin.

### Payment Rail Checklist

**Prerequisites:**
- [x] Admin authentication — `GET/POST /login`, `before_request` hook, exempt paths. Must be done before any payment code is written.
- [ ] Legal structure review — merchant model vs CBK PSP license. Engage Africa's Talking compliance or Kenyan fintech counsel.
- [ ] Safaricom Paybill + Daraja application — via Africa's Talking Payments. Timeline: 2-4 weeks.
- [ ] WhatsApp Business API application (apply now — 2-6 week lead time, gates Phase H)

**Phase G — Build:**
- [x] `src/payments/` module: `daraja.py`, `pesapal.py`, `disbursements.py`
- [x] `payment_transactions` table + `migrate_add_payment_transactions()` — raw callback storage
- [x] `disbursements` table — landlord payout records (in migrate_add_payment_transactions)
- [x] `POST /inbound/payment/mpesa` — Daraja callback handler (write-and-return-200)
- [x] `POST /inbound/payment/pesapal` — Pesapal IPN handler (same pattern)
- [x] Background worker: process `payment_transactions WHERE processing_status = 'pending'`
- [x] Tenant portal auth upgrade — 4-digit PIN for payment tab only (`tenants.portal_pin_hash`)
- [x] Tenant portal payment UI — STK Push form (Pesapal card checkout — stub, pending credentials)
- [x] Payment status polling endpoint — `GET /tenant/<token>/pay/status/<checkout_request_id>`
- [x] Disbursement engine — `calculate_disbursement()`, `execute_disbursement()`
- [x] Disbursement scheduler job — 10th of each month
- [x] Landlord disbursement statement — extend owner report tab
- [x] Payment source badge on admin/caretaker views (Paybill / Card / Manual)
- [x] `properties.management_fee_rate` column (default 0.08) — migration required
- [x] Update `.agent/schema.yaml`, `.agent/routes.yaml`, `.agent/jobs.yaml` after each addition

---

## Business Context

- **We are the platform.** Domi is operated by its founders. The platform layer (`/platform/*`) is Domi's control room — never exposed to PMOs or owners.
- **Product name: Domi** — derived from *domus* (Latin: home). Warm, neutral, non-intrusive.
- **First customer:** Mowin Apartments (44 units, Athi River) — used to build and validate the product. Data recreation from Jan 2026 in progress.
- **Target market:** 1,000+ properties at Mowin scale (40–120 units) across Kenya → East Africa. The market is large, fragmented, and almost entirely undigitized.
- **Revenue model (locked 2026-05-19):** Transaction fee on money flowing through the platform. `platform_fee_rate` (default 1%) deducted from each disbursement before owner receives funds. Not a SaaS subscription. No invoice to the PMO.
  - Bridge period: informal monthly payment from each PMO while payment rail credentials are pending. Not in the app.
  - Target economics: KES 8M/month at 1,000 properties at 1% fee → significant scale with no marginal cost per additional PMO.
- **PMO onboarding:** Platform creates each org account manually (controlled growth). PMO receives credentials, logs in, completes the first-time wizard. No self-signup in Phase I.
- Kenya market: M-Pesa dominant, WhatsApp is primary communication channel for all user types
- The payment rail + intelligence layer IS the differentiator — spreadsheets cannot do either
- **WhatsApp Business API application must be started immediately** — Meta approval has 2-6 week lead time; apply via Africa's Talking. This gates Phase H.

### WhatsApp Strategy
- One WhatsApp Business number for the entire platform (not per property)
- Identity routing: sender phone number → lookup in DB → role + property + entity
- Multi-property owners: prompted to select property if ambiguous (cached in inbound_sessions)
- All proactive outbound messages require pre-approved Meta templates (utility category)
- SMS via Africa's Talking remains the fallback for users without WhatsApp
- Quality rating protection: only high-value, relevant messages; opt-out always available
- Cost at scale: ~$0.065/conversation (Africa pricing tier) — manageable, bundled into service fee

### Scaling Path
- 1-5 properties: SQLite + APScheduler + single Fly.io worker
- 5-15 properties: PostgreSQL on Fly.io + Redis/RQ job queue + second worker for agent and payment jobs
- 15+ properties: `src/agent/` and `src/payments/` extracted to separate Fly.io services; dedicated inbound processor

---

## For AI Agents: Update Protocol

After completing any work on this project:

1. **Update the checkboxes above** — mark completed items as `[x]`
2. **Update `.agent/schema.yaml`** if you added tables or columns
3. **Update `.agent/routes.yaml`** if you added routes or blueprints
4. **Update `.agent/jobs.yaml`** if you added scheduled jobs
5. **Update `CLAUDE.md`** — technical additions only, never restructure
6. **Update `AGENTS.md`** — overwrite "Last Session" with what you built and what's next
7. **Respect the data-descriptive language rule** — review all user-facing strings before marking work complete
