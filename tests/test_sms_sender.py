"""Per-property SMS sender: a property with its own registered name sends under it and its
texts drop the leading building name; a shared name keeps it; everything else goes under the
default sender unchanged. Only the platform sets it."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SmsSender(unittest.TestCase):
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
            'ENVIRONMENT': 'development', 'FLASK_RUN_FROM_CLI': 'true', 'AT_SMS_ENABLED': 'true',
            'SMS_PROVIDER': 'textsms', 'TEXTSMS_API_KEY': 'k', 'TEXTSMS_PARTNER_ID': 'p', 'SMS_SENDER_ID': '',
        })
        self.env.start()
        from app import app
        from src.database.db import get_connection
        from src.messaging import delivery
        self.app, self.connection, self.delivery = app, get_connection, delivery
        with get_connection() as conn:
            self.prop = dict(conn.execute("SELECT id, name FROM properties LIMIT 1").fetchone())

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def set_sender(self, sender, property_id=None):
        with self.connection() as conn:
            conn.execute("UPDATE properties SET sms_sender_id = ? WHERE id = ?", (sender, property_id or self.prop['id']))

    def text(self, rest):
        return f"{self.prop['name'].title()}: {rest}"

    def test_default_sender_keeps_the_building_name(self):
        self.assertEqual(self.delivery.sender_for(self.text('payment of KES 100 confirmed.')),
                         (None, self.text('payment of KES 100 confirmed.'), None))

    def test_own_sender_drops_the_building_name(self):
        self.set_sender('MOWIN')
        sender, message, pid = self.delivery.sender_for(self.text('payment of KES 100 confirmed.'))
        self.assertEqual((sender, message, pid), ('MOWIN', 'Payment of KES 100 confirmed.', self.prop['id']))

    def test_shared_sender_keeps_the_building_name(self):
        self.set_sender('AGENCY')
        with self.connection() as conn:
            other = conn.execute("""INSERT INTO properties (id, name, sms_sender_id) VALUES ('PROP-OTHER', 'Second Block', 'AGENCY')""")
        sender, message, _ = self.delivery.sender_for(self.text('water off Saturday.'))
        self.assertEqual((sender, message), ('AGENCY', self.text('water off Saturday.')))

    def test_explicit_property_without_a_leading_name(self):
        self.set_sender('MOWIN')
        self.assertEqual(self.delivery.sender_for('Your code is 123456.', self.prop['id'])[0], 'MOWIN')
        self.assertIsNone(self.delivery.sender_for('Your code is 123456.')[0])     # unknown → default

    def test_send_uses_the_sender_and_logs_it(self):
        self.set_sender('MOWIN')
        with patch.object(self.delivery, '_send_textsms', return_value=[('+254712345678', True, None)]) as send:
            self.delivery.send_sms(['0712345678'], self.text('payment confirmed.'))
        numbers, message, sender = send.call_args[0]
        self.assertEqual((message, sender), ('Payment confirmed.', 'MOWIN'))
        with self.connection() as conn:
            row = conn.execute("SELECT subject, property_id FROM platform_outbox ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual((row['subject'], row['property_id']), ('From MOWIN', self.prop['id']))

    def test_without_a_registered_name_textsms_default_is_used(self):
        with patch.object(self.delivery, '_send_textsms', return_value=[('+254712345678', True, None)]) as send:
            self.delivery.send_sms(['0712345678'], self.text('payment confirmed.'))
        self.assertIsNone(send.call_args[0][2])               # → TextSMS's own sender

    def test_platform_sets_it_and_rejects_bad_names(self):
        client = self.app.test_client()
        self.assertEqual(client.get('/platform/sms-senders').status_code, 302)      # platform sign-in only
        with client.session_transaction() as s:
            s['platform_admin'] = True
        client.post('/platform/sms-senders', data={'property_id': self.prop['id'], 'sms_sender_id': 'WAY-TOO-LONG-NAME'})
        client.post('/platform/sms-senders', data={'property_id': self.prop['id'], 'sms_sender_id': 'MOWIN'})
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT sms_sender_id FROM properties WHERE id = ?",
                                          (self.prop['id'],)).fetchone()[0], 'MOWIN')
            self.assertTrue(conn.execute("SELECT 1 FROM audit_log WHERE action = 'sms_sender_changed'").fetchone())
        self.assertIn('MOWIN', client.get('/platform/sms-senders').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
