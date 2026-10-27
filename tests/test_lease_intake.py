"""Lease intake: a new lease opens a clean account; old debt moves only with evidence."""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Upload(io.BytesIO):
    def __init__(self, data, filename):
        super().__init__(data)
        self.filename = filename


class LeaseIntake(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A database built exactly as the app builds it: schema + every migration.
        cls.template_dir = tempfile.mkdtemp()
        cls.template_db = os.path.join(cls.template_dir, 'template.db')
        env = dict(os.environ, DATABASE_PATH=cls.template_db, FLASK_ENV='development',
                   PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([sys.executable, '-c', 'import app'], cwd=ROOT, env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.template_dir, ignore_errors=True)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, 'test.db')
        shutil.copy(self.template_db, self.db)
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db, 'AT_SMS_ENABLED': 'false'})
        self.env.start()
        from src.database.db import get_connection
        self.get_connection = get_connection
        with get_connection() as conn:
            conn.execute("INSERT INTO organizations (id, name) VALUES ('ORG1', 'Agency')")
            conn.execute("INSERT INTO properties (id, name, organization_id, rent_due_day) "
                         "VALUES ('P1', 'Mowin', 'ORG1', 5)")
            for uid, num, status in (('U1', '6A', 'occupied'), ('U2', '6B', 'vacant')):
                conn.execute("INSERT INTO units (id, property_id, unit_number, monthly_rent, service_charge, status) "
                             "VALUES (?, 'P1', ?, 15000, 1000, ?)", (uid, num, status))
            conn.execute("""INSERT INTO tenants (id, property_id, unit_id, name, phone, status, access_token,
                                                 short_code, deposit_paid)
                            VALUES ('T-OLD', 'P1', 'U1', 'Jane Wanjiru', '+254700000001', 'active', 'tok-old',
                                    'ABC123', 15000)""")
            # Old records: spreadsheet arrears + April charge, one bank-matched payment.
            conn.execute("INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount) "
                         "VALUES ('C0', 'P1', 'U1', 'ARREARS', 'rent', 20000)")
            conn.execute("INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount) "
                         "VALUES ('C1', 'P1', 'U1', '2026-04', 'rent', 15000)")
            conn.execute("INSERT INTO payments (id, property_id, unit_id, amount, payment_date, assignment_type) "
                         "VALUES ('PAY1', 'P1', 'U1', 10000, '2026-04-03', 'manual')")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _prop(self, conn):
        return conn.execute("SELECT * FROM properties WHERE id = 'P1'").fetchone()

    def _form(self, **kw):
        form = {'unit_id': 'U1', 'who': 'same', 'name': 'Jane Wanjiru', 'phone': '0700000001',
                'national_id': '12345678', 'start_date': '2026-11-01', 'monthly_rent': '16000',
                'service_charge': '1000', 'deposit_required': '16000', 'deposit_held': '15000',
                'brought_forward': '0'}
        form.update(kw)
        return form

    def _balance(self, conn, tenant_id):
        return conn.execute("SELECT balance FROM tenant_balances WHERE tenant_id = ?", (tenant_id,)).fetchone()[0]

    def test_relase_freezes_old_record_and_starts_clean(self):
        from src.leases import intake
        with self.get_connection() as conn:
            self.assertEqual(self._balance(conn, 'T-OLD'), 25000)
            lease_id, new_id = intake.record_lease(conn, self._prop(conn), self._form(), 'admin:test')
            old = conn.execute("SELECT * FROM tenants WHERE id = 'T-OLD'").fetchone()
            new = conn.execute("SELECT * FROM tenants WHERE id = ?", (new_id,)).fetchone()
            self.assertEqual(old['status'], 'inactive')
            self.assertEqual(old['move_out_date'], '2026-10-31')
            self.assertIsNone(old['access_token'])
            self.assertEqual((new['status'], new['move_in_date']), ('active', '2026-11-01'))
            self.assertEqual((new['access_token'], new['short_code']), ('tok-old', 'ABC123'))  # links keep working
            self.assertEqual(self._balance(conn, new_id), 0)
            self.assertEqual(self._balance(conn, 'T-OLD'), 25000)                           # history untouched
            lease = conn.execute("SELECT * FROM leases WHERE id = ?", (lease_id,)).fetchone()
            self.assertEqual((lease['status'], lease['prior_balance'], lease['prior_tenant_id']),
                             ('pending', 25000, 'T-OLD'))
            unit = conn.execute("SELECT monthly_rent FROM units WHERE id = 'U1'").fetchone()
            self.assertEqual(unit['monthly_rent'], 16000)
            self.assertTrue(conn.execute("SELECT 1 FROM audit_log WHERE action = 'lease_recorded'").fetchone())

            # A payment from before the lease, matched later, stays on the old record;
            # one after it goes to the new account.
            conn.execute("INSERT INTO payments (id, property_id, unit_id, amount, payment_date, assignment_type) "
                         "VALUES ('PAY2', 'P1', 'U1', 5000, '2026-09-10', 'manual')")
            conn.execute("INSERT INTO payments (id, property_id, unit_id, amount, payment_date, assignment_type) "
                         "VALUES ('PAY3', 'P1', 'U1', 16000, '2026-11-04', 'manual')")
            owners = dict(conn.execute("SELECT id, tenant_id FROM payments WHERE id IN ('PAY2','PAY3')").fetchall())
            self.assertEqual(owners, {'PAY2': 'T-OLD', 'PAY3': new_id})
            # November rent is the new account's.
            conn.execute("INSERT INTO rent_charges (id, property_id, unit_id, period, charge_type, amount) "
                         "VALUES ('C2', 'P1', 'U1', '2026-11', 'rent', 16000)")
            self.assertEqual(conn.execute("SELECT tenant_id FROM rent_charges WHERE id = 'C2'").fetchone()[0], new_id)

    def test_brought_forward_needs_evidence_and_lands_on_new_account(self):
        from src.leases import intake
        with self.get_connection() as conn:
            with self.assertRaises(intake.LeaseError):
                intake.record_lease(conn, self._prop(conn), self._form(brought_forward='8000'), 'admin:test')
            self.assertEqual(conn.execute("SELECT status FROM tenants WHERE id = 'T-OLD'").fetchone()[0], 'active')
            _, new_id = intake.record_lease(conn, self._prop(conn), self._form(
                brought_forward='8000', brought_forward_evidence='Tenant acknowledged in writing at signing'),
                'admin:test')
            self.assertEqual(self._balance(conn, new_id), 8000)
            row = conn.execute("SELECT charge_type, period FROM rent_charges WHERE tenant_id = ? ", (new_id,)).fetchone()
            self.assertEqual((row['charge_type'], row['period']), ('brought_forward', 'ARREARS'))
            self.assertTrue(conn.execute("SELECT 1 FROM platform_shadow_log WHERE action = 'lease_recorded'").fetchone())

    def test_correcting_brought_forward(self):
        from src.leases import intake
        with self.get_connection() as conn:
            prop = self._prop(conn)
            lease_id, new_id = intake.record_lease(conn, prop, self._form(
                brought_forward='8000', brought_forward_evidence='Tenant acknowledged in writing'), 'admin:test')
            lease = intake.get_lease(conn, lease_id, 'P1')
            with self.assertRaises(intake.LeaseError):
                intake.set_brought_forward(conn, lease, prop, '5000', '', 'admin:test')     # no reason
            intake.set_brought_forward(conn, lease, prop, '0', 'Old statements reconciled; nothing owed',
                                       'admin:test')
            self.assertEqual(self._balance(conn, new_id), 0)
            lease = intake.get_lease(conn, lease_id, 'P1')
            self.assertIsNone(lease['brought_forward_charge_id'])
            intake.set_brought_forward(conn, lease, prop, '3000', 'Confirmed unpaid after reconciliation',
                                       'admin:test')
            self.assertEqual(self._balance(conn, new_id), 3000)

    def test_someone_new_requires_move_out_first(self):
        from src.leases import intake
        with self.get_connection() as conn:
            with self.assertRaises(intake.LeaseError):
                intake.record_lease(conn, self._prop(conn), self._form(who='new', name='Peter Otieno'),
                                    'admin:test')

    def test_vacant_unit_and_board(self):
        from src.leases import intake
        with self.get_connection() as conn:
            _, new_id = intake.record_lease(conn, self._prop(conn), self._form(
                unit_id='U2', who='', name='Peter Otieno', phone='0711000002', start_date='2026-11-15',
                signed='1', signed_on='2026-11-10'), 'admin:test')
            t = conn.execute("SELECT move_in_date FROM tenants WHERE id = ?", (new_id,)).fetchone()
            self.assertEqual(t['move_in_date'], '2026-11-15')
            rows, counts = intake.unit_board(conn, 'P1')
            self.assertEqual({r['unit_number']: r['phase'] for r in rows}, {'6A': 'recorded', '6B': 'signed'})
            self.assertEqual(counts['rentable'], 2)
            with self.assertRaises(intake.LeaseError):                                  # already has one
                intake.record_lease(conn, self._prop(conn), self._form(unit_id='U2', who='same'), 'admin:test')

    def test_documents_accept_pdf_and_photos_only(self):
        from src.leases import intake
        buf = io.BytesIO()
        Image.new('RGB', (40, 30), 'white').save(buf, 'PNG')
        self.assertEqual(intake.store_document(_Upload(buf.getvalue(), 'page.png'))[1], 'image/jpeg')
        self.assertEqual(intake.store_document(_Upload(b'%PDF-1.4 test', 'lease.pdf'))[1], 'application/pdf')
        self.assertIsNone(intake.store_document(_Upload(b'hello', 'notes.txt')))
        self.assertTrue(intake.documents_dir().startswith(self.dir))


if __name__ == '__main__':
    unittest.main()
