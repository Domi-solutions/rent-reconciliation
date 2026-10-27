"""An upload retires an earlier statement only when it replaces it: same bank, fully covered."""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _txn(day, ref):
    from src.parsers.pdf_parser import Transaction
    return Transaction(transaction_date=day, clearing_date=day, txn_code='014', txn_type='PAYBILL_CREDIT',
                       reference=ref, sender='JANE WANJIRU', narration='MOWIN', unit_hint=None,
                       amount=Decimal('1000.00'), direction='credit', running_balance=Decimal('0'),
                       raw_text='', page_number=1, parse_warnings=[])


def _result(fmt, days):
    return {'success': True, 'statement_format': fmt, 'opening_balance': Decimal('0'),
            'closing_balance': Decimal('1000'), 'validation': {'valid': True}, 'errors': [], 'warnings': [],
            'summary': {'paybill_credits': len(days)},
            'transactions': [_txn(d, f'REF{i}{fmt[:2]}') for i, d in enumerate(days)]}


class StatementSupersede(unittest.TestCase):
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
        # Other test modules import `app` fresh so its startup migrations run on their own database.
        sys.modules.pop('app', None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.db = os.path.join(self.tmp, 'test.db')
        shutil.copy(self.template, self.db)
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db, 'AT_SMS_ENABLED': 'false',
                                           'FLASK_ENV': 'development'})
        self.env.start()
        import app as app_module
        self.app_module = app_module
        app_module.app.config['UPLOAD_FOLDER'] = self.tmp
        from src.database.db import get_connection
        self.get_connection = get_connection
        with get_connection() as conn:
            conn.execute("INSERT INTO organizations (id, name) VALUES ('ORG1', 'Agency')")
            conn.execute("INSERT INTO properties (id, name, organization_id) VALUES ('P1', 'Mowin', 'ORG1')")
            for sid, fmt, start, end in (('S-FAM-MAR', 'family_bank', '2026-03-02', '2026-05-07'),
                                         ('S-NBK-MAR', 'national_bank', '2026-03-01', '2026-04-24'),
                                         ('S-FAM-MAY', 'family_bank', '2026-05-04', '2026-05-31')):
                conn.execute("""INSERT INTO bank_statements (id, org_id, filename, file_path, period_start,
                                period_end, bank_format, status) VALUES (?, 'ORG1', 'x.pdf', 'x.pdf', ?, ?, ?, 'active')""",
                             (sid, start, end, fmt))

    def tearDown(self):
        self.env.stop()

    def _upload(self, result):
        client = self.app_module.app.test_client()
        with client.session_transaction() as s:
            s.update(admin_authenticated=True, admin_master=True, org_selection_done=True,
                     org_id='ORG1', property_id='P1')
        with patch.object(self.app_module, 'parse_bank_statement', return_value=result):
            client.post('/statements/upload', data={'file': (io.BytesIO(b'%PDF-1.4'), 'new.pdf')},
                        content_type='multipart/form-data')
        with self.get_connection() as conn:
            return dict(conn.execute("SELECT id, status FROM bank_statements WHERE id LIKE 'S-%'").fetchall())

    def test_family_may_june_retires_only_the_family_statement_it_covers(self):
        status = self._upload(_result('family_bank', ['02-MAY-2026', '30-JUN-2026']))
        self.assertEqual(status, {'S-FAM-MAR': 'active',      # overlaps 1–7 May only: kept
                                  'S-NBK-MAR': 'active',      # another bank: kept
                                  'S-FAM-MAY': 'superseded'}) # same bank, fully covered

    def test_scanned_upload_retires_nothing(self):
        status = self._upload(_result('scanned', ['01-MAR-2026', '30-JUN-2026']))
        self.assertEqual(set(status.values()), {'active'})


if __name__ == '__main__':
    unittest.main()
