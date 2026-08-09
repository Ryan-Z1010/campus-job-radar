import json
import unittest
from pathlib import Path

from job_radar.config import load_sources


class FourCityGapSourcesConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]
        self.batch_path = self.root / "configs" / "sources.batch45.four-city-gaps.json"
        self.base_path = self.root / "configs" / "sources.json"
        self.batch = json.loads(self.batch_path.read_text(encoding="utf-8"))["sources"]

    def test_batch_has_forty_five_unique_enabled_sources(self):
        self.assertEqual(len(self.batch), 45)
        self.assertEqual(len({item["id"] for item in self.batch}), 45)
        self.assertEqual(len({item["company"] for item in self.batch}), 45)
        self.assertTrue(all(item["enabled"] for item in self.batch))
        self.assertTrue(all(item["type"] == "campaign_watch" for item in self.batch))
        self.assertEqual(sum(item["company_type"] == "央企" for item in self.batch), 17)
        self.assertEqual(sum(item["company_type"] == "国企" for item in self.batch), 28)

    def test_batch_companies_are_disjoint_from_existing_sources(self):
        base = json.loads(self.base_path.read_text(encoding="utf-8"))["sources"]
        previous = []
        for name in ("sources.batch100.json", "sources.batch200.json"):
            previous.extend(
                json.loads((self.root / "configs" / name).read_text(encoding="utf-8"))["sources"]
            )
        existing = {item.get("company") for item in [*base, *previous] if item.get("company")}
        self.assertTrue({item["company"] for item in self.batch}.isdisjoint(existing))

    def test_loader_includes_and_normalizes_batch(self):
        loaded = load_sources(str(self.base_path))
        self.assertEqual(len(loaded), 497)
        by_id = {item["id"]: item for item in loaded}
        for item in self.batch:
            source = by_id[item["id"]]
            self.assertEqual(source["graduation_years"], [2027], item["id"])
            self.assertTrue(source["target_keywords"], item["id"])
            self.assertTrue(source["title"], item["id"])
            self.assertTrue(source["fallback_homepage"], item["id"])


if __name__ == "__main__":
    unittest.main()
