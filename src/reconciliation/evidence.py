"""One payment, several pieces of evidence.

A tenant's money can reach Domi's records four ways: the M-Pesa prompt or paybill
(Daraja callback), a line on an uploaded bank statement (the paybill settles to the
bank, so the same money shows up there again, sometimes on several uploaded copies),
and an M-Pesa SMS pasted by the tenant, the caretaker or management.

The M-Pesa receipt code is the key. One code is one payment (`payments.mpesa_ref`,
stamped by trigger; a second payment with a code already booked in the organisation
is refused by trigger `trg_payments_one_per_ref`). Later evidence for the same code
is attached to the payment that exists instead of creating another:

- a bank line → `settle_statement` links it (`payments.bank_txn_id`), or, when the
  payment already has a bank line (a second copy of the statement), sets the line
  aside as ignored with the reason — it is never offered for matching again;
- a pasted code → `attach_claim` answers the claim from the payment.
"""

DUPLICATE = 'DUPLICATE_MPESA_REF'          # the trigger's abort message


def norm(ref):
    ref = (ref or '').strip().upper()
    return ref or None


def booked(conn, ref, org_id=None):
    """The payment already recorded for this code (any source), or None."""
    ref = norm(ref)
    if not ref:
        return None
    sql = """SELECT p.id, p.tenant_id, p.unit_id, p.property_id, p.amount, p.payment_date, p.bank_txn_id,
                    p.claim_id, p.source, u.unit_number, t.name AS tenant_name
             FROM payments p
             LEFT JOIN units u ON u.id = p.unit_id
             LEFT JOIN tenants t ON t.id = p.tenant_id
             LEFT JOIN properties pr ON pr.id = p.property_id
             WHERE p.mpesa_ref = ?"""
    params = [ref]
    if org_id:
        sql += " AND pr.organization_id = ?"
        params.append(org_id)
    return conn.execute(sql + " ORDER BY p.created_at, p.rowid LIMIT 1", params).fetchone()


def describe(payment):
    """'KES 19,000 for house 6F (Jane Doe) on 2026-03-02' — for messages to staff."""
    who = f" ({payment['tenant_name']})" if payment['tenant_name'] else ''
    return (f"KES {float(payment['amount'] or 0):,.0f} for house {payment['unit_number'] or '?'}{who} "
            f"on {(payment['payment_date'] or '')[:10] or '?'}")


def already_recorded_text(ref, payment):
    return f"M-Pesa code {norm(ref)} is already recorded: {describe(payment)}. Nothing was added."


def attach_claim(conn, claim_id, payment, actor='system'):
    """Answer a pasted code from the payment already recorded for it: the claim is verified
    and linked. Nothing new is booked."""
    if not payment['claim_id']:
        conn.execute("UPDATE payments SET claim_id = ? WHERE id = ? AND claim_id IS NULL", (claim_id, payment['id']))
    conn.execute("""UPDATE payment_claims SET status = 'verified', verified_at = CURRENT_TIMESTAMP, flag_reason = NULL
                    WHERE id = ?""", (claim_id,))
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('claim_matched_existing_payment', 'claim', claim_id,
                  f"Already recorded as payment {payment['id']} — {describe(payment)}", actor))


def settle_statement(conn, statement_id, org_id=None):
    """Bank lines on this statement whose M-Pesa code is already a payment stop asking to be matched.

    → (linked, set_aside): lines attached to an M-Pesa payment that had no bank line yet,
    and repeat lines (another copy of a statement) set aside as ignored. A line whose amount
    differs from the recorded payment is left for a person to look at."""
    linked = set_aside = 0
    lines = conn.execute("""
        SELECT bt.* FROM bank_transactions bt
        WHERE bt.statement_id = ? AND bt.txn_type = 'PAYBILL_CREDIT' AND COALESCE(bt.mpesa_ref, '') != ''
          AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.bank_txn_id = bt.id)
    """, (statement_id,)).fetchall()
    for line in lines:
        payment = booked(conn, line['mpesa_ref'], org_id)
        if not payment or abs(float(payment['amount'] or 0) - float(line['amount'] or 0)) > 1:
            continue
        if not payment['bank_txn_id']:
            conn.execute("UPDATE payments SET bank_txn_id = ? WHERE id = ? AND bank_txn_id IS NULL",
                         (line['id'], payment['id']))
            conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                         ('bank_line_linked', 'payment', payment['id'],
                          f"Ref: {norm(line['mpesa_ref'])} | bank line {line['id']} is the same money as this "
                          f"{payment['source'] or 'recorded'} payment — nothing new booked", 'system'))
            linked += 1
        elif not line['ignored']:
            conn.execute("""UPDATE bank_transactions SET ignored = 1, ignored_at = CURRENT_TIMESTAMP, ignored_reason = ?
                            WHERE id = ?""",
                         (f"Already recorded: payment {payment['id']} has the same M-Pesa code", line['id']))
            conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                         ('bank_line_repeat', 'bank_transaction', line['id'],
                          f"Ref: {norm(line['mpesa_ref'])} | repeat of payment {payment['id']} "
                          f"(another copy of a statement) — set aside", 'system'))
            set_aside += 1
    return linked, set_aside


def resolve_claim(conn, claim, org_id=None, actor='system'):
    """A pasted code whose payment is already recorded (by M-Pesa, a bank line, or staff).

    → ('attached', payment) when it is this claim's tenant's (or, with no tenant on the claim,
    this house's): the claim is answered, nothing new is booked. → ('held', payment) when the
    code is on someone else's account: held for management (claim_owner), never called fake.
    → (None, None) when the code is not recorded yet."""
    payment = booked(conn, claim['mpesa_ref'], org_id)
    if not payment:
        return None, None
    keys = claim.keys()
    claim_tenant = claim['tenant_id'] if 'tenant_id' in keys else None
    departed = claim['departed_tenant_id'] if 'departed_tenant_id' in keys else None
    if payment['tenant_id'] and payment['tenant_id'] in (claim_tenant, departed):
        mine = True
    else:
        mine = not claim_tenant and payment['unit_id'] == claim['unit_id']
    if mine:
        attach_claim(conn, claim['id'], payment, actor)
        return 'attached', payment
    from src.reconciliation import claim_owner
    already_held = ('status' in keys and 'flag_reason' in keys and claim['status'] == 'flagged'
                    and claim['flag_reason'] == claim_owner.ON_OTHER_ACCOUNT)
    if not already_held:
        claim_owner.hold(conn, claim, claim_owner.ON_OTHER_ACCOUNT, org_id=org_id)
    return 'held', payment
