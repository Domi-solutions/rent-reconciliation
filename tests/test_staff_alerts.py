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

    def write(self, topic, notify=None):
        token = self.tenant['access_token']
        data = {'topic': topic, 'subject': 'Kitchen tap leaking', 'body': 'Dripping since morning'}
        if notify:
            data['notify'] = notify                       # a tenant cannot choose any more; ignored
        r = self.client.post(f'/tenant/{token}/messages/new', data=data)
        return r.headers['Location'].split('/messages/')[1].split('?')[0]

    def route_repairs_to(self, route, hours=24):
        with self.connection() as conn:
            conn.execute("UPDATE properties SET repair_alerts_to = ?, repair_escalate_hours = ?", (route, hours))

    def reply_as_tenant(self, conv_id):
        self.client.post(f"/tenant/{self.tenant['access_token']}/messages/{conv_id}", data={'body': 'Still dripping'})

    def phones(self):
        return sorted(p for phones, _ in self.sent for p in phones)

    def test_repair_texts_the_caretaker_by_default_whatever_the_tenant_sends(self):
        self.write('repair', notify='both')
        self.assertEqual(self.phones(), ['+254711000002'])
        self.assertIn('tenant in A1 wrote "Kitchen tap leaking"', self.sent[0][1])
        self.assertIn('/caretaker/', self.sent[0][1])

    def test_management_setting_decides_repair_texts(self):
        self.route_repairs_to('both')
        self.write('repair')
        self.assertEqual(self.phones(), ['+254711000001', '+254711000002'])

    def test_money_and_other_text_management_only(self):
        self.route_repairs_to('caretaker')
        self.write('money')
        self.write('other')
        self.assertEqual(self.phones(), ['+254711000001', '+254711000001'])
        self.assertIn('https://domi.test/messages/inbox/', self.sent[0][1])

    def test_form_tells_the_tenant_who_gets_a_text(self):
        page = self.client.get(f"/tenant/{self.tenant['access_token']}/messages/new?topic=repair").get_data(as_text=True)
        self.assertIn('The caretaker gets a text that you wrote.', page)
        self.assertNotIn('name="notify"', page)

    def test_one_text_until_staff_reply(self):
        self.route_repairs_to('both')
        conv_id = self.write('repair')
        self.assertEqual(self.phones(), ['+254711000001', '+254711000002'])
        self.sent.clear()
        self.reply_as_tenant(conv_id)
        self.assertEqual(self.sent, [])                   # already told, nobody has replied yet
        from src.messaging import threads
        with self.connection() as conn:
            threads.add_message(conn, conv_id, 'caretaker', 'Juma', 'Coming at 4pm')
        self.reply_as_tenant(conv_id)
        self.assertEqual(self.phones(), ['+254711000001', '+254711000002'])

    def escalate(self):
        from src.messaging import staff_alerts
        with self.connection() as conn:
            texts = staff_alerts.escalations(conn, 'https://domi.test')
        return texts

    def age(self, conv_id, hours):
        with self.connection() as conn:
            conn.execute("UPDATE conversations SET staff_alerted_at = datetime('now', ?) WHERE id = ?",
                         (f'-{hours} hours', conv_id))

    def test_unanswered_repair_texts_management_after_24_hours_once(self):
        conv_id = self.write('repair')                    # caretaker only
        self.age(conv_id, 23)
        self.assertEqual(self.escalate(), [])             # not yet
        self.age(conv_id, 25)
        texts = self.escalate()
        self.assertEqual([t[0] for t in texts], [['+254711000001']])
        self.assertIn('has had no reply for 24 hours', texts[0][1])
        self.assertEqual(self.escalate(), [])             # once only
        from src.messaging import threads
        with self.connection() as conn:                   # a reply starts it over
            threads.add_message(conn, conv_id, 'caretaker', 'Juma', 'On my way')
            row = conn.execute("SELECT staff_alerted_at, escalated_at FROM conversations WHERE id = ?",
                               (conv_id,)).fetchone()
        self.assertEqual((row[0], row[1]), (None, None))

    def test_no_safety_net_when_off_or_management_already_texted(self):
        self.route_repairs_to('caretaker', hours=0)
        conv_id = self.write('repair')
        self.age(conv_id, 100)
        self.assertEqual(self.escalate(), [])
        from src.messaging import threads
        with self.connection() as conn:                   # answered, so out of the way
            threads.add_message(conn, conv_id, 'caretaker', 'Juma', 'Fixed')
        self.route_repairs_to('both', hours=24)
        conv_id = self.write('repair')
        self.age(conv_id, 100)
        self.assertEqual(self.escalate(), [])


if __name__ == '__main__':
    unittest.main()
