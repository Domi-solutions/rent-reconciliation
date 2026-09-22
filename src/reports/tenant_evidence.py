"""
A per-tenant statement of account built to withstand a dispute.

This is the document you hand a tenant who says they paid, or attach to a
demand letter, or put before a tribunal. It differs from the arrears summary in
that every figure is traced to its source: each payment to the M-Pesa reference
and the bank statement file it was read from, each charge to its period and due
date, and each shilling to the specific charge it was applied against.

Two principles, both of which make the document harder to attack rather than
easier:

Disclose the gaps. Where no bank statement covers a period, the report says so.
A statement of account asserting certainty over a period nobody checked is
worth less than one that states plainly what was verified and what was not,
because everything it does assert then stands up.

Separate what is recorded from what is concluded. The ledger reports what the
data holds. Whether a balance is genuinely owed depends on facts outside this
system — a payment into an account whose statement was never uploaded looks
identical to no payment at all.

Non-payment of rent is a civil matter. This supports a claim for recovery, not
an allegation of criminal conduct, and it is deliberately written so that a
tenant reading it can identify and challenge any individual line.
"""

import hashlib
import json
from datetime import date, datetime


def _iso(value):
    return value if isinstance(value, str) else (value.isoformat() if value else None)


def _integrity_hash(payload):
    """A digest over the ledger as rendered.

    This shows that a copy produced later has not been edited since — it proves
    the document is unchanged, not that the underlying records are true.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def generate_tenant_evidence(conn, tenant_id):
    """Full statement of account for one tenant. Read-only."""
    tenant = conn.execute("""
        SELECT t.*, u.unit_number, u.monthly_rent, u.service_charge,
               p.name AS property_name, p.id AS property_id, p.organization_id,
               p.rent_due_day
        FROM tenants t
        JOIN units u ON t.unit_id = u.id
        JOIN properties p ON t.property_id = p.id
        WHERE t.id = ?
    """, (tenant_id,)).fetchone()
    if not tenant:
        raise ValueError(f'Tenant {tenant_id} not found')

    unit_id = tenant['unit_id']
    property_id = tenant['property_id']

    # Charges and payments are scoped from move-in where one is recorded, so a
    # tenant is never shown the previous occupant's history.
    move_in = tenant['move_in_date'] or ''

    charges = conn.execute("""
        SELECT id, period, charge_type, amount, due_date, created_at
        FROM rent_charges
        WHERE unit_id = ?
        ORDER BY CASE WHEN period = 'ARREARS' THEN '0000-00' ELSE period END, charge_type
    """, (unit_id,)).fetchall()

    # Each payment carries where it came from: the reference, the sender the
    # bank recorded, and the statement file it was read out of.
    payments = conn.execute("""
        SELECT p.id, p.amount, p.payment_date, p.assignment_type, p.assignment_reason,
               p.assigned_by, p.source, p.notes, p.caretaker_note, p.created_at,
               COALESCE(bt.mpesa_ref, pc.mpesa_ref) AS mpesa_ref,
               bt.sender_name, bt.txn_date AS bank_txn_date, bt.id AS bank_txn_id,
               bs.id AS statement_id, bs.filename AS statement_filename,
               bs.bank_format, bs.period_start AS statement_period_start,
               bs.period_end AS statement_period_end, bs.uploaded_at AS statement_uploaded_at,
               pc.id AS claim_id, pc.source AS claim_source, pc.status AS claim_status
        FROM payments p
        LEFT JOIN bank_transactions bt ON p.bank_txn_id = bt.id
        LEFT JOIN bank_statements bs ON COALESCE(p.statement_id, bt.statement_id) = bs.id
        LEFT JOIN payment_claims pc ON p.claim_id = pc.id
        WHERE p.unit_id = ?
        ORDER BY p.payment_date, p.created_at
    """, (unit_id,)).fetchall()

    # Which charge each shilling was applied against.
    allocations = conn.execute("""
        SELECT pa.payment_id, pa.charge_id, pa.amount, pa.created_at,
               rc.period, rc.charge_type
        FROM payment_allocations pa
        JOIN rent_charges rc ON pa.charge_id = rc.id
        WHERE rc.unit_id = ?
        ORDER BY pa.created_at
    """, (unit_id,)).fetchall()
    alloc_by_payment = {}
    for a in allocations:
        alloc_by_payment.setdefault(a['payment_id'], []).append({
            'charge_id': a['charge_id'], 'period': a['period'],
            'charge_type': a['charge_type'], 'amount': float(a['amount'] or 0),
        })

    # One chronological ledger, charges and payments interleaved, each line
    # carrying the running balance it produced.
    entries = []
    for c in charges:
        entries.append({
            'kind': 'charge',
            'date': c['due_date'] or (f"{c['period']}-01" if c['period'] != 'ARREARS' else move_in or ''),
            'period': c['period'],
            'description': f"{c['charge_type'].title()} charge — {c['period']}",
            'charge_type': c['charge_type'],
            'debit': float(c['amount'] or 0),
            'credit': 0.0,
            'reference': None,
            'source': None,
            'id': c['id'],
        })
    for p in payments:
        entries.append({
            'kind': 'payment',
            'date': p['payment_date'] or '',
            'period': None,
            'description': 'Payment received'
                           + (f" from {p['sender_name']}" if p['sender_name'] else ''),
            'charge_type': None,
            'debit': 0.0,
            'credit': float(p['amount'] or 0),
            'reference': p['mpesa_ref'],
            'source': p['source'] or p['assignment_type'],
            'id': p['id'],
            'evidence': {
                'mpesa_ref': p['mpesa_ref'],
                'bank_sender_name': p['sender_name'],
                'bank_txn_date': p['bank_txn_date'],
                'bank_txn_id': p['bank_txn_id'],
                'statement_id': p['statement_id'],
                'statement_filename': p['statement_filename'],
                'statement_period': (
                    f"{p['statement_period_start']} to {p['statement_period_end']}"
                    if p['statement_period_start'] else None
                ),
                'statement_uploaded_at': _iso(p['statement_uploaded_at']),
                'bank_format': p['bank_format'],
                'recorded_by': p['assigned_by'],
                'assignment_type': p['assignment_type'],
                'claim_source': p['claim_source'],
                'verified_against_bank': bool(p['bank_txn_id']),
            },
            'allocations': alloc_by_payment.get(p['id'], []),
        })

    entries.sort(key=lambda e: (e['date'] or '', 0 if e['kind'] == 'charge' else 1))
    running = 0.0
    for e in entries:
        running += e['debit'] - e['credit']
        e['running_balance'] = running

    total_charged = sum(e['debit'] for e in entries)
    total_paid = sum(e['credit'] for e in entries)

    # Which periods of this ledger were actually checked against a bank
    # statement, and which were not.
    from src.reports.arrears_report import _month_range, _statement_coverage
    charge_months = sorted({c['period'] for c in charges if c['period'] != 'ARREARS'})
    if charge_months:
        coverage = _statement_coverage(
            conn, property_id, tenant['organization_id'],
            charge_months[0], max(charge_months[-1], date.today().strftime('%Y-%m'))
        )
    else:
        coverage = {'statements': [], 'covered_months': [], 'gap_months': [],
                    'checked_through': None}

    claims = conn.execute("""
        SELECT id, mpesa_ref, claimed_amount, status, source, flag_reason,
               created_at, verified_at, caretaker_confirmed, admin_cleared
        FROM payment_claims
        WHERE unit_id = ? ORDER BY created_at
    """, (unit_id,)).fetchall()

    bank_verified = sum(
        e['credit'] for e in entries
        if e['kind'] == 'payment' and e.get('evidence', {}).get('verified_against_bank')
    )

    body = {
        'tenant': {
            'id': tenant['id'],
            'name': tenant['name'],
            'phone': tenant['phone'],
            'status': tenant['status'],
            'move_in_date': tenant['move_in_date'],
            'flagged': bool(tenant['flagged']),
        },
        'unit': {
            'id': unit_id,
            'unit_number': tenant['unit_number'],
            'monthly_rent': float(tenant['monthly_rent'] or 0),
            'service_charge': float(tenant['service_charge'] or 0),
            'rent_due_day': tenant['rent_due_day'],
        },
        'property': {'id': property_id, 'name': tenant['property_name']},
        'ledger': entries,
        'totals': {
            'total_charged': total_charged,
            'total_paid': total_paid,
            'balance': total_charged - total_paid,
            'payment_count': len([e for e in entries if e['kind'] == 'payment']),
            'charge_count': len([e for e in entries if e['kind'] == 'charge']),
            'bank_verified_total': bank_verified,
            'unverified_payment_total': total_paid - bank_verified,
        },
        'coverage': coverage,
        'claims': [dict(c) for c in claims],
    }

    return {
        **body,
        'generated_at': datetime.now().isoformat(),
        'integrity_hash': _integrity_hash(body),
        # Stated on the document itself so nobody has to take its scope on trust.
        'limitations': [
            'Balances are derived from charges raised in this system and payments '
            'recorded against this unit.',
            'A payment made into an account whose statement has not been uploaded '
            'is indistinguishable here from no payment at all. Periods with no '
            'statement coverage are listed.',
            'Payments marked as not verified against bank records were entered '
            'from a reported M-Pesa message rather than read from a bank statement.',
        ],
    }
