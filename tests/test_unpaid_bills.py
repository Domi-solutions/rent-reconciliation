"""What "Still owed" is made of: unpaid_bills() lists the bills with money left on them."""

import unittest

from src.utils.tenant_documents import unpaid_bills


def charge(period, charge_type, amount, allocated, due):
    return {'row_type': 'charge', 'period': period, 'charge_type': charge_type, 'is_arrears': period == 'ARREARS',
            'debit': amount, 'allocated': allocated, 'remaining': round(amount - allocated, 2), 'row_date': due}


class UnpaidBills(unittest.TestCase):
    def test_lists_open_bills_oldest_first_and_adds_up_to_the_balance(self):
        rows = [
            charge('2026-10', 'water', 980, 0, '2026-10-05'),
            charge('2026-10', 'rent', 18000, 0, '2026-10-05'),
            charge('2026-09', 'rent', 18000, 18000, '2026-09-05'),        # paid: left out
            charge('2026-09', 'water', 980, 480, '2026-09-05'),           # part paid
            charge('ARREARS', 'brought_forward', 2000, 0, '2026-06-01'),
            {'row_type': 'payment', 'period': None, 'credit': 18480},
        ]
        owed = unpaid_bills(rows, balance=21480, today='2026-10-28')
        self.assertEqual([b['label'] for b in owed['bills']],
                         ['Earlier unpaid bills', 'Water — September 2026', 'Rent — October 2026', 'Water — October 2026'])
        self.assertEqual(owed['total'], 21480)
        self.assertEqual(owed['unapplied'], 0)
        part = owed['bills'][1]
        self.assertEqual((part['remaining'], part['paid'], part['amount']), (500, 480, 980))
        self.assertTrue(part['late'])
        self.assertFalse(owed['bills'][0]['late'])                       # earlier bills have no due date to be late on

    def test_not_late_before_the_due_date(self):
        owed = unpaid_bills([charge('2026-11', 'rent', 18000, 0, '2026-11-05')], 18000, today='2026-10-28')
        self.assertFalse(owed['bills'][0]['late'])
        self.assertEqual(owed['bills'][0]['due'], '5 Nov 2026')

    def test_payment_not_yet_set_against_a_bill_is_shown(self):
        owed = unpaid_bills([charge('2026-10', 'rent', 18000, 0, '2026-10-05')], balance=8000, today='2026-10-28')
        self.assertEqual((owed['total'], owed['unapplied']), (18000, 10000))


if __name__ == '__main__':
    unittest.main()
