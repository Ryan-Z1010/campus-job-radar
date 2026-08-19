import json
import unittest
from pathlib import Path


class FinalSourcesConfigTests(unittest.TestCase):
    def test_final_ten_sources_are_enabled_and_use_official_pages(self):
        path = Path(__file__).parents[1] / "configs" / "sources.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources = {item["id"]: item for item in payload["sources"]}
        expected = {
            "sap_china_campus": "https://jobs.sap.com/go/China/8807101/",
            "tesla_china_campus": "https://www.tesla.com/careers",
            "state_grid_campus": "https://zhaopin.sgcc.com.cn/",
            "cnpc_campus": "https://trxf.cnpc.com.cn/content/h/content_44239.shtml",
            "sinopec_campus": "http://job.sinopec.com/",
            "cnooc_campus": "https://cnooc.zhaopin.com/notice/index.html",
            "baidu_campus_2027": "https://talent.baidu.com/jobs/campus",
            "bytedance_campus_2027": "https://jobs.bytedance.com/campus/",
            "jd_campus_2027": "https://zhaopin.jd.com/",
            "meituan_campus_2027": "https://zhaopin.meituan.com/",
        }
        self.assertTrue(set(expected).issubset(sources))
        for source_id, homepage in expected.items():
            source = sources[source_id]
            self.assertTrue(source["enabled"], source_id)
            self.assertEqual(source["type"], "campaign_watch", source_id)
            self.assertEqual(source["homepage"], homepage, source_id)
            self.assertTrue(source["required_text"], source_id)
            self.assertTrue(source["target_keywords"], source_id)
            self.assertEqual(len(source["graduation_years"]), 1, source_id)
            self.assertEqual(source["graduation_years"][0], 2027, source_id)
        self.assertEqual(
            sources["meituan_campus_2027"]["collector"],
            "meituan_official_campus",
        )
        self.assertTrue(sources["meituan_campus_2027"]["daily_monitor"])


if __name__ == "__main__":
    unittest.main()
