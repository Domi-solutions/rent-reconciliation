#!/usr/bin/env python3
"""
Demo Script: SMS Message Verification Against PDF

Allows you to paste an Mpesa SMS message and verify it against
the bank statement PDF transactions.

Usage:
    python demo_sms_verification.py
    # Then paste your Mpesa message when prompted
"""

import sys
import os
from decimal import Decimal
from typing import Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

from src.parsers.pdf_parser import parse_bank_statement
from src.parsers.sms_parser import parse_mpesa_message, create_sms_claim, ClaimStatus
from src.reconciliation.matcher import match_sms_to_bank
from src.reconciliation.state_machine import ClaimStateMachine
from typing import Optional


def print_separator(char='='):
    """Print a separator line."""
    print(char * 80)


def format_amount(amount):
    """Format amount for display."""
    if amount is None:
        return "N/A"
    return f"KES {amount:,.2f}"


def format_datetime(dt):
    """Format datetime for display."""
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d %I:%M:%S %p")


def verify_sms_against_pdf(sms_text: str, pdf_path: str = 'data/input/ACSTMT-VIEW (2)-1.pdf', state_machine: Optional[ClaimStateMachine] = None):
    """
    Verify an SMS message against the PDF bank statement.
    
    Args:
        sms_text: The Mpesa SMS message text
        pdf_path: Path to the PDF bank statement
        state_machine: Optional state machine instance (creates new if None)
    """
    # Initialize state machine if not provided
    if state_machine is None:
        state_machine = ClaimStateMachine()
    
    print_separator()
    print("SMS MESSAGE VERIFICATION")
    print_separator()
    print()
    
    # Step 1: Parse the SMS message
    print("STEP 1: Parsing SMS Message")
    print("-" * 80)
    print(f"Input: {sms_text[:100]}..." if len(sms_text) > 100 else f"Input: {sms_text}")
    print()
    
    parsed_sms = parse_mpesa_message(sms_text)
    
    if not parsed_sms['success']:
        print(f"❌ FAILED: {parsed_sms['error']}")
        return
    
    print(f"✓ Successfully parsed!")
    print(f"  Reference Code: {parsed_sms['reference']}")
    print(f"  Amount: {format_amount(parsed_sms['amount'])}")
    print(f"  Timestamp: {format_datetime(parsed_sms['timestamp'])}")
    print(f"  Sender Hint: {parsed_sms['sender_hint'] or 'N/A'}")
    print(f"  Format Detected: {parsed_sms['format_detected']}")
    
    if parsed_sms['parse_warnings']:
        print(f"  ⚠ Warnings:")
        for warning in parsed_sms['parse_warnings']:
            print(f"    - {warning}")
    print()
    
    # Step 1.5: Check state machine for duplicates
    print("STEP 1.5: Checking State Machine")
    print("-" * 80)
    sms_claim = create_sms_claim(parsed_sms)
    state_result = state_machine.add_claim(sms_claim)
    
    if state_result['is_duplicate']:
        print(f"⚠ DUPLICATE DETECTED!")
        print(f"  {state_result['message']}")
        existing = state_result['existing_claim']
        print(f"  Original received at: {format_datetime(existing.received_at)}")
        print(f"  Current status: {existing.status.value}")
        if existing.bank_match_id:
            print(f"  Already matched to bank transaction: {existing.bank_match_id}")
        print()
        print("Using existing claim from state...")
        sms_claim = state_result['claim']
    else:
        print(f"✓ {state_result['message']}")
        print()
    
    # Step 2: Load PDF transactions
    print("STEP 2: Loading Bank Statement PDF")
    print("-" * 80)
    
    if not os.path.exists(pdf_path):
        print(f"❌ ERROR: PDF file not found at: {pdf_path}")
        print(f"   Please ensure the PDF is in the correct location.")
        return
    
    print(f"Loading PDF: {pdf_path}")
    pdf_result = parse_bank_statement(pdf_path)
    
    if not pdf_result['success']:
        print(f"❌ FAILED to parse PDF: {pdf_result.get('errors', ['Unknown error'])}")
        return
    
    # Filter to Paybill Credits only (rent payments)
    paybill_transactions = [
        txn for txn in pdf_result['transactions']
        if txn.txn_type == 'PAYBILL_CREDIT' and txn.reference
    ]
    
    print(f"✓ PDF loaded successfully!")
    print(f"  Total Transactions: {len(pdf_result['transactions'])}")
    print(f"  Paybill Credits (Rent): {len(paybill_transactions)}")
    print()
    
    # Step 3: Match SMS to bank transaction
    print("STEP 3: Matching SMS to Bank Transaction")
    print("-" * 80)
    
    match_result = match_sms_to_bank(sms_claim, paybill_transactions)
    
    if not match_result['matched']:
        print(f"❌ NO MATCH FOUND")
        print(f"  {match_result['message']}")
        print()
        print("Available reference codes in PDF:")
        refs = sorted(set([txn.reference for txn in paybill_transactions if txn.reference]))
        for i, ref in enumerate(refs[:20], 1):  # Show first 20
            print(f"  {i}. {ref}")
        if len(refs) > 20:
            print(f"  ... and {len(refs) - 20} more")
        return
    
    # Match found!
    bank_txn = match_result['bank_transaction']
    
    print(f"✓ MATCH FOUND!")
    print()
    print("MATCH DETAILS:")
    print_separator('-')
    print(f"Reference Code: {sms_claim.reference}")
    print()
    
    print("SMS Details:")
    print(f"  Amount: {format_amount(sms_claim.amount)}")
    print(f"  Timestamp: {format_datetime(sms_claim.timestamp)}")
    print(f"  Format: {sms_claim.format_detected}")
    print()
    
    print("Bank Details:")
    print(f"  Amount: {format_amount(bank_txn.amount)}")
    print(f"  Transaction Date: {bank_txn.transaction_date or 'N/A'}")
    print(f"  Clearing Date: {bank_txn.clearing_date or 'N/A'}")
    print(f"  Sender: {bank_txn.sender or 'N/A'}")
    print(f"  Narration: {bank_txn.narration or 'N/A'}")
    print(f"  Unit Hint: {bank_txn.unit_hint or 'N/A'}")
    print(f"  Running Balance: {format_amount(bank_txn.running_balance)}")
    print()
    
    # Amount comparison
    if match_result['amount_match']:
        print("✓ Amount Match: CONFIRMED")
        if sms_claim.amount is None:
            print(f"  (SMS had no amount - using bank amount as source of truth)")
        else:
            print(f"  SMS Amount: {format_amount(sms_claim.amount)}")
            print(f"  Bank Amount: {format_amount(bank_txn.amount)}")
    else:
        print("⚠ Amount Mismatch:")
        print(f"  SMS Amount: {format_amount(sms_claim.amount)}")
        print(f"  Bank Amount: {format_amount(bank_txn.amount)}")
        print(f"  Discrepancy: {format_amount(match_result['amount_discrepancy'])}")
        print()
        print("  ⚠ WARNING: Reference matches but amounts differ!")
        print("  Bank amount is the source of truth.")
    
    print()
    print(f"Status: {match_result['status'].value}")
    
    # Update state machine with match result
    if match_result['matched']:
        state_machine.update_status(
            sms_claim.reference,
            match_result['status'],
            bank_match_id=match_result['bank_transaction'].reference if match_result['bank_transaction'] else None,
            bank_amount=match_result['bank_transaction'].amount if match_result['bank_transaction'] else None
        )
        print(f"✓ State updated: {match_result['status'].value}")
    
    # Show state statistics
    stats = state_machine.get_statistics()
    print()
    print("STATE STATISTICS:")
    print(f"  Total Claims: {stats['total_claims']}")
    for status, count in stats['by_status'].items():
        if count > 0:
            print(f"  {status}: {count}")
    
    print_separator()


def interactive_mode():
    """Run in interactive mode, prompting for SMS messages."""
    print_separator()
    print("MPESA SMS VERIFICATION DEMO")
    print_separator()
    print()
    print("This tool verifies Mpesa SMS messages against the bank statement PDF.")
    print("You can paste:")
    print("  - Full Mpesa message")
    print("  - Just the reference code (e.g., TLU9G289GY)")
    print("  - Partial message")
    print()
    print("State is persisted to: data/state/claims.json")
    print("Duplicate detection is enabled.")
    print()
    print("Type 'quit' or 'exit' to stop.")
    print()
    
    pdf_path = 'data/input/ACSTMT-VIEW (2)-1.pdf'
    state_machine = ClaimStateMachine()
    
    # Show initial statistics
    stats = state_machine.get_statistics()
    if stats['total_claims'] > 0:
        print(f"Loaded {stats['total_claims']} existing claim(s) from state.")
        print()
    
    while True:
        print_separator('-')
        print("Paste your Mpesa message (or reference code):")
        print("(Press Enter twice or Ctrl+D when done)")
        
        lines = []
        try:
            while True:
                line = input()
                if line.strip() == '' and lines:  # Empty line after content
                    break
                if line.strip().lower() in ['quit', 'exit', 'q']:
                    print("\nGoodbye!")
                    return
                lines.append(line)
        except EOFError:
            pass
        
        if not lines:
            print("No input provided. Try again.")
            continue
        
        sms_text = '\n'.join(lines).strip()
        
        if not sms_text:
            print("Empty message. Try again.")
            continue
        
        print()
        verify_sms_against_pdf(sms_text, pdf_path, state_machine)
        print()


if __name__ == '__main__':
    state_machine = ClaimStateMachine()
    
    if len(sys.argv) > 1:
        # Command line mode: pass SMS text as argument
        sms_text = ' '.join(sys.argv[1:])
        verify_sms_against_pdf(sms_text, state_machine=state_machine)
    else:
        # Interactive mode
        interactive_mode()
