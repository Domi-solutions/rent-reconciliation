"""
Reconciliation Matcher

Matches SMS claims with bank transactions by reference code.
"""

from typing import List, Optional, Dict
from decimal import Decimal
from ..parsers.pdf_parser import Transaction
from ..parsers.sms_parser import SMSClaim, ClaimStatus


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
