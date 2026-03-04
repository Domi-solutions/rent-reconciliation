# Memory & Quick Reference

> **For AI agents:** This file provides quick access to key context. For full details, see `CLAUDE.md` (technical) and `ROADMAP.md` (product vision).

## Quick Links

- **Technical Patterns:** See `CLAUDE.md` for database schema, code patterns, routes, migrations
- **Product Vision:** See `ROADMAP.md` for design rules, phases, and implementation status
- **Deployment:** See `DEPLOY_GUIDE.md` and `STEPS.md` for deployment instructions

## Key Context

### Project Purpose
Rental property management financial intelligence layer. Surfaces what the data shows, not what the agency did.

### Design Rule (CRITICAL)
**Data-descriptive language only** — "KES 312,000 verified against bank records" NOT "We collected KES 312,000"

### Current Status
- ✅ Phase 0: Core reconciliation engine (COMPLETE)
- ✅ Phase 1: Dashboard upgrade "The Pulse" (COMPLETE)
- ✅ Phase 2: Monthly report "The Ledger" (COMPLETE)
- ⏳ Phase 3: Weekly digest "The Signal" (FUTURE - needs email/WhatsApp)
- ⏳ Phase 4: Yearly view "The Investment View" (FUTURE - needs 12+ months data)

### Database Migrations
All migrations run automatically at startup in `app.py`:
- `migrate_add_charge_type()` - Adds charge_type to rent_charges
- `migrate_add_apartment_size()` - Adds apartment_size to units
- `migrate_add_payment_allocations()` - Creates payment_allocations table
- `migrate_allocate_existing_payments()` - Backfills allocations
- `migrate_add_status_changed_at()` - Adds status_changed_at to units
- `migrate_add_landlord_reports()` - Creates report_settings and landlord_reports tables

### New Files (Phase 1 & 2)
- `src/reports/__init__.py`
- `src/reports/landlord_report.py` - Report generator (pure function)
- `src/routes/report_routes.py` - Admin report routes
- `templates/reports/generate.html` - Admin report generation form
- `templates/reports/preview.html` - Admin report preview
- `templates/reports/history.html` - Admin report history
- `templates/viewer/reports.html` - Viewer report list
- `templates/viewer/report_detail.html` - Viewer report detail

### Key Routes
- Admin: `/reports/generate`, `/reports/<report_id>`, `/reports/history`
- Viewer: `/view/<property_id>/reports`, `/view/<property_id>/reports/<report_id>`

### Deployment
- Platform: Fly.io (Johannesburg region)
- URL: https://rent-reconciliation.fly.dev/
- Deploy: `fly deploy` from project root
- Database: SQLite at `/data/rent.db` (production)

## Before Making Changes

1. Read `CLAUDE.md` for technical patterns
2. Read `ROADMAP.md` for product vision and what's complete
3. Check if feature already exists before building
4. Use data-descriptive language (see ROADMAP.md table)
5. Update ROADMAP.md checkboxes after completing work
6. Update CLAUDE.md if adding new routes/tables/files
