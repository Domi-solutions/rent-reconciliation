"""
Shared tenant statement builder and quick-verify helper.
Used by admin, caretaker, and owner viewer routes.
"""
import re


def quick_verify_ref(conn, text, unit_id, org_id):
    """
    Parse an M-Pesa message or bare ref code, then search bank_transactions.

    Returns a dict with keys:
      status  — 'found' | 'already_this_unit' | 'already_other_unit' | 'not_found' | 'parse_error'
      ref     — the extracted ref code
      amount, date, sender, statement_filename  — from the bank row (if found)
      txn_id  — bank_transactions.id (when status='found')
      assigned_unit — unit_number (when already assigned)
      parsed_amount, parsed_sender, parsed_date — from SMS parse (when not_found)
      message — human-readable error (when parse_error)
    """
    from src.parsers.sms_parser import parse_mpesa_message

    text = (text or '').strip()
    if not text:
        return {'status': 'parse_error', 'message': 'No input provided'}

    parsed = parse_mpesa_message(text)
    if parsed.get('success'):
        ref = parsed['reference']
        parsed_amount = parsed.get('amount')
        parsed_sender = parsed.get('sender_name') or parsed.get('sender')
        parsed_date   = str(parsed.get('payment_date') or '')
    else:
        # Treat bare ref codes like "UD19DBDCL3" directly
        bare = re.sub(r'\s+', '', text).upper()
        if re.match(r'^[A-Z0-9]{8,14}$', bare):
            ref           = bare
            parsed_amount = None
            parsed_sender = None
            parsed_date   = None
        else:
            return {'status': 'parse_error',
                    'message': parsed.get('error', 'Could not extract a reference code from the input')}

    # Look up in bank_transactions (org-scoped)
    txn = conn.execute("""
        SELECT bt.id, bt.mpesa_ref, bt.amount, bt.txn_date, bt.sender_name,
               bt.statement_id, bs.filename AS statement_filename
        FROM bank_transactions bt
        JOIN bank_statements bs ON bs.id = bt.statement_id
        WHERE bs.org_id = ? AND bt.mpesa_ref = ?
    """, (org_id, ref)).fetchone()

    if not txn:
        return {
            'status':         'not_found',
            'ref':            ref,
            'parsed_amount':  float(parsed_amount) if parsed_amount else None,
            'parsed_sender':  parsed_sender,
            'parsed_date':    parsed_date,
        }

    # Check whether the transaction is already assigned
    payment = conn.execute("""
        SELECT p.id, p.unit_id, u.unit_number
        FROM payments p JOIN units u ON u.id = p.unit_id
        WHERE p.bank_txn_id = ?
    """, (txn['id'],)).fetchone()

    base = {
        'ref':                ref,
        'amount':             float(txn['amount']),
        'date':               str(txn['txn_date'] or ''),
        'sender':             txn['sender_name'],
        'statement_filename': txn['statement_filename'],
    }

    if payment:
        base['status']        = 'already_this_unit' if payment['unit_id'] == unit_id else 'already_other_unit'
        base['assigned_unit'] = payment['unit_number']
        return base

    base['status'] = 'found'
    base['txn_id'] = txn['id']
    return base


def get_tenant_statement(conn, tenant_id, property_id):
    """
    Returns (tenant_dict, ledger_rows, open_claims).

    tenant_dict   — all tenant columns + unit_number, monthly_rent
    ledger_rows   — list of dicts sorted by date with running_balance
    open_claims   — pending/flagged claims with no matching payment
    """
    tenant = conn.execute("""
        SELECT t.*, u.unit_number, u.monthly_rent, u.id AS unit_id_check
        FROM tenants t
        LEFT JOIN units u ON t.unit_id = u.id
        WHERE t.id = ? AND t.property_id = ?
    """, (tenant_id, property_id)).fetchone()

    if not tenant:
        return None, [], []

    tenant = dict(tenant)
    unit_id    = tenant['unit_id']
    move_in    = tenant.get('move_in_date') or '2000-01-01'
    move_in_period = move_in[:7]  # YYYY-MM

    # ── Charges ────────────────────────────────────────────────
    # Include ARREARS period explicitly — string sort puts 'ARREARS' < any 'YYYY-MM'
    charges = conn.execute("""
        SELECT
            rc.id,
            COALESCE(rc.due_date, rc.created_at) AS row_date,
            rc.period,
            rc.charge_type,
            rc.amount,
            COALESCE(
                (SELECT SUM(pa.amount) FROM payment_allocations pa
                 WHERE pa.charge_id = rc.id),
                0
            ) AS allocated
        FROM rent_charges rc
        WHERE rc.unit_id = ? AND (rc.period >= ? OR rc.period = 'ARREARS')
        ORDER BY CASE WHEN rc.period = 'ARREARS' THEN '0000-00' ELSE rc.period END,
                 rc.charge_type
    """, (unit_id, move_in_period)).fetchall()

    # ── Payments ───────────────────────────────────────────────
    payments = conn.execute("""
        SELECT
            p.id,
            p.payment_date AS row_date,
            p.amount,
            p.source,
            p.notes,
            COALESCE(bt.mpesa_ref, pc.mpesa_ref) AS mpesa_ref,
            bt.sender_name
        FROM payments p
        LEFT JOIN bank_transactions bt ON bt.id = p.bank_txn_id
        LEFT JOIN payment_claims pc ON pc.id = p.claim_id
        WHERE p.unit_id = ? AND p.payment_date >= ?
        ORDER BY p.payment_date
    """, (unit_id, move_in)).fetchall()

    # ── Open claims (no matching payment yet) ─────────────────
    raw_claims = conn.execute("""
        SELECT pc.id, pc.mpesa_ref, pc.claimed_amount, pc.status,
               pc.created_at, pc.flag_reason, pc.raw_message,
               pc.source, pc.mpesa_period, pc.flagged_at,
               pc.caretaker_confirmed, pc.caretaker_note,
               pc.admin_cleared, pc.admin_note
        FROM payment_claims pc
        WHERE pc.unit_id = ?
          AND pc.status IN ('pending', 'flagged')
          AND (pc.admin_cleared IS NULL OR pc.admin_cleared = 0)
          AND NOT EXISTS (
              SELECT 1 FROM payments p WHERE p.claim_id = pc.id
          )
        ORDER BY pc.created_at DESC
    """, (unit_id,)).fetchall()

    # For each flagged claim, find which bank statements covered (and thus checked) its period
    org_id = conn.execute(
        "SELECT p.organization_id FROM properties p WHERE p.id = ?", (property_id,)
    ).fetchone()
    org_id_val = org_id['organization_id'] if org_id else None

    open_claims = []
    for c in raw_claims:
        claim = dict(c)
        claim['source_label'] = {
            'caretaker': 'Caretaker portal',
            'web':       'Admin',
            'whatsapp':  'WhatsApp',
            'sms':       'SMS',
        }.get(claim.get('source') or 'web', 'Admin')

        # Statements that were checked against this claim's period
        if claim.get('mpesa_period') and org_id_val:
            stmts = conn.execute("""
                SELECT DISTINCT bs.filename, bs.uploaded_at
                FROM bank_statements bs
                JOIN bank_transactions bt ON bt.statement_id = bs.id
                WHERE bs.org_id = ?
                  AND strftime('%Y-%m', bt.txn_date) = ?
                ORDER BY bs.uploaded_at DESC
            """, (org_id_val, claim['mpesa_period'])).fetchall()
            claim['checked_statements'] = [dict(s) for s in stmts]
        else:
            claim['checked_statements'] = []

        open_claims.append(claim)

    # ── Build ledger ───────────────────────────────────────────
    CHARGE_LABELS = {'rent': 'Rent', 'service': 'Service charge', 'water': 'Water charge'}

    rows = []
    for c in charges:
        allocated = float(c['allocated'] or 0)
        amount    = float(c['amount'])
        if allocated >= amount:
            status = 'Paid'
        elif allocated > 0:
            status = 'Partial'
        else:
            status = 'Outstanding'
        label = CHARGE_LABELS.get(c['charge_type'], c['charge_type'].title())
        is_arrears = c['period'] == 'ARREARS'
        description = f"Opening arrears — {label}" if is_arrears else f"{label} — {c['period']}"
        rows.append({
            'row_type':    'charge',
            'row_date':    c['row_date'],
            'period':      c['period'],
            'description': description,
            'is_arrears':  is_arrears,
            'debit':       amount,
            'credit':      None,
            'ref':         None,
            'sender':      None,
            'status':      status,
            'notes':       None,
        })

    for p in payments:
        source_label = {'daraja': 'M-Pesa Paybill', 'pesapal': 'Card'}.get(p['source'], 'M-Pesa')
        rows.append({
            'row_type':    'payment',
            'row_date':    p['row_date'],
            'period':      None,
            'description': source_label,
            'is_arrears':  False,
            'debit':       None,
            'credit':      float(p['amount']),
            'ref':         p['mpesa_ref'],
            'sender':      p['sender_name'],
            'status':      'Verified',
            'notes':       p['notes'],
        })

    # Sort: ARREARS first, then by date asc; on same date charges before payments
    rows.sort(key=lambda x: (
        '0' if x['is_arrears'] else '1',
        x['row_date'] or '',
        0 if x['row_type'] == 'charge' else 1
    ))

    # Running balance
    balance = 0.0
    for r in rows:
        if r['row_type'] == 'charge':
            balance += r['debit']
        else:
            balance -= r['credit']
        r['running_balance'] = round(balance, 2)

    return tenant, rows, open_claims
