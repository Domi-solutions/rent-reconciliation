"""The system sends no alerts on its own unless AUTOMATIC_ALERTS_ENABLED=true.

Flags and audit rows are still written; only the texts are held back, so a
person acts on the dashboard lists instead (reactive, not proactive).
"""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from src.agent import coordinator
from src.agent.detector import detect_stale_claims
from src.messaging.outreach import automatic_alerts_enabled

CLAIM = {'id': 'CLM1', 'mpesa_ref': 'ABC123', 'claimed_amount': 5000, 'created_at': '2026-09-01',
         'unit_id': 'U1', 'unit_number': '6A', 'tenant_id': 'T1', 'tenant_name': 'Jane',
         'tenant_phone': '+254700000001', 'phone': '+254700000002'}


def _conn():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [CLAIM]
    return conn


class AutomaticAlertsGate(unittest.TestCase):
    def test_default_is_disabled(self):
        with patch.dict(os.environ, {'AUTOMATIC_ALERTS_ENABLED': ''}):
            self.assertFalse(automatic_alerts_enabled())

    def _stale_claim_sms(self, flag):
        conn = _conn()
        with patch.dict(os.environ, {'AUTOMATIC_ALERTS_ENABLED': flag}), \
             patch('src.platform.guardian.raise_alert'), patch('src.platform.guardian.platform_log'), \
             patch('src.messaging.delivery.send_sms_async') as sms:
            detect_stale_claims(conn, 'PROP1')
        flagged = any("SET status = 'flagged'" in str(c.args[0]) for c in conn.execute.call_args_list)
        return sms, flagged

    def test_stale_claims_still_flagged_but_nobody_texted_when_disabled(self):
        sms, flagged = self._stale_claim_sms('false')
        self.assertTrue(flagged)
        sms.assert_not_called()

    def test_stale_claims_texted_when_enabled(self):
        sms, _ = self._stale_claim_sms('true')
        self.assertEqual(sms.call_count, 2)          # caretaker + tenant

    def test_weekly_digest_texts_no_owner_when_disabled(self):
        @contextmanager
        def fake_connection():
            yield _conn()
        with patch.dict(os.environ, {'AUTOMATIC_ALERTS_ENABLED': 'false'}), \
             patch.object(coordinator, 'get_connection', fake_connection), \
             patch.object(coordinator, '_active_property_ids', return_value=['PROP1']), \
             patch.object(coordinator, 'generate_weekly_digest', return_value='digest'), \
             patch.object(coordinator, 'route_message') as route:
            coordinator.weekly_digest_job()
        route.assert_not_called()


if __name__ == '__main__':
    unittest.main()
