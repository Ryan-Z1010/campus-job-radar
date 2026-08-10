import json
import unittest
from pathlib import Path

from job_radar.config import load_sources


class Batch13MarketInfrastructureConfigTests(unittest.TestCase):
    CONFIG = Path("configs/sources.batch13.market-infrastructure.json")

    def setUp(self):
        self.data = json.loads(self.CONFIG.read_text(encoding="utf-8"))
        self.batch = self.data["sources"]

    def test_batch_has_twelve_unique_enabled_campaign_sources(self):
        self.assertEqual(len(self.batch), 12)
        self.assertEqual(len({item["id"] for item in self.batch}), 12)
        self.assertEqual(len({item["company"] for item in self.batch}), 12)
        self.assertTrue(all(item["enabled"] for item in self.batch))
        self.assertTrue(all(item["type"] == "campaign_watch" for item in self.batch))
        self.assertTrue(all(item["company_type"] == "国企" for item in self.batch))
        self.assertTrue(all(item["industry"] == "金融市场基础设施" for item in self.batch))

    def test_batch_is_loaded_and_covers_active_window_markers(self):
        sources = load_sources("configs/sources.json")
        by_id = {item["id"]: item for item in sources}
        self.assertEqual(len(by_id), 1014)
        self.assertEqual(
            sum(item["type"] == "campaign_watch" for item in sources), 953
        )
        for item in self.batch:
            source = by_id[item["id"]]
            self.assertIn("2026秋招", source["target_keywords"], item["id"])
            self.assertIn("2027春招", source["target_keywords"], item["id"])
            self.assertEqual(
                source["campaign_window"],
                {"start": "2026-07-01", "end": "2027-06-30"},
            )
            self.assertTrue(source["required_text"], item["id"])

    def test_batch_does_not_duplicate_existing_sources(self):
        data = json.loads(Path("configs/sources.json").read_text(encoding="utf-8"))
        inline_ids = {item["id"] for item in data["sources"]}
        included_ids = set()
        for include in data["includes"]:
            if include == self.CONFIG.name:
                continue
            included = json.loads(
                (Path("configs") / include).read_text(encoding="utf-8")
            )
            included_ids.update(item["id"] for item in included["sources"])
        self.assertEqual(inline_ids & set(item["id"] for item in self.batch), set())
        self.assertEqual(included_ids & set(item["id"] for item in self.batch), set())


if __name__ == "__main__":
    unittest.main()
