"""Messaging one tenant: write, tap Send — no subject, no channel to pick. The message is kept in
the portal; the tenant is texted a link to sign in and read it (before sign-in links are switched
on, the text carries the message). The SMS goes after the save, and the page says what happened."""

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

    def test_before_links_are_on_the_sms_carries_the_message(self):
        page = self.admin.post(self.url, data={'body': 'Your balance is KES {balance}.'},
                               follow_redirects=True).get_data(as_text=True)
        row = self.saved()
        self.assertEqual(row['delivery_channel'], 'sms')
        self.assertNotIn('{balance}', row['body'])
        (recipients, text), _ = self.sms.call_args
        self.assertEqual(recipients[0]['phone'], self.tenant['phone'])
        self.assertTrue(text.startswith(self.tenant['property_name'].title() + ': Your balance is KES '))
        self.assertIn('got an SMS with the message', page)

    def test_with_links_on_the_sms_is_a_sign_in_link_not_the_message(self):
        with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': 'true'}):
            page = self.admin.post(self.url, data={'subject': 'Your deposit', 'body': 'Private matter about your deposit.'},
                                   follow_redirects=True).get_data(as_text=True)
        (_, text), _ = self.sms.call_args
        self.assertIn(f"/t/{self.tenant['short_code']}", text)
        self.assertIn('new message from management', text)
        self.assertIn('"Your deposit"', text)                       # the title, never the message
        self.assertNotIn('Private matter', text)
        self.assertIn('with a link to read it', page)
        self.assertIn('Private matter about your deposit.', self.saved()['body'])

    def test_title_is_kept_and_texted_before_links_are_on(self):
        self.admin.post(self.url, data={'subject': 'Water off on Saturday', 'body': 'From 9am to 1pm.'})
        self.assertEqual(self.saved()['subject'], 'Water off on Saturday')
        (_, text), _ = self.sms.call_args
        self.assertEqual(text, f"{self.tenant['property_name'].title()}: Water off on Saturday\nFrom 9am to 1pm.")

    def test_broadcast_is_texted_in_full_with_the_portal_link(self):
        with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': 'true'}):
            self.admin.post('/messages/broadcast', data={'tenant_ids': [self.tenant['id']],
                                                         'subject': 'Water off on Saturday',
                                                         'body': 'From 9am to 1pm.'})
        (_, text), _ = self.sms.call_args
        self.assertIn('Water off on Saturday\nFrom 9am to 1pm.', text)
        self.assertTrue(text.endswith(f"/t/{self.tenant['short_code']}"))
        self.assertEqual(self.saved()['delivery_channel'], 'sms')

    def test_broadcast_before_links_are_on_has_no_link(self):
        self.admin.post('/messages/broadcast', data={'tenant_ids': [self.tenant['id']],
                                                     'subject': 'Notice', 'body': 'Hello.'})
        (_, text), _ = self.sms.call_args
        self.assertNotIn('/t/', text)

    def test_no_phone_posts_in_portal_only(self):
        with self.connection() as conn:
            conn.execute("UPDATE tenants SET phone = NULL WHERE id = ?", (self.tenant['id'],))
        self.admin.post(self.url, data={'body': 'See you Monday.'})
        self.assertEqual(self.saved()['delivery_channel'], 'portal')
        self.sms.assert_not_called()

    def test_empty_message_is_refused(self):
        before = self.saved()
        self.admin.post(self.url, data={'body': '  '})
        self.assertEqual(self.saved(), before)
        self.sms.assert_not_called()

    def test_switched_off_is_said(self):
        self.sms.return_value = (0, 1, ['SMS disabled (AT_SMS_ENABLED=false)'])
        page = self.admin.post(self.url, data={'body': 'Hello'},
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn('switched off', page)

    def test_quick_messages_show_real_figures(self):
        page = self.admin.get(self.url).get_data(as_text=True)
        self.assertIn('Rent reminder', page)
        self.assertNotIn('data-body="Hi {tenant_name}', page)
        self.assertNotIn('Custom broadcast', page)


if __name__ == '__main__':
    unittest.main()
