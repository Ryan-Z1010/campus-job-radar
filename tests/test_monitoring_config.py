import unittest

from job_radar.config import (
    filter_sources_for_monitoring,
    load_monitoring,
    load_sources,
)


class MonitoringConfigTests(unittest.TestCase):
    def test_applied_companies_are_excluded_from_source_pool(self):
        sources = load_sources("configs/sources.json")
        monitoring = load_monitoring("configs/monitoring.json")
        filtered = filter_sources_for_monitoring(sources, monitoring)
        filtered_ids = {source["id"] for source in filtered}

        for source_id in (
            "tencent_china",
            "meituan_campus_2027",
            "kpmg_hot_2027",
            "kuaishou_hot_2027",
            "didi_hot_2027",
            "dji",
            "bytedance_campus_2027",
            "alibaba_hot_2027",
            "xpeng",
            "xiaomi_hot_2027",
            "zte_future_leader",
            "zte_fresh_graduate_2027",
            "netease_game_guangzhou",
            "china_merchants_group_campus",
        ):
            self.assertNotIn(source_id, filtered_ids)

        self.assertEqual(len(sources) - len(filtered), 15)
        self.assertTrue(monitoring["daily_scan_all"])


if __name__ == "__main__":
    unittest.main()
