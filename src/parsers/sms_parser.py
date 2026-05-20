"""
SMS Parser for Mpesa Confirmation Messages

Parses Mpesa SMS messages in multiple formats:
1. Full message with "Dear [NAME], Mpesa transaction..."
2. Message without "Dear" prefix
3. Different format starting with reference code
4. Reference code only (minimal input)

Handles:
- Reference code extraction (PRIMARY KEY)
- Amount extraction (multiple formats)
- Timestamp extraction (multiple formats)
- Sender hint extraction
- Duplicate detection via state machine
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from decimal import Decimal
from datetime import datetime
from enum import Enum


class ClaimStatus(Enum):
    """Status states for SMS claims."""
    NEW = "NEW"
    PARSING = "PARSING"
    PARSED = "PARSED"
    DUPLICATE = "DUPLICATE"
    INVALID = "INVALID"
    CLAIMED = "CLAIMED"
    CONFIRMED = "CONFIRMED"
    UNCONFIRMED = "UNCONFIRMED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    PARSE_ERROR = "PARSE_ERROR"


@dataclass
class SMSClaim:
    """Represents an Mpesa SMS claim."""
    reference: str  # PRIMARY KEY - REQUIRED
    amount: Optional[Decimal] = None
    timestamp: Optional[datetime] = None
    received_at: datetime = field(default_factory=datetime.now)
    sender_hint: Optional[str] = None
    status: ClaimStatus = ClaimStatus.NEW
    format_detected: str = "unknown"
    raw_text: str = ""
    parse_warnings: List[str] = field(default_factory=list)
    bank_match_id: Optional[str] = None
    bank_amount: Optional[Decimal] = None
    
    def __post_init__(self):
        """Validate that reference code exists."""
        if not self.reference:
            raise ValueError("Reference code is required")
        if not re.match(r'^[TU][A-Z0-9]{9,10}$', self.reference):
            raise ValueError(f"Invalid reference code format: {self.reference}")


def extract_reference_code(text: str) -> Optional[str]:
    """Extract Mpesa reference code from text."""
    ref_pattern = re.compile(r'\b([TU][A-Z0-9]{9,10})\b')
    match = ref_pattern.search(text)
    return match.group(1) if match else None


def extract_amount_multiformat(text: str) -> Optional[Decimal]:
    """Extract amount from text handling multiple formats."""
    # Pattern 1: "KES" or "Ksh" followed by amount with optional comma
    pattern1 = re.compile(r'(?:KES|Ksh)\s*(\d{1,3}(?:,\d{3})*\.\d{2})', re.IGNORECASE)
    match1 = pattern1.search(text)
    if match1:
        try:
            amount_str = match1.group(1).replace(',', '')
            return Decimal(amount_str)
        except:
            pass
    
    # Pattern 2: "KES" or "Ksh" followed by amount without comma
    pattern2 = re.compile(r'(?:KES|Ksh)\s*(\d+\.\d{2})', re.IGNORECASE)
    match2 = pattern2.search(text)
    if match2:
        try:
            return Decimal(match2.group(1))
        except:
            pass
    
    # Pattern 3: Amount with currency prefix (no space)
    pattern3 = re.compile(r'(?:KES|Ksh)(\d{1,3}(?:,\d{3})*\.\d{2})', re.IGNORECASE)
    match3 = pattern3.search(text)
    if match3:
        try:
            amount_str = match3.group(1).replace(',', '')
            return Decimal(amount_str)
        except:
            pass
    
    return None


def _apply_ampm(hour: int, am_pm: str) -> int:
    """Convert 12-hour clock to 24-hour."""
    if am_pm.upper() == 'PM' and hour != 12:
        return hour + 12
    if am_pm.upper() == 'AM' and hour == 12:
        return 0
    return hour


def extract_timestamp_multiformat(text: str) -> Optional[datetime]:
    """Extract timestamp from text handling multiple formats.

    Kenya M-Pesa messages use DD/MM/YY format (e.g. "20/5/25 at 2:06 AM").
    That pattern is tried first; other formats follow as fallbacks.
    """
    # Pattern 0 (PRIMARY): Kenya M-Pesa "DD/MM/YY at H:MM AM/PM"
    # e.g. "on 20/5/25 at 2:06 AM" → 2025-05-20 02:06
    p0 = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{2})\s+at\s+(\d{1,2}):(\d{2})\s+(AM|PM)', re.IGNORECASE)
    m0 = p0.search(text)
    if m0:
        try:
            day, month, yr, hour, minute, am_pm = m0.groups()
            year = 2000 + int(yr)
            return datetime(year, int(month), int(day), _apply_ampm(int(hour), am_pm), int(minute), 0)
        except (ValueError, TypeError):
            pass

    # Pattern 1: "MM/DD/YYYY HH:MM:SS AM/PM"
    p1 = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s+(AM|PM)', re.IGNORECASE)
    m1 = p1.search(text)
    if m1:
        try:
            month, day, year, hour, minute, second, am_pm = m1.groups()
            return datetime(int(year), int(month), int(day), _apply_ampm(int(hour), am_pm), int(minute), int(second))
        except (ValueError, TypeError):
            pass

    # Pattern 2: "MM/DD/YYYY HH:MM AM/PM" (no seconds)
    p2 = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s+(AM|PM)', re.IGNORECASE)
    m2 = p2.search(text)
    if m2:
        try:
            month, day, year, hour, minute, am_pm = m2.groups()
            return datetime(int(year), int(month), int(day), _apply_ampm(int(hour), am_pm), int(minute), 0)
        except (ValueError, TypeError):
            pass

    return None


def extract_mpesa_period(text: str) -> Optional[str]:
    """Return YYYY-MM period string from the M-Pesa message timestamp, or None."""
    ts = extract_timestamp_multiformat(text)
    if ts:
        return ts.strftime('%Y-%m')
    return None


def extract_sender_hint(text: str) -> Optional[str]:
    """Extract sender name hint from "Dear [NAME]," pattern."""
    pattern = re.compile(r'Dear\s+([A-Z][A-Z\s]+?)[,\s]', re.IGNORECASE)
    match = pattern.search(text)
    if match:
        sender = match.group(1).strip()
        sender = re.sub(r'^\d+\s+', '', sender)
        return sender if sender else None
    return None


def detect_format(text: str) -> str:
    """Detect which format the message is in."""
    text_upper = text.upper()
    
    if re.match(r'^[TU][A-Z0-9]{9,10}$', text.strip()):
        return 'reference_only'
    
    if 'CONFIRMED' in text_upper and 'BIASHARA PAYBILL' in text_upper:
        return 'compact'
    
    if 'MPESA TRANSACTION' in text_upper and 'MPESA REF' in text_upper:
        return 'full'
    
    if extract_reference_code(text):
        return 'partial'
    
    return 'unknown'


def parse_mpesa_message(text: str, received_at: Optional[datetime] = None) -> Dict:
    """
    Parse Mpesa message in any format.
    
    Returns:
        {
            'success': bool,
            'reference': str,  # PRIMARY KEY - REQUIRED
            'amount': Optional[Decimal],
            'timestamp': Optional[datetime],
            'sender_hint': Optional[str],
            'format_detected': str,
            'received_at': datetime,
            'raw_text': str,
            'parse_warnings': List[str],
            'error': Optional[str]
        }
    """
    if received_at is None:
        received_at = datetime.now()
    
    text = text.strip()
    
    # PRIORITY 1: Check if it's JUST a reference code (minimal input)
    minimal_ref_pattern = re.compile(r'^([TU][A-Z0-9]{9,10})$')
    minimal_match = minimal_ref_pattern.match(text)
    
    if minimal_match:
        return {
            'success': True,
            'reference': minimal_match.group(1),
            'amount': None,
            'timestamp': None,
            'sender_hint': None,
            'format_detected': 'reference_only',
            'received_at': received_at,
            'raw_text': text,
            'parse_warnings': ['Only reference code provided - will match by reference only'],
            'error': None
        }
    
    # PRIORITY 2: Extract reference code (MUST EXIST)
    reference = extract_reference_code(text)
    if not reference:
        return {
            'success': False,
            'reference': None,
            'amount': None,
            'timestamp': None,
            'sender_hint': None,
            'format_detected': 'unknown',
            'received_at': received_at,
            'raw_text': text,
            'parse_warnings': [],
            'error': 'No Mpesa reference code found. Please send the full Mpesa message or just the reference code (e.g., TLU9G289GY)'
        }
    
    # PRIORITY 3: Detect format
    format_type = detect_format(text)
    
    # PRIORITY 4: Extract other fields (optional)
    amount = extract_amount_multiformat(text)
    timestamp = extract_timestamp_multiformat(text)
    sender_hint = extract_sender_hint(text)
    
    # If timestamp not found, use received_at
    if timestamp is None:
        timestamp = received_at
    
    # Collect warnings
    warnings = []
    if amount is None:
        warnings.append('Amount not found - will use bank amount as source of truth')
    if timestamp == received_at and format_type != 'reference_only':
        warnings.append('Timestamp not found - using message received time')
    
    return {
        'success': True,
        'reference': reference,
        'amount': amount,
        'timestamp': timestamp,
        'sender_hint': sender_hint,
        'format_detected': format_type,
        'received_at': received_at,
        'raw_text': text,
        'parse_warnings': warnings,
        'error': None
    }


def create_sms_claim(parsed_data: Dict) -> SMSClaim:
    """Create SMSClaim object from parsed data."""
    if not parsed_data.get('success'):
        raise ValueError(f"Cannot create claim from failed parse: {parsed_data.get('error')}")
    
    return SMSClaim(
        reference=parsed_data['reference'],
        amount=parsed_data.get('amount'),
        timestamp=parsed_data.get('timestamp'),
        received_at=parsed_data.get('received_at', datetime.now()),
        sender_hint=parsed_data.get('sender_hint'),
        status=ClaimStatus.PARSED,
        format_detected=parsed_data.get('format_detected', 'unknown'),
        raw_text=parsed_data.get('raw_text', ''),
        parse_warnings=parsed_data.get('parse_warnings', [])
    )
