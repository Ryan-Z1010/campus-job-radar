import json
import unittest
from pathlib import Path

from job_radar.config import load_sources


class Batch200SourcesConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]
        self.batch_path = self.root / "configs" / "sources.batch200.json"
        self.base_path = self.root / "configs" / "sources.json"
        self.batch = json.loads(self.batch_path.read_text(encoding="utf-8"))["sources"]

    def test_batch_has_two_hundred_unique_enabled_sources(self):
        self.assertEqual(len(self.batch), 200)
        self.assertEqual(len({item["id"] for item in self.batch}), 200)
        self.assertEqual(len({item["company"] for item in self.batch}), 200)
        self.assertTrue(all(item["enabled"] for item in self.batch))
        self.assertTrue(all(item["type"] == "campaign_watch" for item in self.batch))
        self.assertEqual(sum(item["company_type"] == "央企" for item in self.batch), 39)
        self.assertEqual(sum(item["company_type"] == "国企" for item in self.batch), 161)

    def test_batch_companies_are_disjoint_from_existing_batches(self):
        base = json.loads(self.base_path.read_text(encoding="utf-8"))["sources"]
        previous = json.loads(
            (self.root / "configs" / "sources.batch100.json").read_text(encoding="utf-8")
        )["sources"]
        existing_names = {
            item.get("company") for item in [*base, *previous] if item.get("company")
        }
        self.assertTrue(
            {item["company"] for item in self.batch}.isdisjoint(existing_names)
        )

    def test_loader_includes_all_four_hundred_forty_sources(self):
        loaded = load_sources(str(self.base_path))
        self.assertEqual(len(loaded), 1014)
        by_id = {item["id"]: item for item in loaded}
        for item in self.batch:
            source = by_id[item["id"]]
            self.assertEqual(source["graduation_years"], [2027], item["id"])
            self.assertTrue(source["target_keywords"], item["id"])
            self.assertTrue(source["title"], item["id"])

    def test_loader_assigns_official_fallback_portals(self):
        loaded = load_sources(str(self.base_path))
        by_id = {item["id"]: item for item in loaded}
        for item in self.batch:
            source = by_id[item["id"]]
            self.assertTrue(source["fallback_homepage"], item["id"])
            self.assertTrue(source["fallback_required_text"], item["id"])
        self.assertEqual(
            by_id["shougang_campus_2027"]["fallback_homepage"],
            "https://gzw.beijing.gov.cn/",
        )
        self.assertEqual(
            by_id["sigc_campus_2027"]["fallback_homepage"],
            "https://www.gzw.sh.gov.cn/",
        )
        self.assertEqual(
            by_id["jsgx_campus_2027"]["fallback_homepage"],
            "https://www.gov.cn/",
        )


if __name__ == "__main__":
    unittest.main()
