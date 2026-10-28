"""A tenant's pasted M-Pesa code is booked to them only if the payment is recognisably theirs.

Anyone who has seen a neighbour's M-Pesa SMS could paste its code; without this
check the first person to paste it was credited with the neighbour's money.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = 'TKX9OWNER1'
SMS = f'{REF} Confirmed. Ksh5,000.00 sent to KIJANI COURT for account A1 on 3/10/26 at 10:15 AM.'


class ClaimOwner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.template = os.path.join(cls.tmp, 'template.db')
        subprocess.run([sys.executable, os.path.join(ROOT, 'scripts/seed_demo.py'), cls.template],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=dict(os.environ, FLASK_RUN_FROM_CLI='true', AT_SMS_ENABLED='false'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def setUp(self):
        self.db = os.path.join(self.tmp, 'case.db')
        shutil.copy(self.template, self.db)
        self.env = patch.dict(os.environ, {
            'DATABASE_PATH': self.db, 'DEMO_MODE': 'true', 'FLASK_ENV': 'development',
            'ENVIRONMENT': 'development', 'FLASK_RUN_FROM_CLI': 'true', 'AT_SMS_ENABLED': 'false',
        })
        self.env.start()
        from app import app
        from src.database.db import get_connection
        self.connection = get_connection
        self.client = app.test_client()
        with get_connection() as conn:
            self.brian = dict(conn.execute("SELECT t.* FROM tenants t JOIN units u ON u.id=t.unit_id "
                                           "WHERE u.unit_number='A1' AND t.status='active'").fetchone())
            self.faith = dict(conn.execute("SELECT t.* FROM tenants t JOIN units u ON u.id=t.unit_id "
                                           "WHERE u.unit_number='A2' AND t.status='active'").fetchone())
            org = conn.execute("SELECT organization_id FROM properties WHERE id=?",
                               (self.brian['property_id'],)).fetchone()[0]
            conn.execute("INSERT INTO bank_statements (id, org_id, filename, file_path, period_start, period_end) "
                         "VALUES ('STMT-OWNER', ?, 'x.pdf', 'x.pdf', '2026-10-01', '2026-10-31')", (org,))
        with self.client.session_transaction() as session:
            session['tenant_person_id'] = self.brian['person_id']

    def tearDown(self):
        self.env.stop()

    def bank_line(self, sender):
        with self.connection() as conn:
            conn.execute("INSERT INTO bank_transactions (id, statement_id, mpesa_ref, amount, txn_type, sender_name, "
                         "txn_date, raw_text) VALUES ('BTX-OWNER', 'STMT-OWNER', ?, 5000, 'PAYBILL_CREDIT', ?, "
                         "'2026-10-03', ?)", (REF, sender, f'{REF} {sender}'))

    def report(self):
        return self.client.post(f"/tenant/{self.brian['access_token']}/report-payment", data={'mpesa_sms': SMS})

    def state(self):
        with self.connection() as conn:
            claim = conn.execute("SELECT status, flag_reason FROM payment_claims WHERE mpesa_ref=? AND tenant_id=?",
                                 (REF, self.brian['id'])).fetchone()
            pay = conn.execute("SELECT tenant_id FROM payments WHERE bank_txn_id='BTX-OWNER'").fetchone()
            flagged = conn.execute("SELECT flagged FROM tenants WHERE id=?", (self.brian['id'],)).fetchone()[0]
        return claim, pay, flagged

    def test_own_payment_is_booked_to_the_tenant(self):
        self.bank_line('BRIAN OTIENO')
        self.report()
        claim, pay, _ = self.state()
        self.assertEqual(claim['status'], 'verified')
        self.assertEqual(pay['tenant_id'], self.brian['id'])

    def test_someone_elses_payment_is_held_not_booked(self):
        self.bank_line('FAITH CHEBET')
        response = self.report()
        claim, pay, flagged = self.state()
        self.assertEqual((claim['status'], claim['flag_reason']), ('flagged', 'sender_not_tenant'))
        self.assertIsNone(pay)                              # bank line stays unassigned for management
        self.assertFalse(flagged)                           # paying from a relative's phone is normal
        self.assertIn('notice=', response.headers['Location'])

    def test_code_already_on_another_account_is_not_shown_as_confirmed(self):
        self.bank_line('FAITH CHEBET')
        with self.connection() as conn:
            conn.execute("INSERT INTO payments (id, property_id, unit_id, tenant_id, bank_txn_id, statement_id, amount, "
                         "payment_date, assignment_type) VALUES ('PAY-FAITH', ?, ?, ?, 'BTX-OWNER', 'STMT-OWNER', "
                         "5000, '2026-10-03', 'manual')",
                         (self.faith['property_id'], self.faith['unit_id'], self.faith['id']))
        self.report()
        claim, pay, _ = self.state()
        self.assertEqual((claim['status'], claim['flag_reason']), ('flagged', 'on_other_account'))
        self.assertEqual(pay['tenant_id'], self.faith['id'])  # Faith's payment untouched
        page = self.client.get(f"/tenant/{self.brian['access_token']}/pay").get_data(as_text=True)
        self.assertIn('already on another account', page)


if __name__ == '__main__':
    unittest.main()
