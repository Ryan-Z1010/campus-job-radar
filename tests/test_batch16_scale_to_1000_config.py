import json
import unittest
from pathlib import Path

from job_radar.config import load_sources


class Batch16ScaleTo1000ConfigTests(unittest.TestCase):
    ROOT = Path(__file__).parents[1]
    CONFIG = ROOT / "configs" / "sources.batch16.scale-to-1000.json"
    BASE = ROOT / "configs" / "sources.json"

    def test_group_config_has_exactly_367_names(self):
        data = json.loads(self.CONFIG.read_text(encoding="utf-8"))
        names = [name for group in data["company_groups"] for name in group["names"]]
        self.assertEqual(len(names), 367)
        self.assertEqual(len(set(names)), 367)
        self.assertEqual(len(data["company_groups"]), 7)

    def test_loader_expands_to_1000_company_pool_without_duplicates(self):
        loaded = load_sources(str(self.BASE))
        self.assertEqual(len(loaded), 1014)
        self.assertEqual(sum(item["type"] == "campaign_watch" for item in loaded), 953)
        new = [item for item in loaded if item["id"].startswith("b16_")]
        self.assertEqual(len(new), 367)
        names = [item["company"] for item in new]
        self.assertEqual(len(set(names)), 367)
        self.assertEqual(sum(item["company_type"] in {"央企", "国企"} for item in new), 320)
        self.assertEqual(sum(item["company_type"] == "私企" for item in new), 32)
        self.assertEqual(sum(item["company_type"] == "外企" for item in new), 15)
        self.assertTrue(all(item["campaign_window"] == {"start": "2026-07-01", "end": "2027-06-30"} for item in new))
        self.assertTrue(all("2026秋招" in item["target_keywords"] and "2027春招" in item["target_keywords"] for item in new))

    def test_expanded_names_are_disjoint_from_previous_pool(self):
        loaded = load_sources(str(self.BASE))
        old_names = {item.get("company") for item in loaded if item.get("company") and not item["id"].startswith("b16_")}
        new_names = {item["company"] for item in loaded if item["id"].startswith("b16_")}
        self.assertTrue(old_names.isdisjoint(new_names))


if __name__ == "__main__":
    unittest.main()
