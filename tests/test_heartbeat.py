import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tool.update_heartbeat import update_heartbeat


class HeartbeatTests(unittest.TestCase):
    def test_only_updates_after_minimum_age(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat.json"
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self.assertTrue(update_heartbeat(path, timedelta(days=30), start))
            self.assertFalse(update_heartbeat(path, timedelta(days=30), start + timedelta(days=29)))
            self.assertTrue(update_heartbeat(path, timedelta(days=30), start + timedelta(days=30)))
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                value["last_successful_automation_check"],
                (start + timedelta(days=30)).isoformat(),
            )


if __name__ == "__main__":
    unittest.main()
