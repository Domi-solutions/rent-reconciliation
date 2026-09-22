#!/usr/bin/env python3
"""
Read-only reconciliation audit: bank statement PDFs vs. what the database holds.

Answers four questions without writing a single byte to the database:

  1. What does the database think it is owed, and by whom?
  2. Which statement periods has it actually seen, and where are the gaps?
  3. Does every M-Pesa reference in these PDFs exist in bank_transactions?
  4. Is any tenant flagged for a reference that is, in fact, present?

Question 4 matters because of the sub-KES-1,000 amount defect fixed in
e069890: a credit under 1,000 never became a bank_transactions row, so the
tenant's claim was flagged for a "missing" reference and — per the claim
rules — tenants.flagged was set permanently. Any tenant this report names
was flagged by arithmetic rather than by evidence.

The database is opened mode=ro so an accidental run against a production
copy cannot alter it, and so that importing the app's migrations (which
write on import) is avoided entirely.

Usage:
    ./venv/bin/python scripts/audit_reconciliation.py --db data/dev.db \
        --pdf statement1.pdf --pdf statement2.pdf
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.parsers.pdf_parser import parse_bank_statement  # noqa: E402


def connect_readonly(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        sys.exit(f"No database at {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1", (name,)
    ).fetchone()
    return row is not None


def heading(text: str) -> None:
    print(f"\n{text}\n{'=' * len(text)}")


def money(value) -> str:
    return f"{Decimal(str(value or 0)):,.2f}"


def report_portfolio(conn) -> None:
    heading("1. WHAT THE DATABASE HOLDS")
    for table in ("organizations", "properties", "units", "tenants",
                  "bank_statements", "bank_transactions", "payments",
                  "payment_claims", "rent_charges"):
        if not has_table(conn, table):
            print(f"  {table:<20} (table absent)")
            continue
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<20} {count:>8,}")

    if not has_table(conn, "properties"):
        return
    print()
    for prop in conn.execute(
        "SELECT id, name, rent_due_day FROM properties ORDER BY name"
    ):
        units = conn.execute(
            "SELECT COUNT(*) FROM units WHERE property_id = ?", (prop["id"],)
        ).fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM tenants WHERE property_id = ? AND status = 'active'",
            (prop["id"],),
        ).fetchone()[0]
        print(f"  {prop['name']}  —  {units} units, {active} active tenants "
              f"(rent due day {prop['rent_due_day']})")


def report_arrears(conn) -> None:
    heading("2. WHAT IS OWED")
    if not has_table(conn, "rent_charges"):
        print("  rent_charges absent — cannot compute arrears.")
        return

    # Charges and payments are compared per unit. unit_balances already scopes
    # both to the sitting tenant's move_in_date, so a new tenant does not
    # inherit the previous one's history; fall back to raw sums without it.
    if has_table(conn, "unit_balances"):
        # Columns come from the view definition, not from the first row: an
        # empty portfolio would otherwise look like a view with no balance.
        rows = conn.execute(
            "SELECT unit_number, tenant_name, monthly_rent, total_charged, "
            "total_paid, balance FROM unit_balances ORDER BY balance DESC"
        ).fetchall()
        owing = [r for r in rows if Decimal(str(r["balance"] or 0)) > 0]
        credit = [r for r in rows if Decimal(str(r["balance"] or 0)) < 0]
        total = sum(Decimal(str(r["balance"])) for r in owing)
        charged = sum(Decimal(str(r["total_charged"] or 0)) for r in rows)
        collected = sum(Decimal(str(r["total_paid"] or 0)) for r in rows)

        print("  (via unit_balances — charges and payments scoped to the "
              "sitting tenant)\n")
        print(f"  charged to date    KES {money(charged)}")
        print(f"  collected to date  KES {money(collected)}")
        print(f"  outstanding        KES {money(total)} across {len(owing)} unit(s)")
        if credit:
            print(f"  in credit          KES "
                  f"{money(-sum(Decimal(str(r['balance'])) for r in credit))} "
                  f"across {len(credit)} unit(s)")
        if not owing:
            return
        print(f"\n  {'UNIT':<10}{'TENANT':<28}{'RENT':>11}{'CHARGED':>13}"
              f"{'PAID':>13}{'OWES':>13}")
        for r in owing[:40]:
            months = ""
            rent = Decimal(str(r["monthly_rent"] or 0))
            if rent > 0:
                months = f"  ({Decimal(str(r['balance'])) / rent:.1f} mo)"
            print(f"  {str(r['unit_number'] or '?'):<10}"
                  f"{str(r['tenant_name'] or '(vacant)')[:27]:<28}"
                  f"{money(r['monthly_rent']):>11}{money(r['total_charged']):>13}"
                  f"{money(r['total_paid']):>13}{money(r['balance']):>13}{months}")
        if len(owing) > 40:
            print(f"  ... and {len(owing) - 40} more")
        return

    charged = conn.execute(
        "SELECT unit_id, SUM(amount) AS total FROM rent_charges GROUP BY unit_id"
    ).fetchall()
    paid = dict(conn.execute(
        "SELECT unit_id, SUM(amount) FROM payments GROUP BY unit_id"
    ).fetchall())
    balances = []
    for row in charged:
        owed = Decimal(str(row["total"] or 0)) - Decimal(str(paid.get(row["unit_id"], 0)))
        if owed > 0:
            balances.append((row["unit_id"], owed))
    balances.sort(key=lambda pair: -pair[1])
    print(f"  {len(balances)} units in arrears, "
          f"totalling KES {money(sum(b for _, b in balances))}\n")
    for unit_id, owed in balances[:40]:
        unit = conn.execute(
            "SELECT unit_number FROM units WHERE id = ?", (unit_id,)
        ).fetchone()
        print(f"    {(unit['unit_number'] if unit else unit_id):<12} {money(owed):>14}")


def report_statement_coverage(conn) -> None:
    heading("3. STATEMENT COVERAGE")
    if not has_table(conn, "bank_statements"):
        print("  bank_statements absent.")
        return
    rows = conn.execute(
        "SELECT filename, bank_format, period_start, period_end, status, "
        "total_transactions, uploaded_at FROM bank_statements "
        "ORDER BY period_start"
    ).fetchall()
    if not rows:
        print("  No statements recorded — the database has never ingested one.")
        return
    for r in rows:
        print(f"  {str(r['period_start']):<12} → {str(r['period_end']):<12} "
              f"{str(r['bank_format']):<16} {str(r['status']):<14} "
              f"{r['total_transactions'] or 0:>5} txns   {r['filename']}")
    print(f"\n  Latest period end on record: {rows[-1]['period_end']}")


def report_pdf_vs_db(conn, pdf_paths) -> None:
    heading("4. THESE STATEMENTS vs. bank_transactions")
    if not has_table(conn, "bank_transactions"):
        print("  bank_transactions absent.")
        return

    known = {
        (row[0] or "").strip().upper()
        for row in conn.execute("SELECT mpesa_ref FROM bank_transactions")
        if row[0]
    }
    print(f"  {len(known):,} references already in bank_transactions\n")

    grand_missing = Decimal(0)
    for path in pdf_paths:
        result = parse_bank_statement(path)
        credits = [t for t in result["transactions"] if t.txn_type == "PAYBILL_CREDIT"]
        missing = [t for t in credits if (t.reference or "").upper() not in known]
        total = sum(t.amount for t in credits)
        gap = sum(t.amount for t in missing)
        grand_missing += gap

        print(f"  {os.path.basename(path)}")
        print(f"    parsed {len(credits)} credits worth KES {money(total)}; "
              f"validation {'passed' if result['success'] else 'FAILED'}")
        print(f"    {len(missing)} not in the database, worth KES {money(gap)}")
        small = [t for t in missing if t.amount < 1000]
        if small:
            print(f"    of these, {len(small)} are under KES 1,000 — the amounts "
                  f"the pre-e069890 parser could not read:")
            for t in small:
                print(f"      {t.transaction_date}  {t.reference}  "
                      f"{money(t.amount):>10}  {t.sender}")
        print()
    print(f"  Unrecorded across all statements: KES {money(grand_missing)}")


def report_suspect_flags(conn, pdf_paths) -> None:
    heading("5. TENANTS FLAGGED FOR REFERENCES THAT DO EXIST")
    if not (has_table(conn, "tenants") and has_table(conn, "payment_claims")):
        print("  tenants or payment_claims absent.")
        return

    refs_in_pdfs = {}
    for path in pdf_paths:
        for t in parse_bank_statement(path)["transactions"]:
            if t.txn_type == "PAYBILL_CREDIT" and t.reference:
                refs_in_pdfs[t.reference.upper()] = t

    flagged = conn.execute(
        "SELECT id, name, unit_id, flagged FROM tenants WHERE flagged = 1"
    ).fetchall()
    if not flagged:
        print("  No tenant currently carries flagged = 1.")
        return
    print(f"  {len(flagged)} tenant(s) flagged. Checking each against these statements:\n")

    for tenant in flagged:
        claims = conn.execute(
            "SELECT mpesa_ref, claimed_amount, status, flag_reason FROM payment_claims "
            "WHERE mpesa_ref IS NOT NULL AND status = 'flagged' AND unit_id = ?",
            (tenant["unit_id"],),
        ).fetchall()
        vindicating = [
            c for c in claims if (c["mpesa_ref"] or "").upper() in refs_in_pdfs
        ]
        if vindicating:
            print(f"  {tenant['name']}  — flagged, but these references ARE on the statements:")
            for c in vindicating:
                txn = refs_in_pdfs[c["mpesa_ref"].upper()]
                print(f"      {c['mpesa_ref']}  bank says KES {money(txn.amount)} "
                      f"on {txn.transaction_date}; claim said {money(c['claimed_amount'])}")
            print("      -> review before this flag is treated as evidence of anything.\n")
        else:
            print(f"  {tenant['name']}  — no vindicating reference in these statements.\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/dev.db")
    ap.add_argument("--pdf", action="append", default=[],
                    help="statement PDF; repeat for several")
    args = ap.parse_args()

    for path in args.pdf:
        if not os.path.exists(path):
            sys.exit(f"No PDF at {path}")

    conn = connect_readonly(args.db)
    print(f"Auditing {args.db} (read-only) against {len(args.pdf)} statement(s)")

    report_portfolio(conn)
    report_arrears(conn)
    report_statement_coverage(conn)
    if args.pdf:
        report_pdf_vs_db(conn, args.pdf)
        report_suspect_flags(conn, args.pdf)
    conn.close()
    print("\nNothing was written. Applying corrections is a separate, deliberate step.")


if __name__ == "__main__":
    main()
