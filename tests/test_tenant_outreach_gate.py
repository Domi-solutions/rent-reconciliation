"""Scheduled outreach stays dormant until onboarding is explicitly approved."""

import os
import unittest
from unittest.mock import Mock, patch

from src.agent.coordinator import send_monthly_checkins_job
from src.messaging.outreach import enabled
from src.messaging.reminders import generate_due_reminders


class TenantOutreachGate(unittest.TestCase):
    def test_default_is_disabled(self):
        with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': ''}):
            self.assertFalse(enabled())

    def test_due_reminders_do_not_touch_database_when_disabled(self):
        conn = Mock()
        with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': 'false'}):
            generate_due_reminders(conn, 'PROP1')
        conn.execute.assert_not_called()

    def test_monthly_checkins_do_not_touch_database_when_disabled(self):
        with patch.dict(os.environ, {'TENANT_OUTREACH_ENABLED': 'false'}), \
             patch('src.agent.coordinator.get_connection') as connection:
            send_monthly_checkins_job('PROP1')
        connection.assert_not_called()


if __name__ == '__main__':
    unittest.main()
