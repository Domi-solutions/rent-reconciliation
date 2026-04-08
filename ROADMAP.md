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

## Three Audiences (Over Time)

1. **Landlord** (NOW — priority): What's happening with my asset
2. **Agency** (FUTURE): Where are we performing well/poorly across properties
3. **Client-facing** (FUTURE): Same data, tone may shift for external presentation

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
- [ ] `inbound_messages` table — all inbound messages from any channel; async processing pipeline
- [ ] `inbound_sessions` table — conversation state (24-hour window); resolves "yes"/"no" replies
- [ ] `checkin_responses` table — tenant check-in responses; aggregated monthly into sentiment briefings
- [ ] `property_info` table — local amenities per property: id, property_id, category (pharmacy/grocery/wifi/hospital/gas/etc.), name, details. Admin-managed at onboarding or anytime. Queried when tenant asks Domi about local services.
- [ ] `tenants.language_preference` column — `'en'` | `'sw'` | NULL. NULL = not yet set; triggers language prompt on first inbound contact. Stored permanently on tenant record.
- [ ] `tenants.flagged` column — boolean, default false. Set when payment claim rejected (M-Pesa reference absent from bank statement). Cleared manually by admin only.
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
- [x] Log Payment tab: caretaker submits M-Pesa SMS for a tenant; creates payment_claims record (`source='caretaker'`); shows last 15 claims with Verified/Pending status; notifies property owners

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
- [ ] Inbound webhook foundation — `POST /inbound/sms` and `POST /inbound/whatsapp` → write to `inbound_messages`, return 200 immediately
- [ ] `inbound_messages` + `inbound_sessions` tables + migrations
- [ ] `tenants.language_preference` column + migration
- [ ] Payment rejection notification: fires when bank statement processed + claim has no matching reference → SMS to tenant
- [ ] Scheduled delivery — Monday morning via SMS (WhatsApp added later)

### Phase 4: The Conversation — All Roles
*Pulled forward from Layer 6 — core to the product value proposition.*

**Tenant:**
- [ ] `src/agent/llm.py` — LLM wrapper (Anthropic Claude API)
- [ ] `src/agent/inbound.py` — intent classifier; extended intent set (see below)
- [ ] `src/agent/state.py` — session state management
- [ ] `src/agent/responder.py` — response message generator (bilingual: EN/SW)
- [ ] Language preference prompt on first contact; store to `tenants.language_preference`
- [ ] Payment claim via WhatsApp/SMS → pending state (not treated same as non-payer)
- [ ] Falsification detection: claim rejected after bank statement → `tenants.flagged = true`; flagged claims lose pending status; admin clears manually
- [ ] `tenants.flagged`, `tenants.flagged_reason`, `tenants.flagged_at` columns + migration
- [ ] Complaint submission (maintenance, noise, domestic violence, staff) → `maintenance_issues` record + urgency routing
- [ ] `maintenance_issues.priority` column + migration (`'urgent'` | `'routine'`)
- [ ] Balance / payment history query → answered from live DB
- [ ] Local amenities query → pull from `property_info` table (anytime, not just on greeting)
- [ ] `property_info` table + migration + admin management UI
- [ ] Unknown number → polite decline
- [ ] `checkin_responses` table + migration
- [ ] Monthly check-in outbound: Domi sends "How is everything? Reply 1/2/3 + optional comment"
- [ ] Check-in response handler: store numeric + free text, classify category via LLM
- [ ] Check-in aggregator: monthly sentiment briefing for caretaker and owner

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
- [ ] Message simulator admin page (`GET /agent/simulator`) — test all inbound scenarios as any user role
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

### Phase 8: The Voice (Newsletter)
- [ ] Apply for WhatsApp Business API via Africa's Talking (do this NOW — long lead time)
- [ ] Design newsletter brand: "Domi Weekly" or "The Domi Brief"
- [ ] Read-only aggregate stats API endpoint (`GET /api/v1/aggregate-stats`) in this codebase
- [ ] Separate newsletter repository
- [ ] AI agent pipeline: data ingestion → analysis → content generation → distribution
- [ ] Web presence (SEO/LLM indexing) + LinkedIn + email list

---

## Business Context

- Property management agency in Kenya, 2-3 person team
- **Product name: Domi** — derived from *domus* (Latin: home). Warm, neutral, non-intrusive.
- Currently: 1 property (Mowin Apartments, 44 units, property ID: PROP-45ED445A)
- Target: 3-5 properties near-term; 15+ at scale
- Revenue model: not locked down (likely 8-10% of verified collections — aligns product revenue with landlord success)
- Kenya market: M-Pesa dominant, WhatsApp is the primary communication channel for all user types
- The intelligence + push delivery IS the sales differentiator vs. spreadsheets and WhatsApp group chats
- No social media presence yet — newsletter (Phase 7) is the content/SEO/LinkedIn strategy
- **WhatsApp Business API application must be started immediately** — Meta approval has 2-6 week lead time; apply via Africa's Talking (existing relationship). This gates Phase 6.

### WhatsApp Strategy
- One WhatsApp Business number for the entire platform (not per property)
- Identity routing: sender phone number → lookup in DB → role + property + entity
- Multi-property owners: prompted to select property if ambiguous (cached in inbound_sessions)
- All proactive outbound messages require pre-approved Meta templates (utility category)
- SMS via Africa's Talking remains the fallback for users without WhatsApp
- Quality rating protection: only high-value, relevant messages; opt-out always available
- Cost at scale: ~$0.065/conversation (Africa pricing tier) — manageable and bundled into service fee

### Scaling Path
- 1-5 properties: SQLite + APScheduler + single Fly.io worker
- 5-15 properties: PostgreSQL on Fly.io + Redis/RQ job queue + second worker for agent jobs
- 15+ properties: `src/agent/` extracted to separate Fly.io service; dedicated inbound processor

---

## For AI Agents: Update Protocol

After completing any work on this project:

1. **Update the checkboxes above** — mark completed items as `[x]`
2. **If you added new files**, update the project structure in `CLAUDE.md`
3. **If you added new routes**, document them in `CLAUDE.md` under the appropriate section
4. **If you added new database tables/columns**, document in `CLAUDE.md` under "Database Tables" and ensure migration function exists in `db.py`
5. **If you modified the viewer**, note changes under the Phase 1/2 sections above
6. **Respect the data-descriptive language rule** — review all user-facing strings before marking work complete
