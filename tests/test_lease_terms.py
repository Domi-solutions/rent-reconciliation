"""Lease terms: fixed terms ending, rent reviews billed from the right month, the tenant's lease page."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def month_after(day, n=1):
    y, m = divmod(day.month - 1 + n, 12)
    return f'{day.year + y:04d}-{m + 1:02d}'


class LeaseTerms(unittest.TestCase):
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
        patch('src.messaging.delivery.send_sms_async').start()
        from app import app
        from src.database.db import get_connection
        self.app, self.connection = app, get_connection
        self.admin = app.test_client()
        self.admin.post('/login', data={'email': 'demo@demo', 'password': 'demo', 'next': ''})
        self.admin.get('/properties')
        with get_connection() as conn:
            self.lease = dict(conn.execute("""SELECT l.*, u.unit_number FROM leases l JOIN units u ON u.id = l.unit_id
                                              JOIN tenants t ON t.id = l.tenant_id
                                              WHERE u.unit_number = 'A1' AND t.status = 'active'""").fetchone())
            self.tenant = dict(conn.execute("SELECT * FROM tenants WHERE id = ?", (self.lease['tenant_id'],)).fetchone())
        self.tenant_client = app.test_client()
        with self.tenant_client.session_transaction() as s:
            s['tenant_person_id'] = self.tenant['person_id']

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def set_terms(self, **form):
        return self.admin.post(f"/leases/{self.lease['id']}/terms", data=form)

    def notices(self):
        with self.connection() as conn:
            return [r[0] for r in conn.execute("SELECT subject FROM messages WHERE tenant_id = ? AND message_type = 'lease'",
                                               (self.tenant['id'],))]

    def test_fixed_term_ending_soon_then_renewed(self):
        end = (date.today() + timedelta(days=20)).isoformat()
        self.set_terms(term_type='fixed', end_date=end, notice_days='30', early_exit_terms='Rent due to the end of notice.')
        page = self.admin.get('/leases/renewals').get_data(as_text=True)
        self.assertIn(end, page)
        new_end = (date.today() + timedelta(days=385)).isoformat()
        self.admin.post('/leases/renewals', data={'lease_id': self.lease['id'], 'action': 'decide',
                                                  'decision': 'renew', 'new_end': new_end})
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT end_date FROM leases WHERE id = ?", (self.lease['id'],)).fetchone()[0], new_end)
        self.assertIn('About your lease', self.notices())
        self.assertNotIn(end, self.admin.get('/leases/renewals').get_data(as_text=True))

    def test_switch_to_month_to_month(self):
        self.set_terms(term_type='fixed', end_date=(date.today() + timedelta(days=10)).isoformat())
        self.admin.post('/leases/renewals', data={'lease_id': self.lease['id'], 'action': 'decide', 'decision': 'monthly'})
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT term_type FROM leases WHERE id = ?", (self.lease['id'],)).fetchone()[0],
                             'periodic')

    def test_end_before_start_is_refused(self):
        self.set_terms(term_type='fixed', end_date='2000-01-01')
        with self.connection() as conn:
            self.assertNotEqual(conn.execute("SELECT end_date FROM leases WHERE id = ?", (self.lease['id'],)).fetchone()[0],
                                '2000-01-01')

    def test_rent_review_is_billed_from_the_chosen_month(self):
        with self.connection() as conn:     # started 11 months and 20 days ago, so the review is in ~10 days
            start = (date.today() - timedelta(days=355)).isoformat()
            conn.execute("UPDATE leases SET start_date = ? WHERE id = ?", (start, self.lease['id']))
        self.set_terms(term_type='periodic', notice_days='30', rent_increase_pct='10', rent_increase_every_months='12')
        page = self.admin.get('/leases/renewals').get_data(as_text=True)
        self.assertIn('Rent reviews due', page)
        self.assertIn('19800', page)                        # 18,000 + 10%
        period = month_after(date.today(), 2)
        self.admin.post('/leases/renewals', data={'lease_id': self.lease['id'], 'action': 'rent',
                                                  'new_rent': '19800', 'effective_period': period})
        self.assertTrue(any(s.startswith('Your rent from') for s in self.notices()))   # told now
        with self.connection() as conn:
            self.assertEqual(conn.execute("SELECT monthly_rent FROM units WHERE id = ?", (self.lease['unit_id'],)).fetchone()[0],
                             18000)                                                 # not yet billed
        self.admin.post('/charges/generate', data={'period': month_after(date.today(), 1)})
        self.admin.post('/charges/generate', data={'period': period})
        with self.connection() as conn:
            amounts = dict(conn.execute("""SELECT period, amount FROM rent_charges WHERE unit_id = ? AND charge_type = 'rent'
                                           AND period IN (?, ?)""", (self.lease['unit_id'], month_after(date.today(), 1), period)).fetchall())
        self.assertEqual(amounts, {month_after(date.today(), 1): 18000, period: 19800})

    def test_tenant_sees_terms_and_only_shared_documents(self):
        end = (date.today() + timedelta(days=200)).isoformat()
        self.set_terms(term_type='fixed', end_date=end, early_exit_terms='Two months rent if leaving early.')
        with self.connection() as conn:
            for doc_id, kind, visible in (('DOC-LEASE', 'lease', 1), ('DOC-ID', 'id_copy', 1), ('DOC-HIDDEN', 'lease', 0)):
                conn.execute("""INSERT INTO lease_documents (id, lease_id, kind, file_name, content_type, tenant_visible)
                                VALUES (?, ?, ?, 'x.pdf', 'application/pdf', ?)""", (doc_id, self.lease['id'], kind, visible))
        page = self.tenant_client.get(f"/tenant/{self.tenant['access_token']}/lease").get_data(as_text=True)
        self.assertIn('Fixed term', page)
        self.assertIn(end, page)
        self.assertIn('Two months rent if leaving early.', page)
        self.assertIn('DOC-LEASE', page)
        self.assertNotIn('DOC-ID', page)
        self.assertNotIn('DOC-HIDDEN', page)
        for doc_id in ('DOC-ID', 'DOC-HIDDEN'):
            self.assertEqual(self.tenant_client.get(f"/tenant/{self.tenant['access_token']}/lease/document/{doc_id}").status_code, 404)
        move = self.tenant_client.get(f"/tenant/{self.tenant['access_token']}/move-out").get_data(as_text=True)
        self.assertIn('fixed until', move)
        self.assertIn('Two months rent if leaving early.', move)


if __name__ == '__main__':
    unittest.main()
