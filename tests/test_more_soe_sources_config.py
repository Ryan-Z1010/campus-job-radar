import json
import unittest
from pathlib import Path


class MoreSoeSourcesConfigTests(unittest.TestCase):
    def test_twenty_more_soe_sources_are_enabled_and_distinct(self):
        path = Path(__file__).parents[1] / "configs" / "sources.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources = {item["id"]: item for item in payload["sources"]}
        expected = {
            "china_coal_campus_2027": ("https://www.chinacoal.com/", "央企"),
            "china_railway_group_campus_2027": ("https://www.crecg.com/", "央企"),
            "china_tourism_group_campus_2027": ("https://www.ctg.cn/", "央企"),
            "china_huadian_campus_2027": ("https://www.chd.com.cn/", "央企"),
            "sinopharm_campus_2027": ("https://www.sinopharm.com/", "央企"),
            "cncec_campus_2027": ("https://www.cncec.com.cn/", "央企"),
            "china_gold_campus_2027": ("https://www.cngold.org.cn/", "央企"),
            "shanghai_jianke_consulting_campus_2027": (
                "https://www.gzw.sh.gov.cn/shgzw_xxgk_cqzp/20260525/aaaf1e33b4c042e087b15f638400ccd8.html",
                "国企",
            ),
            "shenzhen_tagen_campus_2027": ("https://www.tagen.cn/", "国企"),
            "shenzhen_seg_campus_2027": ("https://www.seg.com.cn/seg/index.html", "国企"),
            "shenzhen_talent_group_campus_2027": ("https://www.szhr.com/szhr/index.html", "国企"),
            "shenzhen_environment_water_campus_2027": (
                "https://cg.sz-water.com.cn/about.jhtml",
                "国企",
            ),
            "guangzhou_city_investment_campus_2027": ("https://www.gzci.net/index.aspx", "国企"),
            "travelsky_campus_2027": ("https://www.travelsky.cn/", "央企"),
            "beijing_construction_engineering_campus_2027": (
                "https://campus.51job.com/bcegc2025/info.html",
                "国企",
            ),
            "shenzhen_hightech_investment_campus_2027": (
                "https://www.szhti.com.cn/gaoxt/gaoxt-join/personnel/index.html",
                "国企",
            ),
            "sdic_campus_2027": ("https://www.sdic.com.cn/", "央企"),
            "pipechina_campus_2027": ("https://www.pipechina.com.cn/", "央企"),
            "aecc_campus_2027": ("https://www.aecc.cn/", "央企"),
            "dongfang_electric_campus_2027": ("https://www.dongfang.com/", "央企"),
        }
        self.assertTrue(set(expected).issubset(sources))
        for source_id, (homepage, company_type) in expected.items():
            source = sources[source_id]
            self.assertTrue(source["enabled"], source_id)
            self.assertEqual(source["type"], "campaign_watch", source_id)
            self.assertEqual(source["homepage"], homepage, source_id)
            self.assertTrue(source["required_text"], source_id)
            self.assertTrue(source["target_keywords"], source_id)
            self.assertEqual(source["company_type"], company_type, source_id)
            self.assertEqual(source["graduation_years"], [2027], source_id)

        existing_names = {
            item.get("company")
            for item in payload["sources"]
            if item.get("id") not in expected and item.get("company")
        }
        additional_names = {sources[source_id]["company"] for source_id in expected}
        self.assertEqual(len(additional_names), 20)
        self.assertTrue(additional_names.isdisjoint(existing_names))


if __name__ == "__main__":
    unittest.main()
