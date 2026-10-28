"""Getting a sign-in code: a mistyped number is caught on the spot, not after a code that never came."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TenantVerify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, 'verify.db')
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
        self.client = app.test_client()

    def tearDown(self):
        self.env.stop()

    def test_short_number_stays_on_the_form_with_a_reason(self):
        r = self.client.post('/tenant/verify', data={'phone': '07000000'})
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        self.assertIn('not a full phone number', page)
        self.assertIn('value="07000000"', page)

    def test_demo_number_gets_a_code(self):
        r = self.client.post('/tenant/verify', data={'phone': '0700000103'})
        self.assertIn('/tenant/verify/code', r.headers['Location'])
        page = self.client.get('/tenant/verify/code').get_data(as_text=True)
        self.assertIn('Your tenant code is', page)
        self.assertNotIn('no demo tenant has', page)


if __name__ == '__main__':
    unittest.main()
