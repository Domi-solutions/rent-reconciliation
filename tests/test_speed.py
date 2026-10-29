"""Speed fixes keep behaviour: balances add up one tenant at a time but give the same figures;
photos are stored with a small copy and cached; pages are compressed; static files carry a
version tag and are cached; texts no longer go out while the database is held."""

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


class Speed(unittest.TestCase):
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
        from app import app
        from src.database.db import get_connection
        self.app, self.connection = app, get_connection
        self.admin = app.test_client()
        self.admin.post('/login', data={'email': 'demo@demo', 'password': 'demo', 'next': ''})
        self.admin.get('/properties')

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    def test_balances_match_adding_up_everything(self):
        with self.connection() as conn:
            view = {r['tenant_id']: (r['total_charged'], r['total_paid'], r['balance'])
                    for r in conn.execute("SELECT * FROM tenant_balances")}
            for t in conn.execute("SELECT id FROM tenants"):
                charged = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM rent_charges WHERE tenant_id = ?",
                                       (t['id'],)).fetchone()[0]
                paid = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE tenant_id = ?",
                                    (t['id'],)).fetchone()[0]
                self.assertEqual(view[t['id']], (charged, paid, charged - paid))
            plan = ' '.join(r[3] for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT balance FROM unit_balances WHERE property_id = 'X'"))
        self.assertNotIn('MATERIALIZE', plan)        # never sums the whole database first

    def test_photo_gets_a_small_copy_and_is_cached(self):
        from werkzeug.datastructures import FileStorage
        from src.messaging import threads
        buf = io.BytesIO()
        Image.new('RGB', (3000, 2000), (120, 160, 90)).save(buf, 'JPEG')
        buf.seek(0)
        with self.connection() as conn:
            conv = conn.execute("SELECT id, property_id FROM conversations LIMIT 1").fetchone()
            threads.add_message(conn, conv['id'], 'admin', 'Management', 'see photo',
                                [FileStorage(buf, filename='p.jpg', content_type='image/jpeg')])
            photo = conn.execute("SELECT id, file_name, width FROM conversation_photos ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual(photo['width'], 1600)
        full = self.admin.get(f"/messages/inbox/photo/{photo['id']}")
        small = self.admin.get(f"/messages/inbox/photo/{photo['id']}?size=s")
        self.assertEqual((full.status_code, small.status_code), (200, 200))
        self.assertLess(len(small.data), len(full.data))
        self.assertEqual(Image.open(io.BytesIO(small.data)).width, threads.SMALL_MAX_SIDE)
        self.assertIn('immutable', small.headers['Cache-Control'])

    def test_pages_are_compressed_and_styles_cached(self):
        page = self.admin.get('/review', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(page.headers.get('Content-Encoding'), 'gzip')
        html = self.admin.get('/review').get_data(as_text=True)
        self.assertRegex(html, r'/static/interface\.css\?v=\w+')
        import re
        css = re.search(r'(/static/interface\.css\?v=\w+)', html).group(1)
        self.assertIn('max-age=31536000', self.admin.get(css).headers['Cache-Control'])

    def test_broadcast_texts_wait_for_the_save(self):
        with self.connection() as conn:
            tenant = conn.execute("SELECT id FROM tenants WHERE status = 'active' AND phone IS NOT NULL LIMIT 1").fetchone()
        with patch('src.messaging.delivery.send_many_async') as background, \
             patch('src.messaging.delivery.send_sms_async'), \
             patch('src.messaging.delivery.send_sms') as direct:
            self.admin.post('/messages/broadcast', data={'tenant_ids': [tenant['id']], 'subject': 'Notice', 'body': 'Hi'})
        direct.assert_not_called()                   # nothing sent while the request held the database
        self.assertEqual(len(background.call_args.args[0]), 1)


    def test_activity_is_a_page_at_a_time(self):
        with self.connection() as conn:
            conn.executemany("INSERT INTO audit_log (action, entity_type, entity_id, details, user_id) VALUES (?, ?, ?, ?, ?)",
                             [('test_entry', 'x', str(i), f'entry {i}', 'test') for i in range(150)])
        first = self.admin.get('/activity').get_data(as_text=True)
        self.assertIn('Older', first)
        second = self.admin.get('/activity?page=2').get_data(as_text=True)
        self.assertIn('Newer', second)

    def test_payments_show_one_month_with_the_payer(self):
        page = self.admin.get('/review?tab=confirmed').get_data(as_text=True)
        self.assertIn('name="month"', page)
        with self.connection() as conn:
            months = [r[0] for r in conn.execute("""SELECT DISTINCT substr(payment_date, 1, 7) FROM payments
                                                    WHERE payment_date != '' ORDER BY 1 DESC""")]
            older = months[-1]
            n_older = conn.execute("SELECT COUNT(*) FROM payments WHERE payment_date LIKE ?", (older + '%',)).fetchone()[0]
        page = self.admin.get(f'/review?tab=confirmed&month={older}').get_data(as_text=True)
        self.assertEqual(page.count('class="data-row"'), n_older)

if __name__ == '__main__':
    unittest.main()
