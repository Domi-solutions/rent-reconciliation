"""Money out: caretaker submits, admin approves and pays back; cash book adds up; nothing is edited away."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Expenses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.template = os.path.join(cls.tmp, 'template.db')
        subprocess.run([sys.executable, '-c', 'import app'], cwd=ROOT, check=True,
                       env=dict(os.environ, DATABASE_PATH=cls.template, FLASK_ENV='development',
                                PYTHONDONTWRITEBYTECODE='1'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.db = os.path.join(self.tmp, 'test.db')
        shutil.copy(self.template, self.db)
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db})
        self.env.start()
        from src.database.db import get_connection
        self.get_connection = get_connection
        with get_connection() as conn:
            conn.execute("INSERT INTO properties (id, name) VALUES ('P1', 'Mowin')")
            conn.execute("INSERT INTO units (id, property_id, unit_number, monthly_rent, status) VALUES ('U1','P1','1A',10000,'occupied')")
            conn.execute("INSERT INTO caretakers (id, property_id, name) VALUES ('CT1', 'P1', 'Juma')")
            conn.execute("INSERT INTO payments (id, property_id, unit_id, amount, payment_date, assignment_type) "
                         "VALUES ('PAY1', 'P1', 'U1', 30000, ?, 'manual')", (date.today().isoformat(),))

    def tearDown(self):
        self.env.stop()

    def _prop(self, conn):
        return conn.execute("SELECT * FROM properties WHERE id = 'P1'").fetchone()

    def test_caretaker_expense_needs_approval_then_payback(self):
        from src.money import expenses as ex
        today = date.today().isoformat()
        with self.get_connection() as conn:
            eid, _, _ = ex.record(conn, self._prop(conn), {'category': 'repairs', 'amount': '800', 'expense_date': today,
                                  'description': 'New tap 1A', 'paid_myself': '1'}, 'caretaker:Juma',
                                  by='caretaker', caretaker_id='CT1')
            e = ex.get(conn, eid, 'P1')
            self.assertEqual((e['status'], e['reimburse_caretaker']), ('submitted', 1))
            self.assertEqual(ex.waiting_count(conn, 'P1'), 1)
            _, t = ex.cash_book(conn, 'P1')
            self.assertEqual(t['expenses'], 0)                    # not money out until paid
            with self.assertRaises(ex.ExpenseError):
                ex.mark_paid(conn, e, 'mpesa', 'X', 'admin:L')    # can't pay before approval
            ex.decide(conn, e, True, '', 'admin:L')
            ex.mark_paid(conn, ex.get(conn, eid, 'P1'), 'mpesa', 'UJ1', 'admin:L')
            _, t = ex.cash_book(conn, 'P1')
            self.assertEqual((t['collected'], t['expenses'], t['held']), (30000, 800, 29200))

    def test_admin_records_owner_payout_and_voids_mistake(self):
        from src.money import expenses as ex
        today = date.today().isoformat()
        with self.get_connection() as conn:
            prop = self._prop(conn)
            ex.record(conn, prop, {'category': 'owner_payout', 'amount': '20000', 'expense_date': today,
                                   'description': 'October to owner', 'already_paid': '1', 'paid_method': 'bank'}, 'admin:L')
            eid, _, _ = ex.record(conn, prop, {'category': 'security', 'amount': '5000', 'expense_date': today,
                                               'description': 'Guard Oct', 'already_paid': '1', 'paid_method': 'cash'}, 'admin:L')
            ex.void(conn, ex.get(conn, eid, 'P1'), 'Entered twice', 'admin:L')
            months, t = ex.cash_book(conn, 'P1')
            self.assertEqual((t['owner_payouts'], t['expenses'], t['held']), (20000, 0, 10000))
            self.assertEqual(months[0]['net'], 10000)
            with self.assertRaises(ex.ExpenseError):              # caretakers can't record owner payouts
                ex.record(conn, prop, {'category': 'owner_payout', 'amount': '1', 'expense_date': today,
                                       'description': 'x x x'}, 'caretaker:J', by='caretaker', caretaker_id='CT1')
            self.assertTrue(conn.execute("SELECT 1 FROM audit_log WHERE action = 'expense_voided'").fetchone())


if __name__ == '__main__':
    unittest.main()
