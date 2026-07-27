import json
import unittest
from pathlib import Path
from unittest.mock import patch

from job_radar.collectors import (
    CampaignWatchCollector,
    ChinaSouthernPowerGridCollector,
    JsonApiCollector,
    NoticeJsonCollector,
    WebNoticeCollector,
    ZhaopinCampusCompanyCollector,
)


class CollectorTests(unittest.TestCase):
    @patch("job_radar.collectors.fetch_bytes")
    def test_notice_json_filters_and_maps_campaign_announcements(self, fetch):
        fixture = (
            Path(__file__).parent / "fixtures" / "china_mobile_notices.json"
        ).read_bytes()
        fetch.return_value = fixture
        source = {
            "id": "china_mobile",
            "name": "中国移动",
            "type": "notice_json",
            "homepage": "https://job.10086.cn/",
            "url": "https://job.10086.cn/personal/notice/notices.json",
            "list_path": "cData.list",
            "company": "中国移动",
            "company_prefix": "中国移动",
            "company_type": "央企",
            "location": "待核对",
            "location_map": {"广东": "广东省"},
            "target_keywords": ["2027届校园招聘", "2027届校招"],
            "exclude_keywords": ["实习", "社会招聘", "录用结果"],
            "description": "请核对人工智能、数据和算法岗位。",
            "education": "应届毕业生，具体要求以公告为准",
            "graduation_years": [2027],
        }

        jobs = NoticeJsonCollector(source).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "notice-2027-campus")
        self.assertEqual(jobs[0].title, "广东公司 2027届校园招聘正式启动")
        self.assertEqual(jobs[0].company, "中国移动广东公司")
        self.assertEqual(jobs[0].company_type, "央企")
        self.assertEqual(jobs[0].location, "广东省")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].published_at, "2026-08-20 09:00:00")
        self.assertEqual(jobs[0].deadline, "2026-10-31 23:59:59")
        self.assertEqual(
            jobs[0].url,
            "https://job.10086.cn/personal/notice/detail.html?id=2027",
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_notice_json_returns_empty_before_target_campaign(self, fetch):
        fetch.return_value = json.dumps(
            {
                "cData": {
                    "list": [
                        {
                            "_orderId": "notice-2026-spring",
                            "text1": "中移金科",
                            "text3": "中移金科2026春季校园招聘全面启动",
                            "detail_href": "/personal/notice/2026.html",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ).encode("utf-8")
        source = {
            "id": "china_mobile",
            "name": "中国移动",
            "type": "notice_json",
            "url": "https://example.com/notices.json",
            "list_path": "cData.list",
            "target_keywords": ["2027届校园招聘"],
        }

        self.assertEqual(NoticeJsonCollector(source).collect(), [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_notice_json_rejects_changed_schema(self, fetch):
        fetch.return_value = json.dumps({"cData": {}}).encode("utf-8")
        source = {
            "id": "china_mobile",
            "name": "中国移动",
            "type": "notice_json",
            "url": "https://example.com/notices.json",
            "list_path": "cData.list",
        }

        with self.assertRaisesRegex(
            ValueError, "公告 JSON 来源的 list_path 不存在"
        ):
            NoticeJsonCollector(source).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_campaign_watch_returns_empty_before_launch(self, fetch):
        fetch.return_value = (
            "<main><h2>招聘信息</h2><p>校园招聘敬请期待</p></main>"
            '<script>const nextCampaign = "2027校园招聘"</script>'
        ).encode("utf-8")
        source = {
            "id": "gac",
            "name": "广汽集团",
            "type": "campaign_watch",
            "homepage": "https://example.com/talent",
            "required_text": "招聘信息",
            "target_keywords": ["2027校园招聘"],
            "title": "广汽集团2027校园招聘已启动",
        }

        jobs = CampaignWatchCollector(source).collect()

        self.assertEqual(jobs, [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_campaign_watch_emits_official_launch_link_once_keyword_appears(
        self, fetch
    ):
        fetch.return_value = (
            '<main><h2>招聘信息</h2><a href="https://campus.example.com/2027">'
            "广汽集团2027 届校园招聘</a></main>"
        ).encode("utf-8")
        source = {
            "id": "gac",
            "name": "广汽集团",
            "type": "campaign_watch",
            "homepage": "https://example.com/talent",
            "required_text": "招聘信息",
            "target_keywords": ["2027届校园招聘"],
            "link_keywords": ["2027"],
            "external_id": "gac-campus-2027-launch",
            "title": "广汽集团2027校园招聘已启动",
            "company": "广汽集团",
            "company_type": "国企",
            "location": "广州",
            "graduation_years": [2027],
        }

        jobs = CampaignWatchCollector(source).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "gac-campus-2027-launch")
        self.assertEqual(
            jobs[0].url, "https://campus.example.com/2027"
        )
        self.assertEqual(jobs[0].graduation_years, [2027])

    @patch("job_radar.collectors.fetch_bytes")
    def test_campaign_watch_ignores_previous_china_telecom_campaign(self, fetch):
        fetch.return_value = (
            "<main><p>中国电信集团有限公司携下属分子公司和专业机构，"
            "正式启动2026年度春季校园招聘活动。</p></main>"
        ).encode("utf-8")
        source = {
            "id": "china_telecom",
            "name": "中国电信",
            "type": "campaign_watch",
            "homepage": "https://campus.example.com/chinatelecom/about.html",
            "required_text": "中国电信集团有限公司",
            "target_keywords": [
                "2027年度校园招聘",
                "2027届校园招聘",
                "2027秋季校园招聘",
            ],
            "title": "中国电信2027校园招聘已启动",
        }

        self.assertEqual(CampaignWatchCollector(source).collect(), [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_csg_anonymous_session_and_job_mapping(self, fetch):
        fixture = (
            Path(__file__).parent / "fixtures" / "csg_search_response.json"
        ).read_bytes()
        fetch.side_effect = [
            json.dumps(
                {"code": 200, "data": {"access_token": "temporary-token"}}
            ).encode("utf-8"),
            fixture,
        ]
        source = {
            "id": "csg",
            "name": "中国南方电网",
            "type": "csg_api",
            "homepage": "https://zhaopin.csg.cn/",
            "guest_token_url": "https://example.com/guest",
            "url": "https://example.com/search",
            "company": "中国南方电网",
            "company_type": "央企",
            "request_json": {"pageSize": 100, "keyword": ""},
        }

        jobs = ChinaSouthernPowerGridCollector(source).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "人工智能算法工程师")
        self.assertEqual(jobs[0].company, "南方电网数字电网集团有限公司")
        self.assertEqual(jobs[0].location, "广东省/广州市")
        self.assertEqual(jobs[0].education, "硕士研究生")
        self.assertEqual(jobs[0].external_id, "example-post-001")
        self.assertEqual(
            jobs[0].url,
            "https://zhaopin.csg.cn/#/post-list-detail"
            "?gobackUrl=/job-list&postId=example-post-001&canback=no",
        )
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(
            fetch.call_args_list[0].kwargs,
            {"method": "POST", "json_body": {}, "headers": None},
        )
        self.assertEqual(
            fetch.call_args_list[1].kwargs["headers"],
            {"Authorization": "Bearer temporary-token"},
        )
        self.assertEqual(
            fetch.call_args_list[1].kwargs["json_body"]["pageNo"], 1
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_csg_rejects_failed_anonymous_session_without_exposing_body(
        self, fetch
    ):
        fetch.return_value = json.dumps(
            {
                "code": 500,
                "message": "temporary-token-should-not-be-reported",
            }
        ).encode("utf-8")
        source = {"id": "csg", "name": "中国南方电网", "type": "csg_api"}

        with self.assertRaisesRegex(
            ValueError, r"南方电网匿名会话失败（code=500）"
        ) as raised:
            ChinaSouthernPowerGridCollector(source).collect()

        self.assertNotIn(
            "temporary-token-should-not-be-reported", str(raised.exception)
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_csg_empty_result_is_successful(self, fetch):
        fetch.side_effect = [
            json.dumps(
                {"code": 200, "data": {"access_token": "temporary-token"}}
            ).encode("utf-8"),
            json.dumps(
                {
                    "code": 200,
                    "message": "操作成功",
                    "data": {"pageNo": 0, "count": 0, "list": []},
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        ]
        source = {"id": "csg", "name": "中国南方电网", "type": "csg_api"}

        jobs = ChinaSouthernPowerGridCollector(source).collect()

        self.assertEqual(jobs, [])
        self.assertEqual(fetch.call_count, 2)

    @patch("job_radar.collectors.fetch_bytes")
    def test_post_json_api_mapping_and_url_template(self, fetch):
        fetch.return_value = json.dumps(
            {
                "list": [
                    {
                        "pkPublish": "abc123",
                        "jobName": "数据分析师",
                        "orgName": "南航测试单位",
                        "workPlace": "广东广州",
                        "publishDate": "2026-07-24",
                        "endDate": "2026-09-30",
                        "jobType": "3",
                    }
                ]
            }
        ).encode("utf-8")
        source = {
            "id": "csair",
            "name": "中国南方航空",
            "type": "json_api",
            "url": "https://example.com/api",
            "method": "POST",
            "request_json": {"jobType": "3"},
            "list_path": "list",
            "company_type": "央企",
            "field_map": {
                "external_id": "pkPublish",
                "title": "jobName",
                "company": "orgName",
                "location": "workPlace",
                "published_at": "publishDate",
                "deadline": "endDate",
            },
            "url_template": "https://example.com/job?id={pkPublish}&type={jobType}",
        }
        jobs = JsonApiCollector(source).collect()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "数据分析师")
        self.assertEqual(jobs[0].company_type, "央企")
        self.assertEqual(
            jobs[0].url, "https://example.com/job?id=abc123&type=3"
        )
        fetch.assert_called_once_with(
            source["url"],
            method="POST",
            json_body={"jobType": "3"},
            headers=None,
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_web_notice_requires_page_marker(self, fetch):
        fetch.return_value = "<title>广汽集团2027校园招聘</title>".encode("utf-8")
        source = {
            "id": "gac_notice",
            "name": "广汽集团",
            "type": "web_notice",
            "homepage": "https://example.com/",
            "title": "广汽集团2027校园招聘",
            "required_text": "广汽集团2027校园招聘",
            "url": "https://example.com/campus/2027",
            "company": "广汽集团",
            "company_type": "国企",
            "location": "广州",
            "graduation_years": [2027],
        }
        jobs = WebNoticeCollector(source).collect()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].url, "https://example.com/campus/2027")

    @patch("job_radar.collectors.fetch_bytes")
    def test_zhaopin_company_filters_cycle_and_maps_target_jobs(self, fetch):
        fetch.return_value = (
            Path(__file__).parent / "fixtures" / "zhaopin_unicom_company.html"
        ).read_bytes()
        source = {
            "id": "china_unicom_guangdong",
            "name": "中国联通广东省分公司",
            "type": "zhaopin_campus_company",
            "homepage": (
                "https://xiaoyuan.zhaopin.com/company/"
                "KA0145093017D90000138000"
            ),
            "company_number": "KA0145093017D90000138000",
            "company": "中国联通广东省分公司",
            "company_type": "央企",
            "min_first_published_at": "2026-07-01",
            "work_types": ["校园"],
            "include_keywords": ["AI", "人工智能", "数据", "算法"],
            "exclude_keywords": ["销售", "客服"],
            "graduation_years": [2027],
        }

        jobs = ZhaopinCampusCompanyCollector(source).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "CC-2027-DATA-001")
        self.assertEqual(jobs[0].title, "AI数据开发工程师")
        self.assertEqual(jobs[0].company, "中国联通广东省分公司")
        self.assertEqual(jobs[0].company_type, "央企")
        self.assertEqual(jobs[0].location, "广东省/广州市/海珠区")
        self.assertEqual(jobs[0].education, "硕士")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].published_at, "2026-08-20 09:00:00")
        self.assertEqual(jobs[0].deadline, "2026-10-31 23:59:59")
        self.assertIn("ETL", jobs[0].description)
        self.assertEqual(jobs[0].url, source["homepage"])

    @patch("job_radar.collectors.fetch_bytes")
    def test_zhaopin_company_empty_job_list_is_successful(self, fetch):
        fetch.return_value = (
            "<script>window.__INITIAL_DATA__ = "
            '{"company":{"recruitingPositionsState":{"count":0,"list":[]}}};'
            "</script>"
        ).encode("utf-8")
        source = {
            "id": "unicom",
            "name": "中国联通广东省分公司",
            "type": "zhaopin_campus_company",
            "homepage": "https://example.com/company",
        }

        self.assertEqual(ZhaopinCampusCompanyCollector(source).collect(), [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_zhaopin_company_rejects_changed_page_schema(self, fetch):
        fetch.return_value = "<main>校园招聘</main>".encode("utf-8")
        source = {
            "id": "unicom",
            "name": "中国联通广东省分公司",
            "type": "zhaopin_campus_company",
            "homepage": "https://example.com/company",
        }

        with self.assertRaisesRegex(
            ValueError, "智联校园公司页缺少公开初始数据"
        ):
            ZhaopinCampusCompanyCollector(source).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_zhaopin_company_rejects_partial_first_page(self, fetch):
        fetch.return_value = (
            "<script>window.__INITIAL_DATA__ = "
            '{"company":{"recruitingPositionsState":{"count":21,"list":[]}}};'
            "</script>"
        ).encode("utf-8")
        source = {
            "id": "unicom",
            "name": "中国联通广东省分公司",
            "type": "zhaopin_campus_company",
            "homepage": "https://example.com/company",
        }

        with self.assertRaisesRegex(ValueError, "只返回了部分岗位"):
            ZhaopinCampusCompanyCollector(source).collect()


if __name__ == "__main__":
    unittest.main()
