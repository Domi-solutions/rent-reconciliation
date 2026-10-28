"""Texting staff when a tenant writes: who gets it, and only once until someone replies."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StaffAlerts(unittest.TestCase):
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
            'APP_BASE_URL': 'https://domi.test',
        })
        self.env.start()
        from app import app
        from src.database.db import get_connection
        self.connection = get_connection
        self.client = app.test_client()
        with get_connection() as conn:
            self.tenant = dict(conn.execute("SELECT t.* FROM tenants t JOIN units u ON u.id=t.unit_id "
                                            "WHERE u.unit_number='A1' AND t.status='active'").fetchone())
            conn.execute("UPDATE admin_users SET phone = '+254711000001'")
            conn.execute("UPDATE caretakers SET phone = '+254711000002'")
        with self.client.session_transaction() as session:
            session['tenant_person_id'] = self.tenant['person_id']
        self.sent = []
        patch('src.messaging.delivery.send_sms_async',
              side_effect=lambda phones, text: self.sent.append((phones, text))).start()

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def write(self, topic, notify):
        token = self.tenant['access_token']
        r = self.client.post(f'/tenant/{token}/messages/new', data={
            'topic': topic, 'subject': 'Kitchen tap leaking', 'body': 'Dripping since morning', 'notify': notify})
        return r.headers['Location'].split('/messages/')[1].split('?')[0]

    def reply_as_tenant(self, conv_id):
        self.client.post(f"/tenant/{self.tenant['access_token']}/messages/{conv_id}", data={'body': 'Still dripping'})

    def phones(self):
        return sorted(p for phones, _ in self.sent for p in phones)

    def test_repair_texts_who_the_tenant_chose(self):
        self.write('repair', 'caretaker')
        self.assertEqual(self.phones(), ['+254711000002'])
        self.assertIn('tenant in A1 wrote "Kitchen tap leaking"', self.sent[0][1])
        self.assertIn('/caretaker/', self.sent[0][1])

    def test_money_always_texts_management_only(self):
        self.write('money', 'caretaker')                  # a forged choice is ignored
        self.assertEqual(self.phones(), ['+254711000001'])
        self.assertIn('https://domi.test/messages/inbox/', self.sent[0][1])

    def test_one_text_until_staff_reply(self):
        conv_id = self.write('repair', 'both')
        self.assertEqual(self.phones(), ['+254711000001', '+254711000002'])
        self.sent.clear()
        self.reply_as_tenant(conv_id)
        self.assertEqual(self.sent, [])                   # already told, nobody has replied yet
        from src.messaging import threads
        with self.connection() as conn:
            threads.add_message(conn, conv_id, 'caretaker', 'Juma', 'Coming at 4pm')
        self.reply_as_tenant(conv_id)
        self.assertEqual(self.phones(), ['+254711000001', '+254711000002'])


if __name__ == '__main__':
    unittest.main()
