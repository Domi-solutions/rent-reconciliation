# Rent Reconciliation

Financial intelligence layer for rental property management. Built for a Kenyan property agency — surfaces what the data shows, makes no claims about actions taken.

## What it does

- Parse M-Pesa bank statement PDFs → extract transactions
- Match tenant SMS payment claims → verify against bank records
- FIFO allocation of payments across rent / service / water charges
- Live dashboard: collection gap, arrears concentration, vacancy cost
- Monthly report generator: collections, occupancy, tenant movement, arrears
- Owner portal: share-link + password, view-only financial dashboard
- Caretaker portal: live arrears follow-up, tenant directory, occupancy
- Tenant portal: token-based read-only view of own balance and charges
- Messaging: broadcasts, reminder templates, auto due-date reminders
- Excel exports: current state, activity log, payment audit trail

## Tech stack

Python 3.13, Flask 3.0+, SQLite, Jinja2, Bootstrap 5, pdfplumber, openpyxl. No ORM — raw SQL throughout.

## Deployed

[https://rent-reconciliation.fly.dev](https://rent-reconciliation.fly.dev) — Fly.io, Johannesburg region, persistent SQLite volume.

## Local development

```bash
# First time: pull production data
./scripts/download_prod_db.sh

# Start app (uses local copy, production untouched)
./scripts/run_dev.sh
# → http://localhost:5000  (no password in dev mode)

# Reset test database back to latest production snapshot
./scripts/reset_dev_db.sh
```

## Deploy

```bash
export PATH="$HOME/.fly/bin:$PATH"
fly deploy
```

First-time Fly.io setup: create app (`fly apps create rent-reconciliation`), create volume (`fly volumes create data_vol --region jnb --size 1`), set secrets (`fly secrets set SECRET_KEY=... ADMIN_PASSWORD=... VIEWER_PASSWORD=...`), then deploy.

## For AI agents

Read `CLAUDE.md` for technical reference (routes, schema, patterns, conventions).
Read `ROADMAP.md` for product vision, phase status, and language rules.
