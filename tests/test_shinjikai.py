import json
import tempfile
import time
import unittest
from pathlib import Path

import Shinjikai
from Shinjikai import FetchResult, FetchStatus, ScrapeConfig, ScrapeError


def word(word_id: int) -> dict:
    return {"Word": {"Id": word_id, "Kana": f"word-{word_id}", "Meanings": []}}


class ScraperTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir()
        self.state_path = self.root / "state.json"
        self.original_image_dir = Shinjikai.IMAGE_DIR
        Shinjikai.IMAGE_DIR = self.root / "images"

    def tearDown(self):
        Shinjikai.IMAGE_DIR = self.original_image_dir
        self.temporary_directory.cleanup()

    def config(self, **overrides) -> ScrapeConfig:
        values = {
            "max_workers": 3,
            "batch_size": 4,
            "max_scan": 20,
            "end_missing_threshold": 3,
            "requests_per_second": 1000,
        }
        values.update(overrides)
        return ScrapeConfig(**values)

    def seed(self, *word_ids: int) -> None:
        path = self.data_dir / "data_0.jsonl"
        with path.open("w", encoding="utf-8") as output:
            for word_id in word_ids:
                output.write(json.dumps(word(word_id)) + "\n")

    def test_fetch_batch_returns_numeric_order_not_completion_order(self):
        def fetcher(word_id):
            time.sleep((4 - word_id) * 0.002)
            return FetchResult(word_id, FetchStatus.MISSING)

        results = Shinjikai.fetch_batch([1, 2, 3], fetcher, max_workers=3)
        self.assertEqual([result.word_id for result in results], [1, 2, 3])

    def test_rate_limit_fails_instead_of_becoming_database_end(self):
        self.seed(1)

        def fetcher(word_id):
            if word_id == 2:
                return FetchResult(word_id, FetchStatus.RATE_LIMITED, detail="HTTP 429")
            return FetchResult(word_id, FetchStatus.MISSING)

        with self.assertRaisesRegex(ScrapeError, "rate_limited"):
            Shinjikai.run_scrape(
                self.config(), fetcher=fetcher, data_dir=self.data_dir, state_path=self.state_path
            )
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIn(2, state["pending_retry_ids"])

    def test_repairs_historical_gaps_before_scanning_forward(self):
        self.seed(1, 3)
        calls = []

        def fetcher(word_id):
            calls.append(word_id)
            if word_id == 2:
                return FetchResult(word_id, FetchStatus.FOUND, word(word_id))
            if word_id == 3:
                return FetchResult(word_id, FetchStatus.FOUND, word(word_id))
            return FetchResult(word_id, FetchStatus.MISSING)

        summary = Shinjikai.run_scrape(
            self.config(), fetcher=fetcher, data_dir=self.data_dir, state_path=self.state_path
        )
        self.assertEqual(calls[0], 2)
        self.assertEqual(summary["entries_found"], 1)
        self.assertIn(2, Shinjikai.read_finished_ids(self.data_dir))

    def test_end_requires_a_successful_known_word_health_check(self):
        self.seed(1)

        def fetcher(word_id):
            if word_id == 1:
                return FetchResult(word_id, FetchStatus.BLOCKED, detail="HTTP 403")
            return FetchResult(word_id, FetchStatus.MISSING)

        with self.assertRaisesRegex(ScrapeError, "could not be verified"):
            Shinjikai.run_scrape(
                self.config(), fetcher=fetcher, data_dir=self.data_dir, state_path=self.state_path
            )


if __name__ == "__main__":
    unittest.main()
