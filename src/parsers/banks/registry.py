"""
Bank parser registry.

Adding a new bank:
  1. Write a parser module in src/parsers/banks/ that exports:
       BANK_FORMAT = 'my_bank'          # unique key
       BANK_DISPLAY_NAME = 'My Bank'
       def can_parse(raw_text: str, page_numbers) -> bool
       def parse(raw_text: str, page_numbers) -> dict   # same shape as _finalize_bank_statement_result
  2. Import it here and append to PARSERS.

The orchestrator in pdf_parser.parse_bank_statement calls detect_bank_statement_format
(returns 'cooperative' | 'tabular_kes' | 'unknown').  This registry provides the display
name lookup and is the authoritative place to extend support.
"""

BANK_DISPLAY_NAMES = {
    'cooperative': 'Co-operative Bank',
    'tabular_kes': 'KCB / Tabular',
    'unknown': 'Unknown format',
}

SUPPORTED_FORMATS = list(BANK_DISPLAY_NAMES.keys())


def bank_display_name(fmt: str) -> str:
    return BANK_DISPLAY_NAMES.get(fmt or 'unknown', fmt or 'Unknown')
