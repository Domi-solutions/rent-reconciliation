"""
Monthly invoice and payment receipt for one tenant.

Both are cut from the same ledger as the statement (get_tenant_statement), so a
tenant's statement, invoices and receipts can never disagree with each other.
Everything is scoped to the tenant (tenant_id), never to the unit.
"""

import calendar
import re
from datetime import datetime

from src.utils.statement import get_tenant_statement

CHARGE_LABELS = {'rent': 'Rent', 'service': 'Service charge', 'water': 'Water',
                 'brought_forward': 'Brought forward'}


def charge_label(charge_type):
    """Display name for a charge type — also the `charge_label` template filter."""
    return CHARGE_LABELS.get(charge_type or '', (charge_type or '').replace('_', ' ').capitalize())


def money_amount(value):
    """Keep cents when present, without adding .00 to every whole-shilling amount."""
    return f'{float(value):,.2f}'.rstrip('0').rstrip('.')


def period_label(period):
    """'2026-10' → 'October 2026'; 'ARREARS' → 'Earlier unpaid bills'."""
    if period == 'ARREARS':
        return 'Earlier unpaid bills'
    try:
        return f"{calendar.month_name[int(period[5:7])]} {period[:4]}"
    except (ValueError, IndexError):
        return period


def invoice_periods(rows):
    """Months that have charges, newest first."""
    return sorted({r['period'] for r in rows if r['row_type'] == 'charge' and r['period'] != 'ARREARS'},
                  reverse=True)


def unpaid_bills(rows, balance, today=None):
    """What "Still owed" is made of: every bill with money still due on it, oldest
    first, cut from the same statement rows. `unapplied` is any confirmed payment
    not yet set against a bill (the list then adds up to more than the balance)."""
    today = today or datetime.now().strftime('%Y-%m-%d')
    order = {'rent': 0, 'service': 1, 'water': 2}
    open_rows = [r for r in rows if r['row_type'] == 'charge' and (r['remaining'] or 0) > 0.005]
    open_rows.sort(key=lambda r: (0 if r['is_arrears'] else 1, r['period'] or '',
                                  order.get(r['charge_type'], 9)))
    bills = []
    for r in open_rows:
        due = (r['row_date'] or '')[:10]
        try:
            due_label = datetime.strptime(due, '%Y-%m-%d').strftime('%-d %b %Y')
        except ValueError:
            due_label = None
        bills.append({
            'charge_id': r.get('charge_id'),
            'label': (period_label(r['period']) if r['is_arrears']
                      else f"{CHARGE_LABELS.get(r['charge_type'], (r['charge_type'] or '').title())} — {period_label(r['period'])}"),
            'amount': r['debit'],
            'paid': r['allocated'],
            'remaining': r['remaining'],
            'due': due_label,
            'late': bool(due) and due < today and not r['is_arrears'],
            'is_arrears': r['is_arrears'],
        })
    total = round(sum(b['remaining'] for b in bills), 2)
    return {'bills': bills, 'total': total, 'unapplied': round(max(total - balance, 0), 2)}


def account_coverage(conn, tenant_id):
    """Dates describe the records included, without claiming missing months are complete."""
    raw = conn.execute('SELECT DISTINCT period FROM rent_charges WHERE tenant_id = ?',
                       (tenant_id,)).fetchall()
    periods = sorted(r['period'] for r in raw if r['period'] and
                     re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', r['period']))
    period_range = None
    if periods:
        first, last = period_label(periods[0]), period_label(periods[-1])
        if periods[0] != periods[-1] and periods[0][:4] == periods[-1][:4]:
            first = calendar.month_name[int(periods[0][5:7])]
        period_range = last if periods[0] == periods[-1] else f'{first}–{last}'
    last_paid = conn.execute('SELECT MAX(payment_date) FROM payments WHERE tenant_id = ?',
                             (tenant_id,)).fetchone()[0]
    try:
        payment_date = datetime.strptime((last_paid or '')[:10], '%Y-%m-%d').strftime('%-d %B %Y')
    except ValueError:
        payment_date = None
    return {
        'first_period': period_label(periods[0]) if periods else None,
        'last_period': period_label(periods[-1]) if periods else None,
        'period_range': period_range,
        'has_brought_forward': any(r['period'] == 'ARREARS' for r in raw),
        'last_payment_date': payment_date,
    }


def build_invoice(conn, tenant_id, property_id, period):
    """Invoice for one month: balance brought forward, that month's charges,
    payments received in that month, and the balance it leaves. None if the
    tenant has no charges for the month."""
    tenant, rows, _ = get_tenant_statement(conn, tenant_id, property_id)
    if not tenant:
        return None
    charges = [r for r in rows if r['row_type'] == 'charge' and r['period'] == period]
    if not charges:
        return None
    month_start = f'{period}-01'
    earlier_charges = sum(r['debit'] for r in rows if r['row_type'] == 'charge'
                          and (r['period'] == 'ARREARS' or r['period'] < period))
    earlier_payments = sum(r['credit'] for r in rows if r['row_type'] == 'payment'
                           and (r['row_date'] or '') < month_start)
    paid_in_month = [r for r in rows if r['row_type'] == 'payment' and (r['row_date'] or '')[:7] == period]
    brought_forward = round(earlier_charges - earlier_payments, 2)
    month_total = round(sum(r['debit'] for r in charges), 2)
    paid_total = round(sum(r['credit'] for r in paid_in_month), 2)
    order = {'rent': 0, 'service': 1, 'water': 2}
    return {
        'tenant': tenant,
        'period': period,
        'label': period_label(period),
        'due_date': min((r['row_date'] or '') for r in charges)[:10],
        'brought_forward': brought_forward,
        'charges': [{'label': CHARGE_LABELS.get(r['charge_type'], r['charge_type'].title()), 'amount': r['debit'],
                     'paid': r['allocated'], 'remaining': r['remaining'],
                     'charge_type': r['charge_type'], 'water_breakdown': r.get('water_breakdown')}
                    for r in sorted(charges, key=lambda r: order.get(r['charge_type'], 9))],
        'month_total': month_total,
        'payments': paid_in_month,
        'paid_total': paid_total,
        'balance': round(brought_forward + month_total - paid_total, 2),
    }


def build_receipt(conn, tenant_id, property_id, payment_id):
    """Receipt for one payment: amount, date, reference, what it paid off, and the
    balance after it. None if the payment is not this tenant's."""
    tenant, rows, _ = get_tenant_statement(conn, tenant_id, property_id)
    if not tenant:
        return None
    row = next((r for r in rows if r['row_type'] == 'payment' and r.get('payment_id') == payment_id), None)
    if not row:
        return None
    applied = [{'label': (period_label(a['period']) if a['period'] == 'ARREARS'
                          else CHARGE_LABELS.get(a['charge_type'], a['charge_type'].title()) + ' — '
                          + period_label(a['period'])),
                'amount': float(a['amount'])} for a in row.get('allocations', [])]
    applied_total = sum(a['amount'] for a in applied)
    return {
        'tenant': tenant,
        'payment_id': payment_id,
        'date': row['row_date'],
        'amount': row['credit'],
        'ref': row['ref'],
        'sender': row['sender'],
        'method': row['description'],
        'applied': applied,
        'credit_left': round(row['credit'] - applied_total, 2),
        # Today's balance, not "balance after this payment": charges fall due on
        # their own dates, so a mid-ledger figure reads differently depending on
        # whether a charge due days later is counted — today's is unambiguous.
        'balance_today': rows[-1]['running_balance'] if rows else 0.0,
    }
