import base64
import json
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from job_radar.collectors import (
    BeisenLegacyCampusCollector,
    BeisenPortalCampaignCollector,
    CampaignWatchCollector,
    ChinaSouthernPowerGridCollector,
    GdrcGroupCollector,
    GdutCampusNoticeCollector,
    GiihgCampusCollector,
    GzRecruitCompanyCollector,
    HotjobCampusCollector,
    IguopinCompanyCollector,
    JsonApiCollector,
    NoticeJsonCollector,
    WebNoticeCollector,
    ZhaopinCampusCompanyCollector,
)


class CollectorTests(unittest.TestCase):
    @staticmethod
    def _gdrc_source():
        return {
            "id": "guangdong_communications_group",
            "name": "广东省交通集团",
            "type": "gdrc_group",
            "homepage": (
                "https://jq.gdrc.com/gqzp/"
                "position.html?type=school"
            ),
            "url": "https://jq.gdrc.com/touristApi/listJob",
            "group_id": "1004",
            "campus_flag": 0,
            "page_size": 2,
            "max_pages": 3,
            "company": "广东省交通集团有限公司",
            "company_type": "国企",
            "location": "广州",
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "min_published_at": "2026-07-01",
            "include_keywords": [
                "AI",
                "人工智能",
                "数据",
                "软件",
                "计算机",
                "智能交通",
            ],
            "exclude_keywords": ["实习", "社招", "销售", "2026届"],
            "graduation_years": [2027],
            "degree_map": {"7": "本科", "9": "硕士", "11": "博士"},
        }

    @staticmethod
    def _gdrc_page(items, total=None):
        return json.dumps(
            {
                "code": 0,
                "data": {
                    "pageData": {
                        "total": len(items) if total is None else total,
                        "list": items,
                    }
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")

    @patch("job_radar.collectors.fetch_bytes")
    def test_gdrc_group_paginates_filters_and_maps_jobs(self, fetch):
        fetch.side_effect = [
            self._gdrc_page(
                [
                    {
                        "j_id": "target-agent",
                        "gid": "1004",
                        "shzp": "0",
                        "position": "智能体开发工程师",
                        "companyname": "广东利通科技投资有限公司",
                        "address": "广州市黄埔区",
                        "detailrequirement": (
                            "负责 RAG、大模型与智能交通应用开发；"
                            "计算机相关专业硕士优先。"
                        ),
                        "degreelevel": "9",
                        "headcount": "2",
                        "recruitstartday": "20260820",
                        "recruitendday": "20260930",
                    },
                    {
                        "j_id": "old-data",
                        "gid": "1004",
                        "shzp": "0",
                        "position": "数据分析岗",
                        "companyname": "广东省交通集团有限公司",
                        "address": "广州市",
                        "detailrequirement": "负责经营数据分析。",
                        "degreelevel": "7",
                        "recruitstartday": "20260301",
                        "recruitendday": "20260630",
                    },
                ],
                total=3,
            ),
            self._gdrc_page(
                [
                    {
                        "j_id": "sales-data",
                        "gid": "1004",
                        "shzp": "0",
                        "position": "数据产品销售岗",
                        "companyname": "广东省交通集团有限公司",
                        "address": "深圳市",
                        "detailrequirement": "负责软件产品销售。",
                        "degreelevel": "7",
                        "recruitstartday": "20260821",
                        "recruitendday": "20260930",
                    }
                ],
                total=3,
            ),
        ]

        jobs = GdrcGroupCollector(self._gdrc_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "target-agent")
        self.assertEqual(jobs[0].title, "智能体开发工程师")
        self.assertEqual(jobs[0].company, "广东利通科技投资有限公司")
        self.assertEqual(jobs[0].company_type, "国企")
        self.assertEqual(jobs[0].location, "广州市黄埔区")
        self.assertIn("RAG", jobs[0].description)
        self.assertIn("招聘人数：2", jobs[0].description)
        self.assertEqual(jobs[0].education, "硕士")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].published_at, "2026-08-20")
        self.assertEqual(jobs[0].deadline, "2026-09-30")
        self.assertEqual(
            jobs[0].url,
            (
                "https://jq.gdrc.com/recruitCon/"
                "recruit-job-detail.html?id=target-agent"
            ),
        )
        self.assertEqual(fetch.call_count, 2)
        first_call = fetch.call_args_list[0]
        second_call = fetch.call_args_list[1]
        self.assertEqual(first_call.kwargs["json_body"]["gid"], "1004")
        self.assertEqual(first_call.kwargs["json_body"]["shzp"], 0)
        self.assertEqual(first_call.kwargs["json_body"]["page"], 1)
        self.assertEqual(second_call.kwargs["json_body"]["page"], 2)
        self.assertEqual(
            first_call.kwargs["headers"]["Referer"],
            self._gdrc_source()["homepage"],
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_gdrc_group_empty_result_is_successful(self, fetch):
        fetch.return_value = self._gdrc_page([], total=0)

        self.assertEqual(GdrcGroupCollector(self._gdrc_source()).collect(), [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_gdrc_group_rejects_changed_schema(self, fetch):
        fetch.return_value = json.dumps(
            {"code": 0, "data": {"pageData": {"total": 1}}}
        ).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "缺少岗位数组"):
            GdrcGroupCollector(self._gdrc_source()).collect()

    @staticmethod
    def _giihg_source():
        return {
            "id": "guangzhou_industrial_investment_group",
            "name": "广州工控集团",
            "type": "giihg_campus",
            "homepage": "https://www.giihg.com/xyzp",
            "url": "https://www.giihg.com/prod-api/api/recruit/list",
            "recruit_type": 1,
            "page_size": 2,
            "max_pages": 3,
            "company": "广州工业投资控股集团有限公司",
            "company_type": "国企",
            "location": "广州",
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "min_published_at": "2026-07-01",
            "include_keywords": [
                "AI",
                "人工智能",
                "数据",
                "软件",
                "信息技术",
                "数字化",
            ],
            "exclude_keywords": ["销售", "客服", "市场营销", "2026届"],
            "graduation_years": [2027],
        }

    @staticmethod
    def _giihg_page(items, total=None):
        return json.dumps(
            {
                "total": len(items) if total is None else total,
                "rows": items,
                "code": 200,
                "msg": "查询成功",
            },
            ensure_ascii=False,
        ).encode("utf-8")

    @patch("job_radar.collectors.fetch_bytes")
    def test_giihg_campus_paginates_filters_and_maps_jobs(self, fetch):
        fetch.side_effect = [
            self._giihg_page(
                [
                    {
                        "id": "old-data",
                        "jobName": "数据分析岗",
                        "jobContent": "<p>负责经营数据分析。</p>",
                        "jobDesc": "<p>计算机专业本科及以上。</p>",
                        "publishTime": "2026-03-01 09:00:00",
                        "type": "1",
                        "isDisplay": "1",
                        "isDel": "0",
                        "address": "广州市荔湾区",
                        "companyName": "广州工控集团",
                    },
                    {
                        "id": "target-data",
                        "jobName": "工业数据平台工程师",
                        "jobContent": (
                            "<p>负责工业互联网数据平台建设、"
                            "数据治理与智能分析。</p>"
                        ),
                        "jobDesc": (
                            "<p>计算机科学与技术、软件工程、"
                            "人工智能相关专业硕士优先。</p>"
                        ),
                        "publishTime": "2026-08-20 09:30:00",
                        "type": "1",
                        "isDisplay": "1",
                        "isDel": "0",
                        "address": "广州市荔湾区",
                        "companyName": "广州工控科技创新总院",
                    },
                ],
                total=4,
            ),
            self._giihg_page(
                [
                    {
                        "id": "sales-data",
                        "jobName": "数据产品销售岗",
                        "jobContent": "<p>负责软件产品销售。</p>",
                        "jobDesc": "<p>市场营销专业优先。</p>",
                        "publishTime": "2026-08-21 09:30:00",
                        "type": "1",
                        "isDisplay": "1",
                        "isDel": "0",
                        "address": "广州市",
                        "companyName": "广州工控集团",
                    },
                    {
                        "id": "other-city",
                        "jobName": "软件开发工程师",
                        "jobContent": "<p>负责信息系统开发。</p>",
                        "jobDesc": "<p>计算机相关专业。</p>",
                        "publishTime": "2026-08-22 09:30:00",
                        "type": "1",
                        "isDisplay": "1",
                        "isDel": "0",
                        "address": "湖南株洲",
                        "companyName": "南方宇航",
                    },
                ],
                total=4,
            ),
        ]

        jobs = GiihgCampusCollector(self._giihg_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "target-data")
        self.assertEqual(jobs[0].title, "工业数据平台工程师")
        self.assertEqual(jobs[0].company, "广州工控科技创新总院")
        self.assertEqual(jobs[0].company_type, "国企")
        self.assertEqual(jobs[0].location, "广州市荔湾区")
        self.assertIn("负责工业互联网数据平台建设", jobs[0].description)
        self.assertIn("计算机科学与技术", jobs[0].description)
        self.assertEqual(jobs[0].published_at, "2026-08-20 09:30:00")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].url, self._giihg_source()["homepage"])
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("type=1", fetch.call_args_list[0].args[0])
        self.assertIn("pageNum=2", fetch.call_args_list[1].args[0])
        self.assertEqual(
            fetch.call_args_list[0].kwargs["headers"]["Referer"],
            self._giihg_source()["homepage"],
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_giihg_campus_empty_result_is_successful(self, fetch):
        fetch.return_value = self._giihg_page([], total=0)

        self.assertEqual(GiihgCampusCollector(self._giihg_source()).collect(), [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_giihg_campus_rejects_changed_schema(self, fetch):
        fetch.return_value = json.dumps(
            {"total": 1, "code": 200, "msg": "查询成功"}
        ).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "缺少岗位数组"):
            GiihgCampusCollector(self._giihg_source()).collect()

    @staticmethod
    def _hotjob_source():
        return {
            "id": "yuexiu_group",
            "name": "越秀集团",
            "type": "hotjob_campus",
            "homepage": (
                "https://wecruit.hotjob.cn/"
                "SU-target/pb/school.html"
            ),
            "tenant_id": "SU-target",
            "company": "越秀集团",
            "company_type": "国企",
            "target_keywords": ["2027届校园招聘", "2027校园招聘"],
            "include_keywords": ["AI", "数据", "数字化", "信息技术"],
            "exclude_keywords": ["实习", "销售", "社会招聘"],
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "min_published_at": "2026-07-01",
            "graduation_years": [2027],
            "max_pages": 3,
            "url_template": (
                "https://wecruit.hotjob.cn/SU-target/pb/"
                "posDetail.html?postId={postId}&postType=campus"
            ),
        }

    @staticmethod
    def _hotjob_page(items, current_page=1, total_page=1):
        return json.dumps(
            {
                "state": "200",
                "type": "success",
                "data": {
                    "pageForm": {
                        "currentPage": current_page,
                        "totalPage": total_page,
                        "pageSize": 15,
                        "dataCount": len(items),
                        "pageData": items,
                    }
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")

    @patch("job_radar.collectors.fetch_bytes")
    def test_hotjob_campus_paginates_and_maps_target_jobs(self, fetch):
        fetch.side_effect = [
            self._hotjob_page(
                [
                    {
                        "postId": "yx-2026",
                        "postName": "管培生（数字化方向）",
                        "projectName": "2026届校园招聘",
                        "postTypeName": "IT类",
                        "company": "越秀交通",
                        "department": "数字化部",
                        "workPlaceStr": "广州市-天河区",
                        "educationStr": "硕士研究生及以上",
                        "publishFirstDate": "2026-03-18 00:00:00",
                        "endDate": "2026-06-18 23:59:59",
                    }
                ],
                current_page=1,
                total_page=2,
            ),
            self._hotjob_page(
                [
                    {
                        "postId": "yx-data-2027",
                        "postName": "数据平台工程师",
                        "projectName": "2027届校园招聘",
                        "postTypeName": "IT信息技术类",
                        "company": "越秀集团总部",
                        "department": "数字科技部",
                        "workPlaceStr": "广州市-天河区",
                        "educationStr": "硕士研究生及以上",
                        "publishFirstDate": "2026-08-20 09:30:00",
                        "endDate": "2026-10-15 23:59:59",
                    },
                    {
                        "postId": "yx-sales-2027",
                        "postName": "数据产品销售",
                        "projectName": "2027届校园招聘",
                        "postTypeName": "销售类",
                        "company": "越秀集团",
                        "workPlaceStr": "广州市",
                        "publishFirstDate": "2026-08-20 09:30:00",
                    },
                ],
                current_page=2,
                total_page=2,
            ),
        ]

        jobs = HotjobCampusCollector(self._hotjob_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "yx-data-2027")
        self.assertEqual(jobs[0].title, "数据平台工程师")
        self.assertEqual(jobs[0].company, "越秀集团总部")
        self.assertEqual(jobs[0].company_type, "国企")
        self.assertEqual(jobs[0].location, "广州市-天河区")
        self.assertEqual(jobs[0].education, "硕士研究生及以上")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].published_at, "2026-08-20 09:30:00")
        self.assertEqual(jobs[0].deadline, "2026-10-15 23:59:59")
        self.assertEqual(
            jobs[0].url,
            (
                "https://wecruit.hotjob.cn/SU-target/pb/"
                "posDetail.html?postId=yx-data-2027&postType=campus"
            ),
        )
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(
            fetch.call_args_list[1].kwargs["form_body"]["currentPage"], 2
        )
        self.assertEqual(
            fetch.call_args_list[0].kwargs["headers"]["Referer"],
            self._hotjob_source()["homepage"],
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_hotjob_campus_returns_empty_before_target_cycle(self, fetch):
        fetch.return_value = self._hotjob_page(
            [
                {
                    "postId": "yx-2026",
                    "postName": "管培生（数字化方向）",
                    "projectName": "2026届校园招聘",
                    "postTypeName": "IT类",
                    "company": "越秀交通",
                    "workPlaceStr": "广州市",
                    "publishFirstDate": "2026-03-18 00:00:00",
                }
            ]
        )

        self.assertEqual(HotjobCampusCollector(self._hotjob_source()).collect(), [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_hotjob_campus_rejects_changed_schema(self, fetch):
        fetch.return_value = json.dumps(
            {"state": "200", "data": {"pageForm": {}}}
        ).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "缺少岗位数组"):
            HotjobCampusCollector(self._hotjob_source()).collect()

    @staticmethod
    def _gdut_page(fragment):
        wrapped_html = ("H" * 17 + fragment).encode("utf-8")
        inner_base64 = base64.b64encode(wrapped_html)
        wrapped_base64 = b"Z" * 23 + inner_base64
        encoded = base64.b64encode(zlib.compress(wrapped_base64)).decode()
        return (
            '<section id="content123"></section><script>'
            '$("#content123").each(function(){'
            '$(this).replaceWith(Base64.decode(unzip("'
            + encoded
            + '").substr(23)).substr(17));});</script>'
        ).encode("utf-8")

    @staticmethod
    def _gdut_source():
        return {
            "id": "guangzhou_port_group",
            "name": "广州港集团",
            "type": "gdut_campus_notice",
            "homepage": "https://career.gdut.edu.cn/campus",
            "first_page_url": "https://career.gdut.edu.cn/campus",
            "page_url_template": (
                "https://career.gdut.edu.cn/campus/index/public/page/{page}"
            ),
            "max_pages": 3,
            "company_keywords": ["广州港"],
            "target_keywords": ["2027届校园招聘", "2027校园招聘"],
            "exclude_keywords": ["拟录用"],
            "company": "广州港集团",
            "company_type": "国企",
            "location": "广州",
            "graduation_years": [2027],
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_gdut_notice_scans_pages_and_emits_target_campaign(self, fetch):
        fetch.side_effect = [
            self._gdut_page(
                '<ul class="infoList"><li>'
                '<a href="/campus/view/id/1020700">'
                "其他公司2027届校园招聘</a></li></ul>"
            ),
            self._gdut_page(
                '<ul class="infoList"><li>'
                '<a href="/campus/view/id/1020800">'
                "广州港集团有限公司2027届校园招聘简章</a></li></ul>"
            ),
            self._gdut_page(
                '<div class="empty-container"><p>暂无数据</p></div>'
            ),
        ]

        jobs = GdutCampusNoticeCollector(self._gdut_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "1020800")
        self.assertEqual(
            jobs[0].title, "广州港集团有限公司2027届校园招聘简章"
        )
        self.assertEqual(jobs[0].company, "广州港集团")
        self.assertEqual(jobs[0].company_type, "国企")
        self.assertEqual(jobs[0].location, "广州")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            "https://career.gdut.edu.cn/campus/view/id/1020800",
        )
        self.assertEqual(fetch.call_count, 3)

    @patch("job_radar.collectors.fetch_bytes")
    def test_gdut_notice_returns_empty_before_target_cycle(self, fetch):
        fetch.return_value = self._gdut_page(
            '<ul class="infoList"><li>'
            '<a href="/campus/view/id/1020190">'
            "广州港集团有限公司2026届校园招聘简章</a></li></ul>"
        )
        source = self._gdut_source()
        source["max_pages"] = 1

        self.assertEqual(GdutCampusNoticeCollector(source).collect(), [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_gdut_notice_rejects_changed_page_schema(self, fetch):
        fetch.return_value = (
            "<main>广东工业大学招聘公告页面已改版</main>".encode("utf-8")
        )

        with self.assertRaisesRegex(ValueError, "缺少公开内容片段"):
            GdutCampusNoticeCollector(self._gdut_source()).collect()

    @staticmethod
    def _gzrecruit_source():
        return {
            "id": "guangzhou_digital",
            "name": "广州数字科技集团",
            "type": "gzrecruit_company",
            "homepage": (
                "https://www.gzrecruit.com/groupCompany.html"
                "?unitNo=target-company"
            ),
            "url": "https://www.gzrecruit.com/api2/job/page",
            "unit_no": "target-company",
            "company": "广州数字科技集团有限公司",
            "company_type": "国企",
            "location": "广州",
            "min_published_at": "2026-07-01",
            "recruit_property": 2,
            "include_keywords": ["AI", "人工智能", "数据", "算法"],
            "exclude_keywords": ["销售"],
            "graduation_years": [2027],
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_gzrecruit_company_filters_and_maps_target_jobs(self, fetch):
        fetch.return_value = json.dumps(
            {
                "success": True,
                "totalCount": 4,
                "pageIndex": 1,
                "totalPages": 1,
                "data": [
                    {
                        "recruitNo": "job-ai-2027",
                        "station": "数据与人工智能工程师",
                        "company": {
                            "name": "广州数字科技集团有限公司",
                            "unitNo": "target-company",
                        },
                        "degree": "硕士",
                        "salary": "15K~20K",
                        "workLoc1st": "广州市",
                        "workLoc2nd": "天河区",
                        "tags": ["Python", "人工智能"],
                        "regDate": 1785168000000,
                        "recruitProperty": 2,
                    },
                    {
                        "recruitNo": "job-old",
                        "station": "数据工程师",
                        "company": {
                            "name": "广州数字科技集团有限公司",
                            "unitNo": "target-company",
                        },
                        "regDate": 1751328000000,
                        "recruitProperty": 2,
                    },
                    {
                        "recruitNo": "job-social",
                        "station": "人工智能工程师",
                        "company": {
                            "name": "广州数字科技集团有限公司",
                            "unitNo": "target-company",
                        },
                        "regDate": 1785168000000,
                        "recruitProperty": 1,
                    },
                    {
                        "recruitNo": "job-sales",
                        "station": "数据产品销售",
                        "company": {
                            "name": "广州数字科技集团有限公司",
                            "unitNo": "target-company",
                        },
                        "regDate": 1785168000000,
                        "recruitProperty": 2,
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")

        jobs = GzRecruitCompanyCollector(self._gzrecruit_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "job-ai-2027")
        self.assertEqual(jobs[0].title, "数据与人工智能工程师")
        self.assertEqual(jobs[0].company, "广州数字科技集团有限公司")
        self.assertEqual(jobs[0].company_type, "国企")
        self.assertEqual(jobs[0].location, "广州市/天河区")
        self.assertEqual(jobs[0].education, "硕士")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].published_at, "2026-07-28 00:00:00")
        self.assertEqual(
            jobs[0].url,
            "https://www.gzrecruit.com/jobs/recruit/detail/job-ai-2027",
        )
        self.assertIn("15K~20K", jobs[0].description)
        self.assertEqual(
            fetch.call_args.kwargs["headers"]["X-Requested-With"],
            "XMLHttpRequest",
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_gzrecruit_company_empty_result_is_successful(self, fetch):
        fetch.return_value = json.dumps(
            {
                "success": True,
                "totalCount": 0,
                "pageIndex": 1,
                "totalPages": 0,
                "data": [],
                "empty": True,
            }
        ).encode("utf-8")

        jobs = GzRecruitCompanyCollector(self._gzrecruit_source()).collect()

        self.assertEqual(jobs, [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_gzrecruit_company_rejects_changed_schema(self, fetch):
        fetch.return_value = json.dumps(
            {"success": True, "totalCount": 0, "data": {}}
        ).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "缺少 data 数组"):
            GzRecruitCompanyCollector(self._gzrecruit_source()).collect()

    @staticmethod
    def _iguopin_source():
        return {
            "id": "guangzhou_development_group",
            "name": "广州发展集团",
            "type": "iguopin_company",
            "homepage": "https://gdghr.iguopin.com/job",
            "url": "https://gp-api.iguopin.com/api/jobs/v1/list",
            "campaign_info_url": (
                "https://gp-api.iguopin.com/api/activity/exclusive/v1/info"
            ),
            "campaign_domain": "gdghr",
            "target_campaign_keywords": ["2027校园招聘", "2027届校园招聘"],
            "company_id": "target-company",
            "company": "广州发展集团股份有限公司",
            "company_type": "国企",
            "location": "广州",
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "min_published_at": "2026-07-01",
            "campus_natures": ["campus"],
            "company_name_keywords": ["广州发展"],
            "include_keywords": ["AI", "人工智能", "数据", "Python"],
            "exclude_keywords": ["销售"],
            "graduation_years": [2027],
            "page_size": 2,
            "max_pages": 3,
        }

    @staticmethod
    def _iguopin_page(items, page=1, total=None):
        return json.dumps(
            {
                "code": 200,
                "msg": "OK",
                "data": {
                    "total": len(items) if total is None else total,
                    "page": page,
                    "page_size": 2,
                    "list": items,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")

    @staticmethod
    def _iguopin_campaign(title="广州发展集团2027校园招聘"):
        return json.dumps(
            {
                "code": 200,
                "msg": "OK",
                "data": {
                    "company_id": "target-company",
                    "title": title,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")

    @patch("job_radar.collectors.fetch_bytes")
    def test_iguopin_company_paginates_filters_and_maps_jobs(self, fetch):
        target = {
            "job_id": "job-ai-2027",
            "job_name": "能源数据与人工智能工程师",
            "company_name": "广州发展新能源集团有限公司",
            "recruitment_type_cn": "校园招聘",
            "nature": "campus",
            "category_cn": "数据工程师",
            "major_cn": ["计算机类", "人工智能"],
            "education_cn": "硕士",
            "district_list": [{"area_cn": "广州-天河区"}],
            "contents": "<p>负责能源数据平台和 Python 算法开发。</p>",
            "create_time": "2026-08-20 09:00:00",
            "start_time": "2026-08-20 09:00:00",
            "end_time": "2026-10-31 23:59:59",
            "is_apply": True,
        }
        old = dict(target, job_id="job-old", create_time="2026-05-20 09:00:00")
        sales = dict(
            target,
            job_id="job-sales",
            job_name="数据产品销售",
            create_time="2026-08-21 09:00:00",
        )
        social = dict(
            target,
            job_id="job-social",
            recruitment_type_cn="社会招聘",
            nature="social",
            create_time="2026-08-22 09:00:00",
        )
        fetch.side_effect = [
            self._iguopin_campaign(),
            self._iguopin_page([target, old], page=1, total=4),
            self._iguopin_page([sales, social], page=2, total=4),
        ]

        jobs = IguopinCompanyCollector(self._iguopin_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "job-ai-2027")
        self.assertEqual(jobs[0].title, "能源数据与人工智能工程师")
        self.assertEqual(jobs[0].company, "广州发展新能源集团有限公司")
        self.assertEqual(jobs[0].location, "广州-天河区")
        self.assertEqual(jobs[0].education, "硕士")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].published_at, "2026-08-20 09:00:00")
        self.assertEqual(jobs[0].deadline, "2026-10-31 23:59:59")
        self.assertEqual(
            jobs[0].url,
            "https://gdghr.iguopin.com/job/detail?id=job-ai-2027",
        )
        self.assertIn("Python 算法开发", jobs[0].description)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(
            fetch.call_args_list[1].kwargs["json_body"]["company_id_with_sub"],
            "target-company",
        )
        self.assertEqual(
            fetch.call_args_list[1].kwargs["headers"]["Device"], "pc"
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_iguopin_company_empty_result_is_successful(self, fetch):
        fetch.side_effect = [
            self._iguopin_campaign(),
            self._iguopin_page([]),
        ]

        jobs = IguopinCompanyCollector(self._iguopin_source()).collect()

        self.assertEqual(jobs, [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_iguopin_company_returns_empty_before_target_campaign(self, fetch):
        fetch.return_value = self._iguopin_campaign(
            "广州发展集团2026校园招聘"
        )

        jobs = IguopinCompanyCollector(self._iguopin_source()).collect()

        self.assertEqual(jobs, [])
        self.assertEqual(fetch.call_count, 1)

    @patch("job_radar.collectors.fetch_bytes")
    def test_iguopin_company_rejects_changed_schema(self, fetch):
        fetch.side_effect = [
            self._iguopin_campaign(),
            json.dumps(
                {"code": 200, "data": {"total": 0, "list": {}}}
            ).encode("utf-8"),
        ]

        with self.assertRaisesRegex(ValueError, "缺少 list 数组"):
            IguopinCompanyCollector(self._iguopin_source()).collect()

    @staticmethod
    def _beisen_portal_homepage() -> bytes:
        payload = {
            "Pages": [
                {
                    "Name": "招聘公告",
                    "HtmlAddress": "https://cdn.example.com/notices.html",
                },
                {
                    "Name": "校招公告",
                    "HtmlAddress": "https://cdn.example.com/campus.html",
                },
                {
                    "Name": "社会招聘首页",
                    "HtmlAddress": "https://cdn.example.com/social.html",
                },
            ],
            "tenantInfo": {"Name": "gzmetro"},
        }
        return (
            "<script>var BSGlobal = "
            + json.dumps(payload, ensure_ascii=False)
            + ";</script>"
        ).encode("utf-8")

    @staticmethod
    def _beisen_legacy_page(rows, page_links="") -> bytes:
        rendered_rows = []
        for job_id, title, company, location, published_at in rows:
            href = "/xzxq?jobId={}&jc=2&key=".format(job_id)
            rendered_rows.append(
                "<tr>"
                '<td><a href="{href}">{title}</a></td>'
                '<td><a href="{href}">{company}</a></td>'
                '<td><a href="{href}">{location}</a></td>'
                '<td><a href="{href}" class="ptDate">{date}</a></td>'
                '<td><a href="{href}">详情</a></td>'
                "</tr>".format(
                    href=href,
                    title=title,
                    company=company,
                    location=location,
                    date=published_at,
                )
            )
        return (
            "<main><table><tr><th>职位名称</th><th>招聘单位</th>"
            "<th>工作地点</th><th>发布时间</th></tr>"
            + "".join(rendered_rows)
            + "</table>"
            + page_links
            + "</main>"
        ).encode("utf-8")

    @staticmethod
    def _beisen_legacy_source():
        return {
            "id": "guangdong_yuehai_group",
            "name": "广东粤海控股集团",
            "type": "beisen_legacy_campus",
            "homepage": "https://gdh.example.com/xzzw",
            "page_url_template": (
                "https://gdh.example.com/xzzw/?PageIndex={page}"
            ),
            "company_type": "国企",
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "min_published_at": "2026-07-01",
            "include_keywords": ["AI", "数据", "软件", "数字化", "信息化"],
            "exclude_keywords": ["销售", "博士后", "2026届"],
            "graduation_years": [2027],
            "description": "请核对岗位详情中的学历、专业与毕业时间要求。",
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_beisen_legacy_campus_paginates_filters_and_maps_jobs(self, fetch):
        fetch.side_effect = [
            self._beisen_legacy_page(
                [
                    (
                        "new-data",
                        "数据开发工程师",
                        "广东粤海水务股份有限公司",
                        "广东省-广州市",
                        "2026.08.20",
                    ),
                    (
                        "old-model",
                        "智慧模型研发岗",
                        "广东省水利电力勘测设计研究院有限公司",
                        "广东省-广州市",
                        "2025.03.14",
                    ),
                    (
                        "other-city",
                        "软件开发工程师",
                        "粤海水务",
                        "广东省-东莞市",
                        "2026.08.21",
                    ),
                ],
                '<a href="/xzzw/?PageIndex=2">下一页</a>',
            ),
            self._beisen_legacy_page(
                [
                    (
                        "data-sales",
                        "数据产品销售岗",
                        "广东粤海控股集团有限公司",
                        "广东省-深圳市",
                        "2026.08.22",
                    )
                ],
                '<a href="/xzzw/?PageIndex=1">上一页</a>',
            ),
        ]

        jobs = BeisenLegacyCampusCollector(
            self._beisen_legacy_source()
        ).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "new-data")
        self.assertEqual(jobs[0].title, "数据开发工程师")
        self.assertEqual(jobs[0].company, "广东粤海水务股份有限公司")
        self.assertEqual(jobs[0].location, "广东省-广州市")
        self.assertEqual(jobs[0].published_at, "2026-08-20")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            "https://gdh.example.com/xzxq?jobId=new-data&jc=2&key=",
        )
        self.assertEqual(fetch.call_count, 2)

    @patch("job_radar.collectors.fetch_bytes")
    def test_beisen_legacy_campus_empty_page_is_successful(self, fetch):
        fetch.return_value = self._beisen_legacy_page([])

        jobs = BeisenLegacyCampusCollector(
            self._beisen_legacy_source()
        ).collect()

        self.assertEqual(jobs, [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_beisen_legacy_campus_rejects_changed_page(self, fetch):
        fetch.return_value = "<main>校园招聘系统维护中</main>".encode("utf-8")

        with self.assertRaisesRegex(ValueError, "未出现预期标识"):
            BeisenLegacyCampusCollector(
                self._beisen_legacy_source()
            ).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_beisen_campaign_follows_current_pages_and_emits_link(self, fetch):
        fetch.side_effect = [
            self._beisen_portal_homepage(),
            (
                "<main><p>2026届春招面试通过人员名单</p>"
                '<a href="/recruitFeed/detail?id=metro-2027">'
                "广州地铁集团有限公司2027届校园招聘公告</a></main>"
            ).encode("utf-8"),
            "<main><img alt='校园招聘宣传图'></main>".encode("utf-8"),
        ]
        source = {
            "id": "guangzhou_metro",
            "name": "广州地铁集团",
            "type": "beisen_portal_campaign",
            "homepage": "https://gzmetro.example.com/",
            "tenant_name": "gzmetro",
            "page_names": ["招聘公告", "校招公告"],
            "target_keywords": ["2027届校园招聘", "2027秋招"],
            "external_id": "guangzhou-metro-campus-2027-launch",
            "title": "广州地铁集团2027校园招聘已启动",
            "company": "广州地铁集团",
            "company_type": "国企",
            "location": "广州",
            "graduation_years": [2027],
        }

        jobs = BeisenPortalCampaignCollector(source).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            jobs[0].external_id, "guangzhou-metro-campus-2027-launch"
        )
        self.assertEqual(jobs[0].company, "广州地铁集团")
        self.assertEqual(jobs[0].company_type, "国企")
        self.assertEqual(jobs[0].location, "广州")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            "https://gzmetro.example.com/recruitFeed/detail?id=metro-2027",
        )
        self.assertEqual(fetch.call_count, 2)

    @patch("job_radar.collectors.fetch_bytes")
    def test_beisen_campaign_returns_empty_before_target_cycle(self, fetch):
        fetch.side_effect = [
            self._beisen_portal_homepage(),
            "<main>广州地铁集团2026届春季招聘公示</main>".encode("utf-8"),
            "<main>校园招聘敬请期待</main>".encode("utf-8"),
        ]
        source = {
            "id": "guangzhou_metro",
            "name": "广州地铁集团",
            "type": "beisen_portal_campaign",
            "homepage": "https://gzmetro.example.com/",
            "tenant_name": "gzmetro",
            "page_names": ["招聘公告", "校招公告"],
            "target_keywords": ["2027届校园招聘"],
            "title": "广州地铁集团2027校园招聘已启动",
        }

        self.assertEqual(BeisenPortalCampaignCollector(source).collect(), [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_beisen_campaign_rejects_changed_portal_schema(self, fetch):
        fetch.return_value = "<main>广州地铁招聘</main>".encode("utf-8")
        source = {
            "id": "guangzhou_metro",
            "name": "广州地铁集团",
            "type": "beisen_portal_campaign",
            "homepage": "https://gzmetro.example.com/",
            "title": "广州地铁集团2027校园招聘已启动",
        }

        with self.assertRaisesRegex(ValueError, "缺少公开站点配置"):
            BeisenPortalCampaignCollector(source).collect()

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
