import json
import unittest
from pathlib import Path

from job_radar.config import load_sources


class Batch100SourcesConfigTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parents[1]
        self.root = root
        self.batch_path = root / "configs" / "sources.batch100.json"
        self.base_path = root / "configs" / "sources.json"
        self.batch = json.loads(self.batch_path.read_text(encoding="utf-8"))["sources"]

    def test_batch_has_one_hundred_unique_enabled_sources(self):
        self.assertEqual(len(self.batch), 100)
        self.assertEqual(len({item["id"] for item in self.batch}), 100)
        self.assertEqual(len({item["company"] for item in self.batch}), 100)
        self.assertTrue(all(item["enabled"] for item in self.batch))
        self.assertTrue(all(item["type"] in {"campaign_watch", "gzrecruit_company"} for item in self.batch))
        self.assertEqual(sum(item["company_type"] == "央企" for item in self.batch), 30)
        self.assertEqual(sum(item["company_type"] == "国企" for item in self.batch), 70)

    def test_batch_companies_are_disjoint_from_existing_config(self):
        base = json.loads(self.base_path.read_text(encoding="utf-8"))["sources"]
        existing_names = {item.get("company") for item in base if item.get("company")}
        batch_names = {item["company"] for item in self.batch}
        self.assertTrue(batch_names.isdisjoint(existing_names))

    def test_loader_includes_and_normalizes_the_batch(self):
        loaded = load_sources(str(self.base_path))
        self.assertEqual(len(loaded), 440)
        by_id = {item["id"]: item for item in loaded}
        for item in self.batch:
            source = by_id[item["id"]]
            self.assertEqual(source["graduation_years"], [2027], item["id"])
            if item["type"] == "campaign_watch":
                self.assertTrue(source["target_keywords"], item["id"])
                self.assertTrue(source["title"], item["id"])
                self.assertTrue(source["description"], item["id"])
                self.assertTrue(source["education"], item["id"])


if __name__ == "__main__":
    unittest.main()
