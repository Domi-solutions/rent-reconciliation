# Rent Reconciliation System

A Flask web application for rental management agencies. Features deterministic PDF parsing for bank statements, SMS parsing for M-Pesa claims, Excel import for property onboarding, water charge uploads, FIFO payment allocation across charges (rent/service/water), and comprehensive export reports.

## Setup

```bash
# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the Flask app
python app.py

# Access at http://localhost:5000
# Admin dashboard: http://localhost:5000/
# Owner viewer: http://localhost:5000/view/
```

**Environment Variables (optional):**
- `VIEWER_PASSWORD` - Password for owner viewer access (if not set, viewer is open)
- `SECRET_KEY` - Flask secret key (defaults to dev key if not set)

## Usage

### Web Application

1. **Onboard Property:** Upload Excel with units/tenants at `/onboard`
2. **Monthly Charges:**
   - Upload water readings Excel at `/charges/water` (Step 1)
   - Generate rent + service charges at `/charges/generate` (Step 2)
3. **Process Payments:**
   - Tenants report payments via SMS (creates claims)
   - Upload bank statement PDF at `/statements`
   - Auto-verify claims at `/verify` (payments allocate FIFO automatically)
4. **Exports:**
   - Current State: `/export/current-state`
   - Activity Log: `/export/activity?from=YYYY-MM-DD&to=YYYY-MM-DD`
   - Payment Verification: `/export/payments?from=YYYY-MM-DD&to=YYYY-MM-DD`

### Command Line Parsers (for testing)

```bash
# Parse bank statement
python src/parsers/pdf_parser.py data/input/statement.pdf

# Parse water readings Excel
python -c "from src.parsers.water_parser import parse_water_excel; print(parse_water_excel('water_readings.xlsx'))"
```

### Run Tests

```bash
python tests/test_parser.py
```

## Project Structure

```
rent-reconciliation/
├── app.py                     # Main Flask app, all admin routes
├── src/
│   ├── parsers/
│   │   ├── pdf_parser.py      # Bank statement extraction
│   │   ├── sms_parser.py      # M-Pesa SMS parsing
│   │   ├── excel_parser.py    # Tenant Excel import
│   │   └── water_parser.py   # Water readings Excel parser
│   ├── routes/
│   │   ├── test_routes.py    # /test/* - parser & CRUD testing
│   │   └── viewer_routes.py # /view/* - owner view-only routes
│   ├── database/
│   │   ├── db.py             # Connection, migrations, allocation
│   │   └── schema.sql        # Canonical table definitions
│   └── reconciliation/
│       └── matcher.py        # SMS-to-bank matching
├── templates/
│   ├── base.html             # Admin base template
│   ├── viewer/               # Owner viewer templates
│   └── ...
├── data/
│   ├── rent.db              # SQLite database
│   └── statements/          # Uploaded PDFs
└── requirements.txt
```

## Features

### Core Functionality
- **Property Onboarding**: Excel upload creates units, tenants, and arrears charges
- **Monthly Charges**: Three types (rent, service, water) tracked separately per unit per period
- **Payment Processing**: Auto-verification of SMS claims against bank transactions
- **FIFO Allocation**: Payments automatically allocate to oldest charges first (regardless of type)
- **Owner Viewer**: View-only dashboard at `/view/<property_id>` (password-protected)
- **Exports**: Current state, activity logs, and payment verification reports (Excel)

### PDF Parser
- **Deterministic parsing**: Rule-based extraction, no ML/LLM
- **Balance checksum**: Validates parsing accuracy
- **Transaction classification**: Uses 3-digit codes (014 = Paybill credits/rent)
- **Split amount handling**: Reconstructs amounts split across PDF lines
- **Reference extraction**: Extracts M-Pesa reference codes for matching
- **Edge case handling**: Missing senders, reversals, settlements

### SMS Parser
- **Multiple format support**: Handles full messages, partial messages, and reference-code-only
- **Flexible input**: Works with just a reference code (e.g., `TLU9G289GY`)
- **Amount extraction**: Handles various formats (KES, Ksh, with/without commas)
- **Timestamp parsing**: Multiple date/time formats
- **Reconciliation**: Matches SMS claims to bank transactions by reference code

### Water Parser
- **Excel import**: Parses water meter readings with flexible column names
- **Auto-header detection**: Finds header row automatically (rows 0-2)
- **Unit matching**: Case-insensitive matching against database units
- **Duplicate prevention**: Skips existing water charges for same period

## Validation

The parser validates against known totals:
- Opening Balance: KES 676,851.41
- Closing Balance: KES 399,581.32
- Rent Transactions: ~58
- Total Rent: ~KES 946,000

## Core Principles

```
Bank Statement = SOURCE OF TRUTH
M-Pesa SMS = CLAIM TO BE VERIFIED

No tenant balance is ever updated unless a matching bank credit exists.
Payments allocate FIFO (oldest charges first, regardless of type).
```

## Database Schema

- **rent_charges**: `charge_type` ('rent' | 'service' | 'water'), UNIQUE(unit_id, period, charge_type)
- **payment_allocations**: Links payments to specific charges (FIFO allocation trail)
- **units**: `apartment_size` field for export reports
- **unit_balances** (VIEW): Aggregates all charge types and payments for balance calculation

See `CLAUDE.md` for comprehensive AI agent context and implementation details.
