"""Scanned statements are accepted, but a reading whose totals don't add up is flagged."""

import json
import os
import unittest
from unittest.mock import patch

from src.parsers import pdf_parser


def _vision(amount):
    return json.dumps({'opening_balance': 1000.00, 'closing_balance': 1500.00, 'transactions': [
        {'date': '03 Jul 2026', 'txn_type': 'PAYBILL_CREDIT', 'amount': amount, 'direction': 'credit',
         'reference': 'FT1', 'description': 'NCBA BANK M PESA'}]})


class ScannedTotals(unittest.TestCase):
    def _parse(self, amount):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), \
             patch.object(pdf_parser, '_pdf_pages_to_b64', return_value=['page']), \
             patch('src.agent.llm.call_llm_vision', return_value=_vision(amount)):
            return pdf_parser._parse_scanned_statement('statement.pdf')

    def test_misread_amount_is_flagged_but_kept(self):
        r = self._parse(400.00)                       # the paper says 500
        self.assertTrue(r['validation']['valid'])
        self.assertTrue(any('do not add up' in e and '100.00' in e for e in r['errors']), r['errors'])

    def test_reading_that_adds_up_has_no_error(self):
        r = self._parse(500.00)
        self.assertFalse([e for e in r['errors'] if 'add up' in e], r['errors'])


if __name__ == '__main__':
    unittest.main()
