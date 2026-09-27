# Domi — Full Project Context

I want to tell you about a software project I'm building called **Domi**. I want you to have full context so we can think through it together — the idea, how it came to be, what we're building, why, and how.

---

## What is Domi?

Domi is a **property intelligence and fintech platform** for Kenya — eventually East Africa. The name comes from *domus*, Latin for home. It sits invisibly across residential properties and handles rent reconciliation, financial reporting, maintenance tracking, and communication for landlords, property managers, caretakers, and tenants.

The codebase is named `rent-reconciliation` (the repo/deploy name from the beginning) but the product is Domi.

---

## How it came to be

It started as a simple reconciliation tool for a 2–3 person property management agency in Nairobi managing one property — Mowin Apartments, 44 units. Their problem was basic: tenants pay rent via M-Pesa, the money shows up in a bank statement as a reference number, and manually matching those references to the right tenant every month was error-prone and time-consuming.

The first version did one thing: parse M-Pesa SMS messages and bank statement PDFs, match them together, and produce a clean Excel export of who paid and who didn't.

Over time the insight became clear: the real problem wasn't reconciliation — it was that landlords and property managers were operating blind. They had no structured view of their property as a financial asset. They got information reactively, when they remembered to ask. The tool should be delivering intelligence to them proactively.

---

## The Core Insight

> **The insight is the product. The push is the delivery. The conversation is the data.**

Most property tools require users to log in to get value. Lazy users — the expected baseline — get zero value. Domi flips this: it delivers value TO users regardless of their behavior, meeting them in the channel they already use (WhatsApp and SMS) without requiring them to open a dashboard.

A landlord who reads a 30-second WhatsApp digest every Monday morning outperforms one managing via spreadsheets and gut feel — not because they changed behavior, but because the system does the analytical work automatically.

This is the **pull → push** model shift. Everything is built around it.

---

## Strategic Direction (locked May 2026)

**Domi is a property fintech platform, not a property management tool.** This is the most important strategic decision made so far.

The model:

```
Tenant pays M-Pesa → Domi Paybill (STK Push or self-initiated)
                   → Card (Pesapal hosted checkout)
Domi holds funds
Domi disburses to landlord net of management fee (~8%)
Fee is embedded in disbursement spread — not a visible line item
```

Why this model:
- A landlord *receiving* KES 290,000 doesn't feel the same as paying an 8% invoice. The fee is invisible.
- Tenants use M-Pesa Paybill — they already know this flow from KPLC and Nairobi Water.
- Switching away from Domi requires changing payment instructions for every tenant — very high friction.
- **Domi becomes infrastructure, not software. Infrastructure doesn't get cancelled.**

The legacy bank statement + SMS claim reconciliation workflow stays permanently alongside the payment rail. Both flows write to the same payments table.

---

## Market Context

Kenya: M-Pesa is the dominant payment rail. WhatsApp is the primary communication channel for all user types — tenants, caretakers, owners. The formal property management market is fragmented and spreadsheet-driven. Custom tech is a genuine differentiator.

The plan is to start with 1 property (Mowin Apartments), grow to 3–5 near-term, and scale to 15+ across Kenya and eventually East Africa.

---

## The Product Layers

Domi is built in layers, each answering a different question:

**Layer 0 — Core Reconciliation Engine** *(COMPLETE)*
PDF bank statement parsing, M-Pesa SMS matching, FIFO payment allocation, multi-property support, admin auth, Excel exports. Deployed to Fly.io (Johannesburg).

**Layer 1 — The Pulse** *(COMPLETE)*
"What is the current financial state of my asset?" Live dashboard: net collectible gap (shilling figure, not a %), three-state payment visibility (verified / claimed-but-unverified / no activity), vacancy cost per unit in foregone rent, arrears concentration.

**Layer 2 — The Ledger** *(COMPLETE)*
"How did the numbers move across this period?" Monthly report: collection rate, payment timing distribution, charges generated, tenant movement, claim resolution rate, vacancy cost for the month. Admin generates it, stored in DB, landlord views in a Reports tab.

**Layer 3 — The Signal** *(IN PROGRESS)*
"What changed?" Weekly digest delivered via WhatsApp/SMS every Monday morning. Only surfaces changes — stable data is silence. Payment velocity, arrears state changes vs last week, claim aging, occupancy events. APScheduler and database snapshot infrastructure is in place.

**Layer 4 — The Conversation** *(PLANNED)*
Every user gets a conversational input channel via WhatsApp/SMS. Natural language in, structured data out.
- Tenants: submit M-Pesa SMS as payment claim, ask about their balance, log maintenance issues, reply to monthly check-ins, ask about local amenities.
- Caretakers: log issues, acknowledge alerts, log payments by SMS, broadcast to tenants.
- Owners: reply to their weekly digest as a conversation — query collections, arrears, open issues. SQL-backed conversational AI.

**Layer 5 — The Coordinator** *(PLANNED)*
The AI agent layer. Monitors system state continuously, routes the right action to the right person. Daily balance snapshots, anomaly detection (water charge spikes, vacancy duration, arrears thresholds), caretaker morning briefings, admin task feed on dashboard.

**Layer 6 — The Investment View** *(FUTURE)*
Annual report: collection rate month-by-month trend, arrears trajectory over 12 months, tenant reliability scoring, vacancy cost annual total, revenue composition. Requires 12+ months of data.

**Layer 7 — The Voice** *(FUTURE)*
An AI-written weekly real estate newsletter for Kenya: "Domi Weekly." Anonymized aggregate data from the platform + public Kenya market data. SEO presence + LinkedIn authority. Newsletter builds audience → audience becomes product leads → more properties → richer data → better newsletter.

---

## Multi-Org Architecture (built May 2026)

The system has been upgraded from single-agency to multi-org. Four real properties are entering the system across two agencies. The schema now has:
- **Organizations** — one row per agency using Domi
- **Persons** — shared human identity across roles and properties (one person can be a tenant in two different properties, or an owner across two agencies)
- **Platform admin** — the Domi operator sees all orgs, all parse errors, can impersonate any org

Auth tiers: Platform (Domi operator) → Org admin → Owner (holistic view across properties) → Caretaker → Tenant (holistic view if in multiple units).

---

## Tech Stack

- Python 3.13, Flask 3.0+, SQLite (PostgreSQL migration path at ~10 properties)
- Jinja2 + Bootstrap 5; pdfplumber (PDF parsing), pandas + openpyxl (Excel)
- No ORM — raw SQL with parameterized queries
- APScheduler — background scheduler embedded in Flask
- Anthropic Claude API — LLM inference via a single wrapper module
- Africa's Talking — SMS (live) + WhatsApp Business API (pending Meta approval)
- Daraja (Safaricom) — M-Pesa STK Push + B2C disbursements
- Pesapal — card checkout for tenants
- Deployed on Fly.io, Johannesburg region

Scaling path: SQLite + APScheduler → PostgreSQL + Redis/RQ → extracted agent and payments services on separate Fly workers.

---

## Design Rule (non-negotiable)

**Data-descriptive language everywhere.** The system reports what the data shows — never what the agency did.

| DO say this | NOT this |
|---|---|
| "KES 312,000 verified against bank records" | "We collected KES 312,000" |
| "Unit A7 — KES 42,000 outstanding, 2 months" | "We are following up on Unit A7" |
| "Unit C4 water charge: KES 4,200 — 43% above 3-month average" | "We noticed water usage increased" |

The app makes no claims about actions taken. It surfaces what the data knows.

---

## Where Things Stand (May 2026)

**Complete and deployed:**
- Full reconciliation engine (PDF parsing, SMS matching, FIFO allocation, exports)
- Owner, caretaker, and tenant portals
- Messaging (SMS broadcasts, reminders, payment confirmations via Africa's Talking)
- Monthly report generation and PDF export
- Multi-org architecture (organizations, persons, platform admin, org impersonation)
- Payment rail foundation (Daraja callback handler, payment_transactions table, tenant payment UI with 4-digit PIN, disbursement engine, scheduled 10th-of-month disbursements)
- Agent infrastructure (APScheduler with 5 jobs, weekly digest, monthly tenant check-ins, inbound message pipeline, LLM wrapper isolated in single module)

**Next in order:**
1. Full end-to-end test: fresh property → bank statement workflow → SMS sandbox → M-Pesa STK Push sandbox → disbursement
2. Phase 4 (The Conversation): LLM intent classifier, tenant/caretaker/owner inbound handlers, bilingual responses (English + Kiswahili)
3. Phase 5 (The Coordinator): admin task feed, anomaly detection, caretaker morning briefing
4. WhatsApp live channel — gated on Meta approval via Africa's Talking (apply now, 2–6 week lead time)
5. Flip Africa's Talking from sandbox → live, set Daraja/Pesapal to production when credentials arrive

---

## Revenue Model

Management fee (~8%) embedded in the landlord disbursement spread — not a visible line item. Tenants pay Domi, Domi disburses to landlord net of fee. Landlord never sees an invoice. At scale: volume of funds in transit × spread = revenue. Infrastructure pricing, not SaaS pricing.

---

What would you like to think through?
