"""Tenant notices, dated balances, water evidence and truthful demo-code feedback."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TenantPortalDetails(unittest.TestCase):
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
        self.app, self.connection = app, get_connection
        self.client = app.test_client()
        with get_connection() as conn:
            self.tenant = dict(conn.execute("SELECT t.* FROM tenants t JOIN units u ON u.id=t.unit_id WHERE u.unit_number='A1'").fetchone())
            self.charge = dict(conn.execute("SELECT * FROM rent_charges WHERE tenant_id=? AND charge_type='water' ORDER BY period DESC LIMIT 1", (self.tenant['id'],)).fetchone())
        self.path = '/tenant/' + self.tenant['access_token']
        with self.client.session_transaction() as session:
            session['tenant_person_id'] = self.tenant['person_id']

    def tearDown(self):
        self.env.stop()

    def test_water_uses_stored_rate_not_current_tariff(self):
        from src.utils.water_breakdown import water_breakdowns
        from src.utils.tenant_documents import build_invoice
        with self.connection() as conn:
            conn.execute('UPDATE properties SET water_rate=999 WHERE id=?', (self.tenant['property_id'],))
            detail = water_breakdowns(conn, self.tenant['id'], self.tenant['property_id'])[self.charge['id']]
            invoice = build_invoice(conn, self.tenant['id'], self.tenant['property_id'], self.charge['period'])
        self.assertTrue(detail['available'])
        self.assertEqual(detail['rate'], 100)
        self.assertEqual(detail['units_consumed'], 6.5)
        self.assertEqual(detail['amount'], self.charge['amount'])
        self.assertTrue(next(c for c in invoice['charges'] if c['charge_type']=='water')['water_breakdown']['available'])
        for suffix in ('/statement', '/invoice/' + self.charge['period'], '/charges'):
            response = self.client.get(self.path + suffix)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('Previous reading', html)
            self.assertIn('New reading', html)
            self.assertIn('6.5 units × KES 100 per unit = KES 650', html)

    def test_missing_and_inconsistent_evidence_are_never_inferred(self):
        from src.utils.water_breakdown import water_breakdowns
        with self.connection() as conn:
            conn.execute('UPDATE rent_charges SET amount=amount+1 WHERE id=?', (self.charge['id'],))
            details = water_breakdowns(conn, self.tenant['id'], self.tenant['property_id'])
            self.assertFalse(details[self.charge['id']]['available'])
        html = self.client.get(self.path + '/invoice/' + self.charge['period']).get_data(as_text=True)
        self.assertIn('do not match this charge', html)
        self.assertNotIn('Previous reading', html)
        with self.connection() as conn:
            conn.execute('DELETE FROM water_readings WHERE unit_id=?', (self.tenant['unit_id'],))
        html = self.client.get(self.path + '/statement').get_data(as_text=True)
        self.assertIn('have not been recorded for this charge', html)
        self.assertNotIn('Previous reading', html)

    def test_ambiguous_and_other_property_evidence_is_not_shown(self):
        from src.utils.water_breakdown import water_breakdowns
        with self.connection() as conn:
            r = conn.execute("SELECT wr.* FROM water_readings wr JOIN water_uploads wu ON wu.id=wr.upload_id WHERE wr.unit_id=? AND wu.charge_period=?", (self.tenant['unit_id'], self.charge['period'])).fetchone()
            conn.execute('INSERT INTO water_readings (id, upload_id, unit_id, previous_reading, current_reading, units_consumed, rate, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', ('duplicate', r['upload_id'], r['unit_id'], r['previous_reading'], r['current_reading'], r['units_consumed'], r['rate'], r['amount']))
            self.assertFalse(water_breakdowns(conn, self.tenant['id'], self.tenant['property_id'])[self.charge['id']]['available'])
            self.assertEqual(water_breakdowns(conn, self.tenant['id'], 'other-property'), {})
            self.assertEqual(water_breakdowns(conn, 'other-tenant', self.tenant['property_id']), {})

    def test_fractional_water_bill_keeps_cents_in_total_and_calculation(self):
        with self.connection() as conn:
            conn.execute('UPDATE rent_charges SET amount=650.25 WHERE id=?', (self.charge['id'],))
            conn.execute('UPDATE water_readings SET current_reading=previous_reading+6.5025, units_consumed=6.5025, amount=650.25 '
                         'WHERE unit_id=? AND upload_id IN (SELECT id FROM water_uploads WHERE charge_period=?)',
                         (self.tenant['unit_id'], self.charge['period']))
        html = self.client.get(self.path + '/invoice/' + self.charge['period']).get_data(as_text=True)
        self.assertIn('6.5025 units × KES 100 per unit = KES 650.25', html)
        self.assertIn('class="num">650.25', html)

    def test_coverage_names_recorded_months_and_latest_payment(self):
        from src.utils.tenant_documents import account_coverage, period_label
        with self.connection() as conn:
            coverage = account_coverage(conn, self.tenant['id'])
            latest = conn.execute('SELECT MAX(period) FROM rent_charges WHERE tenant_id=?', (self.tenant['id'],)).fetchone()[0]
        self.assertEqual(coverage['last_period'], period_label(latest))
        self.assertTrue(coverage['last_payment_date'])
        html = self.client.get(self.path).get_data(as_text=True)
        self.assertIn('Bills for ' + coverage['period_range'], html)
        self.assertNotIn('charged across recorded periods', html)
        self.assertIn('Latest confirmed payment:', html)
        with self.connection() as conn:
            self.assertIsNone(account_coverage(conn, 'unknown')['last_period'])
            conn.execute('DELETE FROM payment_allocations WHERE charge_id IN (SELECT id FROM rent_charges WHERE tenant_id=?)', (self.tenant['id'],))
            conn.execute('DELETE FROM rent_charges WHERE tenant_id=?', (self.tenant['id'],))
            conn.execute("INSERT INTO rent_charges (id,property_id,unit_id,tenant_id,period,charge_type,amount) VALUES ('bf',?,?,?,'ARREARS','brought_forward',1200)", (self.tenant['property_id'], self.tenant['unit_id'], self.tenant['id']))
            coverage = account_coverage(conn, self.tenant['id'])
        self.assertIsNone(coverage['last_period'])
        self.assertTrue(coverage['has_brought_forward'])

    def test_notices_stand_out_and_unseen_older_notices_remain_unread(self):
        with self.connection() as conn:
            for i in range(35):
                conn.execute("INSERT INTO messages (id,property_id,tenant_id,subject,body,message_type,created_at) VALUES (?,?,?,'Water shutoff','<script>unsafe</script>','broadcast',?)", (f'notice-{i}',self.tenant['property_id'],self.tenant['id'],f'2026-10-{i%28+1:02d} 12:{i:02d}:00'))
        html = self.client.get(self.path).get_data(as_text=True)
        self.assertLess(html.index('Notices from management'), html.index('Bills &amp; payments'))
        self.assertIn('Read notice', html)
        html = self.client.get(self.path + '/messages').get_data(as_text=True)
        self.assertIn('tenant-notice is-unread', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>unsafe</script>', html)
        with self.connection() as conn:
            remaining = conn.execute('SELECT COUNT(*) FROM messages WHERE tenant_id=? AND read_at IS NULL', (self.tenant['id'],)).fetchone()[0]
        self.assertGreaterEqual(remaining, 5)

    def test_demo_says_sms_unavailable_and_code_still_verifies(self):
        with patch('src.messaging.delivery.send_sms', return_value=(0,1,['SMS disabled (AT_SMS_ENABLED=false)'])) as send:
            response = self.client.post('/tenant/verify', data={'phone':'0700000102'})
        self.assertEqual(response.status_code,302)
        self.assertTrue(send.called)
        code = re.search(r'code is (\d{6})', send.call_args.args[1])[1]
        html = self.client.get('/tenant/verify/code').get_data(as_text=True)
        self.assertIn('SMS delivery is unavailable', html)
        self.assertNotIn('a 6-digit code was sent', html)
        with patch('src.messaging.delivery.send_sms') as resend:
            self.client.post('/tenant/verify', data={'phone':'0700000102'})
        resend.assert_not_called()                 # 60-second throttle preserves the failure status
        self.assertIn('SMS delivery is unavailable', self.client.get('/tenant/verify/code').get_data(as_text=True))
        response = self.client.post('/tenant/verify/code', data={'code':code,'pin':'4321','pin_confirm':'4321'})
        self.assertEqual(response.status_code,302)
        with self.client.session_transaction() as session:
            self.assertTrue(session.get('tenant_person_id'))
            self.assertNotIn('tenant_verify_person', session)

    def test_unknown_phone_cannot_show_stale_demo_code(self):
        with self.client.session_transaction() as session:
            session['demo_sms_code']='123456'
            session['demo_sms_person']='previous-person'
        # A valid-shaped number that cannot be claimed by demo seed.
        with patch('src.demo.phones.claim_first_time'), patch('src.messaging.delivery.send_sms') as send:
            self.client.post('/tenant/verify', data={'phone':'0799888777'})
        self.assertFalse(send.called)
        with self.client.session_transaction() as session:
            self.assertNotIn('demo_sms_code',session)
            self.assertIsNone(session.get('tenant_verify_person'))


if __name__ == '__main__':
    unittest.main()
