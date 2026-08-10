import json
import unittest
from pathlib import Path

from job_radar.config import load_sources


class Batch15HotCompaniesConfigTests(unittest.TestCase):
    ROOT = Path(__file__).parents[1]
    CONFIG = ROOT / "configs" / "sources.batch15.hot-companies.json"
    BASE = ROOT / "configs" / "sources.json"

    def setUp(self):
        self.batch = json.loads(self.CONFIG.read_text(encoding="utf-8"))["sources"]

    def test_batch_has_one_hundred_unique_enabled_sources(self):
        self.assertEqual(len(self.batch), 100)
        self.assertEqual(len({item["id"] for item in self.batch}), 100)
        self.assertEqual(len({item["company"] for item in self.batch}), 100)
        self.assertTrue(all(item["enabled"] for item in self.batch))
        self.assertTrue(all(item["type"] == "campaign_watch" for item in self.batch))
        self.assertTrue(all(item["company_type"] in {"私企", "国企", "外企"} for item in self.batch))
        self.assertTrue(all(item.get("homepage") and item.get("fallback_homepage") for item in self.batch))

    def test_batch_is_disjoint_from_all_existing_companies(self):
        data = json.loads(self.BASE.read_text(encoding="utf-8"))
        existing = {item.get("company") for item in data["sources"] if item.get("company")}
        for include in data.get("includes", []):
            if include == self.CONFIG.name:
                continue
            included = json.loads((self.ROOT / "configs" / include).read_text(encoding="utf-8"))
            existing.update(item.get("company") for item in included["sources"] if item.get("company"))
        self.assertTrue({item["company"] for item in self.batch}.isdisjoint(existing))

    def test_loader_includes_active_window_markers(self):
        loaded = load_sources(str(self.BASE))
        by_id = {item["id"]: item for item in loaded}
        self.assertEqual(len(by_id), 647)
        self.assertEqual(sum(item["type"] == "campaign_watch" for item in loaded), 586)
        for item in self.batch:
            source = by_id[item["id"]]
            self.assertIn("2026秋招", source["target_keywords"], item["id"])
            self.assertIn("2027春招", source["target_keywords"], item["id"])
            self.assertEqual(source["campaign_window"], {"start": "2026-07-01", "end": "2027-06-30"})
            self.assertTrue(source["fallback_required_text"], item["id"])


if __name__ == "__main__":
    unittest.main()
