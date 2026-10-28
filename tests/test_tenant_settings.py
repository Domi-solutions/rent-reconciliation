"""Tenant Settings: contacts, payer numbers, changing the phone number, sign out everywhere."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TenantSettings(unittest.TestCase):
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
            'DATABASE_PATH': self.db, 'DEMO_MODE': 'false', 'FLASK_ENV': 'development',
            'ENVIRONMENT': 'development', 'FLASK_RUN_FROM_CLI': 'true', 'AT_SMS_ENABLED': 'false',
            'APP_BASE_URL': 'https://domi.test',
        })
        self.env.start()
        self.sent = []
        record = lambda phones, text, *a, **k: (self.sent.append((list(phones), text)), (True, [], []))[1]
        patch('src.messaging.delivery.send_sms', side_effect=record).start()
        patch('src.messaging.delivery.send_sms_async', side_effect=record).start()
        from app import app
        from src.database.db import get_connection
        self.app, self.connection = app, get_connection
        self.client = app.test_client()
        with get_connection() as conn:
            self.tenant = dict(conn.execute("SELECT t.* FROM tenants t JOIN units u ON u.id=t.unit_id "
                                            "WHERE u.unit_number='A1' AND t.status='active'").fetchone())
        self.token = self.tenant['access_token']
        self.url = f'/tenant/{self.token}/settings'
        self.sign_in(self.client)

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def sign_in(self, client):
        client.post('/tenant/login', data={'phone': self.tenant['phone'], 'pin': '1234'})

    def post(self, **data):
        return self.client.post(self.url, data=data).get_data(as_text=True)

    # ── contacts ─────────────────────────────────────────────────────────────
    def test_payer_number_makes_a_payment_theirs(self):
        self.assertIn('Saved', self.post(action='add_contact', kind='payer', name='Wife', phone='0722 111 222'))
        from src.reconciliation.claim_owner import sender_is_tenant
        line = {'sender_name': 'MARY WANJIRU', 'raw_text': 'TKX PAYBILL 254722111222 MARY WANJIRU'}
        stranger = {'sender_name': 'JOHN DOE', 'raw_text': 'TKX PAYBILL 254733000000 JOHN DOE'}
        with self.connection() as conn:
            self.assertTrue(sender_is_tenant(conn, line, self.tenant))
            self.assertFalse(sender_is_tenant(conn, stranger, self.tenant))

    def test_emergency_contact_needs_a_name_and_limits_apply(self):
        self.assertIn('Add the name', self.post(action='add_contact', kind='emergency', phone='0722111222'))
        self.post(action='add_contact', kind='second_phone', phone='0722111222')
        self.assertIn('up to 1', self.post(action='add_contact', kind='second_phone', phone='0722111333'))

    # ── phone number change ──────────────────────────────────────────────────
    def test_phone_change_needs_the_code_and_management(self):
        self.post(action='phone_request', new_phone='0799 000 111')
        code = re.search(r'is (\d{6})', self.sent[-1][1]).group(1)
        self.assertEqual(self.sent[-1][0], ['+254799000111'])            # the code goes to the NEW number
        self.assertIn('not correct', self.post(action='phone_confirm', code='000000'))
        self.assertIn('Sent to management', self.post(action='phone_confirm', code=code))
        with self.connection() as conn:
            req = dict(conn.execute("SELECT * FROM phone_change_requests WHERE tenant_id = ?",
                                    (self.tenant['id'],)).fetchone())
            self.assertEqual(conn.execute("SELECT phone FROM tenants WHERE id = ?",
                                          (self.tenant['id'],)).fetchone()[0], self.tenant['phone'])   # not yet
        admin = self.app.test_client()
        admin.post('/login', data={'email': 'demo@demo', 'password': 'demo', 'next': ''})
        admin.get('/properties')
        self.assertIn('Approve the new number', admin.get(f"/messages/inbox/{req['conversation_id']}").get_data(as_text=True))
        self.sent.clear()
        admin.post(f"/messages/inbox/{req['conversation_id']}/phone-change", data={'decision': 'approve'})
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT phone FROM tenants WHERE id = ?", (self.tenant['id'],)).fetchone()[0],
                             '+254799000111')
            from src.utils.tenant_auth import tenancies_for_phone
            self.assertEqual([t['id'] for t in tenancies_for_phone(conn, '0799000111')], [self.tenant['id']])
        told = {tuple(p): t for p, t in self.sent}
        self.assertIn(('+254799000111',), told)
        self.assertIn('call management', told[(req['old_phone'],)])                 # the old number is told

    def test_number_on_another_tenancy_is_refused(self):
        with self.connection() as conn:
            other = conn.execute("SELECT phone FROM tenants t JOIN units u ON u.id=t.unit_id "
                                 "WHERE u.unit_number='A2'").fetchone()[0]
        self.assertIn('on another tenancy', self.post(action='phone_request', new_phone=other))

    # ── sign out everywhere ──────────────────────────────────────────────────
    def test_sign_out_everywhere_signs_out_other_phones(self):
        other_phone = self.app.test_client()
        self.sign_in(other_phone)
        self.assertEqual(other_phone.get(f'/tenant/{self.token}/statement').status_code, 200)
        self.post(action='signout_all')
        r = other_phone.get(f'/tenant/{self.token}/statement')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/tenant/login', r.headers['Location'])
        self.sign_in(other_phone)                                                     # signing in again works
        self.assertEqual(other_phone.get(f'/tenant/{self.token}/statement').status_code, 200)


if __name__ == '__main__':
    unittest.main()
