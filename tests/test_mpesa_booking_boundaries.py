"""Regression checks for early STK callbacks and paybill organization boundaries."""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, request

from src.database.db import get_connection
from src.payments import mpesa


class MpesaBookingBoundaries(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.directory.name, 'test.db')
        self.path_patch = patch.dict(os.environ, {
            'DATABASE_PATH': self.db_path, 'ENVIRONMENT': 'test', 'DARAJA_ENV': 'sandbox',
        })
        self.path_patch.start()
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE organizations (id TEXT PRIMARY KEY);
            CREATE TABLE properties (id TEXT PRIMARY KEY, organization_id TEXT, pay_paybill TEXT,
                                     status TEXT, name TEXT);
            CREATE TABLE units (id TEXT PRIMARY KEY, property_id TEXT, unit_number TEXT);
            CREATE TABLE tenants (id TEXT PRIMARY KEY, property_id TEXT, unit_id TEXT,
                                  short_code TEXT, status TEXT, name TEXT, phone TEXT,
                                  access_token TEXT, flagged INTEGER DEFAULT 0);
            CREATE TABLE payment_transactions (
                id TEXT PRIMARY KEY, org_id TEXT, property_id TEXT, unit_id TEXT, tenant_id TEXT,
                source TEXT, external_reference TEXT UNIQUE, phone TEXT, amount REAL,
                raw_callback TEXT, processing_status TEXT, error_detail TEXT, processed_at TEXT,
                callback_amount REAL, mpesa_ref TEXT, paid_at TEXT, account_ref TEXT,
                attempts INTEGER DEFAULT 0, verified_via TEXT, payment_id TEXT,
                received_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE payments (id TEXT PRIMARY KEY, property_id TEXT, unit_id TEXT,
                                   tenant_id TEXT, claim_id TEXT, bank_txn_id TEXT,
                                   statement_id TEXT, amount REAL, payment_date TEXT,
                                   assignment_type TEXT, assignment_reason TEXT,
                                   assigned_by TEXT, source TEXT, created_at TEXT);
            CREATE TABLE rent_charges (id TEXT PRIMARY KEY, unit_id TEXT, tenant_id TEXT,
                                       amount REAL, period TEXT, charge_type TEXT, created_at TEXT);
            CREATE TABLE payment_allocations (id TEXT PRIMARY KEY, payment_id TEXT,
                                              charge_id TEXT, amount REAL);
            CREATE TABLE payment_claims (id TEXT PRIMARY KEY, property_id TEXT, unit_id TEXT,
                                         tenant_id TEXT, mpesa_ref TEXT, status TEXT, verified_at TEXT);
            CREATE TABLE audit_log (action TEXT, entity_type TEXT, entity_id TEXT,
                                    details TEXT, user_id TEXT);
            INSERT INTO organizations VALUES ('ORG1'), ('ORG2');
            INSERT INTO properties VALUES ('PROP1', 'ORG1', '123456', 'active', 'One Property'),
                                          ('PROP2', 'ORG2', '654321', 'active', 'Two Property');
            INSERT INTO units VALUES ('UNIT1', 'PROP1', 'A1'), ('UNIT2', 'PROP2', 'A1');
            INSERT INTO tenants (id, property_id, unit_id, short_code, status, name, phone)
            VALUES ('TEN1', 'PROP1', 'UNIT1', 'ABC123', 'active', 'One', '254700000001'),
                   ('TEN2', 'PROP2', 'UNIT2', 'XYZ999', 'active', 'Two', '254700000002');
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.path_patch.stop()
        self.directory.cleanup()

    def test_early_success_callback_is_linked_to_original_request(self):
        body = {'Body': {'stkCallback': {
            'CheckoutRequestID': 'ws-early', 'ResultCode': 0,
            'CallbackMetadata': {'Item': [
                {'Name': 'Amount', 'Value': 1000},
                {'Name': 'MpesaReceiptNumber', 'Value': 'ABCDEF1234'},
                {'Name': 'TransactionDate', 'Value': 20261020103000},
            ]}}}}
        self.assertIsNone(mpesa.record_stk_callback(body))
        with get_connection() as conn:
            self.assertTrue(mpesa.link_stk_request(conn, 'ws-early', 'ORG1', 'PROP1', 'UNIT1',
                                                   'TEN1', '254700000001', 1000))
            row = conn.execute("SELECT * FROM payment_transactions WHERE external_reference = 'ws-early'").fetchone()
            self.assertEqual((row['processing_status'], row['tenant_id'], row['callback_amount']),
                             ('pending', 'TEN1', 1000))
            self.assertEqual(row['mpesa_ref'], 'ABCDEF1234')

    def test_early_failed_callback_does_not_queue_a_payment(self):
        body = {'Body': {'stkCallback': {
            'CheckoutRequestID': 'ws-failed', 'ResultCode': 1032,
            'ResultDesc': 'Request cancelled by user'}}}
        self.assertIsNone(mpesa.record_stk_callback(body))
        with get_connection() as conn:
            self.assertTrue(mpesa.link_stk_request(conn, 'ws-failed', 'ORG1', 'PROP1', 'UNIT1',
                                                   'TEN1', '254700000001', 1000))
            row = conn.execute("SELECT processing_status, error_detail FROM payment_transactions "
                               "WHERE external_reference = 'ws-failed'").fetchone()
            self.assertEqual((row['processing_status'], row['error_detail']),
                             ('failed', 'Request cancelled by user'))

    def test_c2b_unknown_payment_cannot_move_to_another_organization(self):
        body = {'TransID': 'ABCDEF1235', 'TransAmount': '1000', 'TransTime': '20261020103000',
                'BusinessShortCode': '123456', 'BillRefNumber': 'UNKNOWN'}
        self.assertIsNone(mpesa.record_c2b_confirmation(body))
        with get_connection() as conn:
            row = conn.execute("SELECT id, org_id FROM payment_transactions WHERE mpesa_ref = 'ABCDEF1235'").fetchone()
            self.assertEqual(row['org_id'], 'ORG1')
            self.assertFalse(mpesa.assign_unmatched(conn, row['id'], 'TEN2', 'ORG2'))
            self.assertFalse(mpesa.assign_unmatched(conn, row['id'], 'TEN2', 'ORG1'))
            self.assertTrue(mpesa.assign_unmatched(conn, row['id'], 'TEN1', 'ORG1'))

    def test_c2b_account_cannot_name_tenant_of_another_organization(self):
        body = {'TransID': 'ABCDEF1236', 'TransAmount': '1000', 'TransTime': '20261020103000',
                'BusinessShortCode': '123456', 'BillRefNumber': 'XYZ999'}
        self.assertIsNone(mpesa.record_c2b_confirmation(body))
        with get_connection() as conn:
            row = conn.execute("SELECT org_id, tenant_id, processing_status FROM payment_transactions "
                               "WHERE mpesa_ref = 'ABCDEF1236'").fetchone()
            self.assertEqual((row['org_id'], row['tenant_id'], row['processing_status']),
                             ('ORG1', None, 'unmatched'))

    def test_stk_callback_amount_must_match_exactly(self):
        with get_connection() as conn:
            conn.execute("""INSERT INTO payment_transactions
                        (id, source, external_reference, amount, callback_amount,
                         mpesa_ref, processing_status)
                        VALUES ('STK1', 'daraja', 'ws-amount', 1000.49, 1000.40,
                                'ABCDEF1237', 'pending')""")
            txn = conn.execute("SELECT * FROM payment_transactions WHERE id = 'STK1'").fetchone()
            with patch.object(mpesa.daraja, 'stk_query') as query:
                self.assertEqual(mpesa._confirm_with_safaricom(conn, txn), 'stop')
                query.assert_not_called()
            row = conn.execute("SELECT processing_status FROM payment_transactions WHERE id = 'STK1'").fetchone()
            self.assertEqual(row['processing_status'], 'failed')

    def test_stk_without_amount_or_receipt_cannot_be_booked(self):
        for callback_amount, receipt in [(None, 'ABCDEF1238'), (1000, None)]:
            with self.subTest(callback_amount=callback_amount, receipt=receipt), get_connection() as conn:
                txn_id = f'STK-{receipt or "no-ref"}'
                conn.execute("""INSERT INTO payment_transactions
                            (id, source, external_reference, amount, callback_amount,
                             mpesa_ref, processing_status)
                            VALUES (?, 'daraja', ?, 1000, ?, ?, 'pending')""",
                             (txn_id, txn_id, callback_amount, receipt))
                txn = conn.execute("SELECT * FROM payment_transactions WHERE id = ?", (txn_id,)).fetchone()
                with patch.object(mpesa.daraja, 'stk_query') as query:
                    self.assertEqual(mpesa._confirm_with_safaricom(conn, txn), 'stop')
                    query.assert_not_called()

    def test_booking_only_resolves_claim_of_paying_tenant(self):
        with get_connection() as conn:
            conn.execute("""INSERT INTO payment_claims VALUES
                         ('CLAIM1', 'PROP1', 'UNIT1', 'TEN1', 'ABCDEF1239', 'pending', NULL)""")
            conn.execute("""INSERT INTO payment_claims VALUES
                         ('CLAIM2', 'PROP2', 'UNIT2', 'TEN2', 'ABCDEF1239', 'flagged', NULL)""")
        body = {'TransID': 'ABCDEF1239', 'TransAmount': '1000', 'TransTime': '20261020103000',
                'BusinessShortCode': '123456', 'BillRefNumber': 'ABC123'}
        txn_id = mpesa.record_c2b_confirmation(body)
        self.assertIsNotNone(txn_id)
        sms = mpesa.book(txn_id, 'https://example.test')
        self.assertIsNotNone(sms)
        self.assertNotIn('Receipt:', sms['body'])  # access_token is intentionally absent
        with get_connection() as conn:
            claims = conn.execute("SELECT id, status FROM payment_claims ORDER BY id").fetchall()
            self.assertEqual([(r['id'], r['status']) for r in claims],
                             [('CLAIM1', 'verified'), ('CLAIM2', 'flagged')])
            payment = conn.execute("SELECT claim_id, tenant_id FROM payments").fetchone()
            self.assertEqual((payment['claim_id'], payment['tenant_id']), ('CLAIM1', 'TEN1'))

    def test_zero_c2b_amount_never_creates_a_payment(self):
        body = {'TransID': 'ABCDEF1240', 'TransAmount': '0', 'TransTime': '20261020103000',
                'BusinessShortCode': '123456', 'BillRefNumber': 'ABC123'}
        txn_id = mpesa.record_c2b_confirmation(body)
        self.assertIsNone(mpesa.book(txn_id))
        with get_connection() as conn:
            row = conn.execute("SELECT processing_status FROM payment_transactions WHERE id = ?", (txn_id,)).fetchone()
            self.assertEqual(row['processing_status'], 'failed')
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)

    def test_stk_and_c2b_with_same_receipt_create_one_payment(self):
        body = {'TransID': 'ABCDEF1241', 'TransAmount': '1000', 'TransTime': '20261020103000',
                'BusinessShortCode': '123456', 'BillRefNumber': 'ABC123'}
        first = mpesa.record_c2b_confirmation(body)
        self.assertIsNotNone(mpesa.book(first))
        with get_connection() as conn:
            conn.execute("""INSERT INTO payment_transactions
                        (id, org_id, property_id, unit_id, tenant_id, source,
                         external_reference, amount, callback_amount, mpesa_ref,
                         raw_callback, processing_status)
                        VALUES ('STK2', 'ORG1', 'PROP1', 'UNIT1', 'TEN1', 'daraja',
                                'ws-duplicate', 1000, 1000, 'ABCDEF1241', '{}', 'pending')""")
        with patch.object(mpesa.daraja, 'configured', return_value=False), \
             patch.dict(os.environ, {'DARAJA_ENV': 'sandbox'}):
            self.assertIsNone(mpesa.book('STK2'))
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)
            row = conn.execute("SELECT processing_status FROM payment_transactions WHERE id = 'STK2'").fetchone()
            self.assertEqual(row['processing_status'], 'duplicate')

    def test_production_callback_requires_correct_token(self):
        app = Flask(__name__)
        with patch.dict(os.environ, {'ENVIRONMENT': 'production', 'DARAJA_ENV': 'sandbox',
                                     'MPESA_CALLBACK_TOKEN': ''}):
            with app.test_request_context('/inbound/payment/mpesa'):
                self.assertFalse(mpesa.callback_allowed(request))
        with patch.dict(os.environ, {'ENVIRONMENT': 'production', 'DARAJA_ENV': 'sandbox',
                                     'MPESA_CALLBACK_TOKEN': 'test-token'}):
            with app.test_request_context('/inbound/payment/mpesa'):
                self.assertFalse(mpesa.callback_allowed(request))
            with app.test_request_context('/inbound/payment/mpesa?t=wrong'):
                self.assertFalse(mpesa.callback_allowed(request))
            with app.test_request_context('/inbound/payment/mpesa?t=test-token'):
                self.assertTrue(mpesa.callback_allowed(request))

    def test_fly_production_flag_alone_refuses_untokened_callbacks(self):
        # fly.toml sets FLASK_ENV=production and never ENVIRONMENT; that must be enough.
        app = Flask(__name__)
        with patch.dict(os.environ, {'FLASK_ENV': 'production', 'ENVIRONMENT': '', 'DARAJA_ENV': 'sandbox',
                                     'MPESA_CALLBACK_TOKEN': ''}):
            with app.test_request_context('/inbound/payment/mpesa'):
                self.assertFalse(mpesa.callback_allowed(request))

    def test_production_never_books_stk_callback_without_query_credentials(self):
        with get_connection() as conn:
            conn.execute("""INSERT INTO payment_transactions
                        (id, source, external_reference, amount, callback_amount,
                         mpesa_ref, processing_status)
                        VALUES ('STK3', 'daraja', 'ws-no-creds', 1000, 1000,
                                'ABCDEF1242', 'pending')""")
            txn = conn.execute("SELECT * FROM payment_transactions WHERE id = 'STK3'").fetchone()
            with patch.dict(os.environ, {'ENVIRONMENT': 'production', 'DARAJA_ENV': 'sandbox'}), \
                 patch.object(mpesa.daraja, 'configured', return_value=False):
                self.assertEqual(mpesa._confirm_with_safaricom(conn, txn), 'stop')
            row = conn.execute("SELECT processing_status FROM payment_transactions WHERE id = 'STK3'").fetchone()
            self.assertEqual(row['processing_status'], 'failed')


if __name__ == '__main__':
    unittest.main()
