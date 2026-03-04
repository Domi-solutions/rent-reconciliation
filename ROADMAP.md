# Product Roadmap — Financial Intelligence Layer

> **For AI agents:** Read this file to understand the product vision, what's built, and what's next.
> After completing work, update the status checkboxes and "Current Implementation Status" section below.
> For technical patterns, database schema, and code conventions, see `CLAUDE.md`.

---

## Vision

This app is a **financial intelligence layer** on property data. It does not report what an agency did — it surfaces what the data shows. Think of it as a stock portfolio dashboard for rental property: it doesn't manage the stocks, it tells you exactly what's happening with your money at every level of zoom.

**The insight is the product.**

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
**Status:** FUTURE — not started. Start WhatsApp API application in parallel.

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

## Current Implementation Status

### Infrastructure (Database)
- [x] Core schema: properties, units, tenants, bank_statements, bank_transactions, payment_claims, payments, rent_charges, payment_allocations, audit_log
- [x] `unit_balances` VIEW for live balance calculation
- [x] `status_changed_at` column on units (migration in `db.py`)
- [x] `report_settings` table (migration in `db.py`)
- [x] `landlord_reports` table (migration in `db.py`)
- [x] `tenants.access_token` + messaging tables: `messages`, `message_templates`, `reminder_settings` (migrations in `db.py`)
- [x] `rent_charges.due_date` set on generate; backfill migration
- [ ] Balance snapshots mechanism (needed for Phase 3 weekly arrears comparison)

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

### Caretaker Portal
- [x] Live operational view at `/caretaker/<property_id>` (separate from owner viewer)
- [x] Auth: `CARETAKER_PASSWORD` env var; session-based
- [x] 3 tabs: Overview (occupancy + vacant units + top arrears), Arrears (full list with KES + phone), Tenants (directory)
- [x] Printable (each tab has Print button, nav hides in `@media print`)

### Owners & Multi-Property
- [x] `owners` table, token + password portal auth
- [x] One owner can have multiple properties (`properties.owner_id` one-to-many)
- [x] Owners page: shows assigned property chips, copy-link button (clipboard API), assign property on creation

### Mobile & UX
- [x] Admin sidebar: backdrop overlay on mobile, closes on tap-outside
- [x] Report two-column sections collapse to single column ≤600px
- [x] Report tables scroll horizontally on mobile
- [x] Topbar "Export Data" hidden on mobile

### Developer Tooling
- [x] Git repository initialized, `.gitignore` (excludes DBs, venv, screenshots)
- [x] `scripts/download_prod_db.sh` — pull production DB from Fly.io
- [x] `scripts/run_dev.sh` — local dev server (uses `data/dev.db`, no passwords)
- [x] `scripts/reset_dev_db.sh` — reset dev DB from latest backup

### Phase 3: The Signal (Weekly Digest)
- [ ] Email delivery infrastructure (smtplib or Resend)
- [ ] WhatsApp Business API integration
- [ ] Weekly digest generation
- [ ] Scheduled delivery (APScheduler or cron)

### Phase 4: The Investment View (Yearly)
- [ ] Requires 12+ months of data accumulation
- [ ] Trend analysis
- [ ] Tenant reliability scoring
- [ ] PDF export

---

## Business Context

- Property management agency in Kenya, 2-3 person team
- Currently: 1 property (Mowin Apartments, 44 units)
- Target: 3-5 properties near-term
- Revenue model: not locked down (likely 8-10% of verified collections)
- Kenya market: M-Pesa dominant, WhatsApp for communication
- The dashboard/tech IS the sales differentiator
- No brand name yet, no social media presence
- WhatsApp Business API application should be started for Phase 3 lead time

---

## For AI Agents: Update Protocol

After completing any work on this project:

1. **Update the checkboxes above** — mark completed items as `[x]`
2. **If you added new files**, update the project structure in `CLAUDE.md`
3. **If you added new routes**, document them in `CLAUDE.md` under the appropriate section
4. **If you added new database tables/columns**, document in `CLAUDE.md` under "Database Tables" and ensure migration function exists in `db.py`
5. **If you modified the viewer**, note changes under the Phase 1/2 sections above
6. **Respect the data-descriptive language rule** — review all user-facing strings before marking work complete
