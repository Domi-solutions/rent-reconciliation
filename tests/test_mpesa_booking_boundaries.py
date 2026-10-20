"""Regression checks for early STK callbacks and paybill organization boundaries."""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.database.db import get_connection
from src.payments import mpesa


class MpesaBookingBoundaries(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.directory.name, 'test.db')
        self.path_patch = patch.dict(os.environ, {'DATABASE_PATH': self.db_path})
        self.path_patch.start()
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE organizations (id TEXT PRIMARY KEY);
            CREATE TABLE properties (id TEXT PRIMARY KEY, organization_id TEXT, pay_paybill TEXT, status TEXT);
            CREATE TABLE units (id TEXT PRIMARY KEY, property_id TEXT, unit_number TEXT);
            CREATE TABLE tenants (id TEXT PRIMARY KEY, property_id TEXT, unit_id TEXT,
                                  short_code TEXT, status TEXT, name TEXT, phone TEXT);
            CREATE TABLE payment_transactions (
                id TEXT PRIMARY KEY, org_id TEXT, property_id TEXT, unit_id TEXT, tenant_id TEXT,
                source TEXT, external_reference TEXT UNIQUE, phone TEXT, amount REAL,
                raw_callback TEXT, processing_status TEXT, error_detail TEXT, processed_at TEXT,
                callback_amount REAL, mpesa_ref TEXT, paid_at TEXT, account_ref TEXT);
            INSERT INTO organizations VALUES ('ORG1'), ('ORG2');
            INSERT INTO properties VALUES ('PROP1', 'ORG1', '123456', 'active'),
                                          ('PROP2', 'ORG2', '654321', 'active');
            INSERT INTO units VALUES ('UNIT1', 'PROP1', 'A1'), ('UNIT2', 'PROP2', 'A1');
            INSERT INTO tenants VALUES ('TEN1', 'PROP1', 'UNIT1', 'ABC123', 'active', 'One', '254700000001'),
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


if __name__ == '__main__':
    unittest.main()
