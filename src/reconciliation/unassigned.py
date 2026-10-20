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
    outstanding, and those on superseded statements, which a later upload has
    already replaced. A reference seen twice is returned once.
    """
    if org_id is None:
        row = conn.execute(
            "SELECT organization_id FROM properties WHERE id = ?", (property_id,)
        ).fetchone()
        org_id = row['organization_id'] if row else None

    rows = conn.execute("""
        SELECT bt.id, bt.mpesa_ref, bt.amount, bt.txn_date, bt.sender_name, bt.unit_hint,
               bt.raw_text,
               bs.id AS statement_id, bs.filename AS statement_filename, bs.bank_format
        FROM bank_transactions bt
        JOIN bank_statements bs ON bt.statement_id = bs.id
        WHERE (bs.property_id = ? OR (bs.property_id IS NULL AND bs.org_id = ?))
          AND bt.txn_type = 'PAYBILL_CREDIT'
          AND (bt.ignored IS NULL OR bt.ignored = 0)
          AND COALESCE(bs.status, '') != 'superseded'
          AND bt.id NOT IN (SELECT bank_txn_id FROM payments WHERE bank_txn_id IS NOT NULL)
          -- Excluding by row id alone is not enough where a statement was
          -- loaded twice: assigning one twin leaves the other as the only
          -- surviving row for that reference, so it reappears as unclaimed
          -- money and the worksheet never comes clean. A reference that has
          -- been paid through ANY row is settled.
          AND (bt.mpesa_ref IS NULL OR UPPER(TRIM(bt.mpesa_ref)) NOT IN (
                SELECT UPPER(TRIM(paid.mpesa_ref))
                FROM bank_transactions paid
                JOIN payments p2 ON p2.bank_txn_id = paid.id
                WHERE paid.mpesa_ref IS NOT NULL AND TRIM(paid.mpesa_ref) != ''
          ))
        ORDER BY bt.txn_date, bt.id
    """, (property_id, org_id)).fetchall()

    # An M-Pesa reference identifies one transfer, so the same reference on two
    # rows is the same money recorded twice — not two payments. Production holds
    # the same statement uploaded more than once, with both copies marked
    # active, which doubled every transaction on it. Counting those twice
    # overstates what is outstanding and puts a line on the caretaker's
    # worksheet that cannot be assigned, because assigning either one leaves the
    # other behind.
    seen_refs = set()
    deduped = []
    for row in rows:
        ref = (row['mpesa_ref'] or '').strip().upper()
        if ref:
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
        deduped.append(row)

    unassigned = [dict(r) for r in deduped]
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

    Payments with no payer name are deliberately NOT collapsed together. They
    are not one person, and merging them produced a single line reading
    "Unknown sender — 84 payments — KES 380,675", which cannot be assigned to
    anything. Each keeps its own row with its date, amount and reference, so
    there is something to work from.
    """
    groups = {}
    unnamed = []
    for row in unassigned:
        name = (row.get('sender_name') or '').strip()
        if not name:
            unnamed.append({
                'sender_name': None,
                'payments': [row],
                'total': float(row.get('amount') or 0),
                'suggested_unit_number': row.get('suggested_unit_number'),
                'suggested_tenant_name': row.get('suggested_tenant_name'),
                'suggestion_source': row.get('suggestion_source'),
                'narration': (row.get('raw_text') or '')[:70],
            })
            continue
        group = groups.setdefault(name.upper(), {
            'sender_name': name,
            'payments': [], 'total': 0.0,
            'suggested_unit_number': None, 'suggested_tenant_name': None,
            'suggestion_source': None, 'narration': None,
        })
        group['payments'].append(row)
        group['total'] += float(row.get('amount') or 0)
        if not group['suggested_unit_number'] and row.get('suggested_unit_number'):
            group['suggested_unit_number'] = row['suggested_unit_number']
            group['suggested_tenant_name'] = row.get('suggested_tenant_name')
            group['suggestion_source'] = row.get('suggestion_source')

    named = sorted(groups.values(), key=lambda g: -g['total'])
    unnamed.sort(key=lambda g: -g['total'])
    # Named payers first: they are the quick decisions.
    return named + unnamed
