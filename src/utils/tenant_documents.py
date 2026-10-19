"""
Monthly invoice and payment receipt for one tenant.

Both are cut from the same ledger as the statement (get_tenant_statement), so a
tenant's statement, invoices and receipts can never disagree with each other.
Everything is scoped to the tenant (tenant_id), never to the unit.
"""

import calendar

from src.utils.statement import get_tenant_statement

CHARGE_LABELS = {'rent': 'Rent', 'service': 'Service charge', 'water': 'Water'}


def period_label(period):
    """'2026-10' → 'October 2026'; 'ARREARS' → 'Arrears brought forward'."""
    if period == 'ARREARS':
        return 'Arrears brought forward'
    try:
        return f"{calendar.month_name[int(period[5:7])]} {period[:4]}"
    except (ValueError, IndexError):
        return period


def invoice_periods(rows):
    """Months that have charges, newest first."""
    return sorted({r['period'] for r in rows if r['row_type'] == 'charge' and r['period'] != 'ARREARS'},
                  reverse=True)


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
                     'paid': r['allocated'], 'remaining': r['remaining']}
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
    applied = [{'label': (CHARGE_LABELS.get(a['charge_type'], a['charge_type'].title()) + ' — '
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
