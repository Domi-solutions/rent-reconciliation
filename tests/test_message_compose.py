"""Messaging one tenant: write, tap "Send SMS" — no subject, no channel to pick. The message is
kept in the portal first; the SMS goes after, and the page says what happened."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MessageCompose(unittest.TestCase):
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
        self.sms = patch('src.messaging.delivery.send_sms', return_value=(1, 0, [])).start()
        from app import app
        from src.database.db import get_connection
        self.connection = get_connection
        self.admin = app.test_client()
        self.admin.post('/login', data={'email': 'demo@demo', 'password': 'demo', 'next': ''})
        self.admin.get('/properties')
        with get_connection() as conn:
            self.tenant = dict(conn.execute("""SELECT t.*, p.name AS property_name FROM tenants t
                                               JOIN properties p ON p.id = t.property_id
                                               WHERE t.status = 'active' AND t.phone IS NOT NULL LIMIT 1""").fetchone())
        self.url = f"/messages/thread/{self.tenant['id']}"

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def saved(self):
        with self.connection() as conn:
            return conn.execute("""SELECT subject, body, delivery_channel FROM messages WHERE tenant_id = ?
                                   ORDER BY rowid DESC LIMIT 1""", (self.tenant['id'],)).fetchone()

    def test_one_tap_sms_without_subject(self):
        page = self.admin.post(self.url, data={'body': 'Your balance is KES {balance}.', 'send': 'sms'},
                               follow_redirects=True).get_data(as_text=True)
        row = self.saved()
        self.assertEqual(row['delivery_channel'], 'sms')
        self.assertNotIn('{balance}', row['body'])
        (recipients, text), _ = self.sms.call_args
        self.assertEqual(recipients[0]['phone'], self.tenant['phone'])
        self.assertTrue(text.startswith(self.tenant['property_name'].title() + ': Your balance is KES '))
        self.assertIn('SMS sent to', page)

    def test_portal_only_sends_no_sms(self):
        self.admin.post(self.url, data={'body': 'See you Monday.', 'send': 'portal'})
        self.assertEqual(self.saved()['delivery_channel'], 'portal')
        self.sms.assert_not_called()

    def test_empty_message_is_refused(self):
        before = self.saved()
        self.admin.post(self.url, data={'body': '  ', 'send': 'sms'})
        self.assertEqual(self.saved(), before)
        self.sms.assert_not_called()

    def test_switched_off_is_said(self):
        self.sms.return_value = (0, 1, ['SMS disabled (AT_SMS_ENABLED=false)'])
        page = self.admin.post(self.url, data={'body': 'Hello', 'send': 'sms'},
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn('switched off', page)

    def test_quick_messages_show_real_figures(self):
        page = self.admin.get(self.url).get_data(as_text=True)
        self.assertIn('Rent reminder', page)
        self.assertNotIn('data-body="Hi {tenant_name}', page)
        self.assertNotIn('Custom broadcast', page)


if __name__ == '__main__':
    unittest.main()
