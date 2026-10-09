import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from delivery import email_retry


class EmailRetryTests(unittest.TestCase):
    def test_queue_deduplicates_pending_notification(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            email_retry, "QUEUE_DIR", Path(temp_dir)
        ):
            first = email_retry.queue_email("Alert", "Body", kind="intraday", dedupe_key="same")
            second = email_retry.queue_email("Alert", "Body", kind="intraday", dedupe_key="same")

            self.assertEqual(first, second)
            self.assertEqual(len(list(Path(temp_dir).glob("*.json"))), 1)

    def test_sent_retry_promotes_queued_alert_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(json.dumps({
                "queued_alert_ids": ["A-1"],
                "sent_alert_ids": [],
            }), encoding="utf-8")
            payload = {
                "metadata": {
                    "alert_state_path": str(state_path),
                    "alert_ids": ["A-1"],
                }
            }

            email_retry.finalize_notification_state(payload, "sent")
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertNotIn("A-1", state["queued_alert_ids"])
        self.assertIn("A-1", state["sent_alert_ids"])


if __name__ == "__main__":
    unittest.main()
