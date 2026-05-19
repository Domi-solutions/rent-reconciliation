"""
Reconciliation Matcher

Matches SMS claims with bank transactions by reference code.
Also provides shared unit suggestion enrichment used by review and statement-detail routes.
"""

import re
from typing import List, Optional, Dict
from decimal import Decimal
from ..parsers.pdf_parser import Transaction
from ..parsers.sms_parser import SMSClaim, ClaimStatus


def _name_tokens(s):
    if not s:
        return []
    return [tok for tok in re.sub(r'[^A-Za-z0-9]+', ' ', s).strip().lower().split() if tok]


def enrich_with_suggestions(rows, conn, property_id, org_id=None):
    """Add suggestion fields to each transaction dict in-place.

    Sets: suggested_unit_id, suggested_unit_number, suggestion_source,
          suggested_tenant_name (name_match only).

    Tier 1 — unit_hint exact match against unit_number (org-scoped or property-scoped).
    Tier 2 — sender name token overlap against active tenant names (>= 2 tokens in common).
    """
    if not rows:
        return

    # Build unit lookup for Tier 1
    if org_id:
        def _lookup_hint(hint_key):
            return conn.execute("""
                SELECT u.id, u.unit_number FROM units u
                JOIN properties p ON u.property_id = p.id
                WHERE p.organization_id = ? AND UPPER(TRIM(u.unit_number)) = ?
            """, (org_id, hint_key)).fetchall()

        tenant_rows = conn.execute("""
            SELECT t.name, t.unit_id, u.unit_number
            FROM tenants t
            JOIN units u ON t.unit_id = u.id
            JOIN properties p ON u.property_id = p.id
            WHERE p.organization_id = ? AND t.status = 'active'
        """, (org_id,)).fetchall()
    else:
        def _lookup_hint(hint_key):
            return conn.execute("""
                SELECT id, unit_number FROM units
                WHERE property_id = ? AND UPPER(TRIM(unit_number)) = ?
            """, (property_id, hint_key)).fetchall()

        tenant_rows = conn.execute("""
            SELECT t.name, t.unit_id, u.unit_number
            FROM tenants t
            JOIN units u ON t.unit_id = u.id
            WHERE t.property_id = ? AND t.status = 'active'
        """, (property_id,)).fetchall()

    tenant_index = [
        {'unit_id': t['unit_id'], 'unit_number': t['unit_number'],
         'name': t['name'], 'tokens': set(_name_tokens(t['name']))}
        for t in tenant_rows if len(_name_tokens(t['name'])) >= 2
    ]

    for r in rows:
        # Tier 1 — unit hint
        hint = (r.get('unit_hint') or '').strip()
        if hint:
            hits = _lookup_hint(hint.upper())
            if len(hits) == 1:
                r['suggested_unit_id'] = hits[0]['id']
                r['suggested_unit_number'] = hits[0]['unit_number']
                r['suggestion_source'] = 'unit_hint'

        # Tier 2 — sender name match
        if not r.get('suggested_unit_id'):
            stokens = set(_name_tokens(r.get('sender_name') or ''))
            if len(stokens) >= 2:
                for tenant in tenant_index:
                    if len(stokens & tenant['tokens']) >= 2:
                        r['suggested_unit_id'] = tenant['unit_id']
                        r['suggested_unit_number'] = tenant['unit_number']
                        r['suggested_tenant_name'] = tenant['name']
                        r['suggestion_source'] = 'name_match'
                        break


def match_sms_to_bank(
    sms_claim: SMSClaim,
    bank_transactions: List[Transaction]
) -> Dict:
    """
    Match SMS claim with bank transaction by reference code.
    
    Returns:
        {
            'matched': bool,
            'status': ClaimStatus,
            'bank_transaction': Optional[Transaction],
            'amount_match': bool,
            'amount_discrepancy': Optional[Decimal],
            'message': str
        }
    """
    # Find bank transaction with matching reference
    bank_match = None
    for txn in bank_transactions:
        if txn.reference == sms_claim.reference:
            bank_match = txn
            break
    
    if not bank_match:
        return {
            'matched': False,
            'status': ClaimStatus.UNCONFIRMED,
            'bank_transaction': None,
            'amount_match': None,
            'amount_discrepancy': None,
            'message': f'No bank transaction found with reference {sms_claim.reference}'
        }
    
    # Check amount match if SMS has amount
    amount_match = True
    amount_discrepancy = None
    
    if sms_claim.amount is not None:
        discrepancy = abs(sms_claim.amount - bank_match.amount)
        if discrepancy > Decimal('0.01'):  # Allow KES 0.01 rounding
            amount_match = False
            amount_discrepancy = discrepancy
    
    # Determine status
    if not amount_match:
        status = ClaimStatus.AMOUNT_MISMATCH
        message = f'Reference matches but amount differs. SMS: {sms_claim.amount}, Bank: {bank_match.amount}'
    else:
        status = ClaimStatus.CONFIRMED
        message = f'Match confirmed. Reference: {sms_claim.reference}, Amount: {bank_match.amount}'
    
    return {
        'matched': True,
        'status': status,
        'bank_transaction': bank_match,
        'amount_match': amount_match,
        'amount_discrepancy': amount_discrepancy,
        'message': message
    }
