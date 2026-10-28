"""The admin tenant statement reads like the tenant's: today's account first, then bills and
payments newest first, each opening into its details — worded for management."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AdminStatement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, 'demo.db')
        subprocess.run([sys.executable, os.path.join(ROOT, 'scripts/seed_demo.py'), cls.db],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=dict(os.environ, FLASK_RUN_FROM_CLI='true', AT_SMS_ENABLED='false'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def setUp(self):
        self.env = patch.dict(os.environ, {
            'DATABASE_PATH': self.db, 'DEMO_MODE': 'true', 'FLASK_ENV': 'development',
            'ENVIRONMENT': 'development', 'FLASK_RUN_FROM_CLI': 'true', 'AT_SMS_ENABLED': 'false',
        })
        self.env.start()
        from app import app
        from src.database.db import get_connection
        self.admin = app.test_client()
        self.admin.post('/login', data={'email': 'demo@demo', 'password': 'demo', 'next': ''})
        self.admin.get('/properties')
        with get_connection() as conn:
            self.owing = conn.execute("""SELECT b.tenant_id FROM tenant_balances b JOIN tenants t ON t.id = b.tenant_id
                                         WHERE t.status = 'active' ORDER BY b.balance DESC LIMIT 1""").fetchone()[0]

    def tearDown(self):
        self.env.stop()

    def test_account_first_rows_expand_newest_first(self):
        page = self.admin.get(f'/tenants/{self.owing}/statement').get_data(as_text=True)
        self.assertIn('Still owed: <span class="owed-figure">', page)
        self.assertIn('unless the tenant chose', page)                # management wording, not the tenant's
        self.assertNotIn('Make a payment', page)
        self.assertLess(page.index('Account now'), page.index('statement-ledger'))
        self.assertIn('<details class="statement-entry', page)
        self.assertIn('This payment covered', page)
        self.assertNotIn('Water you used', page)
        dates = [line.split('>')[1][:10] for line in page.split('class="statement-date"')[1:]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertIn('statement.css', page)


if __name__ == '__main__':
    unittest.main()
