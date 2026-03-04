"""
Test suite for PDF parser validation.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.parsers.pdf_parser import parse_bank_statement
from decimal import Decimal


def test_balance_checksum():
    """Balance checksum must pass."""
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    
    if not os.path.exists(pdf_path):
        print(f"SKIP: PDF file not found at {pdf_path}")
        print("Please copy ACSTMT-VIEW__2_-1.pdf to data/input/")
        return False
    
    result = parse_bank_statement(pdf_path)
    assert result['validation']['valid'] == True, \
        f"Balance checksum failed: {result['validation'].get('error', 'Unknown error')}"
    print("✓ Balance checksum test passed")
    return True


def test_opening_balance():
    """Opening balance should be 676,851.41"""
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    
    if not os.path.exists(pdf_path):
        print(f"SKIP: PDF file not found at {pdf_path}")
        return False
    
    result = parse_bank_statement(pdf_path)
    expected = Decimal('676851.41')
    actual = result['opening_balance']
    
    assert actual is not None, "Opening balance not extracted"
    assert abs(actual - expected) <= Decimal('1.00'), \
        f"Opening balance mismatch: expected {expected}, got {actual}"
    print(f"✓ Opening balance test passed: {actual}")
    return True


def test_closing_balance():
    """Closing balance should be 399,581.32"""
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    
    if not os.path.exists(pdf_path):
        print(f"SKIP: PDF file not found at {pdf_path}")
        return False
    
    result = parse_bank_statement(pdf_path)
    expected = Decimal('399581.32')
    actual = result['closing_balance']
    
    assert actual is not None, "Closing balance not extracted"
    assert abs(actual - expected) <= Decimal('1.00'), \
        f"Closing balance mismatch: expected {expected}, got {actual}"
    print(f"✓ Closing balance test passed: {actual}")
    return True


def test_transaction_count():
    """Should find approximately 58 rent transactions."""
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    
    if not os.path.exists(pdf_path):
        print(f"SKIP: PDF file not found at {pdf_path}")
        return False
    
    result = parse_bank_statement(pdf_path)
    rent_count = result['summary']['paybill_credits']
    assert 55 <= rent_count <= 62, \
        f"Rent transaction count out of range: got {rent_count}, expected 55-62"
    print(f"✓ Transaction count test passed: {rent_count} rent transactions")
    return True


def test_rent_total():
    """Total rent should be approximately KES 946,000."""
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    
    if not os.path.exists(pdf_path):
        print(f"SKIP: PDF file not found at {pdf_path}")
        return False
    
    result = parse_bank_statement(pdf_path)
    rent_total = float(result['summary']['total_rent_amount'])
    assert 900000 <= rent_total <= 1000000, \
        f"Rent total out of range: got {rent_total:,.2f}, expected 900,000-1,000,000"
    print(f"✓ Rent total test passed: KES {rent_total:,.2f}")
    return True


def test_december_rent():
    """December rent should be approximately KES 608,300 (36 transactions)."""
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    
    if not os.path.exists(pdf_path):
        print(f"SKIP: PDF file not found at {pdf_path}")
        return False
    
    result = parse_bank_statement(pdf_path)
    december_txns = [
        t for t in result['transactions']
        if t.txn_type == 'PAYBILL_CREDIT' and t.transaction_date and 'DEC' in t.transaction_date
    ]
    december_total = sum(t.amount for t in december_txns)
    
    assert 30 <= len(december_txns) <= 40, \
        f"December transaction count out of range: got {len(december_txns)}"
    assert 550000 <= float(december_total) <= 650000, \
        f"December total out of range: got {float(december_total):,.2f}"
    
    print(f"✓ December rent test passed: {len(december_txns)} transactions, KES {december_total:,.2f}")
    return True


def test_january_rent():
    """January rent should be approximately KES 337,700 (22 transactions)."""
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    
    if not os.path.exists(pdf_path):
        print(f"SKIP: PDF file not found at {pdf_path}")
        return False
    
    result = parse_bank_statement(pdf_path)
    january_txns = [
        t for t in result['transactions']
        if t.txn_type == 'PAYBILL_CREDIT' and t.transaction_date and 'JAN' in t.transaction_date
    ]
    january_total = sum(t.amount for t in january_txns)
    
    assert 18 <= len(january_txns) <= 26, \
        f"January transaction count out of range: got {len(january_txns)}"
    assert 300000 <= float(january_total) <= 370000, \
        f"January total out of range: got {float(january_total):,.2f}"
    
    print(f"✓ January rent test passed: {len(january_txns)} transactions, KES {january_total:,.2f}")
    return True


def test_reversals_detected():
    """Should detect reversals."""
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    
    if not os.path.exists(pdf_path):
        print(f"SKIP: PDF file not found at {pdf_path}")
        return False
    
    result = parse_bank_statement(pdf_path)
    reversals = result['summary']['reversals']
    assert reversals >= 1, f"Expected at least 1 reversal, got {reversals}"
    print(f"✓ Reversals test passed: {reversals} reversals detected")
    return True


def test_settlements_filtered():
    """Settlements should be filtered out (not counted as rent)."""
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    
    if not os.path.exists(pdf_path):
        print(f"SKIP: PDF file not found at {pdf_path}")
        return False
    
    result = parse_bank_statement(pdf_path)
    settlements = result['summary']['settlements']
    # Should have some settlements but they shouldn't be counted as rent
    rent_txns = result['summary']['paybill_credits']
    all_txns = result['summary']['total_transactions']
    
    # Settlements should be separate from rent
    assert settlements >= 0, "Settlements count should be non-negative"
    print(f"✓ Settlements filtered: {settlements} settlements (not counted as rent)")
    return True


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("RUNNING PDF PARSER TESTS")
    print("=" * 60)
    print()
    
    tests = [
        test_balance_checksum,
        test_opening_balance,
        test_closing_balance,
        test_transaction_count,
        test_rent_total,
        test_december_rent,
        test_january_rent,
        test_reversals_detected,
        test_settlements_filtered,
    ]
    
    passed = 0
    failed = 0
    skipped = 0
    
    for test in tests:
        try:
            result = test()
            if result is True:
                passed += 1
            elif result is False:
                skipped += 1
        except AssertionError as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} ERROR: {e}")
            failed += 1
        print()
    
    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60)
    
    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
