"""Demo site: real SMS only to numbers typed into the demo; made-up numbers never texted; capped."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DemoSms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, 'demo.db')
        subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'seed_demo.py'), cls.db], cwd=ROOT, check=True,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db, 'FLASK_ENV': 'demo', 'DEMO_MODE': 'true',
                                           'AT_SMS_ENABLED': 'true', 'SMS_PROVIDER': 'textsms',
                                           'TEXTSMS_API_KEY': 'k', 'TEXTSMS_PARTNER_ID': '1'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_only_claimed_numbers_get_real_sms(self):
        from src.database.db import get_connection
        from src.demo import phones
        from src.messaging import delivery
        sent = []
        fake = lambda nums, msg, sender=None: (sent.extend(nums) or [(n, True, None) for n in nums])
        with patch.object(delivery, '_send_textsms', side_effect=fake):
            delivery.send_sms(['0700000103'], 'code')                 # made-up demo number
            self.assertEqual(sent, [])
            with get_connection() as conn:
                tid = phones.claim_first_time(conn, '0711222333')     # a stakeholder's own phone
                name = conn.execute("SELECT name FROM tenants WHERE id = ?", (tid,)).fetchone()[0]
            self.assertEqual(name, 'Amina Wanjiku')
            delivery.send_sms(['0711222333', '0700000101'], 'receipt')
            self.assertEqual(sent, ['+254711222333'])
            for _ in range(phones.PER_PHONE_CAP + 5):
                delivery.send_sms(['0711222333'], 'again')
            self.assertEqual(len(sent), phones.PER_PHONE_CAP)          # capped per number


if __name__ == '__main__':
    unittest.main()
