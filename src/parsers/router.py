"""
Input Router - Central entry point for all parsing operations.
Auto-detects input type and routes to appropriate parser.
"""
import os
from typing import Optional, Union
from enum import Enum
import re


class InputType(Enum):
    MPESA_SMS = 'mpesa_sms'
    BANK_STATEMENT_PDF = 'bank_statement_pdf'
    TENANT_EXCEL = 'tenant_excel'
    UNKNOWN = 'unknown'


class ParseResult:
    """Standardized result from any parser."""
    def __init__(
        self,
        success: bool,
        input_type: InputType,
        data: dict = None,
        errors: list = None,
        warnings: list = None,
        metadata: dict = None
    ):
        self.success = success
        self.input_type = input_type
        self.data = data or {}
        self.errors = errors or []
        self.warnings = warnings or []
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            'success': self.success,
            'input_type': self.input_type.value,
            'data': self.data,
            'errors': self.errors,
            'warnings': self.warnings,
            'metadata': self.metadata
        }


def detect_input_type(data: Union[str, bytes], filename: str = None) -> InputType:
    """
    Auto-detect the type of input data.

    Args:
        data: Raw input (file path, text content, or bytes)
        filename: Optional filename hint for file uploads

    Returns:
        InputType enum value
    """
    # Check if it's a file path
    if isinstance(data, str) and os.path.isfile(data):
        ext = os.path.splitext(data)[1].lower()
        if ext == '.pdf':
            return InputType.BANK_STATEMENT_PDF
        elif ext in ('.xlsx', '.xls'):
            return InputType.TENANT_EXCEL

    # Check filename hint
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext == '.pdf':
            return InputType.BANK_STATEMENT_PDF
        elif ext in ('.xlsx', '.xls'):
            return InputType.TENANT_EXCEL

    # Check if it's M-Pesa SMS text
    if isinstance(data, str):
        mpesa_patterns = [
            r'[A-Z0-9]{10}',
            r'confirmed',
            r'received',
            r'Ksh\s*[\d,]+',
            r'M-PESA',
            r'Mpesa'
        ]
        matches = sum(1 for p in mpesa_patterns if re.search(p, data, re.IGNORECASE))
        if matches >= 2:
            return InputType.MPESA_SMS

    return InputType.UNKNOWN


def parse_input(
    data: Union[str, bytes],
    input_type: InputType = None,
    filename: str = None,
    **kwargs
) -> ParseResult:
    """
    Central parsing function - routes to appropriate parser.

    Args:
        data: Raw input (file path, text content, or bytes)
        input_type: Optional explicit type (auto-detected if None)
        filename: Optional filename hint
        **kwargs: Additional arguments passed to specific parser

    Returns:
        ParseResult with standardized format
    """
    if input_type is None:
        input_type = detect_input_type(data, filename)

    if input_type == InputType.UNKNOWN:
        return ParseResult(
            success=False,
            input_type=InputType.UNKNOWN,
            errors=['Could not determine input type. Please specify the type or check the format.']
        )

    if input_type == InputType.MPESA_SMS:
        return _parse_mpesa_sms(data)
    elif input_type == InputType.BANK_STATEMENT_PDF:
        return _parse_bank_statement(data, **kwargs)
    elif input_type == InputType.TENANT_EXCEL:
        return _parse_tenant_excel(data)

    return ParseResult(
        success=False,
        input_type=input_type,
        errors=[f'No parser available for type: {input_type.value}']
    )


def _parse_mpesa_sms(text: str) -> ParseResult:
    """Wrapper for SMS parser."""
    from src.parsers.sms_parser import parse_mpesa_message

    result = parse_mpesa_message(text)
    amount = result.get('amount')
    timestamp = result.get('timestamp')
    # JSON-serializable values for data
    data_amount = float(amount) if amount is not None else None
    data_timestamp = timestamp.isoformat() if hasattr(timestamp, 'isoformat') else timestamp

    return ParseResult(
        success=result.get('success', False),
        input_type=InputType.MPESA_SMS,
        data={
            'reference': result.get('reference'),
            'amount': data_amount,
            'sender': result.get('sender_hint'),
            'timestamp': data_timestamp
        },
        errors=[result.get('error')] if result.get('error') else [],
        metadata={'format_detected': result.get('format_detected')}
    )


def _parse_bank_statement(file_path: str, **kwargs) -> ParseResult:
    """Wrapper for PDF parser."""
    from src.parsers.pdf_parser import parse_bank_statement

    result = parse_bank_statement(file_path)
    validation = result.get('validation', {})
    valid = validation.get('valid', False)
    errors = list(result.get('errors', []))
    if validation.get('error'):
        errors.append(validation.get('error'))

    if not valid:
        return ParseResult(
            success=False,
            input_type=InputType.BANK_STATEMENT_PDF,
            errors=errors or ['Failed to parse bank statement']
        )

    transactions = result.get('transactions', [])
    serialized = []
    for t in transactions:
        if hasattr(t, '__dict__'):
            d = dict(t.__dict__)
            for k, v in d.items():
                if hasattr(v, 'isoformat'):
                    d[k] = v.isoformat()
                elif hasattr(v, '__float__') and not isinstance(v, (int, float)):
                    try:
                        d[k] = float(v)
                    except (TypeError, ValueError):
                        pass
            serialized.append(d)
        else:
            serialized.append(t)

    warnings = result.get('warnings', [])
    if not isinstance(warnings, list):
        warnings = [warnings] if warnings else []

    ob = result.get('opening_balance')
    cb = result.get('closing_balance')
    return ParseResult(
        success=True,
        input_type=InputType.BANK_STATEMENT_PDF,
        data={
            'transactions': serialized,
            'transaction_count': len(transactions),
            'opening_balance': float(ob) if ob is not None else None,
            'closing_balance': float(cb) if cb is not None else None
        },
        warnings=warnings,
        metadata={}
    )


def _parse_tenant_excel(file_path: str) -> ParseResult:
    """Wrapper for Excel parser."""
    from src.parsers.excel_parser import parse_tenant_excel

    result = parse_tenant_excel(file_path)
    return ParseResult(
        success=result.get('success', False),
        input_type=InputType.TENANT_EXCEL,
        data={
            'rows': result.get('rows', []),
            'row_count': len(result.get('rows', [])),
            'total_arrears': result.get('total_arrears', 0)
        },
        errors=result.get('errors', []),
        warnings=result.get('warnings', [])
    )
