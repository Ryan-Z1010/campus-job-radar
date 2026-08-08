import unittest

from job_radar.config import load_sources


class CampaignWatchSeasonMarkerTests(unittest.TestCase):
    def test_default_campaign_watch_covers_the_full_active_window(self):
        sources = load_sources("configs/sources.json")
        source = next(
            item for item in sources if item["id"] == "crgroup_campus_2027"
        )

        for marker in (
            "2026年下半年校园招聘",
            "2026秋招",
            "2026-2027年校园招聘",
            "2027春招",
            "2027年上半年校园招聘",
            "2027届校园招聘",
        ):
            self.assertIn(marker, source["target_keywords"])
        self.assertEqual(
            source["campaign_window"],
            {"start": "2026-07-01", "end": "2027-06-30"},
        )
        self.assertIn("近期秋招/校园招聘", source["title"])

    def test_explicit_campaign_keywords_keep_their_markers_and_add_window_markers(self):
        sources = load_sources("configs/sources.json")
        source = next(
            item for item in sources if item["id"] == "china_telecom"
        )

        self.assertIn("2026年下半年招聘", source["target_keywords"])
        self.assertIn("2026秋招", source["target_keywords"])
        self.assertIn("2027春招", source["target_keywords"])
        self.assertIn("2027秋招", source["target_keywords"])


if __name__ == "__main__":
    unittest.main()
