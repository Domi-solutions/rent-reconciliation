"""Reminder and message templates say what the account is: Still owed, Extra paid (and by how
much) or Nothing owed — never "Outstanding: KES -23,000", never "please settle" to someone ahead."""

import unittest

import os
from unittest.mock import patch

from src.messaging.outreach import portal_footer
from src.messaging.wording import fill

REMINDER = ('Hi {tenant_name}, a gentle reminder that rent is due in 5 days on {due_date}.\n\n'
            'Monthly rent: KES {monthly_rent}\n'
            'Paid to date: KES {total_paid}\n'
            'Outstanding: KES {balance}\n\n'
            'Kindly settle the outstanding balance before {due_date}.\n\n'
            'Kind regards.')


def reminder(balance):
    return fill(REMINDER, {'tenant_name': 'Peter', 'due_date': '5 Nov 2026', 'monthly_rent': '22,000',
                           'total_paid': '45,000', 'balance': f'{balance:,.0f}', 'balance_value': balance})


class MessageWording(unittest.TestCase):
    def test_owed(self):
        text = reminder(72600)
        self.assertIn('Still owed: KES 72,600', text)
        self.assertIn('Kindly settle the outstanding balance before 5 Nov 2026.', text)
        self.assertNotIn('Outstanding', text)

    def test_paid_ahead_says_extra_paid_and_how_much(self):
        text = reminder(-23000)
        self.assertIn('Extra paid: KES 23,000', text)
        self.assertIn('used for your next bill', text)
        self.assertIn('Nothing to pay now.', text)
        self.assertNotIn('-23', text)
        self.assertNotIn('settle', text)

    def test_nothing_owed(self):
        text = reminder(0)
        self.assertIn('Nothing owed', text)
        self.assertNotIn('settle', text)

    def test_balance_elsewhere_is_never_negative(self):
        self.assertEqual(fill('You owe KES {balance}; ahead by KES {extra_paid}.', {'balance_value': -500}),
                         'You owe KES 0; ahead by KES 500.')

    def test_without_a_balance_nothing_changes(self):
        self.assertEqual(fill('Outstanding: KES {balance}', {'balance': '10'}), 'Outstanding: KES 10')


class PortalFooter(unittest.TestCase):
    def test_footer_only_once_links_are_approved(self):
        with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': ''}):
            self.assertEqual(portal_footer('Rent due.', 'https://x.test', 'ABC123'), 'Rent due.')
        with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': 'true'}):
            text = portal_footer('Rent due.', 'https://x.test/', 'ABC123')
            self.assertEqual(text, 'Rent due.\n\nOpen Domi: https://x.test/t/ABC123')
            self.assertEqual(portal_footer(text, 'https://x.test', 'ABC123'), text)     # never twice
            self.assertEqual(portal_footer('Rent due.', 'https://x.test', None), 'Rent due.')


if __name__ == '__main__':
    unittest.main()
