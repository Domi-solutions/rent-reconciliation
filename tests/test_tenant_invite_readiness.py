"""Invitation review must show tenants whose portal access is not enabled."""

import os
import sqlite3
import tempfile
import unittest
from importlib import import_module
from unittest.mock import patch

from flask import Flask

from src.messaging.tenant_payment import payment_confirmation_body
from src.routes.messaging_routes import messaging_bp


class TenantInviteReadiness(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.directory.name, 'invite.db')
        self.env_patch = patch.dict(os.environ, {
            'DATABASE_PATH': self.db_path, 'APP_BASE_URL': 'https://example.test',
            'TENANT_OUTREACH_ENABLED': 'true',
        })
        self.env_patch.start()
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE properties (id TEXT PRIMARY KEY, name TEXT, status TEXT);
            CREATE TABLE units (id TEXT PRIMARY KEY, property_id TEXT, unit_number TEXT);
            CREATE TABLE tenants (
                id TEXT PRIMARY KEY, property_id TEXT, unit_id TEXT, name TEXT, phone TEXT,
                short_code TEXT, access_token TEXT, status TEXT, language_preference TEXT, person_id TEXT);
            CREATE TABLE persons (id TEXT PRIMARY KEY, pin_hash TEXT);
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, property_id TEXT, tenant_id TEXT, subject TEXT, body TEXT,
                message_type TEXT, delivery_channel TEXT, delivery_status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE audit_log (action TEXT, entity_type TEXT, entity_id TEXT, details TEXT, user_id TEXT);
            INSERT INTO properties VALUES ('PROP1', 'Test Building', 'active');
            INSERT INTO units VALUES ('UNIT1', 'PROP1', 'A1'), ('UNIT2', 'PROP1', 'A2');
            INSERT INTO tenants VALUES
                ('READY', 'PROP1', 'UNIT1', 'Ready', '+254700000001', 'ABC123', 'secret-token', 'active', 'en', NULL),
                ('BLOCKED', 'PROP1', 'UNIT2', 'Blocked', '+254700000002', 'DEF456', NULL, 'active', 'en', NULL);
        """)
        conn.commit()
        conn.close()
        self.app = Flask(__name__)
        self.app.secret_key = 'test-only'
        self.app.register_blueprint(messaging_bp)

    def tearDown(self):
        self.env_patch.stop()
        self.directory.cleanup()

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['property_id'] = 'PROP1'
        return client

    def test_preview_includes_blocked_tenant_without_a_link(self):
        context = {}

        def capture(template, **values):
            context.update(values)
            return 'preview'

        with patch('src.routes.messaging_routes.render_template', side_effect=capture):
            response = self._client().get('/messages/invite')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(context['missing_links'], 1)
        rows = {row['id']: row for row in context['rows']}
        self.assertEqual(set(rows), {'READY', 'BLOCKED'})
        self.assertTrue(rows['READY']['link_ready'])
        self.assertFalse(rows['BLOCKED']['link_ready'])
        self.assertEqual(rows['BLOCKED']['text'], '')

    def test_post_cannot_send_to_tenant_without_portal_access(self):
        with patch('src.messaging.delivery.send_sms_async') as send:
            response = self._client().post('/messages/invite', data={
                'tenant_id': ['READY', 'BLOCKED'],
            })
        self.assertEqual(response.status_code, 302)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], ['+254700000001'])
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM messages WHERE message_type = 'invite'").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_paused_outreach_rejects_post_even_with_a_valid_tenant(self):
        context = {}

        def capture(template, **values):
            context.update(values)
            return 'preview'

        with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': 'false'}), \
             patch('src.messaging.delivery.send_sms_async') as send, \
             patch('src.routes.messaging_routes.render_template', side_effect=capture):
            preview = self._client().get('/messages/invite')
            response = self._client().post('/messages/invite', data={'tenant_id': ['READY']})
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(context['outreach_enabled'])
        self.assertEqual(response.status_code, 302)
        send.assert_not_called()
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM messages WHERE message_type = 'invite'").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


class PaymentConfirmationAccess(unittest.TestCase):
    def test_missing_token_never_creates_a_portal_link(self):
        body = payment_confirmation_body({'name': 'Tenant', 'access_token': None}, 1200,
                                         'https://example.test/')
        self.assertEqual(body, 'Hi Tenant, KES 1,200 payment confirmed.')

    def test_existing_token_is_used_without_changing_it(self):
        body = payment_confirmation_body({'name': 'Tenant', 'access_token': 'existing-token'}, 1200,
                                         'https://example.test/')
        self.assertIn('https://example.test/tenant/existing-token', body)


class TenantShortLink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.directory.name, 'short-link.db')
        cls.env_patch = patch.dict(os.environ, {
            'DATABASE_PATH': cls.db_path,
            'UPLOAD_FOLDER': os.path.join(cls.directory.name, 'uploads'),
            'FLASK_RUN_FROM_CLI': 'true',
            'AT_SMS_ENABLED': 'false',
        })
        cls.env_patch.start()
        cls.app = import_module('app').app
        cls.app.config['TESTING'] = True
        from src.database.db import get_connection
        with get_connection() as conn:
            conn.execute("INSERT INTO properties (id, name) VALUES ('PROP1', 'Test Building')")
            conn.execute("INSERT INTO units (id, property_id, unit_number, monthly_rent) "
                         "VALUES ('UNIT1', 'PROP1', 'A1', 1000)")
            conn.execute("""INSERT INTO tenants
                         (id, property_id, unit_id, name, status, short_code, access_token)
                         VALUES ('TEN1', 'PROP1', 'UNIT1', 'Test Tenant', 'active', 'ABC123', NULL)""")

    @classmethod
    def tearDownClass(cls):
        cls.env_patch.stop()
        cls.directory.cleanup()

    def setUp(self):
        from src.database.db import get_connection
        with get_connection() as conn:
            conn.execute("UPDATE tenants SET access_token = NULL WHERE id = 'TEN1'")

    def test_short_link_without_portal_access_is_not_a_server_error(self):
        response = self.app.test_client().get('/t/ABC123')
        self.assertEqual(response.status_code, 404)

    def test_existing_portal_link_still_redirects(self):
        from src.database.db import get_connection
        with get_connection() as conn:
            conn.execute("UPDATE tenants SET access_token = 'existing-token' WHERE id = 'TEN1'")
        response = self.app.test_client().get('/t/ABC123')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/tenant/existing-token', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
