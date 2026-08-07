import json
import unittest
from pathlib import Path


class NextTenCentralSourcesConfigTests(unittest.TestCase):
    def test_next_ten_central_sources_are_enabled_and_use_public_pages(self):
        path = Path(__file__).parents[1] / "configs" / "sources.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources = {item["id"]: item for item in payload["sources"]}
        expected = {
            "norinco_campus_2027": "http://www.norincogroup.com.cn/",
            "avic_campus_2027": "https://www.avic.com/",
            "casc_campus_2027": "https://m.spacechina.com/",
            "casic_campus_2027": "http://www.casic.com.cn/",
            "cetc_campus_2027": "https://www.cetc.com.cn/zgdk/1593022/1592495/index.html",
            "crsc_campus_2027": "https://www.crsc.cn/news/tsi_1078_14155_94223.html",
            "airchina_group_campus_2027": "https://et.airchina.com.cn/cn/about_us/recruitment/ground_crew_info/184923.shtml",
            "sinochem_campus_2027": "https://www.sinochem.com/",
            "mcc_campus_2027": "https://www.mcc.com.cn/",
            "china_datang_campus_2027": "https://zhaopin.china-cdt.com/help?sid=3",
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
            if item.get("id") not in expected and item.get("company")
        }
        additional_names = {sources[source_id]["company"] for source_id in expected}
        self.assertEqual(len(additional_names), 10)
        self.assertTrue(additional_names.isdisjoint(existing_names))


if __name__ == "__main__":
    unittest.main()
