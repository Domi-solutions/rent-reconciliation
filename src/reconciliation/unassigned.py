"""
Money received that no unit has been credited with.

One definition, used by the caretaker screen, the arrears report and the
assignment worksheet, so the three never disagree about how much is sitting
unmatched. They did disagree before: the caretaker screen looked only at
statements tagged to the property, while a statement uploaded org-wide has a
NULL property_id and was invisible to it.
"""

from src.reconciliation.matcher import enrich_with_suggestions


def get_unassigned_credits(conn, property_id, org_id=None):
    """Paybill credits on this property's statements with no payment against them.

    Includes statements left org-wide (property_id NULL), which is how the
    upload flow stores them when no property is chosen. Excludes transactions
    marked ignored, which stay assignable but must not be counted as
    outstanding.
    """
    if org_id is None:
        row = conn.execute(
            "SELECT organization_id FROM properties WHERE id = ?", (property_id,)
        ).fetchone()
        org_id = row['organization_id'] if row else None

    rows = conn.execute("""
        SELECT bt.id, bt.mpesa_ref, bt.amount, bt.txn_date, bt.sender_name, bt.unit_hint,
               bs.id AS statement_id, bs.filename AS statement_filename, bs.bank_format
        FROM bank_transactions bt
        JOIN bank_statements bs ON bt.statement_id = bs.id
        WHERE (bs.property_id = ? OR (bs.property_id IS NULL AND bs.org_id = ?))
          AND bt.txn_type = 'PAYBILL_CREDIT'
          AND (bt.ignored IS NULL OR bt.ignored = 0)
          AND bt.id NOT IN (SELECT bank_txn_id FROM payments WHERE bank_txn_id IS NOT NULL)
        ORDER BY bt.txn_date, bt.id
    """, (property_id, org_id)).fetchall()

    unassigned = [dict(r) for r in rows]
    enrich_with_suggestions(unassigned, conn, property_id, org_id)

    for row in unassigned:
        row['suggested_tenant_id'] = None
        if row.get('suggested_unit_id'):
            tenant = conn.execute(
                "SELECT id, name FROM tenants WHERE unit_id = ? AND status = 'active'",
                (row['suggested_unit_id'],)
            ).fetchone()
            if tenant:
                row['suggested_tenant_id'] = tenant['id']
                row['suggested_tenant_name'] = row.get('suggested_tenant_name') or tenant['name']
    return unassigned


def group_by_sender(unassigned):
    """Collapse to one row per payer.

    A caretaker identifies people, not transactions: ninety-odd payments are
    perhaps thirty payers, and deciding once per payer is the difference
    between a job and an afternoon.
    """
    groups = {}
    for row in unassigned:
        key = (row.get('sender_name') or 'Unknown sender').strip().upper()
        group = groups.setdefault(key, {
            'sender_name': row.get('sender_name') or 'Unknown sender',
            'payments': [], 'total': 0.0,
            'suggested_unit_number': None, 'suggested_tenant_name': None,
            'suggestion_source': None,
        })
        group['payments'].append(row)
        group['total'] += float(row.get('amount') or 0)
        if not group['suggested_unit_number'] and row.get('suggested_unit_number'):
            group['suggested_unit_number'] = row['suggested_unit_number']
            group['suggested_tenant_name'] = row.get('suggested_tenant_name')
            group['suggestion_source'] = row.get('suggestion_source')

    return sorted(groups.values(), key=lambda g: -g['total'])
