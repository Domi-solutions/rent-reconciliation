"""Charges fall due per the lease: the rent due day of the same month."""

import unittest

from src.utils.due_dates import charge_due_date


class ChargeDueDate(unittest.TestCase):
    def test_same_month_on_the_due_day(self):
        self.assertEqual(charge_due_date('2026-11', 5), '2026-11-05')
        self.assertEqual(charge_due_date('2026-12', 5), '2026-12-05')       # no rollover into January

    def test_zero_means_last_day_and_short_months_clamp(self):
        self.assertEqual(charge_due_date('2026-02', 0), '2026-02-28')
        self.assertEqual(charge_due_date('2026-02', 31), '2026-02-28')
        self.assertEqual(charge_due_date('2026-04', None), '2026-04-05')

    def test_non_month_periods_have_no_due_date(self):
        self.assertIsNone(charge_due_date('ARREARS', 5))
        self.assertIsNone(charge_due_date('2026-13', 5))


if __name__ == '__main__':
    unittest.main()
