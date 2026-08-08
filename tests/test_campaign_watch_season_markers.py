import unittest

from job_radar.config import load_sources


class CampaignWatchSeasonMarkerTests(unittest.TestCase):
    def test_default_campaign_watch_includes_2026_autumn_and_2027_cohort_markers(self):
        sources = load_sources("configs/sources.json")
        source = next(
            item for item in sources if item["id"] == "crgroup_campus_2027"
        )

        self.assertIn("2026秋招", source["target_keywords"])
        self.assertIn("2026届秋季校园招聘", source["target_keywords"])
        self.assertIn("2027届校园招聘", source["target_keywords"])
        self.assertIn("近期秋招/校园招聘", source["title"])

    def test_explicit_campaign_keywords_keep_their_markers_and_add_2026_autumn(self):
        sources = load_sources("configs/sources.json")
        source = next(
            item for item in sources if item["id"] == "china_telecom"
        )

        self.assertIn("2026秋招", source["target_keywords"])
        self.assertIn("2026届秋季校园招聘", source["target_keywords"])
        self.assertIn("2027秋招", source["target_keywords"])


if __name__ == "__main__":
    unittest.main()
