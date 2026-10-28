"""
Whose payment is it? The check before a tenant's own report of an M-Pesa code
books a bank line to them.

A pasted code only proves the code exists — anyone who has seen a neighbour's
M-Pesa SMS could paste it. So a tenant's self-report (payment_claims.source =
'tenant') is booked to them only when the bank line's sender is recognisably
theirs. Otherwise the claim is held for management: status 'flagged' with a
reason below, the bank line stays unassigned, and a person decides whose it is.
The tenant is not flagged (paying from a relative's phone is normal).

Claims entered by staff (caretaker, admin) are not checked here.
"""

import re

from src.platform.guardian import platform_log
from src.utils.phone import normalize_to_e164

ON_OTHER_ACCOUNT = 'on_other_account'
SENDER_NOT_TENANT = 'sender_not_tenant'

# What management sees on the flagged-claims pages.
STAFF_TEXT = {
    ON_OTHER_ACCOUNT: "This M-Pesa code is already booked to another tenant's account.",
    SENDER_NOT_TENANT: "The sender on the bank record is not this tenant — confirm whose payment it is, "
                       "then assign the bank line.",
}
# What the tenant sees.
TENANT_TEXT = {
    ON_OTHER_ACCOUNT: 'This M-Pesa code is already on another account. Management will check — '
                      'message them if the payment is yours.',
    SENDER_NOT_TENANT: 'The name on this payment is not yours, so management will confirm it before it counts. '
                       'If someone paid for you, message management and say who.',
}


def _words(name):
    return {w for w in re.findall(r'[a-z]+', (name or '').lower()) if len(w) >= 3}


def sender_is_tenant(conn, bank_txn, tenant):
    """True when the bank line's sender is recognisably this tenant."""
    sender = (bank_txn['sender_name'] or '').strip()
    mine = _words(tenant['name'])
    # 1. Name: two words in common (one if the tenant's record has only one name)
    if mine and len(_words(sender) & mine) >= min(2, len(mine)):
        return True
    # 2. The tenant's phone number appears on the bank line
    phone = normalize_to_e164(tenant['phone']) if tenant['phone'] else None
    if phone:
        digits = re.sub(r'\D', '', f"{bank_txn['raw_text'] or ''} {sender}")
        if phone[-9:] in digits:
            return True
    # 3. The same sender has paid for this tenant before (a spouse, an employer)
    if sender and conn.execute("""
        SELECT 1 FROM payments p JOIN bank_transactions bt ON bt.id = p.bank_txn_id
        WHERE p.tenant_id = ? AND UPPER(TRIM(bt.sender_name)) = UPPER(?) LIMIT 1
    """, (tenant['id'], sender)).fetchone():
        return True
    return False


def hold(conn, claim, reason, org_id=None):
    """Hold a tenant's claim for management. Nothing is booked; the bank line stays unassigned."""
    conn.execute("""UPDATE payment_claims SET status = 'flagged', flag_reason = ?, flagged_at = CURRENT_TIMESTAMP
                    WHERE id = ?""", (reason, claim['id']))
    details = f"Ref: {claim['mpesa_ref']} | {STAFF_TEXT[reason]}"
    conn.execute("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                 ('claim_held', 'claim', claim['id'], details, 'system'))
    platform_log(conn, 'claim_held', 'claim', claim['id'], details,
                 org_id=org_id, property_id=claim['property_id'], actor='system')
