"""Follow-up list: overdue money only, per tenant, more than one month behind first."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FollowUp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.template = os.path.join(cls.tmp, 'template.db')
        subprocess.run([sys.executable, '-c', 'import app'], cwd=ROOT, check=True,
                       env=dict(os.environ, DATABASE_PATH=cls.template, FLASK_ENV='development',
                                PYTHONDONTWRITEBYTECODE='1'),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.db = os.path.join(self.tmp, 'test.db')
        shutil.copy(self.template, self.db)
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db})
        self.env.start()
        from src.database.db import get_connection
        self.get_connection = get_connection
        with get_connection() as conn:
            conn.execute("INSERT INTO properties (id, name) VALUES ('P1', 'Mowin')")
            for uid, num in (('U1', '1A'), ('U2', '1B'), ('U3', '1C')):
                conn.execute("INSERT INTO units (id, property_id, unit_number, monthly_rent, service_charge, status) "
                             "VALUES (?, 'P1', ?, 9000, 1000, 'occupied')", (uid, num))
            for tid, uid, name in (('T1', 'U1', 'Two months'), ('T2', 'U2', 'Not yet due'), ('T3', 'U3', 'Old tenant')):
                conn.execute("INSERT INTO tenants (id, property_id, unit_id, name, phone, status) "
                             "VALUES (?, 'P1', ?, ?, '0712345678', 'active')", (tid, uid, name))

            def charge(cid, uid, tid, period, due, amount=10000):
                conn.execute("""INSERT INTO rent_charges (id, property_id, unit_id, tenant_id, period, charge_type,
                                amount, due_date) VALUES (?, 'P1', ?, ?, ?, 'rent', ?, ?)""",
                             (cid, uid, tid, period, amount, due))
            charge('C1', 'U1', 'T1', '2026-09', '2026-09-05')
            charge('C2', 'U1', 'T1', '2026-10', '2026-10-05')
            charge('C3', 'U1', 'T1', '2026-11', '2026-11-05')      # not yet due on 3 Nov
            charge('C4', 'U2', 'T2', '2026-11', '2026-11-05')
            conn.execute("UPDATE tenants SET status = 'inactive' WHERE id = 'T3'")
            conn.execute("INSERT INTO tenants (id, property_id, unit_id, name, status) "
                         "VALUES ('T4', 'P1', 'U3', 'New tenant', 'active')")
            charge('C5', 'U3', 'T3', '2026-08', '2026-08-05', 30000)  # the previous occupant's debt
            conn.execute("""INSERT INTO payment_claims (id, property_id, unit_id, tenant_id, mpesa_ref,
                            claimed_amount, status) VALUES ('PC1', 'P1', 'U1', 'T1', 'ABC', 5000, 'pending')""")

    def tearDown(self):
        self.env.stop()

    def test_overdue_only_per_tenant_and_reported_payments_beside_not_netted(self):
        from src.reports.follow_up import behind_list
        with self.get_connection() as conn:
            everyone = {r['name']: r for r in behind_list(conn, 'P1', min_months=0, today=date(2026, 11, 3))}
            follow = behind_list(conn, 'P1', today=date(2026, 11, 3))
        self.assertEqual(set(everyone), {'Two months'})          # Nov not yet due; old debt not the new tenant's
        t1 = everyone['Two months']
        self.assertEqual((t1['overdue'], t1['months_behind'], t1['oldest_unpaid']), (20000, 2.0, 'Sep 2026'))
        self.assertEqual(t1['pending_amount'], 5000)              # shown beside, not taken off
        self.assertEqual([r['name'] for r in follow], ['Two months'])

    def test_exactly_one_month_is_not_more_than_one(self):
        from src.reports.follow_up import behind_list
        with self.get_connection() as conn:
            conn.execute("DELETE FROM rent_charges WHERE id = 'C1'")
            self.assertEqual(behind_list(conn, 'P1', today=date(2026, 11, 3)), [])
            self.assertEqual(len(behind_list(conn, 'P1', min_months=0, today=date(2026, 11, 3))), 1)


if __name__ == '__main__':
    unittest.main()
