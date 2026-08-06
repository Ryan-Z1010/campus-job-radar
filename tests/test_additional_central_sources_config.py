import json
import unittest
from pathlib import Path


class AdditionalCentralSourcesConfigTests(unittest.TestCase):
    def test_ten_additional_central_sources_are_enabled_and_distinct(self):
        path = Path(__file__).parents[1] / "configs" / "sources.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources = {item["id"]: item for item in payload["sources"]}
        expected = {
            "cscec_campus_2027": "https://recruit.cscec.com/",
            "china_railway_campus_2027": "https://rczp.china-railway.com.cn/",
            "crrc_campus_2027": "https://crrc.hotjob.cn/",
            "china_post_campus_2027": "https://chinapost2026.zhaopin.com/sky/index.html",
            "picc_campus_2027": "https://picc.zhiye.com/custom/campus",
            "spic_campus_2027": "https://zhaopin.spic.com.cn/",
            "cnnc_campus_2027": "https://cnnc.zhiye.com/xiaoyuan",
            "cgn_campus_2027": "https://cgn.hotjob.cn/",
            "chnenergy_campus_2027": "https://zhaopin.chnenergy.com.cn/",
            "huaneng_campus_2027": "https://www.chng.com.cn/index.html",
        }
        self.assertTrue(set(expected).issubset(sources))
        for source_id, homepage in expected.items():
            source = sources[source_id]
            self.assertTrue(source["enabled"], source_id)
            self.assertEqual(source["type"], "campaign_watch", source_id)
            self.assertEqual(source["homepage"], homepage, source_id)
            self.assertTrue(source["required_text"], source_id)
            self.assertTrue(source["target_keywords"], source_id)
            self.assertEqual(source["company_type"], "央企", source_id)
            self.assertEqual(source["graduation_years"], [2027], source_id)

        existing_names = {
            item.get("company")
            for item in payload["sources"]
            if item.get("id") not in expected
            and item.get("company")
        }
        additional_names = {sources[source_id]["company"] for source_id in expected}
        self.assertTrue(additional_names.isdisjoint(existing_names))


if __name__ == "__main__":
    unittest.main()
