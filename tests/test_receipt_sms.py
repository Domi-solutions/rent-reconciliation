"""Payment receipts: what was paid, what it settled, the balance today — no portal link before approval."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ReceiptSms(unittest.TestCase):
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
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db, 'TENANT_OUTREACH_ENABLED': ''})
        self.env.start()
        from src.database.db import allocate_payment, get_connection
        self.get_connection, self.allocate = get_connection, allocate_payment
        with get_connection() as conn:
            conn.execute("INSERT INTO properties (id, name) VALUES ('P1', 'mowin apartments')")
            conn.execute("INSERT INTO units (id, property_id, unit_number, monthly_rent, status) "
                         "VALUES ('U1', 'P1', '6A', 17000, 'occupied')")
            conn.execute("""INSERT INTO tenants (id, property_id, unit_id, name, phone, status, access_token, short_code)
                            VALUES ('T1', 'P1', 'U1', 'Jane', '0712345678', 'active', 'tok', 'ABC123')""")
            for cid, ctype, amt in (('C1', 'rent', 17000), ('C2', 'service', 1000), ('C3', 'water', 4500)):
                conn.execute("INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount) "
                             "VALUES (?, 'P1', 'U1', '2026-10', ?, ?)", (cid, ctype, amt))
            conn.execute("INSERT INTO bank_statements (id, org_id, filename, file_path) VALUES ('S1', NULL, 'x', 'x')")
            conn.execute("INSERT INTO bank_transactions (id, statement_id, mpesa_ref, amount, txn_type) "
                         "VALUES ('B1', 'S1', 'UJ81O9VVRZ', 18000, 'PAYBILL_CREDIT')")

    def tearDown(self):
        self.env.stop()

    def _pay(self, conn, pid, amount, day='2026-10-09', bank='B1'):
        conn.execute("""INSERT INTO payments (id, property_id, unit_id, bank_txn_id, amount, payment_date, assignment_type)
                        VALUES (?, 'P1', 'U1', ?, ?, ?, 'manual')""", (pid, bank, amount, day))
        self.allocate(conn, pid, 'U1', amount)

    def test_receipt_lists_what_was_settled_and_the_balance(self):
        from src.messaging.tenant_payment import receipt_sms
        with self.get_connection() as conn:
            self._pay(conn, 'PAY1', 18000)
            phone, body = receipt_sms(conn, 'PAY1', 'https://domi.co.ke')
        self.assertEqual(phone, '+254712345678')
        self.assertEqual(body, 'Mowin Apartments: payment of KES 18,000 confirmed (UJ81O9VVRZ, 9 Oct). '
                               'Applied: Oct rent 17,000, Oct service 1,000. Balance: KES 4,500 owed.')

    def test_portal_link_only_once_outreach_is_approved(self):
        from src.messaging.tenant_payment import receipt_sms
        with self.get_connection() as conn:
            self._pay(conn, 'PAY1', 18000)
            with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': 'true'}):
                _, body = receipt_sms(conn, 'PAY1', 'https://domi.co.ke/')
                self.assertTrue(body.endswith('Receipt: https://domi.co.ke/t/ABC123'), body)
                conn.execute("UPDATE tenants SET access_token = NULL WHERE id = 'T1'")
                _, body = receipt_sms(conn, 'PAY1', 'https://domi.co.ke/')
                self.assertNotIn('http', body)

    def test_credit_and_nothing_owed(self):
        from src.messaging.tenant_payment import receipt_sms
        with self.get_connection() as conn:
            self._pay(conn, 'PAY1', 22500)
            _, body = receipt_sms(conn, 'PAY1')
            self.assertIn('Balance: nothing owed.', body)
            conn.execute("INSERT INTO bank_transactions (id, statement_id, mpesa_ref, amount, txn_type) "
                         "VALUES ('B2', 'S1', 'UJ9', 2000, 'PAYBILL_CREDIT')")
            self._pay(conn, 'PAY2', 2000, bank='B2')
            _, body = receipt_sms(conn, 'PAY2')
            self.assertIn('Held as credit for your next charges.', body)
            self.assertIn('Balance: KES 2,000 in credit.', body)

    def test_no_phone_on_tenancy_uses_the_paying_phone_without_a_link(self):
        from src.messaging.tenant_payment import receipt_sms
        with self.get_connection() as conn:
            conn.execute("UPDATE tenants SET phone = NULL WHERE id = 'T1'")
            self._pay(conn, 'PAY1', 18000)
            self.assertIsNone(receipt_sms(conn, 'PAY1'))
            with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': 'true'}):
                phone, body = receipt_sms(conn, 'PAY1', 'https://domi.co.ke', fallback_phone='0799000000')
            self.assertEqual(phone, '+254799000000')
            self.assertNotIn('http', body)
            self.assertIsNone(conn.execute("SELECT phone FROM tenants WHERE id = 'T1'").fetchone()[0])


if __name__ == '__main__':
    unittest.main()
