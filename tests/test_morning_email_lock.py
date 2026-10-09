import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.morning_email import send_morning_brief_email


class MorningEmailLockTests(unittest.TestCase):
    def test_duplicate_morning_run_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "morning.lock"
            lock_file = lock_path.open("a+", encoding="utf-8")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with patch("agents.morning_email.LOCK_PATH", lock_path):
                    result = send_morning_brief_email(dry_run=True)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()

        self.assertTrue(result["skipped"])
        self.assertFalse(result["sent"])


if __name__ == "__main__":
    unittest.main()
