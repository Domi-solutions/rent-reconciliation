"""Assigning a payment from a tenant's statement (Quick verify) sends the same receipt SMS as every other path."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class QuickAssignReceipt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, 'qa.db')
        subprocess.run([sys.executable, os.path.join(ROOT, 'scripts/seed_demo.py'), cls.db],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=dict(os.environ, FLASK_RUN_FROM_CLI='true', AT_SMS_ENABLED='false'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def setUp(self):
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db, 'DEMO_MODE': 'true', 'FLASK_ENV': 'development',
                                           'ENVIRONMENT': 'development', 'FLASK_RUN_FROM_CLI': 'true',
                                           'AT_SMS_ENABLED': 'false'})
        self.env.start()
        self.sent = []
        patch('src.messaging.delivery.send_sms_async', side_effect=lambda r, t: self.sent.append((r, t))).start()

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def test_receipt_goes_out(self):
        from app import app
        from src.database.db import get_connection
        with get_connection() as conn:
            t = conn.execute("SELECT t.* FROM tenants t JOIN units u ON u.id=t.unit_id "
                             "WHERE u.unit_number='B2' AND t.status='active'").fetchone()
            org = conn.execute("SELECT organization_id FROM properties WHERE id=?", (t['property_id'],)).fetchone()[0]
            conn.execute("INSERT INTO bank_statements (id, org_id, filename, file_path) VALUES ('STMT-QA', ?, 'x', 'x')", (org,))
            conn.execute("INSERT INTO bank_transactions (id, statement_id, mpesa_ref, amount, txn_type, sender_name, txn_date) "
                         "VALUES ('BTX-QA', 'STMT-QA', 'TKQA000001', 5000, 'PAYBILL_CREDIT', 'DAVID KIPROP', '2026-10-20')")
        admin = app.test_client()
        admin.post('/login', data={'email': 'demo@demo', 'password': 'demo', 'next': ''})
        admin.get('/properties')
        admin.post(f"/tenants/{t['id']}/quick-assign/BTX-QA")
        with get_connection() as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM payments WHERE bank_txn_id='BTX-QA'").fetchone())
        self.assertEqual(len(self.sent), 1)
        self.assertIn('5,000 confirmed', self.sent[0][1])


if __name__ == '__main__':
    unittest.main()
