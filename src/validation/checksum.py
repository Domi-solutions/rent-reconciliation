"""
Balance Checksum Validation Module

Provides validation functions for verifying parsed bank statement accuracy.
"""

from decimal import Decimal
from typing import List
from ..parsers.pdf_parser import Transaction


def validate_balance_checksum(
    transactions: List[Transaction],
    opening_balance: Decimal,
    closing_balance: Decimal
) -> dict:
    """
    CRITICAL SAFETY CHECK.
    
    Verify: Opening + Credits - Debits = Closing
    
    If this fails, the parser has bugs. Do not proceed.
    
    Returns:
    {
        'valid': bool,
        'calculated_closing': Decimal,
        'expected_closing': Decimal,
        'discrepancy': Decimal,
        'total_credits': Decimal,
        'total_debits': Decimal,
        'error': Optional[str]
    }
    """
    if opening_balance is None or closing_balance is None:
        return {
            'valid': False,
            'calculated_closing': None,
            'expected_closing': closing_balance,
            'discrepancy': None,
            'total_credits': Decimal('0.00'),
            'total_debits': Decimal('0.00'),
            'error': 'Opening or closing balance not found'
        }
    
    total_credits = Decimal('0.00')
    total_debits = Decimal('0.00')
    
    for txn in transactions:
        if txn.direction == 'credit':
            total_credits += txn.amount
        elif txn.direction == 'debit':
            total_debits += txn.amount
    
    calculated_closing = opening_balance + total_credits - total_debits
    discrepancy = abs(calculated_closing - closing_balance)
    
    # Allow KES 1.00 for rounding differences
    valid = discrepancy <= Decimal('1.00')
    
    return {
        'valid': valid,
        'calculated_closing': calculated_closing,
        'expected_closing': closing_balance,
        'discrepancy': discrepancy,
        'total_credits': total_credits,
        'total_debits': total_debits,
        'error': None if valid else f'Balance mismatch: {discrepancy:.2f}'
    }
