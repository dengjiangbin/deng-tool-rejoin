import os
import tempfile
import unittest
from pathlib import Path

from agent.lockfile import LockError, LockManager, read_pid


class LockfileTests(unittest.TestCase):
    def test_duplicate_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manager = LockManager(pid_path=tmp_path / "agent.pid", lock_path=tmp_path / "agent.lock")
            manager.acquire()
            try:
                self.assertEqual(read_pid(manager.pid_path), os.getpid())
                with self.assertRaises(LockError):
                    LockManager(pid_path=manager.pid_path, lock_path=manager.lock_path).acquire()
            finally:
                manager.release()
            self.assertFalse(manager.pid_path.exists())
            self.assertFalse(manager.lock_path.exists())

    def test_stale_lock_is_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pid_path = tmp_path / "agent.pid"
            lock_path = tmp_path / "agent.lock"
            pid_path.write_text("999999999\n", encoding="utf-8")
            lock_path.write_text('{"product":"DENG Tool: Rejoin","pid":999999999}\n', encoding="utf-8")
            manager = LockManager(pid_path=pid_path, lock_path=lock_path)
            manager.acquire()
            try:
                self.assertEqual(read_pid(pid_path), os.getpid())
            finally:
                manager.release()


if __name__ == "__main__":
    unittest.main()
