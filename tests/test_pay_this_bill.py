"""'Pay this bill': the tenant says which bill a payment is for; that bill is settled first,
anything above it oldest first. A plain payment still pays the oldest bills first."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PayThisBill(unittest.TestCase):
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
        patch('src.routes.tenant_routes.send_sms_async').start()
        from app import app
        from src.database.db import get_connection
        self.connection = get_connection
        self.client = app.test_client()
        with get_connection() as conn:                  # David, B2: two months behind
            self.tenant = dict(conn.execute("SELECT t.* FROM tenants t JOIN units u ON u.id=t.unit_id "
                                            "WHERE u.unit_number='B2' AND t.status='active'").fetchone())
            self.open = [dict(r) for r in conn.execute("""
                SELECT rc.id, rc.charge_type, rc.period, rc.amount FROM rent_charges rc
                WHERE rc.tenant_id = ? AND NOT EXISTS (SELECT 1 FROM payment_allocations pa WHERE pa.charge_id = rc.id)
                ORDER BY rc.created_at""", (self.tenant['id'],))]
            self.other_charge = conn.execute("""SELECT rc.id FROM rent_charges rc JOIN units u ON u.id = rc.unit_id
                                                WHERE u.unit_number = 'A4' LIMIT 1""").fetchone()[0]
        self.token = self.tenant['access_token']
        with self.client.session_transaction() as session:
            session['tenant_person_id'] = self.tenant['person_id']
            session[f'pay_auth_{self.token}'] = True

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def pay(self, amount, charge_id=None):
        data = {'amount': str(amount), 'phone': '0700000108'}
        if charge_id:
            data['charge_id'] = charge_id
        r = self.client.post(f'/tenant/{self.token}/pay/stk', data=data)
        self.assertTrue(r.get_json()['success'], r.get_json())
        with self.connection() as conn:
            return [tuple(a) for a in conn.execute("""
                SELECT pa.charge_id, pa.amount FROM payment_allocations pa JOIN payments p ON p.id = pa.payment_id
                WHERE p.tenant_id = ? ORDER BY p.created_at DESC, pa.rowid""", (self.tenant['id'],))]

    def test_chosen_bill_is_paid_first(self):
        newest_water = [c for c in self.open if c['charge_type'] == 'water'][-1]
        allocations = self.pay(int(newest_water['amount']), newest_water['id'])
        self.assertEqual(allocations[0], (newest_water['id'], newest_water['amount']))

    def test_more_than_the_bill_goes_to_the_oldest(self):
        newest_water = [c for c in self.open if c['charge_type'] == 'water'][-1]
        oldest = self.open[0]
        allocations = self.pay(int(newest_water['amount']) + 500, newest_water['id'])
        self.assertEqual(allocations[:2], [(newest_water['id'], newest_water['amount']), (oldest['id'], 500)])

    def test_plain_payment_pays_the_oldest_first(self):
        allocations = self.pay(500)
        self.assertEqual(allocations[0], (self.open[0]['id'], 500))

    def test_someone_elses_bill_is_ignored(self):
        allocations = self.pay(500, self.other_charge)
        self.assertEqual(allocations[0], (self.open[0]['id'], 500))

    def test_pay_page_shows_the_chosen_bill(self):
        newest_water = [c for c in self.open if c['charge_type'] == 'water'][-1]
        page = self.client.get(f"/tenant/{self.token}/pay?bill={newest_water['id']}").get_data(as_text=True)
        self.assertIn('Paying for', page)
        self.assertIn(f'name="charge_id" value="{newest_water["id"]}"', page)

    def test_pasted_code_for_a_bill_is_kept_on_the_claim(self):
        newest_water = [c for c in self.open if c['charge_type'] == 'water'][-1]
        sms = 'TKX9BILL01 Confirmed. Ksh980.00 sent to KIJANI COURT for account B2 on 3/10/26 at 10:15 AM.'
        self.client.post(f'/tenant/{self.token}/report-payment',
                         data={'mpesa_sms': sms, 'charge_id': newest_water['id']})
        with self.connection() as conn:
            target = conn.execute("SELECT target_charge_id FROM payment_claims WHERE mpesa_ref = 'TKX9BILL01'").fetchone()[0]
        self.assertEqual(target, newest_water['id'])


if __name__ == '__main__':
    unittest.main()
