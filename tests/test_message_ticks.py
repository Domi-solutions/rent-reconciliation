"""Message ticks: a message is 'seen' once the other side opens the conversation after it was sent."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MessageTicks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, 'ticks.db')
        subprocess.run([sys.executable, os.path.join(ROOT, 'scripts/seed_demo.py'), cls.db],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=dict(os.environ, FLASK_RUN_FROM_CLI='true', AT_SMS_ENABLED='false'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def setUp(self):
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_tenant_message_turns_seen_when_management_opens_it(self):
        from src.database.db import get_connection
        from src.messaging import threads
        with get_connection() as conn:
            conv_id = conn.execute("SELECT id FROM conversations LIMIT 1").fetchone()[0]
            # Sent long after anyone last opened it: not seen yet
            conn.execute("UPDATE conversations SET staff_read_at = '2000-01-01 00:00:00', "
                         "tenant_read_at = '2000-01-01 00:00:00' WHERE id = ?", (conv_id,))
            _, msgs = threads.thread(conn, conv_id)
            tenant_msgs = [m for m in msgs if m['sender_type'] == 'tenant']
            staff_msgs = [m for m in msgs if m['sender_type'] in ('admin', 'caretaker')]
            self.assertTrue(tenant_msgs and staff_msgs)
            self.assertFalse(any(m['seen'] for m in tenant_msgs + staff_msgs))
            # Management opens the conversation: the tenant's messages are seen, management's are not yet
            conn.execute("UPDATE conversations SET staff_read_at = '2999-01-01 00:00:00' WHERE id = ?", (conv_id,))
            _, msgs = threads.thread(conn, conv_id)
            self.assertTrue(all(m['seen'] for m in msgs if m['sender_type'] == 'tenant'))
            self.assertFalse(any(m['seen'] for m in msgs if m['sender_type'] in ('admin', 'caretaker')))
            self.assertTrue(all(m['seen'] is None for m in msgs if m['sender_type'] == 'system'))

    def test_staff_chat_message_turns_seen_when_caretaker_opens_it(self):
        from src.database.db import get_connection
        from src.messaging import staff_chat
        with get_connection() as conn:
            ct = conn.execute("SELECT caretaker_id FROM staff_messages WHERE sender_type = 'admin' LIMIT 1").fetchone()[0]
            conn.execute("DELETE FROM staff_chat_reads WHERE caretaker_id = ?", (ct,))
            self.assertFalse(any(m['seen'] for m in staff_chat.thread(conn, ct)))
            conn.execute("INSERT INTO staff_chat_reads (caretaker_id, side, read_at) VALUES (?, 'caretaker', '2999-01-01')", (ct,))
            msgs = staff_chat.thread(conn, ct)
            self.assertTrue(all(m['seen'] for m in msgs if m['sender_type'] == 'admin'))
            self.assertFalse(any(m['seen'] for m in msgs if m['sender_type'] == 'caretaker'))


if __name__ == '__main__':
    unittest.main()
