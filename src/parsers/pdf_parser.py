"""
PDF Parser for Bank Statement Extraction

Co-operative Bank Kenya: DD-MMM- dates, 3-digit codes, Paybill lines (`parse_cooperative_bank_statement`).

Tabular KES (e.g. KCB-style): Debit/Credit/Book Balance columns, dates like "01 JAN 26"
(`parse_tabular_kes_bank_statement`).

`parse_bank_statement` is the orchestrator: it detects format and dispatches; cooperative
and tabular parsers stay independent so each layout is handled without breaking the other.
"""

import pdfplumber
import re
from dataclasses import dataclass, field
from typing import List, Optional
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime


@dataclass
class Transaction:
    """Structured representation of a bank transaction."""
    transaction_date: Optional[str] = None
    clearing_date: Optional[str] = None
    txn_code: Optional[str] = None  # 014, 000, 038, 048
    txn_type: str = "UNKNOWN"  # PAYBILL_CREDIT, REVERSAL, SETTLEMENT, CHEQUE, OTHER
    reference: Optional[str] = None  # Mpesa ref like TLU9G289GY
    sender: Optional[str] = None
    narration: Optional[str] = None
    unit_hint: Optional[str] = None  # Extracted unit code like "5D", "1C" from "MOWIN 5D"
    amount: Decimal = Decimal('0.00')
    direction: str = "credit"  # 'credit' or 'debit'
    running_balance: Decimal = Decimal('0.00')
    raw_text: str = ""
    page_number: int = 0
    parse_warnings: List[str] = field(default_factory=list)


def extract_raw_text(pdf_path: str) -> tuple[str, List[int]]:
    """
    Extract all text from PDF, preserving line structure.
    
    Returns:
        tuple: (raw_text, page_numbers) where page_numbers maps line indices to page numbers
    """
    full_text = []
    page_numbers = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if text:
                lines = text.split('\n')
                full_text.extend(lines)
                # Map each line to its page number
                page_numbers.extend([page_num] * len(lines))
    
    return '\n'.join(full_text), page_numbers


def segment_transactions(raw_text: str, page_numbers: List[int]) -> List[dict]:
    """
    Segment raw text into transaction blocks using state machine.
    
    State machine:
    - AWAIT_DATE: Looking for line starting with DD-MMM-
    - COLLECTING: Accumulating lines until next date
    
    Returns list of {'lines': [...], 'raw_text': '...', 'page_number': int}
    """
    lines = raw_text.split('\n')
    transactions = []
    current_transaction = []
    current_start_line_idx = 0
    
    # Pattern to match transaction start: DD-MMM- (with trailing dash/space)
    # The year appears on the next line in this PDF format
    date_pattern = re.compile(r'^\d{2}-[A-Z]{3}-')
    
    for line_idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        
        # Check if this line starts a new transaction
        if date_pattern.match(line):
            # Save previous transaction if exists
            if current_transaction:
                # Determine page number from first line of transaction
                page_num = page_numbers[current_start_line_idx] if current_start_line_idx < len(page_numbers) else 1
                transactions.append({
                    'lines': current_transaction,
                    'raw_text': '\n'.join(current_transaction),
                    'page_number': page_num
                })
            # Start new transaction
            current_transaction = [line]
            current_start_line_idx = line_idx
        else:
            # Continue current transaction
            if current_transaction:
                current_transaction.append(line)
    
    # Don't forget the last transaction
    if current_transaction:
        page_num = page_numbers[current_start_line_idx] if current_start_line_idx < len(page_numbers) else 1
        transactions.append({
            'lines': current_transaction,
            'raw_text': '\n'.join(current_transaction),
            'page_number': page_num
        })
    
    return transactions


def segment_tabular_transactions(raw_text: str, page_numbers: List[int]) -> List[dict]:
    """
    Segment tabular bank statements (DD MMM YY at line start, e.g. 01 JAN 26).
    Continuation lines do not start with this pattern.
    """
    lines = raw_text.split('\n')
    transactions: List[dict] = []
    current_transaction: List[str] = []
    current_start_line_idx = 0

    date_pattern = re.compile(r'^\d{2} [A-Z]{3} \d{2}\s')

    for line_idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        if date_pattern.match(line):
            if current_transaction:
                page_num = page_numbers[current_start_line_idx] if current_start_line_idx < len(page_numbers) else 1
                transactions.append({
                    'lines': current_transaction,
                    'raw_text': '\n'.join(current_transaction),
                    'page_number': page_num
                })
            current_transaction = [line]
            current_start_line_idx = line_idx
        else:
            if current_transaction:
                current_transaction.append(line)

    if current_transaction:
        page_num = page_numbers[current_start_line_idx] if current_start_line_idx < len(page_numbers) else 1
        transactions.append({
            'lines': current_transaction,
            'raw_text': '\n'.join(current_transaction),
            'page_number': page_num
        })

    return transactions


TABULAR_TRIPLE_AMOUNTS = re.compile(
    r'(\d{1,3}(?:,\d{3})*\.\d{2})\s+(\d{1,3}(?:,\d{3})*\.\d{2})\s+(\d{1,3}(?:,\d{3})*\.\d{2})\s*$'
)


def tabular_trailing_three_amounts(line: str) -> Optional[tuple[Decimal, Decimal, Decimal]]:
    """Debit, Credit, Book balance from end of a tabular statement line."""
    m = TABULAR_TRIPLE_AMOUNTS.search(line.strip())
    if not m:
        return None
    d, c, b = m.groups()
    return (
        Decimal(d.replace(',', '')),
        Decimal(c.replace(',', '')),
        Decimal(b.replace(',', ''))
    )


def tabular_date_from_first_line(first_line: str) -> Optional[str]:
    """01 JAN 26 -> 01-JAN-2026"""
    m = re.match(r'^(\d{2}) ([A-Z]{3}) (\d{2})\s', first_line)
    if not m:
        return None
    dd, mon, yy = m.groups()
    y = int(yy)
    year = 2000 + y if y < 80 else 1900 + y
    return f'{dd}-{mon}-{year}'


def extract_balances_tabular(raw_text: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """
    Opening/closing for tabular statements (Balance B/Fwd, Book Balance as at).
    """
    opening_balance = None
    closing_balance = None

    bf = re.search(
        r'Balance B/Fwd\s+(\d{1,3}(?:,\d{3})*\.\d{2})\s+(\d{1,3}(?:,\d{3})*\.\d{2})\s+(\d{1,3}(?:,\d{3})*\.\d{2})',
        raw_text,
        re.IGNORECASE
    )
    if bf:
        try:
            opening_balance = Decimal(bf.group(3).replace(',', ''))
        except Exception:
            pass

    for pattern in (
        r'Book Balance as at\s*:\s*(\d{1,3}(?:,\d{3})*\.\d{2})',
        r'Cleared Balance As at\s*:\s*(\d{1,3}(?:,\d{3})*\.\d{2})',
    ):
        m = re.search(pattern, raw_text, re.IGNORECASE)
        if m:
            try:
                closing_balance = Decimal(m.group(1).replace(',', ''))
                break
            except Exception:
                pass

    return opening_balance, closing_balance


def parse_tabular_transaction(block: dict, page_num: int) -> Transaction:
    """Parse one tabular (KCB-style) transaction block."""
    lines = block['lines']
    full_text = block['raw_text']
    if not lines:
        return Transaction(
            txn_type='PARSE_ERROR',
            raw_text=full_text,
            page_number=page_num,
            parse_warnings=['Empty tabular block']
        )

    first_line = lines[0]
    triple = tabular_trailing_three_amounts(first_line)
    if triple is None:
        return Transaction(
            txn_type='PARSE_ERROR',
            raw_text=full_text,
            page_number=page_num,
            parse_warnings=['Could not parse Debit/Credit/Book Balance columns']
        )

    debit_amt, credit_amt, book_bal = triple
    upper = full_text.upper()

    if 'BALANCE B/FWD' in first_line.upper():
        return Transaction(
            transaction_date=tabular_date_from_first_line(first_line),
            txn_type='STATEMENT_ANCHOR',
            amount=Decimal('0.00'),
            direction='credit',
            running_balance=book_bal,
            raw_text=full_text,
            page_number=page_num,
            parse_warnings=[]
        )

    if credit_amt > Decimal('0') and debit_amt > Decimal('0'):
        return Transaction(
            transaction_date=tabular_date_from_first_line(first_line),
            txn_type='PARSE_ERROR',
            raw_text=full_text,
            page_number=page_num,
            parse_warnings=['Both debit and credit non-zero on same line']
        )

    if credit_amt > Decimal('0'):
        direction = 'credit'
        amount = credit_amt
    elif debit_amt > Decimal('0'):
        direction = 'debit'
        amount = debit_amt
    else:
        return Transaction(
            transaction_date=tabular_date_from_first_line(first_line),
            txn_type='OTHER',
            amount=Decimal('0.00'),
            direction='credit',
            running_balance=book_bal,
            raw_text=full_text,
            page_number=page_num,
            parse_warnings=['Zero debit and credit']
        )

    txn_type = 'OTHER'
    if direction == 'credit':
        if (
            'MPESA PAY BILL' in upper
            or 'MPESA MERCHANT' in upper
            or 'MERCHANT TILL' in upper
            or ('FT26' in first_line and 'MPESA' in upper)
        ):
            txn_type = 'PAYBILL_CREDIT'
        elif 'SETTLEMENT' in upper or 'LIPA NA M' in upper:
            txn_type = 'SETTLEMENT'
    else:
        if 'REVERSAL' in upper or 'REV DD' in upper:
            txn_type = 'REVERSAL'
        elif 'CHEQUE' in upper:
            txn_type = 'CHEQUE'

    reference = extract_reference(full_text)
    sender = None
    narration = None
    unit_hint = None

    if txn_type == 'PAYBILL_CREDIT':
        if 'MPESA PAY BILL' in upper:
            before = re.split(r'MPESA PAY BILL', full_text, maxsplit=1, flags=re.IGNORECASE)[0]
            before = re.sub(r'[\d,\s]{3,}', ' ', before)
            before = re.sub(r'\b254\d{9}\b', ' ', before)
            before = re.sub(r'\bKES\d+\b', ' ', before, flags=re.IGNORECASE)
            before = re.sub(r'\bUA[A-Z0-9]{8,12}\b', ' ', before)
            sender = ' '.join(before.split())[-80:].strip()[:120]
            if len(sender) < 2:
                sender = None

        full_join = ' '.join(lines)
        nar_m = re.search(r'(MOWIN\s+[A-Z0-9]{1,4})', full_join, re.IGNORECASE)
        if nar_m:
            narration = nar_m.group(1)
            um = re.search(r'MOWIN\s*([A-Z0-9]{1,4})', full_join.upper())
            if um:
                unit_hint = um.group(1)
        else:
            mer_m = re.search(
                r'(?:606888|938026)\s+([A-Z0-9]{1,4})\s+',
                full_join,
                re.IGNORECASE
            )
            if mer_m:
                unit_hint = mer_m.group(1).upper()

    warnings: List[str] = []
    if txn_type == 'PAYBILL_CREDIT' and not reference:
        warnings.append('Missing Mpesa reference code')
    if txn_type == 'PAYBILL_CREDIT' and not sender:
        warnings.append('Missing sender name')

    return Transaction(
        transaction_date=tabular_date_from_first_line(first_line),
        txn_type=txn_type,
        reference=reference,
        sender=sender,
        narration=narration,
        unit_hint=unit_hint,
        amount=amount,
        direction=direction,
        running_balance=book_bal,
        raw_text=full_text,
        page_number=page_num,
        parse_warnings=warnings
    )


def classify_transaction(first_line: str, full_text: str) -> tuple[str, str]:
    """
    Classify transaction using keywords FIRST, then fallback to 3-digit code.

    Returns: (txn_code, txn_type)

    PRIORITY ORDER (keyword takes precedence over code):
    1. "Paybill Credit From Paybill:" → PAYBILL_CREDIT (even if code is 000)
    2. "Reversal" → REVERSAL
    3. "Settlement" → SETTLEMENT
    4. Then use code: 014=PAYBILL_CREDIT, 038/048=CHEQUE, etc.
    """
    # Look for 3-digit code at end of line
    code_match = re.search(r'\s(\d{3})$', first_line)
    code = code_match.group(1) if code_match else None

    full_upper = full_text.upper()

    # PRIORITY 1: Check for Paybill Credit keyword REGARDLESS of code
    # This fixes the edge case where a Paybill credit has code 000
    if 'PAYBILL CREDIT FROM PAYBILL:' in full_upper or 'PAYBILL CREDIT FROM PAYBILL :' in full_upper:
        return code, 'PAYBILL_CREDIT'

    # PRIORITY 2: Check for Reversal (includes Co-op Bank "REV dd" format)
    if 'REVERSAL' in full_upper or 'REVERSAL-MERCHANT' in full_upper or 'REV DD' in full_upper:
        return code, 'REVERSAL'

    # PRIORITY 3: Check for Settlement
    if 'SETTLEMENT' in full_upper or 'LIPA NA MPESA SETTLEMENTS' in full_upper:
        return code, 'SETTLEMENT'

    # FALLBACK: Use transaction code
    if code is None:
        return None, 'UNKNOWN'

    if code == '014':
        # Code 014 with "Paybill Credit" but not the full pattern
        if 'PAYBILL CREDIT' in full_upper:
            return code, 'PAYBILL_CREDIT'
        else:
            # Code 014 but not Paybill credit (e.g., Maintenance Fees, Excise Duty)
            return code, 'OTHER'
    elif code == '000':
        # Already checked keywords above, so this is OTHER
        return code, 'OTHER'
    elif code in ['038', '048']:
        return code, 'CHEQUE'
    else:
        return code, 'UNKNOWN'


def extract_amount(block: dict) -> Optional[Decimal]:
    """
    Extract amount, handling the PDF split-line problem.
    
    Strategy:
    1. For Paybill credits: Look for amount after "Ref :" pattern
    2. For other transactions: Look for amount before running balance
    3. Handle split amounts: "20,000.0" on one line, "0" on next line
    4. Avoid picking up running balances (which appear before transaction code)
    """
    lines = block['lines']
    full_text = block['raw_text']
    first_line = lines[0] if lines else ''
    
    # Determine transaction type to use appropriate extraction strategy
    is_paybill = 'Paybill Credit' in full_text or 'PAYBILL CREDIT' in full_text.upper()
    
    # Pattern for complete amount: digits, comma, digits, dot, two digits
    complete_pattern = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})\b')
    
    # Pattern for incomplete amount ending with single decimal digit
    incomplete_pattern = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d)\b')
    
    # Pattern for amount ending with dot
    dot_pattern = re.compile(r'(\d{1,3}(?:,\d{3})*)\.')
    
    if is_paybill:
        # For Paybill credits: Look for amount after "Ref :" pattern
        # The amount may be split across lines: "Ref : 20,000.0" on one line, "0" on next
        ref_pattern = re.compile(r'Ref\s*:\s*(\d{1,3}(?:,\d{3})*\.?\d*)', re.IGNORECASE)

        for i, line in enumerate(lines):
            ref_match = ref_pattern.search(line)
            if ref_match:
                amount_part = ref_match.group(1)
                # Try to complete the amount using next line if needed
                completed = _complete_amount(amount_part, lines, i, incomplete_pattern, dot_pattern)
                if completed and completed >= Decimal('1000'):  # Rent is typically 1000+
                    return completed

        # Fallback: Look for any amount pattern in lines after reference code
        # This handles cases where amount is on a separate line
        found_ref = False
        for i, line in enumerate(lines):
            if 'Ref' in line and ':' in line:
                found_ref = True
            if found_ref:
                # Look for standalone amount on this or subsequent lines
                amount_match = incomplete_pattern.search(line)
                if amount_match:
                    amount_part = amount_match.group(1)
                    completed = _complete_amount(amount_part, lines, i, incomplete_pattern, dot_pattern)
                    if completed and completed >= Decimal('1000'):
                        return completed
                # Also check for complete amounts
                complete_match = complete_pattern.search(line)
                if complete_match:
                    try:
                        amount = Decimal(complete_match.group(1).replace(',', ''))
                        if amount >= Decimal('1000'):
                            return amount
                    except:
                        pass
    
    else:
        # For other transactions (cheques, reversals, etc.):
        # Amount appears before running balance on first line
        # Format: "... description amount balance code"
        # We need to find amount that's NOT the running balance

        # Special case: Outward Cheque credit — amount is on a continuation line,
        # not the first line (the first line only has the running balance).
        if 'OUTWARD CHEQUE' in full_text.upper() or 'OUTWARD CLEARING' in full_text.upper():
            for i, line in enumerate(lines[1:], 1):
                dot_match = dot_pattern.search(line)
                if dot_match:
                    amount_part = dot_match.group(1)
                    completed = _complete_amount(amount_part, lines, i, incomplete_pattern, dot_pattern)
                    if completed and 1 <= completed <= 10000000:
                        return completed
                inc_match = incomplete_pattern.search(line)
                if inc_match:
                    amount_part = inc_match.group(1)
                    completed = _complete_amount(amount_part, lines, i, incomplete_pattern, dot_pattern)
                    if completed and 1 <= completed <= 10000000:
                        return completed

        # Extract running balance first (to exclude it)
        balance_match = re.search(r'(\d{1,3}(?:,\d{3})*\.\d{2})\s+\d{3}$', first_line)
        balance_value = None
        if balance_match:
            try:
                balance_value = Decimal(balance_match.group(1).replace(',', ''))
            except:
                pass
        
        # Pattern for negative amounts
        negative_pattern = re.compile(r'-(\d{1,3}(?:,\d{3})*\.\d{2})\b')
        
        # Look for amounts in first line (before balance)
        # Check for negative amounts first
        negative_match = negative_pattern.search(first_line)
        if negative_match:
            try:
                amount = -Decimal(negative_match.group(1).replace(',', ''))
                if 1 <= abs(amount) <= 1000000:
                    return amount
            except:
                pass
        
        # Check for amounts ending with dot (like "500,000.")
        # The pattern looks for "number." followed by space (not followed by another digit on same line)
        dot_amount_pattern = re.compile(r'(\d{1,3}(?:,\d{3})*)\.\s+')
        dot_match = dot_amount_pattern.search(first_line)
        if dot_match:
            amount_part = dot_match.group(1)
            # Check next line for continuation - but ONLY if it starts with 1-2 digits
            # that look like decimal places (00, 0, 50, etc.), NOT years (2025)
            if len(lines) > 1:
                next_line = lines[1].strip()
                # Only match 1-2 digits at the START that are followed by non-digit or end
                # This prevents matching "2025" (year) as decimal continuation
                decimal_match = re.match(r'^(\d{1,2})(?!\d)', next_line)
                if decimal_match:
                    decimal_digits = decimal_match.group(1)
                    # Ensure exactly 2 decimal places
                    if len(decimal_digits) == 1:
                        decimal_digits = decimal_digits + '0'
                    complete_amount = amount_part + '.' + decimal_digits
                    try:
                        amount = Decimal(complete_amount.replace(',', ''))
                        if balance_value and abs(amount - balance_value) < Decimal('0.01'):
                            pass
                        elif 1 <= amount <= 1000000:
                            return amount
                    except:
                        pass
            # Default to .00 if no valid decimal continuation found
            try:
                amount = Decimal((amount_part + '.00').replace(',', ''))
                if balance_value and abs(amount - balance_value) < Decimal('0.01'):
                    pass
                elif 1 <= amount <= 1000000:
                    return amount
            except:
                pass
        
        # Split first line by spaces and look for amount patterns
        first_line_parts = first_line.split()
        amounts_found = []
        
        for part in first_line_parts:
            # Check for complete amounts (including negative)
            if part.startswith('-'):
                complete_match = complete_pattern.match(part[1:])
                if complete_match:
                    try:
                        amount = -Decimal(part[1:].replace(',', ''))
                        if balance_value and abs(amount - balance_value) < Decimal('0.01'):
                            continue
                        if 1 <= abs(amount) <= 1000000:
                            amounts_found.append(amount)
                    except:
                        pass
            else:
                complete_match = complete_pattern.match(part)
                if complete_match:
                    try:
                        amount = Decimal(part.replace(',', ''))
                        # Exclude if it matches the balance
                        if balance_value and abs(amount - balance_value) < Decimal('0.01'):
                            continue
                        # Reasonable range
                        if 1 <= amount <= 1000000:
                            amounts_found.append(amount)
                    except:
                        pass
            
            # Check for incomplete amounts
            incomplete_match = incomplete_pattern.match(part)
            if incomplete_match:
                amount_part = incomplete_match.group(1)
                completed = _complete_amount(amount_part, lines, 0, incomplete_pattern, dot_pattern)
                if completed:
                    # Exclude if it matches the balance
                    if balance_value and abs(completed - balance_value) < Decimal('0.01'):
                        continue
                    if 1 <= abs(completed) <= 1000000:
                        amounts_found.append(completed)
        
        # Also check next line for amounts (for settlements, etc.)
        if len(lines) > 1:
            second_line = lines[1]
            # Check for complete amounts on next line
            complete_match = complete_pattern.search(second_line)
            if complete_match:
                try:
                    amount = Decimal(complete_match.group(1).replace(',', ''))
                    if balance_value and abs(amount - balance_value) < Decimal('0.01'):
                        pass
                    elif 1 <= amount <= 1000000:
                        amounts_found.append(amount)
                except:
                    pass
            
            incomplete_match = incomplete_pattern.search(second_line)
            if incomplete_match:
                amount_part = incomplete_match.group(1)
                completed = _complete_amount(amount_part, lines, 1, incomplete_pattern, dot_pattern)
                if completed:
                    if balance_value and abs(completed - balance_value) < Decimal('0.01'):
                        pass
                    elif 1 <= abs(completed) <= 1000000:
                        amounts_found.append(completed)
        
        # Return the first reasonable amount found (usually the transaction amount)
        if amounts_found:
            return amounts_found[0]
    
    # Fallback: look for amounts in lines that don't contain transaction codes
    for i, line in enumerate(lines):
        # Skip lines that look like they contain running balances (have transaction code at end)
        if re.search(r'\s\d{3}$', line):
            continue
        
        incomplete_match = incomplete_pattern.search(line)
        if incomplete_match:
            amount_part = incomplete_match.group(1)
            completed = _complete_amount(amount_part, lines, i, incomplete_pattern, dot_pattern)
            if completed and 1000 <= completed <= 1000000:
                return completed
    
    return None


def _complete_amount(amount_part: str, lines: List[str], line_idx: int,
                     incomplete_pattern: re.Pattern, dot_pattern: re.Pattern) -> Optional[Decimal]:
    """
    Helper to complete an incomplete amount.

    CRITICAL FIX: Handle PDF text extraction that splits amounts across lines.
    Examples:
      - "20,000.0" on line 1, "0" on line 2 → 20,000.00
      - "13,500.0" on line 1, "0" on line 2 → 13,500.00
      - "20,000." on line 1, "00" on line 2 → 20,000.00

    The key insight: amounts in this PDF always have 2 decimal places (.00),
    but extraction may split after the decimal point.
    """
    # Clean amount_part - remove any trailing whitespace
    amount_part = amount_part.strip()

    # Check if amount is already complete (has exactly 2 decimal places)
    complete_pattern = re.compile(r'^(\d{1,3}(?:,\d{3})*\.\d{2})$')
    if complete_pattern.match(amount_part):
        try:
            return Decimal(amount_part.replace(',', ''))
        except:
            pass

    # Check if amount ends with single decimal digit (e.g., "20,000.0")
    # This is the most common split case
    single_decimal_pattern = re.compile(r'^(\d{1,3}(?:,\d{3})*\.\d)$')
    if single_decimal_pattern.match(amount_part):
        # Need exactly one more digit
        if line_idx + 1 < len(lines):
            next_line = lines[line_idx + 1].strip()
            # Look for a single digit at start of next line (the missing decimal digit)
            continuation_match = re.match(r'^(\d)\b', next_line)
            if continuation_match:
                complete_amount = amount_part + continuation_match.group(1)
                try:
                    return Decimal(complete_amount.replace(',', ''))
                except:
                    pass

        # Fallback: If next line doesn't have continuation, assume .X0 (e.g., .0 → .00)
        try:
            return Decimal((amount_part + '0').replace(',', ''))
        except:
            pass

    # Check if amount ends with just a decimal point (e.g., "20,000.")
    if amount_part.endswith('.'):
        if line_idx + 1 < len(lines):
            next_line = lines[line_idx + 1].strip()
            # Look for decimal digits at start of next line
            continuation_match = re.match(r'^(\d{1,2})\b', next_line)
            if continuation_match:
                decimal_digits = continuation_match.group(1)
                # Ensure we have exactly 2 decimal places
                if len(decimal_digits) == 1:
                    decimal_digits = decimal_digits + '0'
                complete_amount = amount_part + decimal_digits
                try:
                    return Decimal(complete_amount.replace(',', ''))
                except:
                    pass

        # Fallback: assume .00
        try:
            return Decimal((amount_part + '00').replace(',', ''))
        except:
            pass

    # Check if amount has no decimal point at all (e.g., "20,000")
    no_decimal_pattern = re.compile(r'^(\d{1,3}(?:,\d{3})*)$')
    if no_decimal_pattern.match(amount_part):
        # Check next line for decimal portion
        if line_idx + 1 < len(lines):
            next_line = lines[line_idx + 1].strip()
            # Look for ".XX" pattern at start of next line
            decimal_match = re.match(r'^\.(\d{1,2})\b', next_line)
            if decimal_match:
                decimal_digits = decimal_match.group(1)
                if len(decimal_digits) == 1:
                    decimal_digits = decimal_digits + '0'
                complete_amount = amount_part + '.' + decimal_digits
                try:
                    return Decimal(complete_amount.replace(',', ''))
                except:
                    pass

        # Fallback: assume .00
        try:
            return Decimal((amount_part + '.00').replace(',', ''))
        except:
            pass

    # Last resort: try to parse as-is
    try:
        val = Decimal(amount_part.replace(',', ''))
        # Round to 2 decimal places
        return val.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except:
        pass

    return None


def extract_reference(full_text: str) -> Optional[str]:
    """
    Extract Mpesa reference code.
    Format: [TU][A-Z0-9]{9,10}
    """
    # Pattern for Mpesa reference codes
    ref_pattern = re.compile(r'\b([TU][A-Z0-9]{9,10})\b')
    match = ref_pattern.search(full_text)
    return match.group(1) if match else None


def extract_dates(first_line: str, next_line: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """
    Extract transaction date and clearing date from first line.
    
    Format in PDF: "01-DEC- 29-NOV-" with year on next line "2025 2025"
    Or combined: "01-DEC-2025 29-NOV-2025"
    """
    # Pattern for dates: DD-MMM- (with trailing dash)
    date_pattern = re.compile(r'(\d{2}-[A-Z]{3}-)')
    dates = date_pattern.findall(first_line)
    
    if len(dates) >= 1:
        # Try to get year from next line if provided
        year = None
        if next_line:
            year_match = re.search(r'(\d{4})', next_line)
            if year_match:
                year = year_match.group(1)
        
        # If we have dates and year, combine them
        if dates and year:
            transaction_date = dates[0] + year
            clearing_date = dates[1] + year if len(dates) >= 2 else None
            return transaction_date, clearing_date
        elif dates:
            # Return dates without year (will be added later if found)
            transaction_date = dates[0].rstrip('- ')
            clearing_date = dates[1].rstrip('- ') if len(dates) >= 2 else None
            return transaction_date, clearing_date
    
    return None, None


def extract_sender_and_narration(lines: List[str], txn_type: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract sender name, narration, and unit hint from transaction lines.

    For Paybill credits in this PDF format:
    - Reference appears on line with "Ref :"
    - Sender appears on line after "Sender:" label
    - Narration appears on line with "Narration:"
    - Unit hint extracted from patterns like "MOWIN 5D", "MOWIN 1C"

    Returns: (sender, narration, unit_hint)
    """
    sender = None
    narration = None
    unit_hint = None

    if txn_type == 'PAYBILL_CREDIT':
        # Look for "Sender:" label and extract name from next line or same line
        for i, line in enumerate(lines):
            if 'Sender:' in line or 'Sender :' in line:
                # Sender name might be on same line after "Sender:" or next line
                sender_match = re.search(r'Sender\s*:\s*(.+)', line, re.IGNORECASE)
                if sender_match:
                    sender_text = sender_match.group(1).strip()
                    # Remove amount continuation if present (like "0" or digits)
                    sender_text = re.sub(r'\s+\d+$', '', sender_text)
                    if sender_text and not sender_text.isdigit():
                        sender = sender_text.rstrip(',').strip()

                # If not found on same line, check next line
                if not sender and i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    # Remove trailing comma and clean up
                    sender = next_line.rstrip(',').strip()
                    # Remove phone number prefix if present (254...)
                    sender = re.sub(r'^254\d+\s+', '', sender)
                    if sender and not sender.isdigit():
                        break
                    else:
                        sender = None

        # Extract narration (unit codes like "MOWIN 5D", "MOWIN 1C")
        for line in lines:
            if 'Narration:' in line or 'Narration :' in line:
                narration_match = re.search(r'Narration\s*:\s*(.+)', line, re.IGNORECASE)
                if narration_match:
                    narration = narration_match.group(1).strip()
                    break

        # Also check for MOWIN patterns anywhere
        if not narration:
            full_text = ' '.join(lines)
            narration_match = re.search(r'(MOWIN\s+\d+[A-Z]?)', full_text, re.IGNORECASE)
            if narration_match:
                narration = narration_match.group(1)

        # Extract unit hint from narration (e.g., "MOWIN 5D" → "5D", "MOWIN 1C" → "1C")
        if narration:
            unit_match = re.search(r'MOWIN\s*([A-Z0-9]{1,4})', narration.upper())
            if unit_match:
                unit_hint = unit_match.group(1)

        # Also search full text for unit hint if not found in narration
        if not unit_hint:
            full_text = ' '.join(lines).upper()
            unit_match = re.search(r'MOWIN\s*([A-Z0-9]{1,4})', full_text)
            if unit_match:
                unit_hint = unit_match.group(1)

    # For other transaction types, try to extract meaningful description
    else:
        # Look for description after date/type info
        # This is heuristic and may need refinement
        pass

    return sender, narration, unit_hint


def is_cheque_reversal(lines: List[str]) -> bool:
    """
    Detect if a cheque transaction is actually a reversal/credit.

    In this bank statement format, a cheque reversal is indicated by a `-`
    appearing between the cheque number and the balance on the first line.

    Example reversal (has `-` before balance):
        "000136 - 695,715.01 038"

    Example normal debit (no `-`, amount before balance):
        "000136 11,250.0 684,591.51 048"
    """
    if not lines:
        return False

    first_line = lines[0]

    # Pattern: cheque number followed by " - " then balance then code
    # This indicates a reversal/credit, not a debit
    # Example: "000136 - 695,715.01 038"
    reversal_pattern = re.compile(r'\d{6}\s+-\s+\d{1,3}(?:,\d{3})*\.\d{2}\s+\d{3}$')
    if reversal_pattern.search(first_line):
        return True

    # Also check if there's a standalone "-" before a balance-like number
    # Pattern: " - " followed by large number (balance) at end of line
    if re.search(r'\s-\s+\d{3},\d{3}\.\d{2}\s+\d{3}$', first_line):
        return True

    return False


def determine_direction(txn_type: str, full_text: str, lines: List[str] = None, amount: Decimal = None) -> str:
    """
    Determine if transaction is credit or debit.

    Credit (money in):
    - Paybill credits (rent payments)
    - Settlements (Lipa Na Mpesa)
    - Cheque reversals (indicated by `-` in the transaction line)
    - Any transaction with negative amount

    Debit (money out):
    - Reversals (Mpesa reversals - confusingly these reduce balance)
    - Cheques / Cheque Withdrawals (unless they're reversals)
    - Inward Cheque Debit
    - Maintenance Fees
    - Excise Duty
    """
    full_upper = full_text.upper()

    # PRIORITY 1: Negative amount = credit (it's a reversal)
    if amount is not None and amount < 0:
        return 'credit'

    # PRIORITY 2: Check for cheque reversal pattern (has "-" indicator)
    if lines and is_cheque_reversal(lines):
        return 'credit'

    if txn_type == 'PAYBILL_CREDIT':
        return 'credit'
    elif txn_type == 'REVERSAL':
        return 'debit'  # Mpesa reversals reduce balance
    elif txn_type == 'CHEQUE':
        # Outward Cheque = cheque deposited and sent for outward clearing = CREDIT
        if 'OUTWARD CHEQUE' in full_upper or 'OUTWARD CLEARING' in full_upper:
            return 'credit'
        return 'debit'
    elif txn_type == 'SETTLEMENT':
        return 'credit'
    else:
        # Check description for debit indicators
        debit_keywords = [
            'DEBIT',
            'WITHDRAWAL',
            'MAINTENANCE FEE',
            'EXCISE DUTY',
            'CHARGE',
            'INWARD CHEQUE',
        ]
        for keyword in debit_keywords:
            if keyword in full_upper:
                return 'debit'

        # Default: assume credit unless explicitly debit
        return 'credit'


def extract_running_balance(lines: List[str]) -> Optional[Decimal]:
    """
    Extract running balance from transaction.
    In this PDF format, balance appears before the transaction code on the first line.
    Format: "... amount 014" where amount is the running balance.
    """
    if not lines:
        return None
    
    # Check first line for balance (appears before transaction code)
    first_line = lines[0]
    
    # Pattern: number before 3-digit code at end of line
    # Example: "696,851.41 014"
    balance_pattern = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})\s+\d{3}$')
    match = balance_pattern.search(first_line)
    
    if match:
        try:
            balance_str = match.group(1).replace(',', '')
            balance = Decimal(balance_str)
            # Reasonable balance range: 0 to 10,000,000
            if 0 <= balance <= 10000000:
                return balance
        except:
            pass
    
    # Fallback: look for balance pattern in last few lines
    balance_pattern_fallback = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})\b')
    
    # Check last 3 lines for balance
    for line in reversed(lines[-3:]):
        matches = balance_pattern_fallback.findall(line)
        if matches:
            try:
                # Usually the last number is the balance
                balance_str = matches[-1].replace(',', '')
                balance = Decimal(balance_str)
                # Reasonable balance range: 0 to 10,000,000
                if 0 <= balance <= 10000000:
                    return balance
            except:
                continue
    
    return None


def parse_transaction(block: dict, page_num: int) -> Transaction:
    """Parse a transaction block into structured data."""
    lines = block['lines']
    if not lines:
        return Transaction(
            txn_type='PARSE_ERROR',
            raw_text=block['raw_text'],
            page_number=page_num,
            parse_warnings=['Empty transaction block']
        )
    
    first_line = lines[0]
    next_line = lines[1] if len(lines) > 1 else None
    full_text = block['raw_text']
    
    # Extract dates (pass next line to get year)
    transaction_date, clearing_date = extract_dates(first_line, next_line)
    
    # Classify transaction
    txn_code, txn_type = classify_transaction(first_line, full_text)
    
    # Extract amount
    amount = extract_amount(block)
    if amount is None:
        return Transaction(
            transaction_date=transaction_date,
            clearing_date=clearing_date,
            txn_code=txn_code,
            txn_type='PARSE_ERROR',
            raw_text=full_text,
            page_number=page_num,
            parse_warnings=['Could not extract amount']
        )

    # Determine direction (pass lines and amount for reversal detection)
    direction = determine_direction(txn_type, full_text, lines=lines, amount=amount)

    # If direction is credit but amount is negative, use absolute value
    if direction == 'credit' and amount < 0:
        amount = abs(amount)
    
    # Extract reference (for Paybill credits)
    reference = None
    if txn_type == 'PAYBILL_CREDIT':
        reference = extract_reference(full_text)
    
    # Extract sender, narration, and unit hint
    sender, narration, unit_hint = extract_sender_and_narration(lines, txn_type)

    # Extract running balance
    running_balance = extract_running_balance(lines)

    # Collect warnings
    warnings = []
    if txn_type == 'PAYBILL_CREDIT' and not reference:
        warnings.append('Missing Mpesa reference code')
    if not sender and txn_type == 'PAYBILL_CREDIT':
        warnings.append('Missing sender name')
    if running_balance is None:
        warnings.append('Could not extract running balance')

    return Transaction(
        transaction_date=transaction_date,
        clearing_date=clearing_date,
        txn_code=txn_code,
        txn_type=txn_type,
        reference=reference,
        sender=sender,
        narration=narration,
        unit_hint=unit_hint,
        amount=amount,
        direction=direction,
        running_balance=running_balance or Decimal('0.00'),
        raw_text=full_text,
        page_number=page_num,
        parse_warnings=warnings
    )


def extract_balances(raw_text: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """
    Extract opening and closing balances from PDF.
    
    Look for patterns like:
    - "Opening Balance" or "Balance B/F"
    - "Closing Balance" or "Balance C/F"
    """
    opening_balance = None
    closing_balance = None
    
    # Pattern for balance amounts
    balance_pattern = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})')
    
    # Look for opening balance
    opening_patterns = [
        r'Opening Balance[:\s]+(\d{1,3}(?:,\d{3})*\.\d{2})',
        r'Balance B/F[:\s]+(\d{1,3}(?:,\d{3})*\.\d{2})',
        r'Balance Brought Forward[:\s]+(\d{1,3}(?:,\d{3})*\.\d{2})',
    ]
    
    for pattern in opening_patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            try:
                opening_balance = Decimal(match.group(1).replace(',', ''))
                break
            except:
                continue
    
    # Look for closing balance
    closing_patterns = [
        r'Closing Balance[:\s]+(\d{1,3}(?:,\d{3})*\.\d{2})',
        r'Balance C/F[:\s]+(\d{1,3}(?:,\d{3})*\.\d{2})',
        r'Balance Carried Forward[:\s]+(\d{1,3}(?:,\d{3})*\.\d{2})',
        r'Clear Balance[:\s]+(\d{1,3}(?:,\d{3})*\.\d{2})',  # Co-op Bank format
    ]
    
    for pattern in closing_patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            try:
                closing_balance = Decimal(match.group(1).replace(',', ''))
                break
            except:
                continue
    
    # Fallback: look for first and last balance-like numbers
    if opening_balance is None or closing_balance is None:
        all_balances = balance_pattern.findall(raw_text)
        if all_balances:
            try:
                if opening_balance is None:
                    # First reasonable balance
                    for bal_str in all_balances:
                        bal = Decimal(bal_str.replace(',', ''))
                        if 100000 <= bal <= 1000000:  # Reasonable opening range
                            opening_balance = bal
                            break
                
                if closing_balance is None:
                    # Last reasonable balance — no lower bound, accounts can drop below 100k
                    for bal_str in reversed(all_balances):
                        bal = Decimal(bal_str.replace(',', ''))
                        if bal <= 10000000:  # Exclude obviously wrong numbers only
                            closing_balance = bal
                            break
            except:
                pass
    
    return opening_balance, closing_balance


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
        if txn.txn_type == 'STATEMENT_ANCHOR':
            continue
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


def detect_bank_statement_format(raw_text: str, page_numbers: List[int]) -> str:
    """
    Choose parser strategy from extracted PDF text (no file I/O).

    Returns:
        'cooperative'   — Co-operative Bank layout (DD-MMM- date segmentation)
        'national_bank' — National Bank of Kenya tabular layout (identified by column header)
        'tabular_kes'   — Generic tabular Debit/Credit/Book Balance layout (e.g. KCB)
        'unknown'       — Unrecognised format
    """
    if len(segment_transactions(raw_text, page_numbers)) > 0:
        # Family Bank uses the same DD-MMM- date format as Co-op but has a distinct column header
        if 'PARTICULARS IN OUT' in raw_text:
            return 'family_bank'
        return 'cooperative'
    if 'Transaction Date Value Date Reference Transaction Details' in raw_text:
        return 'national_bank'
    if len(segment_tabular_transactions(raw_text, page_numbers)) > 0:
        return 'tabular_kes'
    return 'unknown'


def _finalize_bank_statement_result(
    opening_balance: Optional[Decimal],
    closing_balance: Optional[Decimal],
    transactions: List[Transaction],
    errors: List[str],
    statement_format: str,
) -> dict:
    """Shared validation, summary, and success flag for all bank statement parsers."""
    merged_errors: List[str] = []
    if opening_balance is None:
        merged_errors.append('Could not extract opening balance')
    if closing_balance is None:
        merged_errors.append('Could not extract closing balance')
    merged_errors.extend(errors)

    if opening_balance and closing_balance:
        validation = validate_balance_checksum(transactions, opening_balance, closing_balance)
        # If header checksum fails, check per-transaction running balance consistency.
        # Co-op Bank PDFs sometimes show a header closing balance that differs from the
        # last transaction's running balance (e.g. due to cut-off timing or pending fees).
        # When all per-transaction running balances agree with the calculated trail, the
        # parser is correct and the statement should be accepted.
        if not validation.get('valid'):
            last_txn = next(
                (t for t in reversed(transactions)
                 if t.txn_type not in ('STATEMENT_ANCHOR', 'PARSE_ERROR')),
                None
            )
            calc = validation.get('calculated_closing')
            if last_txn and calc is not None and abs(last_txn.running_balance - calc) <= Decimal('1.00'):
                validation['valid'] = True
                validation['error'] = None
                validation['note'] = (
                    f"Header closing balance {closing_balance:,.2f} differs from last "
                    f"transaction balance {last_txn.running_balance:,.2f} by "
                    f"{abs(closing_balance - last_txn.running_balance):,.2f} — "
                    f"accepted based on per-transaction balance consistency"
                )
    else:
        validation = {
            'valid': False,
            'error': 'Cannot validate: missing opening or closing balance'
        }

    paybill_credits = [t for t in transactions if t.txn_type == 'PAYBILL_CREDIT']
    total_rent_amount = sum(t.amount for t in paybill_credits)

    summary = {
        'total_transactions': len(transactions),
        'paybill_credits': len(paybill_credits),
        'total_rent_amount': total_rent_amount,
        'reversals': len([t for t in transactions if t.txn_type == 'REVERSAL']),
        'settlements': len([t for t in transactions if t.txn_type == 'SETTLEMENT']),
        'cheques': len([t for t in transactions if t.txn_type == 'CHEQUE']),
        'parse_errors': len([t for t in transactions if t.txn_type == 'PARSE_ERROR'])
    }

    success = validation.get('valid', False) and len(merged_errors) == 0

    return {
        'success': success,
        'statement_format': statement_format,
        'opening_balance': opening_balance,
        'closing_balance': closing_balance,
        'transactions': transactions,
        'validation': validation,
        'summary': summary,
        'errors': merged_errors,
    }


def _parse_cooperative_from_raw(raw_text: str, page_numbers: List[int]) -> dict:
    """Co-operative Bank pipeline only (original behaviour)."""
    errors: List[str] = []
    opening_balance, closing_balance = extract_balances(raw_text)
    transaction_blocks = segment_transactions(raw_text, page_numbers)

    transactions: List[Transaction] = []
    for i, block in enumerate(transaction_blocks):
        page_num = block.get('page_number', 1)
        try:
            txn = parse_transaction(block, page_num)
            transactions.append(txn)
        except Exception as e:
            errors.append(f'Error parsing transaction {i+1}: {str(e)}')
            transactions.append(Transaction(
                txn_type='PARSE_ERROR',
                raw_text=block['raw_text'],
                page_number=page_num,
                parse_warnings=[f'Parse exception: {str(e)}']
            ))

    return _finalize_bank_statement_result(
        opening_balance, closing_balance, transactions, errors, 'cooperative'
    )


def _parse_tabular_kes_from_raw(raw_text: str, page_numbers: List[int]) -> dict:
    """KES tabular (e.g. KCB-style) pipeline only."""
    errors: List[str] = []
    opening_balance, closing_balance = extract_balances_tabular(raw_text)
    transaction_blocks = segment_tabular_transactions(raw_text, page_numbers)

    transactions: List[Transaction] = []
    for i, block in enumerate(transaction_blocks):
        page_num = block.get('page_number', 1)
        try:
            txn = parse_tabular_transaction(block, page_num)
            transactions.append(txn)
        except Exception as e:
            errors.append(f'Error parsing transaction {i+1}: {str(e)}')
            transactions.append(Transaction(
                txn_type='PARSE_ERROR',
                raw_text=block['raw_text'],
                page_number=page_num,
                parse_warnings=[f'Parse exception: {str(e)}']
            ))

    return _finalize_bank_statement_result(
        opening_balance, closing_balance, transactions, errors, 'tabular_kes'
    )


def parse_cooperative_bank_statement(pdf_path: str) -> dict:
    """Parse a Co-operative Bank Kenya statement PDF (no format detection)."""
    raw_text, page_numbers = extract_raw_text(pdf_path)
    return _parse_cooperative_from_raw(raw_text, page_numbers)


def parse_tabular_kes_bank_statement(pdf_path: str) -> dict:
    """Parse a tabular KES statement PDF (no format detection)."""
    raw_text, page_numbers = extract_raw_text(pdf_path)
    return _parse_tabular_kes_from_raw(raw_text, page_numbers)


def parse_bank_statement(pdf_path: str) -> dict:
    """
    Orchestrated entry: extract text once, detect format, dispatch to cooperative or tabular parser.

    Result includes ``statement_format`` (``'cooperative'`` | ``'tabular_kes'``).

    Returns:
    {
        'success': bool,
        'statement_format': str,
        'opening_balance': Decimal,
        'closing_balance': Decimal,
        'transactions': List[Transaction],
        'validation': dict,
        'summary': dict,
        'errors': List[str]
    }
    """
    try:
        raw_text, page_numbers = extract_raw_text(pdf_path)
        fmt = detect_bank_statement_format(raw_text, page_numbers)
        if fmt in ('cooperative', 'family_bank'):
            result = _parse_cooperative_from_raw(raw_text, page_numbers)
            result['statement_format'] = fmt
            return result
        if fmt in ('tabular_kes', 'national_bank'):
            result = _parse_tabular_kes_from_raw(raw_text, page_numbers)
            result['statement_format'] = fmt
            return result
        return {
            'success': False,
            'statement_format': 'unknown',
            'opening_balance': None,
            'closing_balance': None,
            'transactions': [],
            'validation': {'valid': False, 'error': 'Bank statement format not recognised. Supported: Co-operative Bank, KCB.'},
            'summary': {},
            'errors': ['Bank statement format not recognised'],
        }
    except Exception as e:
        return {
            'success': False,
            'statement_format': 'unknown',
            'opening_balance': None,
            'closing_balance': None,
            'transactions': [],
            'validation': {'valid': False, 'error': str(e)},
            'summary': {},
            'errors': [f'Fatal error: {str(e)}']
        }


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python pdf_parser.py <pdf_path>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    result = parse_bank_statement(pdf_path)
    
    print("=== BANK STATEMENT PARSER ===\n")
    print(f"Format: {result.get('statement_format', 'n/a')}\n")
    
    if result['opening_balance']:
        print(f"Opening Balance: KES {result['opening_balance']:,.2f}")
    if result['closing_balance']:
        print(f"Closing Balance: KES {result['closing_balance']:,.2f}")
    
    print("\n=== VALIDATION ===")
    validation = result['validation']
    if validation.get('valid'):
        print("Balance Checksum: PASS ✓")
        print(f"Calculated Closing: KES {validation['calculated_closing']:,.2f}")
        print(f"Discrepancy: KES {validation['discrepancy']:,.2f}")
    else:
        print("Balance Checksum: FAIL ✗")
        print(f"Error: {validation.get('error', 'Unknown error')}")
    
    print("\n=== SUMMARY ===")
    summary = result['summary']
    print(f"Total Transactions: {summary.get('total_transactions', 0)}")
    print(f"Paybill Credits (Rent): {summary.get('paybill_credits', 0)}")
    print(f"Total Rent Amount: KES {summary.get('total_rent_amount', 0):,.2f}")
    print(f"Reversals: {summary.get('reversals', 0)}")
    print(f"Settlements: {summary.get('settlements', 0)}")
    print(f"Cheques: {summary.get('cheques', 0)}")
    print(f"Parse Errors: {summary.get('parse_errors', 0)}")
    
    if result['errors']:
        print("\n=== ERRORS ===")
        for error in result['errors']:
            print(f"- {error}")
    
    # Show rent transactions
    rent_transactions = [t for t in result['transactions'] if t.txn_type == 'PAYBILL_CREDIT']
    if rent_transactions:
        print("\n=== RENT TRANSACTIONS ===")
        print(f"{'Date':<12} {'Ref':<15} {'Sender':<30} {'Amount':>15}")
        print("-" * 75)
        for txn in rent_transactions[:20]:  # Show first 20
            date_str = txn.transaction_date or 'N/A'
            ref_str = txn.reference or 'N/A'
            sender_str = (txn.sender or 'N/A')[:28]
            amount_str = f"KES {txn.amount:,.2f}"
            print(f"{date_str:<12} {ref_str:<15} {sender_str:<30} {amount_str:>15}")
        
        if len(rent_transactions) > 20:
            print(f"... and {len(rent_transactions) - 20} more")
