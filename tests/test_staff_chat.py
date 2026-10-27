"""Management ↔ caretaker chat: append-only, unread counts per side, history outlives the account."""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StaffChat(unittest.TestCase):
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
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.db = os.path.join(self.tmp, 'test.db')
        shutil.copy(self.template, self.db)
        self.env = patch.dict(os.environ, {'DATABASE_PATH': self.db})
        self.env.start()
        from src.database.db import get_connection
        self.get_connection = get_connection
        with get_connection() as conn:
            conn.execute("INSERT INTO properties (id, name) VALUES ('P1', 'Mowin')")
            conn.execute("INSERT INTO caretakers (id, property_id, name) VALUES ('CT1', 'P1', 'Juma')")

    def tearDown(self):
        self.env.stop()

    def test_unread_counts_and_append_only(self):
        from src.messaging import staff_chat
        with self.get_connection() as conn:
            staff_chat.post(conn, 'P1', 'CT1', 'caretaker', 'Juma', 'Tank 3 leaking', kind='report')
            self.assertEqual(staff_chat.unread_for_admin(conn, 'P1'), 1)
            self.assertEqual(staff_chat.unread_for_caretaker(conn, 'CT1'), 0)     # own message
            staff_chat.mark_read(conn, 'CT1', 'admin')
            self.assertEqual(staff_chat.unread_for_admin(conn, 'P1'), 0)
            conn.execute("UPDATE staff_chat_reads SET read_at = datetime('now', '-1 minute')")
            staff_chat.post(conn, 'P1', 'CT1', 'admin', 'Lincks', 'Plumber Thursday')
            self.assertEqual(staff_chat.unread_for_caretaker(conn, 'CT1'), 1)
            msgs = staff_chat.thread(conn, 'CT1')
            self.assertEqual([(m['sender_type'], m['kind']) for m in msgs], [('caretaker', 'report'), ('admin', 'message')])
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE staff_messages SET body = 'edited'")

    def test_chat_survives_caretaker_deletion(self):
        from src.messaging import staff_chat
        with self.get_connection() as conn:
            staff_chat.post(conn, 'P1', 'CT1', 'caretaker', 'Juma', 'Hello')
            conn.execute("DELETE FROM caretakers WHERE id = 'CT1'")                 # revocation still works
            chats = staff_chat.chats_for_admin(conn, 'P1')
        self.assertEqual([(c['name'], bool(c['current'])) for c in chats], [('Juma', False)])


if __name__ == '__main__':
    unittest.main()
