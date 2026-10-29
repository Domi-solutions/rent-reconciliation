"""One M-Pesa code, one payment — however the money reaches Domi's records.

A paybill payment recorded by M-Pesa later shows up as a bank statement line (sometimes on
several uploaded copies), and the tenant, caretaker or management may paste its SMS. None of
those may book it again, and none may call it "not found"."""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class OneCodeOnePayment(unittest.TestCase):
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
        patch('src.messaging.delivery.send_sms', return_value=(0, 1, ['SMS disabled'])).start()
        patch('src.messaging.delivery.send_sms_async').start()
        from app import app
        from src.database.db import get_connection, generate_id
        self.app, self.connection, self.generate_id = app, get_connection, generate_id
        self.admin = app.test_client()
        self.admin.post('/login', data={'email': 'demo@demo', 'password': 'demo', 'next': ''})
        self.admin.get('/properties')
        with get_connection() as conn:
            # A payment M-Pesa recorded, with its receipt code.
            self.paid = dict(conn.execute("""
                SELECT p.*, pr.organization_id AS org_id FROM payments p JOIN properties pr ON pr.id = p.property_id
                WHERE p.source = 'daraja' AND p.mpesa_ref IS NOT NULL AND p.tenant_id IS NOT NULL
                ORDER BY p.created_at LIMIT 1""").fetchone())

    def tearDown(self):
        patch.stopall()
        self.env.stop()

    # ── helpers ──────────────────────────────────────────────────────────────
    def statement_with(self, ref, amount):
        """Upload-like: a statement holding one paybill credit line with this code."""
        stmt, line = self.generate_id('STMT'), self.generate_id('TXN')
        with self.connection() as conn:
            conn.execute("""INSERT INTO bank_statements (id, org_id, filename, file_path, bank_format, status)
                            VALUES (?, ?, 'copy.pdf', '', 'cooperative', 'active')""",
                         (stmt, self.paid['org_id']))
            conn.execute("""INSERT INTO bank_transactions (id, statement_id, mpesa_ref, amount, txn_type, sender_name, txn_date)
                            VALUES (?, ?, ?, ?, 'PAYBILL_CREDIT', 'SOMEONE', ?)""",
                         (line, stmt, ref, amount, self.paid['payment_date']))
        return stmt, line

    def payments_for(self, ref):
        with self.connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM payments WHERE mpesa_ref = ?", (ref.upper(),)).fetchone()[0]

    def line(self, line_id):
        with self.connection() as conn:
            return conn.execute("""SELECT bt.*, (SELECT id FROM payments WHERE bank_txn_id = bt.id) AS payment_id
                                   FROM bank_transactions bt WHERE bt.id = ?""", (line_id,)).fetchone()

    # ── the bank statement ───────────────────────────────────────────────────
    def test_bank_line_for_an_mpesa_payment_links_itself(self):
        stmt, line = self.statement_with(self.paid['mpesa_ref'], self.paid['amount'])
        page = self.admin.get(f'/verify/{stmt}', follow_redirects=True).get_data(as_text=True)
        self.assertEqual(self.line(line)['payment_id'], self.paid['id'])      # linked, not booked again
        self.assertEqual(self.payments_for(self.paid['mpesa_ref']), 1)
        self.assertIn('already recorded', page)

    def test_second_copy_is_set_aside_and_cannot_be_assigned(self):
        stmt1, line1 = self.statement_with(self.paid['mpesa_ref'], self.paid['amount'])
        self.admin.get(f'/verify/{stmt1}')
        stmt2, line2 = self.statement_with(self.paid['mpesa_ref'], self.paid['amount'])
        self.admin.get(f'/verify/{stmt2}')
        self.assertEqual(self.line(line2)['ignored'], 1)
        self.assertIn(self.paid['id'], self.line(line2)['ignored_reason'])
        page = self.admin.post(f'/assign/{line2}', data={'unit_id': self.paid['unit_id'],
                                                          'reason': 'trying to assign the copy'},
                               follow_redirects=True).get_data(as_text=True)
        self.assertEqual(self.payments_for(self.paid['mpesa_ref']), 1)
        self.assertIn('already recorded', page)

    def test_the_database_refuses_a_second_payment_for_a_code(self):
        _, line = self.statement_with(self.paid['mpesa_ref'], self.paid['amount'])
        with self.assertRaises(sqlite3.IntegrityError):
            with self.connection() as conn:
                conn.execute("""INSERT INTO payments (id, property_id, unit_id, bank_txn_id, amount, payment_date,
                                                      assignment_type) VALUES ('PAY-DUP', ?, ?, ?, ?, ?, 'manual')""",
                             (self.paid['property_id'], self.paid['unit_id'], line, self.paid['amount'],
                              self.paid['payment_date']))

    # ── pasted codes ─────────────────────────────────────────────────────────
    def test_management_pasting_a_recorded_code_books_nothing(self):
        page = self.admin.post('/report-payment', data={'message': self.paid['mpesa_ref'], 'unit_id': self.paid['unit_id']},
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn('already recorded', page)
        self.assertEqual(self.payments_for(self.paid['mpesa_ref']), 1)
        with self.connection() as conn:
            claim = conn.execute("SELECT status FROM payment_claims WHERE mpesa_ref = ?", (self.paid['mpesa_ref'],)).fetchone()
        self.assertEqual(claim['status'], 'verified')

    def test_code_on_someone_elses_account_is_held_not_called_fake(self):
        with self.connection() as conn:
            other_unit = conn.execute("SELECT id FROM units WHERE property_id = ? AND id != ? LIMIT 1",
                                      (self.paid['property_id'], self.paid['unit_id'])).fetchone()[0]
        self.admin.post('/report-payment', data={'message': self.paid['mpesa_ref'], 'unit_id': other_unit})
        with self.connection() as conn:
            claim = conn.execute("SELECT status, flag_reason FROM payment_claims WHERE mpesa_ref = ?",
                                 (self.paid['mpesa_ref'],)).fetchone()
            flagged_tenants = conn.execute("SELECT COUNT(*) FROM tenants WHERE unit_id = ? AND flagged = 1",
                                           (other_unit,)).fetchone()[0]
        self.assertEqual((claim['status'], claim['flag_reason']), ('flagged', 'on_other_account'))
        self.assertEqual(flagged_tenants, 0)
        self.assertEqual(self.payments_for(self.paid['mpesa_ref']), 1)

    def test_verify_never_calls_a_code_in_the_statement_not_found(self):
        """A line assigned by hand before Verify ran: the pending claim for it is answered, not flagged."""
        ref = 'TKX9HAND01'
        stmt, line = self.statement_with(ref, 1500)
        with self.connection() as conn:
            conn.execute("""INSERT INTO payment_claims (id, property_id, mpesa_ref, unit_id, tenant_id, claimed_amount,
                                                        raw_message, source, status, mpesa_period, created_at)
                            VALUES ('CLM-HAND', ?, ?, ?, ?, 1500, ?, 'tenant', 'pending', ?, datetime('now', '-2 hours'))""",
                         (self.paid['property_id'], ref, self.paid['unit_id'], self.paid['tenant_id'], ref,
                          self.paid['payment_date'][:7]))
        self.admin.post(f'/assign/{line}', data={'unit_id': self.paid['unit_id'], 'reason': 'assigned by hand first'})
        self.admin.get(f'/verify/{stmt}')
        with self.connection() as conn:
            claim = conn.execute("SELECT status, flag_reason FROM payment_claims WHERE id = 'CLM-HAND'").fetchone()
            flagged = conn.execute("SELECT flagged FROM tenants WHERE id = ?", (self.paid['tenant_id'],)).fetchone()[0]
        self.assertEqual(claim['status'], 'verified')
        self.assertFalse(flagged)
        self.assertEqual(self.payments_for(ref), 1)

    def test_caretaker_logging_a_recorded_code_books_nothing(self):
        with self.connection() as conn:
            ct = conn.execute("SELECT id, name FROM caretakers WHERE property_id = ? LIMIT 1",
                              (self.paid['property_id'],)).fetchone()
        client = self.app.test_client()
        with client.session_transaction() as s:
            s['caretaker_id'], s['caretaker_name'] = ct['id'], ct['name']
        with patch.dict(os.environ, {'CARETAKER_MONEY_TOOLS': '1'}):
            page = client.post(f"/caretaker/{self.paid['property_id']}/log-payment",
                               data={'message': self.paid['mpesa_ref'], 'unit_id': self.paid['unit_id'],
                                     'payment_mode': 'active'}, follow_redirects=True).get_data(as_text=True)
        self.assertIn('already recorded', page)
        self.assertEqual(self.payments_for(self.paid['mpesa_ref']), 1)

    # ── M-Pesa arriving after the bank line was booked ───────────────────────
    def test_mpesa_callback_for_a_code_booked_from_the_bank_adds_nothing(self):
        ref = 'TKX9BANK01'
        stmt, line = self.statement_with(ref, 2000)
        self.admin.post(f'/assign/{line}', data={'unit_id': self.paid['unit_id'], 'reason': 'booked from the bank'})
        with self.connection() as conn:
            conn.execute("""INSERT INTO payment_transactions (id, org_id, property_id, unit_id, tenant_id, source,
                                external_reference, amount, callback_amount, raw_callback, processing_status, mpesa_ref, paid_at)
                            VALUES ('PTX-LATE', ?, ?, ?, ?, 'c2b', 'EXT-LATE', 2000, 2000, '{}', 'pending', ?, ?)""",
                         (self.paid['org_id'], self.paid['property_id'], self.paid['unit_id'], self.paid['tenant_id'],
                          ref, self.paid['payment_date']))
        from src.payments import mpesa
        with patch.object(mpesa, '_confirm_with_safaricom', return_value='go'):
            mpesa.book('PTX-LATE')
        with self.connection() as conn:
            status = conn.execute("SELECT processing_status FROM payment_transactions WHERE id = 'PTX-LATE'").fetchone()[0]
        self.assertEqual(status, 'duplicate')
        self.assertEqual(self.payments_for(ref), 1)

    def test_quick_verify_knows_mpesa_payments(self):
        from src.utils.statement import quick_verify_ref
        with self.connection() as conn:
            result = quick_verify_ref(conn, self.paid['mpesa_ref'], self.paid['unit_id'], self.paid['org_id'])
        self.assertEqual(result['status'], 'already_this_unit')


if __name__ == '__main__':
    unittest.main()
