"""Tenant welcome page (once, first visit) and giving notice to move out."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MoveOutAndWelcome(unittest.TestCase):
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
        self.sent = []
        patch('src.messaging.delivery.send_sms_async', side_effect=lambda p, t: self.sent.append((p, t))).start()
        from app import app
        from src.database.db import get_connection
        self.app, self.connection = app, get_connection
        self.client = app.test_client()
        with get_connection() as conn:
            self.tenant = dict(conn.execute("SELECT t.* FROM tenants t JOIN units u ON u.id=t.unit_id "
                                            "WHERE u.unit_number='A1' AND t.status='active'").fetchone())
            conn.execute("UPDATE admin_users SET phone = '+254711000001'")
        self.token = self.tenant['access_token']
        with self.client.session_transaction() as session:
            session['tenant_person_id'] = self.tenant['person_id']

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def notice(self):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM move_out_notices WHERE tenant_id = ? ORDER BY rowid DESC",
                               (self.tenant['id'],)).fetchone()
        return dict(row) if row else None

    def give(self, days=35, phone='0700000101', reason='Work or school'):
        planned = (date.today() + timedelta(days=days)).isoformat()
        return self.client.post(f'/tenant/{self.token}/move-out',
                                data={'planned_date': planned, 'reason': reason, 'refund_phone': phone})

    # ── welcome ──────────────────────────────────────────────────────────────
    def test_first_visit_shows_the_welcome_once(self):
        with self.connection() as conn:
            conn.execute("UPDATE tenants SET welcomed_at = NULL WHERE id = ?", (self.tenant['id'],))
        r = self.client.get(f'/tenant/{self.token}')
        self.assertIn('/welcome', r.headers['Location'])
        page = self.client.get(f'/tenant/{self.token}/welcome').get_data(as_text=True)
        self.assertIn('Pay with M-Pesa', page)
        self.assertEqual(self.client.get(f'/tenant/{self.token}').status_code, 200)   # not again
        sw = self.client.get(f'/tenant/{self.token}/welcome?lang=sw').get_data(as_text=True)
        self.assertIn('Lipa kwa M-Pesa', sw)

    # ── move-out ─────────────────────────────────────────────────────────────
    def test_notice_opens_a_conversation_and_texts_management(self):
        r = self.give()
        self.assertIn('given=1', r.headers['Location'])
        n = self.notice()
        self.assertEqual((n['status'], n['notice_days'], n['refund_phone']), ('given', 35, '+254700000101'))
        with self.connection() as conn:
            conv = conn.execute("SELECT topic, subject FROM conversations WHERE id = ?", (n['conversation_id'],)).fetchone()
        self.assertEqual(conv['topic'], 'other')
        self.assertTrue(conv['subject'].startswith('Notice to move out'))
        self.assertEqual([p for p, _ in self.sent], [['+254711000001']])            # management only
        page = self.client.get(f'/tenant/{self.token}/move-out').get_data(as_text=True)
        self.assertIn('Waiting for management to see it', page)

    def test_short_notice_is_recorded_not_refused(self):
        self.give(days=10)
        self.assertEqual(self.notice()['notice_days'], 10)
        with self.connection() as conn:
            body = conn.execute("SELECT body FROM conversation_messages WHERE conversation_id = ?",
                                (self.notice()['conversation_id'],)).fetchone()[0]
        self.assertIn('less than the notice my lease asks for', body)

    def test_bad_input_is_explained(self):
        page = self.give(days=0).get_data(as_text=True)
        self.assertIn('Choose a date after today', page)
        page = self.give(phone='07123').get_data(as_text=True)
        self.assertIn('not a full phone number', page)
        self.assertIsNone(self.notice())

    def test_one_open_notice_and_it_can_be_withdrawn(self):
        self.give()
        self.give(days=40)                                   # a second one while the first is open
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM move_out_notices WHERE tenant_id = ?",
                                          (self.tenant['id'],)).fetchone()[0], 1)
        self.client.post(f'/tenant/{self.token}/move-out/withdraw')
        self.assertEqual(self.notice()['status'], 'withdrawn')
        self.give(days=40)
        self.assertEqual(self.notice()['status'], 'given')

    def test_management_sees_it_then_records_the_move_out(self):
        self.give()
        n = self.notice()
        admin = self.app.test_client()
        admin.post('/login', data={'email': 'demo@demo', 'password': 'demo', 'next': ''})
        admin.get('/properties')
        page = admin.get('/leases/moving-out').get_data(as_text=True)
        self.assertIn('Moving out', page)
        self.assertIn(n['planned_date'], page)
        admin.post('/leases/moving-out', data={'notice_id': n['id']})
        self.assertEqual(self.notice()['status'], 'acknowledged')
        form = admin.get(f"/units/{self.tenant['unit_id']}/move-out").get_data(as_text=True)
        self.assertIn('The tenant gave notice', form)
        self.assertIn(f'value="{n["planned_date"]}"', form)
        admin.post(f"/units/{self.tenant['unit_id']}/move-out",
                   data={'departure_date': n['planned_date'], 'deposit_held': '18000', 'resolution': 'active'})
        self.assertEqual(self.notice()['status'], 'completed')

    # ── settings ─────────────────────────────────────────────────────────────
    def test_menu_has_moving_out_and_settings(self):
        page = self.client.get(f'/tenant/{self.token}/statement').get_data(as_text=True)
        self.assertIn('>Moving out</a>', page)
        self.assertIn('>Settings</a>', page)

    def test_change_pin_needs_the_current_one(self):
        url = f'/tenant/{self.token}/settings'
        page = self.client.post(url, data={'action': 'pin', 'current_pin': '9999', 'new_pin': '4321',
                                           'new_pin_again': '4321'}).get_data(as_text=True)
        self.assertIn('current PIN is not correct', page)
        page = self.client.post(url, data={'action': 'pin', 'current_pin': '1234', 'new_pin': '4321',
                                           'new_pin_again': '4321'}).get_data(as_text=True)
        self.assertIn('Your PIN is changed', page)
        from src.utils.tenant_auth import check_pin
        with self.connection() as conn:
            self.assertEqual(check_pin(conn, self.tenant['person_id'], '4321')[0], 'ok')

    def test_language_is_saved(self):
        self.client.post(f'/tenant/{self.token}/settings', data={'action': 'language', 'lang': 'sw'})
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT language_preference FROM tenants WHERE id = ?",
                                          (self.tenant['id'],)).fetchone()[0], 'sw')

    # ── management welcome + help on every page ──────────────────────────────
    def test_management_sees_how_domi_works_once(self):
        admin = self.app.test_client()
        admin.post('/login', data={'email': 'demo@demo', 'password': 'demo', 'next': ''})
        admin.get('/properties')
        r = admin.get('/')
        self.assertIn('/start', r.headers.get('Location', ''))
        page = admin.get('/start').get_data(as_text=True)
        self.assertIn('Words you will see', page)
        self.assertIn('Your month, step by step', page)
        self.assertEqual(admin.get('/').status_code, 200)                 # not again

    def test_every_management_menu_page_has_help(self):
        from src.utils.guides import _T
        for endpoint in ('dashboard', 'manage_units', 'admin_arrears', 'leases.board', 'review', 'flagged_claims_admin',
                         'messaging.inbox', 'money.expenses', 'messaging.caretaker_chats', 'messaging.dashboard',
                         'manage_statements', 'water_uploads_list', 'reports.report_history', 'reports.arrears_report',
                         'reports.payment_lookup', 'activity', 'tools_index', 'team.team', 'manage_caretakers',
                         'manage_owners', 'org_settings', 'generate_charges', 'upload_statement', 'tenant_statement'):
            self.assertIn(endpoint, _T, endpoint)


if __name__ == '__main__':
    unittest.main()
