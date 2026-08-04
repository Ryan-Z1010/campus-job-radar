import base64
import html
import json
import unittest
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from job_radar.collectors import (
    AccentureEarlyCareerCollector,
    BeisenLegacyCampusCollector,
    BeisenPortalCampaignCollector,
    BydCampusCollector,
    CampaignWatchCollector,
    ChinaSouthernPowerGridCollector,
    CmbCampusCollector,
    CvteCampusCollector,
    GdrcGroupCollector,
    GdutCampusNoticeCollector,
    GiihgCampusCollector,
    GzRecruitCompanyCollector,
    HotjobCampusCollector,
    HsbcProgrammeCollector,
    HuaweiCampusCollector,
    IbmEntryLevelCollector,
    IguopinCompanyCollector,
    JsonApiCollector,
    LiepinStaticCampusCollector,
    MokaCampusCollector,
    NeteaseGameCampusCollector,
    NoticeJsonCollector,
    PinganCampusCollector,
    PwcGraduateCampaignCollector,
    SheinCampusCollector,
    TencentCampusCollector,
    WebNoticeCollector,
    ZhaopinCampusCompanyCollector,
)


class CollectorTests(unittest.TestCase):
    @staticmethod
    def _pingan_source():
        return {
            "id": "pingan_fresh_graduate",
            "name": "中国平安正式应届生",
            "type": "pingan_campus",
            "official_config_url": "https://campus.pingan.com/api/config",
            "positions_url": "https://campus.pingan.com/api/positions",
            "detail_url_template": (
                "https://campus.pingan.com{official_path}/positionDetail"
                "?positionId={position_id}"
            ),
            "official_units": [
                {"official_url": "", "business_unit_id": "PA000"}
            ],
            "position_type": "1",
            "company": "中国平安",
            "company_type": "私企",
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "include_keywords": ["AI", "人工智能", "算法", "数据", "软件"],
            "exclude_keywords": ["实习", "营销", "销售"],
            "page_size": 2,
            "max_pages": 3,
            "education": "正式应届生，具体要求以官网为准",
            "graduation_years": [2027],
            "deadline": "以官方岗位页面为准",
        }

    @staticmethod
    def _pingan_config(unit_id="PA000", wecruit_id="PINGAN2027"):
        return json.dumps(
            {
                "responseCode": "10001",
                "responseMsg": "请求成功",
                "data": {
                    "wecruitId": wecruit_id,
                    "businessUnitId": unit_id,
                    "businessUnitName": "平安集团",
                    "hasAvalibleWebsiteModel": "Y",
                },
            }
        ).encode("utf-8")

    @staticmethod
    def _pingan_page(page_no, total_count, total_page, items):
        return json.dumps(
            {
                "responseCode": "10001",
                "responseMsg": "请求成功",
                "data": {
                    "list": items,
                    "pageNo": page_no,
                    "pageSize": 2,
                    "totalCount": total_count,
                    "totalPage": total_page,
                },
            }
        ).encode("utf-8")

    @patch("job_radar.collectors.fetch_bytes")
    def test_pingan_returns_empty_before_formal_campaign_launch(self, fetch):
        fetch.side_effect = [
            self._pingan_config(),
            json.dumps(
                {
                    "responseCode": "10001",
                    "responseMsg": "请求成功",
                    "data": None,
                }
            ).encode("utf-8"),
        ]

        jobs = PinganCampusCollector(self._pingan_source()).collect()

        self.assertEqual(jobs, [])
        self.assertEqual(
            fetch.call_args_list[1].kwargs["json_body"]["positionType"], "1"
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_pingan_paginates_and_filters_target_roles(self, fetch):
        fetch.side_effect = [
            self._pingan_config(),
            self._pingan_page(
                1,
                4,
                2,
                [
                    {
                        "idPosition": "ai-1",
                        "positionName": "AI算法工程师",
                        "positionCategoryName": "科技类",
                        "businessUnitName": "平安科技",
                        "deptShowName": "人工智能中心",
                        "workCity": "深圳市,上海市",
                        "positionType": "1",
                    },
                    {
                        "idPosition": "sales-1",
                        "positionName": "营销管培生",
                        "positionCategoryName": "业务类",
                        "businessUnitName": "平安产险",
                        "workCity": "广州市",
                        "positionType": "1",
                    },
                ],
            ),
            self._pingan_page(
                2,
                4,
                2,
                [
                    {
                        "idPosition": "data-1",
                        "positionName": "数据产品经理",
                        "positionCategoryName": "产品类",
                        "businessUnitName": "平安集团",
                        "workCity": "北京市",
                        "positionType": "1",
                    },
                    {
                        "idPosition": "software-1",
                        "positionName": "软件开发工程师",
                        "positionCategoryName": "科技类",
                        "businessUnitName": "平安科技",
                        "workCity": "南京市",
                        "positionType": "1",
                    },
                ],
            ),
        ]

        jobs = PinganCampusCollector(self._pingan_source()).collect()

        self.assertEqual([job.external_id for job in jobs], ["ai-1", "data-1"])
        self.assertEqual(jobs[0].company, "平安科技")
        self.assertEqual(jobs[0].location, "深圳市、上海市")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            "https://campus.pingan.com/positionDetail?positionId=ai-1",
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_pingan_rejects_non_target_official_unit(self, fetch):
        fetch.return_value = self._pingan_config(unit_id="PA003")

        with self.assertRaisesRegex(ValueError, "非目标监控单位"):
            PinganCampusCollector(self._pingan_source()).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_pingan_rejects_internship_in_formal_api(self, fetch):
        fetch.side_effect = [
            self._pingan_config(),
            self._pingan_page(
                1,
                1,
                1,
                [
                    {
                        "idPosition": "intern-1",
                        "positionName": "AI实习生",
                        "positionCategoryName": "科技类",
                        "businessUnitName": "平安科技",
                        "workCity": "深圳市",
                        "positionType": "2",
                    }
                ],
            ),
        ]

        with self.assertRaisesRegex(ValueError, "非正式应届生岗位"):
            PinganCampusCollector(self._pingan_source()).collect()

    @staticmethod
    def _cmb_source():
        return {
            "id": "cmb_graduate_2027",
            "name": "招商银行2027届应届生",
            "type": "cmb_campus",
            "homepage": "https://career.cmbchina.com/campus/home",
            "recruiting_info_url": "https://career.cmbchina.com/api/info",
            "positions_url": "https://career.cmbchina.com/api/positions",
            "detail_api_url": "https://career.cmbchina.com/api/detail",
            "detail_url_template": (
                "https://career.cmbchina.com/positionDetail/school"
                "?publishId={publish_id}"
            ),
            "recruitment_type_id": (
                "96574F8D-C7ED-4772-AE7C-BAC896D190C1"
            ),
            "company_type": "私企",
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "include_keywords": ["AI", "人工智能", "算法", "数据", "开发"],
            "exclude_keywords": ["实习", "营销", "销售"],
            "target_campaign_keywords": ["2027", "2027届"],
            "page_size": 2,
            "max_pages": 3,
            "education": "本科及以上，具体以岗位详情为准",
            "graduation_years": [2027],
        }

    @staticmethod
    def _cmb_payload(body):
        return json.dumps(
            {"returnCode": "SUC0000", "errorMsg": None, "body": body}
        ).encode("utf-8")

    @classmethod
    def _cmb_info(cls, organizations=None, cities=None):
        return cls._cmb_payload(
            {
                "recruitingOrgList": organizations
                if organizations is not None
                else [
                    {
                        "orgId": "108116",
                        "orgName": "招银网络科技",
                        "recruitingJobCount": 3,
                        "recruitingCityIdList": ["shenzhen", "shanghai"],
                    }
                ],
                "recruitingCityList": cities
                if cities is not None
                else [
                    {
                        "id": "shenzhen",
                        "name": "深圳市",
                        "recruitingOrgIdList": ["108116"],
                    },
                    {
                        "id": "shanghai",
                        "name": "上海市",
                        "recruitingOrgIdList": ["108116"],
                    },
                ],
            }
        )

    @classmethod
    def _cmb_page(cls, total, items):
        return cls._cmb_payload({"total": total, "data": items})

    @classmethod
    def _cmb_detail(
        cls,
        publish_id="ai-1",
        title="算法工程师（深圳）",
        location="深圳市",
        recruitment_type_id="96574F8D-C7ED-4772-AE7C-BAC896D190C1",
        job_code="SZ004-2027-AU",
        requirement="<p>2027年应届毕业生，硕士及以上学历，掌握Python。</p>",
    ):
        return cls._cmb_payload(
            {
                "publishGID": publish_id,
                "recruitmentTypeID": recruitment_type_id,
                "jobCode": job_code,
                "jobDisplay": title,
                "jobResponsibility": (
                    "<p>负责人工智能与机器学习在服务、营销和风控中的应用。</p>"
                ),
                "jobRequirement": requirement,
                "branchCode": "108116",
                "branchCodeName": "招银网络科技",
                "locationName": location,
                "expiredOn": "2026-09-20",
            }
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_cmb_paginates_filters_and_verifies_target_cycle(self, fetch):
        fetch.side_effect = [
            self._cmb_info(),
            self._cmb_page(
                3,
                [
                    {
                        "publishGID": "ai-1",
                        "jobDisplay": "算法工程师（深圳）",
                        "branchCode": "108116",
                        "branchCodeName": "招银网络科技",
                        "location": "shenzhen",
                        "locationName": "深圳市",
                        "expiredOn": "2026-09-20",
                    },
                    {
                        "publishGID": "sales-1",
                        "jobDisplay": "市场营销岗",
                        "branchCode": "108116",
                        "branchCodeName": "招银网络科技",
                        "location": "shenzhen",
                        "locationName": "深圳市",
                        "expiredOn": "2026-09-20",
                    },
                ],
            ),
            self._cmb_page(
                3,
                [
                    {
                        "publishGID": "backend-1",
                        "jobDisplay": "后端开发工程师（上海）",
                        "branchCode": "108116",
                        "branchCodeName": "招银网络科技",
                        "location": "shanghai",
                        "locationName": "上海市",
                        "expiredOn": "2026-09-20",
                    }
                ],
            ),
            self._cmb_detail(),
            self._cmb_detail(
                publish_id="backend-1",
                title="后端开发工程师（上海）",
                location="上海市",
                job_code="SH001-2027-AU",
                requirement="<p>2027届应届毕业生，本科及以上学历。</p>",
            ),
        ]

        jobs = CmbCampusCollector(self._cmb_source()).collect()

        self.assertEqual(
            [job.external_id for job in jobs], ["ai-1", "backend-1"]
        )
        self.assertEqual(jobs[0].company, "招银网络科技")
        self.assertEqual(jobs[0].education, "硕士及以上")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].deadline, "2026-09-20")
        self.assertIn("人工智能与机器学习", jobs[0].description)
        self.assertIn("营销和风控", jobs[0].description)
        self.assertEqual(
            jobs[0].url,
            "https://career.cmbchina.com/positionDetail/school"
            "?publishId=ai-1",
        )
        self.assertEqual(
            fetch.call_args_list[1].kwargs["json_body"]["recruitmentTypeId"],
            "96574F8D-C7ED-4772-AE7C-BAC896D190C1",
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_cmb_returns_empty_when_formal_entry_has_no_jobs(self, fetch):
        fetch.side_effect = [
            self._cmb_info(organizations=[], cities=[]),
            self._cmb_page(0, []),
        ]

        jobs = CmbCampusCollector(self._cmb_source()).collect()

        self.assertEqual(jobs, [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_cmb_skips_previous_graduate_cycle(self, fetch):
        fetch.side_effect = [
            self._cmb_info(),
            self._cmb_page(
                1,
                [
                    {
                        "publishGID": "ai-2026",
                        "jobDisplay": "算法工程师（深圳）",
                        "branchCode": "108116",
                        "branchCodeName": "招银网络科技",
                        "location": "shenzhen",
                        "locationName": "深圳市",
                        "expiredOn": "2025-09-20",
                    }
                ],
            ),
            self._cmb_detail(
                publish_id="ai-2026",
                job_code="SZ004-2026-AU",
                requirement="<p>2026年应届毕业生，硕士及以上学历。</p>",
            ),
        ]

        jobs = CmbCampusCollector(self._cmb_source()).collect()

        self.assertEqual(jobs, [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_cmb_rejects_internship_detail_from_formal_entry(self, fetch):
        fetch.side_effect = [
            self._cmb_info(),
            self._cmb_page(
                1,
                [
                    {
                        "publishGID": "intern-1",
                        "jobDisplay": "算法工程师（深圳）",
                        "branchCode": "108116",
                        "branchCodeName": "招银网络科技",
                        "location": "shenzhen",
                        "locationName": "深圳市",
                        "expiredOn": "2026-09-20",
                    }
                ],
            ),
            self._cmb_detail(
                publish_id="intern-1",
                recruitment_type_id="DF94FD6D-26D3-4A19-9E69-577C4BA1DE82",
            ),
        ]

        with self.assertRaisesRegex(ValueError, "不是正式应届生入口"):
            CmbCampusCollector(self._cmb_source()).collect()

    @staticmethod
    def _byd_source():
        return {
            "id": "byd_china",
            "name": "比亚迪",
            "type": "byd_campus",
            "homepage": "https://job.byd.com/portal/mobile/school-home",
            "url": "https://job.byd.com/portal/api/portal-api/resumeSend/"
            "school-topic/info?zpNature=008501&topicType=yingjs_zp",
            "company": "比亚迪",
            "company_type": "私企",
            "expected_zp_nature": "008501",
            "target_keywords": ["27届", "2027届"],
            "exclude_keywords": ["实习", "博士"],
            "title": "比亚迪2027届正式校园招聘已启动",
            "location": "深圳/上海/北京/广州",
            "description": "请核对AI、数据与软件岗位。",
            "education": "应届毕业生，具体要求以官网为准",
            "graduation_years": [2027],
            "deadline": "以官网为准",
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_byd_returns_empty_before_target_topic_launch(self, fetch):
        fetch.return_value = json.dumps(
            {"code": 0, "data": None, "msg": "操作成功", "oK": True}
        ).encode("utf-8")

        jobs = BydCampusCollector(self._byd_source()).collect()

        self.assertEqual(jobs, [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_byd_maps_target_fresh_graduate_topic(self, fetch):
        fetch.return_value = json.dumps(
            {
                "code": 0,
                "data": {
                    "topicCode": "BYD2027CAMPUS",
                    "topic": "比亚迪2027届校园招聘",
                    "graduationYear": "2027届",
                    "zpNature": "008501",
                },
                "msg": "操作成功",
                "oK": True,
            }
        ).encode("utf-8")

        jobs = BydCampusCollector(self._byd_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "byd_china:BYD2027CAMPUS")
        self.assertEqual(jobs[0].company, "比亚迪")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url, "https://job.byd.com/portal/mobile/school-home"
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_byd_ignores_previous_or_intern_topic(self, fetch):
        fetch.return_value = json.dumps(
            {
                "code": 0,
                "data": {
                    "topicCode": "BYD2026INTERN",
                    "topic": "比亚迪2026届实习生招聘",
                    "graduationYear": "2026届",
                    "zpNature": "008501",
                },
                "oK": True,
            }
        ).encode("utf-8")

        self.assertEqual(BydCampusCollector(self._byd_source()).collect(), [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_byd_rejects_invalid_json(self, fetch):
        fetch.return_value = b"not-json"

        with self.assertRaisesRegex(ValueError, "无效 JSON"):
            BydCampusCollector(self._byd_source()).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_byd_rejects_changed_topic_schema(self, fetch):
        fetch.return_value = json.dumps(
            {
                "code": 0,
                "data": {"topic": "比亚迪2027届校园招聘"},
                "oK": True,
            }
        ).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "缺少必要字段"):
            BydCampusCollector(self._byd_source()).collect()

    @staticmethod
    def _pwc_source():
        return {
            "id": "pwc_china",
            "name": "普华永道中国",
            "type": "pwc_graduate_campaign",
            "homepage": "https://www.pwccn.com/zh/careers/students.html",
            "section_id": "graduate",
            "required_text": "毕业生计划",
            "target_keywords": ["2027届", "2027 届"],
            "application_link_keywords": ["立即申请"],
            "allowed_application_hosts": ["app.mokahr.com"],
            "application_path_prefix": "/campus-recruitment/pwc/148260",
            "external_id": "pwc-china-graduate-2027-launch",
            "title": "普华永道中国2027毕业生计划已启动",
            "company": "普华永道中国",
            "company_type": "外企",
            "location": "广州/上海/深圳/北京（具体岗位待官网确认）",
            "description": "请进入官网核对AI、数据与技术岗位。",
            "education": "全日制本科及以上，具体要求以当届公告为准",
            "graduation_years": [2027],
            "deadline": "以官方项目及岗位为准",
        }

    @staticmethod
    def _pwc_page(graduate_years: str) -> bytes:
        return (
            '<section id="graduate">'
            "<h3>毕业生计划</h3>"
            "<p>欢迎{}届全日制本科及以上学历毕业生报名。</p>"
            '<a href="https://app.mokahr.com/campus-recruitment/'
            'pwc/148260?locale=zh-CN#/jobs?page=1">立即申请</a>'
            "</section>"
            '<section id="internship">'
            "<h3>实习计划</h3><p>欢迎2027届或之后毕业的同学报名。</p>"
            "</section>"
        ).format(graduate_years).encode("utf-8")

    @patch("job_radar.collectors.fetch_bytes")
    def test_pwc_ignores_target_year_outside_graduate_section(self, fetch):
        fetch.return_value = self._pwc_page("2024、2025、2026")

        jobs = PwcGraduateCampaignCollector(
            self._pwc_source()
        ).collect()

        self.assertEqual(jobs, [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_pwc_maps_target_graduate_campaign(self, fetch):
        fetch.return_value = self._pwc_page("2025、2026、2027")

        jobs = PwcGraduateCampaignCollector(
            self._pwc_source()
        ).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            jobs[0].external_id, "pwc-china-graduate-2027-launch"
        )
        self.assertEqual(jobs[0].company, "普华永道中国")
        self.assertEqual(jobs[0].company_type, "外企")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            (
                "https://app.mokahr.com/campus-recruitment/"
                "pwc/148260?locale=zh-CN#/jobs?page=1"
            ),
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_pwc_rejects_missing_graduate_section(self, fetch):
        fetch.return_value = (
            "<section id='internship'>2027届实习计划</section>"
        ).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "缺少毕业生计划区块"):
            PwcGraduateCampaignCollector(
                self._pwc_source()
            ).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_pwc_rejects_unexpected_application_link(self, fetch):
        fetch.return_value = (
            '<section id="graduate"><h3>毕业生计划</h3>'
            "<p>欢迎2027届毕业生报名。</p>"
            '<a href="https://example.com/apply">立即申请</a>'
            "</section>"
        ).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "缺少有效官方申请入口"):
            PwcGraduateCampaignCollector(
                self._pwc_source()
            ).collect()

    @staticmethod
    def _accenture_source():
        return {
            "id": "accenture_china",
            "name": "埃森哲中国",
            "type": "accenture_early_career",
            "homepage": "https://www.accenture.com/cn-en/careers",
            "url": (
                "https://www.accenture.com/api/accenture/"
                "elastic/findjobs"
            ),
            "company": "埃森哲中国",
            "company_type": "外企",
            "country_filter": "China/Mainland",
            "allowed_api_countries": [
                "China/Mainland",
                "China/Hong Kong SAR",
            ],
            "country_site": "cn-en",
            "experience_filter": "Early Career",
            "employee_type": "Full-time",
            "location_keywords": [
                "Guangzhou",
                "Shanghai",
                "Shenzhen",
                "Beijing",
            ],
            "location_map": {
                "Guangzhou": "广州",
                "Shanghai": "上海",
                "Shenzhen": "深圳",
                "Beijing": "北京",
            },
            "include_keywords": [
                "AI",
                "Data",
                "Technology",
                "Software",
                "Business Analyst",
            ],
            "exclude_title_keywords": [
                "Digital Marketing",
                "Sales",
            ],
            "target_cycle_years": [2027],
            "target_updated_start": "2026-07-01",
            "target_updated_end": "2027-06-30",
            "entry_experience_ranges": ["0-2"],
            "max_results": 50,
            "graduation_years": [2027],
        }

    @staticmethod
    def _accenture_job(**overrides):
        job = {
            "guid": "13753726_en",
            "requisitionId": "13753726",
            "title": "Business Analyst",
            "country": "China/Mainland",
            "location": ["Guangzhou"],
            "jobTypeDescription": "Early Career",
            "employeeType": "Full-time",
            "careerLevel": "Associate",
            "yearsOfExperience": "0-2",
            "updateDate": "2026-07-29T10:07:04.663-07:00",
            "jobDetailUrl": (
                "https://www.accenture.com/{0}/careers/jobdetails"
                "?id=13753726_en&title=Business+Analyst"
            ),
            "education": ["Bachelor Degree", "Graduate Degree/PhD"],
            "function": ["Technology Architecture"],
            "skill": ["Software Engineering"],
            "areaOfInterest": ["technology"],
            "jobFamilyGroup": ["Software Engineering"],
            "jobDescriptionClean": (
                "Analyze product requirements and work with delivery teams."
            ),
            "qualificationClean": (
                "Use AI tools and understand APIs and data flows."
            ),
        }
        job.update(overrides)
        return job

    @patch("job_radar.collectors.fetch_bytes")
    def test_accenture_filters_cycle_and_maps_target_job(self, fetch):
        previous_cycle = self._accenture_job(
            guid="R00317304_en",
            title=(
                "2026 Accenture Graduate Program - "
                "Experience Transformation Analyst"
            ),
            location=["Shanghai"],
            yearsOfExperience="2-5",
            updateDate="2026-07-20T10:00:00+08:00",
        )
        unrelated = self._accenture_job(
            guid="R00317301_en",
            title="AAP - Digital Marketing Strategy Analyst",
            location=["Shanghai"],
            yearsOfExperience="2-5",
        )
        hong_kong = self._accenture_job(
            guid="R00339239_en",
            title="Digital Core - Backend Engineer (Java)",
            country="China/Hong Kong SAR",
            location=["Hong Kong"],
        )
        payload = {
            "message": "Success",
            "totalHits": {"total": 4, "overMaxHits": "False"},
            "data": [
                self._accenture_job(),
                previous_cycle,
                unrelated,
                hong_kong,
            ],
            "aggregations": [],
        }
        fetch.return_value = json.dumps(payload).encode("utf-8")

        jobs = AccentureEarlyCareerCollector(
            self._accenture_source()
        ).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "13753726_en")
        self.assertEqual(jobs[0].title, "Business Analyst")
        self.assertEqual(jobs[0].company, "埃森哲中国")
        self.assertEqual(jobs[0].company_type, "外企")
        self.assertEqual(jobs[0].location, "广州")
        self.assertEqual(jobs[0].published_at, "2026-07-29")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertIn("AI tools", jobs[0].description)
        self.assertEqual(
            jobs[0].url,
            (
                "https://www.accenture.com/cn-en/careers/jobdetails"
                "?id=13753726_en&title=Business+Analyst"
            ),
        )
        call = fetch.call_args
        self.assertEqual(call.kwargs["method"], "POST")
        form = call.kwargs["form_body"]
        self.assertEqual(form["jobCountry"], "China/Mainland")
        self.assertIn("Early Career", form["jobFilters"])

    @patch("job_radar.collectors.fetch_bytes")
    def test_accenture_accepts_target_graduate_program(self, fetch):
        target = self._accenture_job(
            guid="R2027TECH_en",
            title="2027 Accenture Graduate Program - Technology Analyst",
            location=["Shanghai", "Beijing"],
            yearsOfExperience="2-5",
            updateDate="2026-08-15T09:30:00+08:00",
        )
        payload = {
            "message": "Success",
            "totalHits": {"total": 1, "overMaxHits": "False"},
            "data": [target],
        }
        fetch.return_value = json.dumps(payload).encode("utf-8")

        jobs = AccentureEarlyCareerCollector(
            self._accenture_source()
        ).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "R2027TECH_en")
        self.assertEqual(jobs[0].location, "上海/北京")

    @patch("job_radar.collectors.fetch_bytes")
    def test_accenture_empty_result_is_successful(self, fetch):
        fetch.return_value = json.dumps(
            {
                "message": "Success",
                "totalHits": {"total": 0, "overMaxHits": "False"},
                "data": [],
            }
        ).encode("utf-8")

        self.assertEqual(
            AccentureEarlyCareerCollector(
                self._accenture_source()
            ).collect(),
            [],
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_accenture_rejects_partial_api_result(self, fetch):
        fetch.return_value = json.dumps(
            {
                "message": "Success",
                "totalHits": {"total": 2, "overMaxHits": "False"},
                "data": [self._accenture_job()],
            }
        ).encode("utf-8")

        with self.assertRaisesRegex(
            ValueError, "没有完整返回筛选结果"
        ):
            AccentureEarlyCareerCollector(
                self._accenture_source()
            ).collect()

    @staticmethod
    def _ibm_source():
        return {
            "id": "ibm_china",
            "name": "IBM 中国",
            "type": "ibm_entry_level",
            "homepage": (
                "https://www.ibm.com/cn-zh/careers/"
                "career-opportunities"
            ),
            "search_page": "https://www.ibm.com/careers/search",
            "url": "https://www-api.ibm.com/search/api/v2",
            "company": "IBM 中国",
            "company_type": "外企",
            "app_id": "careers",
            "scopes": ["careers2"],
            "country_filter": "China",
            "career_level_filter": "Entry Level",
            "location_keywords": [
                "Guangzhou",
                "Shanghai",
                "Shenzhen",
                "Beijing",
            ],
            "location_map": {
                "Guangzhou": "广州",
                "Shanghai": "上海",
                "Shenzhen": "深圳",
                "Beijing": "北京",
            },
            "generic_location_keywords": ["Multiple Cities"],
            "allow_generic_location": True,
            "generic_location": "中国大陆（官网标注多城市，具体地点待核对）",
            "include_keywords": [
                "AI",
                "Data",
                "Software",
                "Technology",
                "Consulting",
            ],
            "exclude_title_keywords": [
                "Intern",
                "Conversion",
                "Senior",
                "Sales",
            ],
            "target_cycle_years": [2027],
            "target_published_start": "2026-07-01",
            "target_published_end": "2027-06-30",
            "page_size": 100,
            "max_results": 500,
            "education": "入门级岗位，具体学历与毕业时间以岗位详情为准",
            "deadline": "以官方岗位页为准",
        }

    @staticmethod
    def _ibm_hit(**overrides):
        source = {
            "language": "en",
            "url": (
                "https://careers.ibm.com/careers/"
                "JobDetail?jobId=CN2027AI"
            ),
            "dcdate": "2026-07-31",
            "title": "2027 Entry Level AI Data Engineer",
            "description": (
                "Build AI-powered data products and production software."
            ),
            "entitled": "",
            "field_keyword_05": "China",
            "field_keyword_08": "Data & Analytics",
            "field_keyword_17": "Hybrid",
            "field_keyword_18": "Entry Level",
            "field_keyword_19": "Guangzhou, CN",
        }
        source.update(overrides.pop("source_overrides", {}))
        hit = {
            "_index": "genesis-prod",
            "_id": "stable-ibm-id",
            "_score": 1,
            "_source": source,
        }
        hit.update(overrides)
        return hit

    @staticmethod
    def _ibm_payload(hits, total=None):
        return {
            "took": 10,
            "timed_out": False,
            "hits": {
                "total": {
                    "value": len(hits) if total is None else total,
                    "relation": "eq",
                },
                "max_score": 1,
                "hits": hits,
            },
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_ibm_filters_and_maps_target_job(self, fetch):
        target = self._ibm_hit()
        previous_cycle = self._ibm_hit(
            _id="previous-cycle",
            source_overrides={
                "url": (
                    "https://careers.ibm.com/careers/"
                    "JobDetail?jobId=CN2026DATA"
                ),
                "title": "2026 Entry Level Data Engineer",
                "field_keyword_19": "Shanghai, CN",
            },
        )
        unrelated = self._ibm_hit(
            _id="unrelated",
            source_overrides={
                "url": (
                    "https://careers.ibm.com/careers/"
                    "JobDetail?jobId=CN2027HR"
                ),
                "title": "2027 Entry Level HR Specialist",
                "description": (
                    "Maintain daily available human resources services."
                ),
                "field_keyword_08": "Enterprise Operations",
                "field_keyword_19": "Beijing, CN",
            },
        )
        internship = self._ibm_hit(
            _id="internship",
            source_overrides={
                "url": (
                    "https://careers.ibm.com/careers/"
                    "JobDetail?jobId=CN2027INTERN"
                ),
                "title": "2027 Intern Conversion: Software Developer",
                "field_keyword_19": "Shenzhen, CN",
            },
        )
        other_city = self._ibm_hit(
            _id="other-city",
            source_overrides={
                "url": (
                    "https://careers.ibm.com/careers/"
                    "JobDetail?jobId=CN2027CHENGDU"
                ),
                "title": "2027 Entry Level Software Developer",
                "field_keyword_19": "Chengdu, CN",
            },
        )
        fetch.return_value = json.dumps(
            self._ibm_payload(
                [
                    target,
                    previous_cycle,
                    unrelated,
                    internship,
                    other_city,
                ]
            )
        ).encode("utf-8")

        jobs = IbmEntryLevelCollector(self._ibm_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "CN2027AI")
        self.assertEqual(jobs[0].company, "IBM 中国")
        self.assertEqual(jobs[0].company_type, "外企")
        self.assertEqual(jobs[0].location, "广州")
        self.assertEqual(jobs[0].published_at, "2026-07-31")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertIn("Data & Analytics", jobs[0].description)
        self.assertEqual(
            jobs[0].url,
            (
                "https://careers.ibm.com/careers/"
                "JobDetail?jobId=CN2027AI"
            ),
        )
        request = fetch.call_args.kwargs
        self.assertEqual(request["method"], "POST")
        self.assertEqual(
            request["headers"]["Origin"], "https://www.ibm.com"
        )
        body = request["json_body"]
        self.assertEqual(body["appId"], "careers")
        self.assertEqual(body["scopes"], ["careers2"])
        self.assertIn(
            {"term": {"field_keyword_05": "China"}},
            body["post_filter"]["bool"]["must"],
        )
        self.assertIn(
            {"term": {"field_keyword_18": "Entry Level"}},
            body["post_filter"]["bool"]["must"],
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_ibm_accepts_recent_undated_entry_level_job(self, fetch):
        target = self._ibm_hit(
            source_overrides={
                "url": (
                    "https://careers.ibm.com/careers/"
                    "JobDetail?jobId=CNENTRYAI"
                ),
                "title": "Entry Level Software Developer",
                "field_keyword_19": "Multiple Cities",
            }
        )
        fetch.return_value = json.dumps(
            self._ibm_payload([target])
        ).encode("utf-8")

        jobs = IbmEntryLevelCollector(self._ibm_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "CNENTRYAI")
        self.assertEqual(jobs[0].graduation_years, [])
        self.assertIn("具体地点待核对", jobs[0].location)

    @patch("job_radar.collectors.fetch_bytes")
    def test_ibm_empty_result_is_successful(self, fetch):
        fetch.return_value = json.dumps(
            self._ibm_payload([])
        ).encode("utf-8")

        self.assertEqual(
            IbmEntryLevelCollector(self._ibm_source()).collect(),
            [],
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_ibm_rejects_partial_api_result(self, fetch):
        fetch.return_value = json.dumps(
            self._ibm_payload([self._ibm_hit()], total=2)
        ).encode("utf-8")

        with self.assertRaisesRegex(
            ValueError, "没有完整返回筛选结果"
        ):
            IbmEntryLevelCollector(self._ibm_source()).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_ibm_rejects_unexpected_country(self, fetch):
        fetch.return_value = json.dumps(
            self._ibm_payload(
                [
                    self._ibm_hit(
                        source_overrides={
                            "field_keyword_05": "Taiwan",
                        }
                    )
                ]
            )
        ).encode("utf-8")

        with self.assertRaisesRegex(
            ValueError, "非目标筛选条件"
        ):
            IbmEntryLevelCollector(self._ibm_source()).collect()

    @staticmethod
    def _huawei_source():
        return {
            "id": "huawei_china",
            "name": "华为",
            "type": "huawei_campus",
            "homepage": (
                "https://career.huawei.com/reccampportal/portal5/"
                "campus-recruitment.html?jobTypes=1"
            ),
            "announcement_url": (
                "https://career.huawei.com/reccampportal/portal5/"
                "news.html"
            ),
            "url": (
                "https://career.huawei.com/reccampportal/services/"
                "portal/portalpub/getJob/newHr/page"
            ),
            "company": "华为",
            "company_type": "私企",
            "required_text": "news-bulletin-list",
            "launch_markers": [
                "华为2027届应届生招聘启动",
                "2027届华为应届生招聘启动",
            ],
            "job_types": "1",
            "job_type": "0",
            "student_abroad_priority": "1",
            "detail_job_type": "2",
            "location_keywords": [
                "Guangzhou",
                "广州",
                "Shanghai",
                "上海",
                "Shenzhen",
                "深圳",
                "Beijing",
                "北京",
            ],
            "location_map": {
                "Guangzhou": "广州",
                "广州": "广州",
                "Shanghai": "上海",
                "上海": "上海",
                "Shenzhen": "深圳",
                "深圳": "深圳",
                "Beijing": "北京",
                "北京": "北京",
            },
            "include_keywords": [
                "AI",
                "人工智能",
                "数据",
                "算法",
                "软件",
                "开发",
                "云计算",
                "计算机",
            ],
            "exclude_keywords": [
                "实习",
                "销售",
                "市场营销",
                "人力资源",
            ],
            "target_cycle_years": [2027],
            "target_published_start": "2026-07-01",
            "target_published_end": "2027-06-30",
            "page_size": 50,
            "max_results": 500,
            "education": (
                "华为校园招聘留学生岗位，具体学历、专业与海外毕业"
                "时间窗口以当届公告及岗位详情为准"
            ),
            "graduation_years": [2027],
        }

    @staticmethod
    def _huawei_page(target_launch=False):
        target = (
            '{ title: "华为2027届应届生招聘启动", '
            'isHot: 1, createTime: "2026-08-15", '
            'link: "newsInfo_32.html" },'
            if target_launch
            else ""
        )
        return (
            '<html><body><div class="news-bulletin-list"></div>'
            "<script>const newsList = ["
            '{ title: "华为2026届应届生招聘启动", '
            'isHot: 1, createTime: "2025-08-15", link: "old.html" },'
            '{ title: "华为2027届实习生招聘正式启动", '
            'isHot: 1, createTime: "2026-03-15", link: "intern.html" },'
            '{ title: "华为2027届顶尖AI人才招聘专项行动", '
            'isHot: 1, createTime: "2026-05-19", link: "ai.html" },'
            + target
            + "];</script></body></html>"
        ).encode("utf-8")

    @staticmethod
    def _huawei_job(**overrides):
        job = {
            "jobId": 27001,
            "advertisementCode": "AD2026081500001",
            "dataSource": 1,
            "jobname": "AI数据开发工程师",
            "jobType": "0",
            "studentAbroadPriority": "1",
            "jobFamilyName": "研发族",
            "jobArea": "中国/深圳,中国/上海",
            "jobAddress": (
                "China\\Guangdong-Shenzhen,"
                "China\\Shanghai-Shanghai"
            ),
            "mainBusiness": (
                "负责大模型数据平台及AI应用的软件开发。"
            ),
            "jobRequire": (
                "计算机、人工智能、数据科学等相关专业硕士优先。"
            ),
            "releaseDate": "2026-08-15T10:30:00.000+0800",
            "expirationDate": "2026-11-30T23:59:59.000+0800",
        }
        job.update(overrides)
        return job

    @staticmethod
    def _huawei_payload(items, total=None, page=1, page_size=50):
        total_rows = len(items) if total is None else total
        total_pages = (
            (total_rows + page_size - 1) // page_size
            if total_rows
            else 0
        )
        return {
            "pageVO": {
                "totalRows": total_rows,
                "curPage": page,
                "pageSize": page_size,
                "totalPages": total_pages,
            },
            "result": items,
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_huawei_ignores_intern_and_ai_campaign_before_grad_launch(
        self, fetch
    ):
        fetch.return_value = self._huawei_page(target_launch=False)

        jobs = HuaweiCampusCollector(self._huawei_source()).collect()

        self.assertEqual(jobs, [])
        self.assertEqual(fetch.call_count, 1)

    @patch("job_radar.collectors.fetch_bytes")
    def test_huawei_filters_and_maps_target_overseas_job(self, fetch):
        previous_cycle = self._huawei_job(
            jobId=26001,
            advertisementCode="AD2025081500001",
            jobname="2026届数据开发工程师",
            jobArea="中国/广州",
            jobAddress="China\\Guangdong-Guangzhou",
            releaseDate="2025-08-15T10:30:00.000+0800",
        )
        unrelated = self._huawei_job(
            jobId=27002,
            advertisementCode="AD2026081500002",
            jobname="公共关系专员",
            jobArea="中国/北京",
            jobAddress="China\\Beijing-Beijing",
            mainBusiness="负责品牌传播和公共关系沟通。",
            jobRequire="新闻传播相关专业。",
        )
        excluded = self._huawei_job(
            jobId=27003,
            advertisementCode="AD2026081500003",
            jobname="AI行业销售经理",
            jobArea="中国/深圳",
            jobAddress="China\\Guangdong-Shenzhen",
        )
        other_city = self._huawei_job(
            jobId=27004,
            advertisementCode="AD2026081500004",
            jobname="软件开发工程师",
            jobArea="中国/杭州",
            jobAddress="China\\Zhejiang-Hangzhou",
        )
        payload = self._huawei_payload(
            [
                self._huawei_job(),
                previous_cycle,
                unrelated,
                excluded,
                other_city,
            ]
        )
        fetch.side_effect = [
            self._huawei_page(target_launch=True),
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ]

        jobs = HuaweiCampusCollector(self._huawei_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "AD2026081500001")
        self.assertEqual(jobs[0].title, "AI数据开发工程师")
        self.assertEqual(jobs[0].company, "华为")
        self.assertEqual(jobs[0].company_type, "私企")
        self.assertEqual(jobs[0].location, "上海/深圳")
        self.assertEqual(jobs[0].published_at, "2026-08-15")
        self.assertEqual(jobs[0].deadline, "2026-11-30")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertIn("大模型数据平台", jobs[0].description)
        self.assertIn("jobId=27001", jobs[0].url)
        self.assertIn("dataSource=1", jobs[0].url)

        api_call = fetch.call_args_list[1]
        self.assertIn("jobTypes=1", api_call.args[0])
        self.assertIn("/page/50/1?", api_call.args[0])
        self.assertEqual(
            api_call.kwargs["headers"]["x-jalor-tenantAlias"], "hcm"
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_huawei_empty_result_after_grad_launch_is_successful(self, fetch):
        fetch.side_effect = [
            self._huawei_page(target_launch=True),
            json.dumps(self._huawei_payload([])).encode("utf-8"),
        ]

        self.assertEqual(
            HuaweiCampusCollector(self._huawei_source()).collect(),
            [],
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_huawei_rejects_partial_api_result(self, fetch):
        fetch.side_effect = [
            self._huawei_page(target_launch=True),
            json.dumps(
                self._huawei_payload([self._huawei_job()], total=2)
            ).encode("utf-8"),
        ]

        with self.assertRaisesRegex(
            ValueError, "没有完整返回筛选结果"
        ):
            HuaweiCampusCollector(self._huawei_source()).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_huawei_rejects_unexpected_recruitment_type(self, fetch):
        payload = self._huawei_payload(
            [
                self._huawei_job(
                    studentAbroadPriority="0",
                )
            ]
        )
        fetch.side_effect = [
            self._huawei_page(target_launch=True),
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ]

        with self.assertRaisesRegex(ValueError, "非目标筛选条件"):
            HuaweiCampusCollector(self._huawei_source()).collect()

    @staticmethod
    def _tencent_source(**overrides):
        source = {
            "id": "tencent_china",
            "name": "腾讯",
            "type": "tencent_campus",
            "homepage": "https://join.qq.com/post.html",
            "project_url": (
                "https://join.qq.com/api/v1/position/getProjectMapping"
            ),
            "url": (
                "https://join.qq.com/api/v1/position/searchPosition"
            ),
            "detail_url": "https://join.qq.com/post_detail.html",
            "company": "腾讯",
            "company_type": "私企",
            "target_graduation_date": "2026-11-30",
            "project_include_keywords": ["校园招聘", "应届生"],
            "project_exclude_keywords": ["实习"],
            "location_keywords": [
                "广州",
                "上海",
                "深圳总部",
                "北京",
            ],
            "location_map": {
                "广州": "广州",
                "上海": "上海",
                "深圳总部": "深圳",
                "北京": "北京",
            },
            "include_keywords": [
                "AI",
                "智能体",
                "大模型",
                "数据",
                "算法",
                "开发",
                "技术研究",
                "云计算",
                "数据库",
            ],
            "exclude_keywords": [
                "销售",
                "市场营销",
                "人力资源",
            ],
            "page_size": 50,
            "max_results": 1000,
            "education": (
                "腾讯正式校园招聘岗位，具体学历和专业要求以岗位详情为准"
            ),
            "deadline": "以官方项目页面为准",
        }
        source.update(overrides)
        return source

    @staticmethod
    def _tencent_project_payload():
        return {
            "message": "",
            "status": 0,
            "data": [
                {
                    "id": 2,
                    "recruitType": 2,
                    "recruitTypeName": "实习生",
                    "status": 1,
                    "subProjectList": [
                        {
                            "mappingId": 2,
                            "projectId": "2",
                            "projectName": "应届实习",
                            "recruitYear": "2026",
                            "status": 1,
                            "recruitRangDesc": (
                                "毕业时间：2026年9月1日-2027年12月31日"
                            ),
                        }
                    ],
                },
                {
                    "id": 1,
                    "recruitType": 1,
                    "recruitTypeName": "应届毕业生",
                    "status": 1,
                    "subProjectList": [
                        {
                            "mappingId": 1,
                            "projectId": "1",
                            "projectName": "2026校园招聘",
                            "recruitYear": "2026",
                            "status": 1,
                            "recruitRangDesc": (
                                "毕业时间：2025年1月1日-2026年12月31日"
                            ),
                        }
                    ],
                },
                {
                    "id": 3,
                    "recruitType": 999,
                    "recruitTypeName": "人才专项",
                    "status": 1,
                    "subProjectList": [
                        {
                            "mappingId": 14,
                            "projectId": "14",
                            "projectName": "青云计划-应届生",
                            "recruitYear": "2027",
                            "status": 1,
                            "recruitRangDesc": (
                                "毕业时间：2026年1月-2027年12月毕业的本硕博同学"
                            ),
                        },
                        {
                            "mappingId": 20,
                            "projectId": "20",
                            "projectName": "青云计划-实习生",
                            "recruitYear": "2026",
                            "status": 1,
                            "recruitRangDesc": (
                                "毕业时间：2026年9月以后毕业的本硕博同学"
                            ),
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def _tencent_position(**overrides):
        position = {
            "id": 21275,
            "postId": "1148729229714935808",
            "position": 101,
            "positionTitle": "后台开发",
            "positionFamily": 2,
            "projectId": 1,
            "bgs": "CDG CSIG TEG",
            "workCities": "深圳总部 北京 上海 广州",
            "projectName": "应届毕业生",
            "recruitLabelName": "应届毕业生",
        }
        position.update(overrides)
        return position

    @staticmethod
    def _tencent_search_payload(items, count=None):
        return {
            "message": "",
            "status": 0,
            "data": {
                "positionList": items,
                "count": len(items) if count is None else count,
            },
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_tencent_selects_date_eligible_projects_and_filters_jobs(
        self, fetch
    ):
        qingyun = self._tencent_position(
            id=22025,
            postId="1274356447064759296",
            positionTitle="基于大模型Agent的推荐研究",
            projectId=14,
            projectName="青云计划-应届生",
            recruitLabelName="应届毕业生 青云计划",
            workCities="上海",
        )
        excluded = self._tencent_position(
            id=22030,
            postId="1274356447064759301",
            positionTitle="AI行业销售经理",
            workCities="深圳总部",
        )
        unrelated = self._tencent_position(
            id=22031,
            postId="1274356447064759302",
            positionTitle="视觉设计",
            workCities="广州",
        )
        other_city = self._tencent_position(
            id=22032,
            postId="1274356447064759303",
            positionTitle="数据分析",
            workCities="杭州",
        )
        fetch.side_effect = [
            json.dumps(
                self._tencent_project_payload(), ensure_ascii=False
            ).encode("utf-8"),
            json.dumps(
                self._tencent_search_payload(
                    [
                        self._tencent_position(),
                        qingyun,
                        excluded,
                        unrelated,
                        other_city,
                    ]
                ),
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        jobs = TencentCampusCollector(self._tencent_source()).collect()

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].title, "后台开发")
        self.assertEqual(jobs[0].company, "腾讯")
        self.assertEqual(jobs[0].company_type, "私企")
        self.assertEqual(jobs[0].location, "广州/上海/深圳/北京")
        self.assertEqual(jobs[0].graduation_years, [2025, 2026])
        self.assertIn("2026校园招聘", jobs[0].description)
        self.assertIn(
            "postid=1148729229714935808", jobs[0].url
        )
        self.assertEqual(jobs[1].graduation_years, [2026, 2027])
        self.assertIn("青云计划-应届生", jobs[1].description)

        search_call = fetch.call_args_list[1]
        self.assertEqual(search_call.kwargs["method"], "POST")
        self.assertEqual(
            search_call.kwargs["json_body"]["projectMappingIdList"],
            [1, 14],
        )
        self.assertEqual(
            search_call.kwargs["json_body"]["workCountryType"], 1
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_tencent_returns_empty_when_no_project_covers_graduation(
        self, fetch
    ):
        fetch.return_value = json.dumps(
            self._tencent_project_payload(), ensure_ascii=False
        ).encode("utf-8")

        jobs = TencentCampusCollector(
            self._tencent_source(
                target_graduation_date="2028-11-30"
            )
        ).collect()

        self.assertEqual(jobs, [])
        self.assertEqual(fetch.call_count, 1)

    @patch("job_radar.collectors.fetch_bytes")
    def test_tencent_rejects_partial_api_result(self, fetch):
        fetch.side_effect = [
            json.dumps(
                self._tencent_project_payload(), ensure_ascii=False
            ).encode("utf-8"),
            json.dumps(
                self._tencent_search_payload(
                    [self._tencent_position()], count=2
                ),
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        with self.assertRaisesRegex(
            ValueError, "没有完整返回筛选结果"
        ):
            TencentCampusCollector(self._tencent_source()).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_tencent_rejects_unexpected_project(self, fetch):
        unexpected = self._tencent_position(
            projectId=2,
            projectName="应届实习",
            recruitLabelName="应届实习",
        )
        fetch.side_effect = [
            json.dumps(
                self._tencent_project_payload(), ensure_ascii=False
            ).encode("utf-8"),
            json.dumps(
                self._tencent_search_payload([unexpected]),
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        with self.assertRaisesRegex(ValueError, "非目标招聘项目"):
            TencentCampusCollector(self._tencent_source()).collect()

    @patch("job_radar.collectors.fetch_bytes")
    def test_tencent_rejects_duplicate_post_id(self, fetch):
        duplicate = self._tencent_position(id=21276)
        fetch.side_effect = [
            json.dumps(
                self._tencent_project_payload(), ensure_ascii=False
            ).encode("utf-8"),
            json.dumps(
                self._tencent_search_payload(
                    [self._tencent_position(), duplicate]
                ),
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        with self.assertRaisesRegex(ValueError, "重复 ID"):
            TencentCampusCollector(self._tencent_source()).collect()

    @staticmethod
    def _hsbc_source():
        return {
            "id": "hsbc_china",
            "name": "汇丰中国",
            "type": "hsbc_programme",
            "homepage": (
                "https://www.hsbc.com/careers/"
                "students-and-graduates/"
            ),
            "url": (
                "https://www.hsbc.com/careers/students-and-graduates/"
                "find-a-programme?location=mainland-china&"
                "programme-type=graduate-programme&page=1&take=50"
            ),
            "api_url": (
                "https://www.hsbc.com/api/programmes/get-programmes"
            ),
            "company": "汇丰中国（HSBC）",
            "company_type": "外企",
            "location_keywords": [
                "Guangzhou",
                "Shanghai",
                "Shenzhen",
                "Beijing",
            ],
            "location_map": {
                "Guangzhou": "广州",
                "Shanghai": "上海",
                "Shenzhen": "深圳",
                "Beijing": "北京",
            },
            "include_keywords": [
                "AI",
                "Data",
                "Engineering",
                "Technology",
                "Cyber",
            ],
            "exclude_keywords": [
                "Relationship Management",
                "Sales",
                "Trading",
            ],
            "programme_type": "Graduate Programme",
            "target_opening_start": "2026-07-01",
            "target_opening_end": "2027-06-30",
            "target_start_years": [2027],
            "reference_date": "2026-07-30",
            "max_programmes": 50,
            "graduation_years": [2027],
            "education": "应届毕业生或近期毕业生，具体资格以项目为准",
        }

    @staticmethod
    def _hsbc_fragment(
        external_id,
        title,
        location,
        area,
        description,
        opening,
        closing,
        start="",
    ):
        start_html = (
            '<div class="program-text__group '
            'program-text__group--start">'
            '<dt class="program-text__label">Start Date</dt>'
            '<dd class="program-text__value">{}</dd></div>'.format(start)
            if start
            else ""
        )
        return (
            '<li class="program-item" data-cs-override-id="{external_id}">'
            "<article>"
            '<div class="program-location">{location}</div>'
            '<div class="program-text">'
            '<div class="program-text__area">{area}</div>'
            '<a href="https://apply.careers.hsbc.com/{external_id}" '
            'class="program-text__destination-link">'
            '<h2 class="program-text__destination">{title}</h2></a>'
            '<div class="program-text__short-description">'
            "{description}</div><hr>"
            '<dl class="program-text__groups">'
            '<div class="program-text__group program-text__group--type">'
            '<dt class="program-text__label">Programme type</dt>'
            '<dd class="program-text__value">Graduate Programme</dd>'
            "</div>"
            '<div class="program-text__group '
            'program-text__group--opening">'
            '<dt class="program-text__label">Opening Date</dt>'
            '<dd class="program-text__value">{opening}</dd></div>'
            '<div class="program-text__group '
            'program-text__group--closing">'
            '<dt class="program-text__label">Closing Date</dt>'
            '<dd class="program-text__value">{closing}</dd></div>'
            "{start_html}</dl></div>"
            '<div class="program-text__link"></div>'
            "</article></li>"
        ).format(
            external_id=external_id,
            title=title,
            location=location,
            area=area,
            description=description,
            opening=opening,
            closing=closing,
            start_html=start_html,
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_hsbc_filters_cycle_and_maps_target_programme(self, fetch):
        finder = (
            '<div class="table-module" data-component="ProgramFinder" '
            'data-props-settings="1234567890abcdef1234567890abcdef" '
            'data-props-total-count="3"></div>'
        )
        target = self._hsbc_fragment(
            "programme-engineering-2027",
            "Engineering",
            "Mainland China, Guangzhou",
            "Technology",
            "Develop the next generation of digital banking.",
            "11th Jul 2026",
            "31st Oct 2026",
            "Mon Jul 19, 2027",
        )
        previous_cycle = self._hsbc_fragment(
            "programme-data-2026",
            "Data",
            "Mainland China, Guangzhou",
            "Technology",
            "Turn data into insight.",
            "11th Jul 2025",
            "31st Dec 2025",
        )
        unrelated = self._hsbc_fragment(
            "programme-relationship-2027",
            "Relationship Management",
            "Mainland China",
            "International Wealth and Premier Banking",
            "Build client relationships.",
            "10th Sep 2026",
            "30th Nov 2026",
            "Mon Jul 19, 2027",
        )
        fetch.side_effect = [
            finder.encode("utf-8"),
            json.dumps(
                [target, previous_cycle, unrelated],
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        jobs = HsbcProgrammeCollector(self._hsbc_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            jobs[0].external_id, "programme-engineering-2027"
        )
        self.assertEqual(jobs[0].company, "汇丰中国（HSBC）")
        self.assertEqual(jobs[0].location, "广州")
        self.assertEqual(jobs[0].published_at, "2026-07-11")
        self.assertEqual(jobs[0].deadline, "2026-10-31")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            (
                "https://apply.careers.hsbc.com/"
                "programme-engineering-2027"
            ),
        )
        self.assertIn("预计开始时间：2027-07-19", jobs[0].description)
        self.assertEqual(fetch.call_count, 2)
        api_url = fetch.call_args_list[1].args[0]
        self.assertIn("skip=0", api_url)
        self.assertIn("take=4", api_url)
        self.assertIn("location=mainland-china", api_url)
        self.assertIn("programme-type=graduate-programme", api_url)
        self.assertIn(
            "s=1234567890abcdef1234567890abcdef", api_url
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_hsbc_empty_result_is_successful(self, fetch):
        finder = (
            '<div data-component="ProgramFinder" '
            'data-props-settings="1234567890abcdef1234567890abcdef" '
            'data-props-total-count="0"></div>'
        )
        empty_card = (
            '<li class="content-card"><h2>'
            "Can’t find what you’re looking for?</h2></li>"
        )
        fetch.side_effect = [
            finder.encode("utf-8"),
            json.dumps([empty_card]).encode("utf-8"),
        ]

        jobs = HsbcProgrammeCollector(self._hsbc_source()).collect()

        self.assertEqual(jobs, [])
        self.assertEqual(fetch.call_count, 2)

    @patch("job_radar.collectors.fetch_bytes")
    def test_hsbc_rejects_incomplete_api_result(self, fetch):
        finder = (
            '<div data-component="ProgramFinder" '
            'data-props-settings="1234567890abcdef1234567890abcdef" '
            'data-props-total-count="1"></div>'
        )
        fetch.side_effect = [
            finder.encode("utf-8"),
            json.dumps([]).encode("utf-8"),
        ]

        with self.assertRaisesRegex(ValueError, "没有完整返回"):
            HsbcProgrammeCollector(self._hsbc_source()).collect()

    @staticmethod
    def _shein_source():
        return {
            "id": "shein",
            "name": "SHEIN",
            "type": "shein_campus",
            "homepage": "https://careers.shein.cn/Students-%26-Graduates",
            "url": (
                "https://careers.shein.cn/api/v1/open/grw/front/jobPage"
            ),
            "company": "SHEIN",
            "company_type": "私企",
            "country_ids": ["CHN"],
            "city_ids": ["1003", "1010", "1004", "1008"],
            "job_category_ids": ["xxjsl"],
            "campus_job_type_id": "CAMPUS",
            "target_cycle_keywords": ["2027届", "2027 校招"],
            "include_keywords": ["AI", "数据", "算法", "开发"],
            "exclude_keywords": ["实习", "销售"],
            "page_size": 2,
            "max_pages": 3,
            "graduation_years": [2027],
            "education": "具体学历要求以岗位为准",
            "deadline": "以官方公告及岗位为准",
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_shein_paginates_filters_and_maps_jobs(self, fetch):
        ai_job = {
            "jobId": "shein-ai-2027",
            "jobTitle": "2027届 AI Agent 开发工程师",
            "jobCategoryId": "xxjsl",
            "jobCategoryName": "信息技术类",
            "countryId": "CHN",
            "countryName": "中国",
            "jobTypeId": "CAMPUS",
            "releaseDate": "2026-07-30 09:00:00",
            "description": (
                "负责大模型智能体研发。<br>要求计算机相关专业，硕士优先。"
            ),
            "jobDetailUrl": (
                "https://careers.shein.cn/JobDetail?"
                "jobId=shein-ai-2027"
            ),
            "cityInfos": [
                {"cityId": "1003", "cityName": "广州市", "jobNum": 1},
                {"cityId": "1004", "cityName": "深圳市", "jobNum": 1},
            ],
        }
        previous_cycle = {
            "jobId": "shein-data-2026",
            "jobTitle": "2026届 数据开发工程师",
            "jobCategoryId": "xxjsl",
            "jobCategoryName": "信息技术类",
            "countryId": "CHN",
            "countryName": "中国",
            "jobTypeId": "CAMPUS",
            "releaseDate": "2025-08-01 09:00:00",
            "description": "负责数据仓库开发。",
            "jobDetailUrl": "https://careers.shein.cn/old",
            "cityInfos": [
                {"cityId": "1010", "cityName": "上海市", "jobNum": 1}
            ],
        }
        sales_job = {
            "jobId": "shein-sales-2027",
            "jobTitle": "2027 校招 数据产品销售",
            "jobCategoryId": "xxjsl",
            "jobCategoryName": "信息技术类",
            "countryId": "CHN",
            "countryName": "中国",
            "jobTypeId": "CAMPUS",
            "releaseDate": "2026-07-30 10:00:00",
            "description": "负责数据产品销售。",
            "jobDetailUrl": "https://careers.shein.cn/sales",
            "cityInfos": [
                {"cityId": "1008", "cityName": "北京市", "jobNum": 1}
            ],
        }
        fetch.side_effect = [
            json.dumps(
                {
                    "code": "0",
                    "msg": "OK",
                    "info": {
                        "current": 1,
                        "size": 2,
                        "total": 3,
                        "records": [ai_job, previous_cycle],
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            json.dumps(
                {
                    "code": "0",
                    "msg": "OK",
                    "info": {
                        "current": 2,
                        "size": 2,
                        "total": 3,
                        "records": [sales_job],
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        jobs = SheinCampusCollector(self._shein_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "shein-ai-2027")
        self.assertEqual(jobs[0].title, "2027届 AI Agent 开发工程师")
        self.assertEqual(jobs[0].company, "SHEIN")
        self.assertEqual(jobs[0].location, "广州市/深圳市")
        self.assertEqual(jobs[0].education, "硕士")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            "https://careers.shein.cn/JobDetail?"
            "jobId=shein-ai-2027",
        )
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(
            fetch.call_args_list[0].kwargs["json_body"],
            {
                "current": 1,
                "cityName": "",
                "countryIds": ["CHN"],
                "cityIds": ["1003", "1010", "1004", "1008"],
                "jobCategoryIds": ["xxjsl"],
                "jobTypeIds": ["CAMPUS"],
                "key": "",
                "langCode": "CN",
                "size": 2,
            },
        )
        self.assertEqual(
            fetch.call_args_list[1].kwargs["json_body"]["current"], 2
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_shein_empty_result_is_successful(self, fetch):
        fetch.return_value = json.dumps(
            {
                "code": "0",
                "msg": "OK",
                "info": {
                    "current": 1,
                    "size": 100,
                    "total": 0,
                    "records": [],
                },
            }
        ).encode("utf-8")
        source = self._shein_source()
        source["page_size"] = 100

        jobs = SheinCampusCollector(source).collect()

        self.assertEqual(jobs, [])
        fetch.assert_called_once()

    @patch("job_radar.collectors.fetch_bytes")
    def test_shein_rejects_changed_job_schema(self, fetch):
        fetch.return_value = json.dumps(
            {
                "code": "0",
                "msg": "OK",
                "info": {
                    "current": 1,
                    "size": 2,
                    "total": 1,
                    "positions": [],
                },
            }
        ).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "岗位分页结构异常"):
            SheinCampusCollector(self._shein_source()).collect()

    @staticmethod
    def _netease_game_source():
        return {
            "id": "netease_game",
            "name": "网易广州（网易游戏互娱）",
            "type": "netease_game_campus",
            "navigation_url": (
                "https://campus.game.163.com/api/campuspc/"
                "project/navigation/list"
            ),
            "url": (
                "https://campus.game.163.com/api/campuspc/"
                "position/getJobList"
            ),
            "detail_url_template": (
                "https://campus.game.163.com/app/detail/index"
                "?id={position_id}"
            ),
            "company": "网易游戏（互娱）",
            "company_type": "私企",
            "project_group_title": "应届生",
            "target_project_keywords": ["网易互娱2027届校园招聘"],
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "include_keywords": [
                "AI",
                "人工智能",
                "Agent",
                "大模型",
                "数据",
                "算法",
                "软件",
                "开发",
                "测试",
            ],
            "exclude_keywords": ["销售", "市场营销"],
            "exclude_position_types": ["游戏艺术", "游戏策划", "运营"],
            "excluded_type_title_exceptions": ["AI+策划"],
            "page_size": 2,
            "max_pages": 3,
            "graduation_years": [2027],
            "education": "具体学历及海外毕业时间要求以岗位为准",
            "deadline": "以官方公告及岗位为准",
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_netease_game_returns_empty_without_target_project(self, fetch):
        fetch.return_value = json.dumps(
            {
                "code": 200,
                "data": [
                    {
                        "title": "应届生",
                        "children": [
                            {
                                "title": "网易互联网2026届校园招聘",
                                "link": (
                                    "https://campus.163.com/app/job/"
                                    "position?id=69"
                                ),
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")

        jobs = NeteaseGameCampusCollector(
            self._netease_game_source()
        ).collect()

        self.assertEqual(jobs, [])
        fetch.assert_called_once_with(
            (
                "https://campus.game.163.com/api/campuspc/"
                "project/navigation/list"
            )
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_netease_game_paginates_filters_and_maps_jobs(self, fetch):
        navigation = {
            "code": 200,
            "data": [
                {
                    "title": "应届生",
                    "children": [
                        {
                            "title": "网易互娱2027届校园招聘",
                            "link": (
                                "https://campus.game.163.com/app/job/"
                                "position?id=102"
                            ),
                        }
                    ],
                }
            ],
        }
        ai_job = {
            "id": 4732,
            "positionName": "AI Agent 工程师（游戏研发方向）",
            "projectId": 102,
            "positionTypeName": "游戏程序",
            "workPlaceName": "杭州,上海,广州",
            "positionDescription": "负责多模态 Agent 研发。",
            "positionRequirement": "本科及以上，2027届应届生。",
            "tagList": [{"id": 1, "name": "AI Agent开发"}],
            "updateTime": 1782095833791,
        }
        art_job = {
            "id": 4733,
            "positionName": "游戏技术美术工程师",
            "projectId": 102,
            "positionTypeName": "游戏艺术",
            "workPlaceName": "杭州,上海,广州",
            "positionDescription": "负责 AIGC 艺术制作。",
            "positionRequirement": "本科及以上。",
            "tagList": [{"id": 2, "name": "艺术×技术跨界"}],
        }
        overseas_job = {
            "id": 4771,
            "positionName": "Application Development Engineer",
            "projectId": 102,
            "positionTypeName": "技术",
            "workPlaceName": "新加坡",
            "positionDescription": "Software development.",
            "positionRequirement": "Bachelor degree.",
            "tagList": [],
        }
        fetch.side_effect = [
            json.dumps(navigation, ensure_ascii=False).encode("utf-8"),
            json.dumps(
                {
                    "code": 200,
                    "data": {
                        "total": 3,
                        "pages": 2,
                        "list": [ai_job, art_job],
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            json.dumps(
                {
                    "code": 200,
                    "data": {
                        "total": 3,
                        "pages": 2,
                        "list": [overseas_job],
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        jobs = NeteaseGameCampusCollector(
            self._netease_game_source()
        ).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "4732")
        self.assertEqual(jobs[0].company, "网易游戏（互娱）")
        self.assertEqual(jobs[0].location, "杭州,上海,广州")
        self.assertEqual(jobs[0].education, "本科")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            "https://campus.game.163.com/app/detail/index?id=4732",
        )
        self.assertEqual(jobs[0].published_at, "2026-06-22 10:37:13")
        self.assertIn("AI Agent开发", jobs[0].description)
        self.assertIn("多模态 Agent", jobs[0].description)
        self.assertEqual(fetch.call_count, 3)
        self.assertIn("page=2", fetch.call_args_list[2].args[0])

    @patch("job_radar.collectors.fetch_bytes")
    def test_netease_game_rejects_changed_position_schema(self, fetch):
        fetch.side_effect = [
            json.dumps(
                {
                    "code": 200,
                    "data": [
                        {
                            "title": "应届生",
                            "children": [
                                {
                                    "title": "网易互娱2027届校园招聘",
                                    "link": (
                                        "https://campus.game.163.com/app/job/"
                                        "position?id=102"
                                    ),
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            json.dumps(
                {
                    "code": 200,
                    "data": {"total": 1, "pages": 1, "positions": []},
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        with self.assertRaisesRegex(ValueError, "岗位分页结构异常"):
            NeteaseGameCampusCollector(
                self._netease_game_source()
            ).collect()

    @staticmethod
    def _moka_source():
        return {
            "id": "vipshop",
            "name": "唯品会",
            "type": "moka_campus",
            "homepage": (
                "https://app-tc.mokahr.com/campus-recruitment/"
                "vipshophr/10039/"
            ),
            "url": (
                "https://app-tc.mokahr.com/api/outer/ats-apply/"
                "website/jobs/v2"
            ),
            "detail_url": (
                "https://app-tc.mokahr.com/api/outer/ats-apply/"
                "website/job"
            ),
            "org_id": "vipshophr",
            "site_id": 10039,
            "company": "唯品会（中国）有限公司",
            "company_type": "私企",
            "location": "工作地点待官网岗位详情补充",
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "target_cycle_keywords": ["2027届"],
            "include_keywords": ["AI", "数据", "软件", "开发", "算法"],
            "exclude_keywords": ["销售", "客服"],
            "exclude_title_keywords": ["实习"],
            "exclude_commitments": ["实习"],
            "page_size": 30,
            "max_pages": 5,
            "graduation_years": [2027],
            "education": "具体学历要求以岗位为准",
            "deadline": "以官方岗位为准",
        }

    def test_moka_decodes_public_api_envelope(self):
        aes_iv = "de7c21ed8d6f50fe"
        key = "5a5eeeb86f96244e"
        payload = {
            "code": 0,
            "data": {"jobs": [], "jobStats": {"total": 0}},
            "msg": "成功",
            "success": True,
        }
        plaintext = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = AES.new(
            key.encode("utf-8"),
            AES.MODE_CBC,
            aes_iv.encode("utf-8"),
        ).encrypt(pad(plaintext, AES.block_size))
        envelope = json.dumps(
            {
                "data": base64.b64encode(encrypted).decode("ascii"),
                "necromancer": key,
            }
        ).encode("utf-8")

        data = MokaCampusCollector._decode_api_payload(
            envelope,
            aes_iv,
            "岗位列表",
        )

        self.assertEqual(data["jobStats"]["total"], 0)
        self.assertEqual(data["jobs"], [])

    @patch("job_radar.collectors.urlopen_with_retry")
    def test_moka_uses_configured_origin_for_custom_portal(self, request_url):
        response = request_url.return_value.__enter__.return_value
        response.read.return_value = json.dumps(
            {
                "code": 0,
                "data": {"jobs": [], "jobStats": {"total": 0}},
                "success": True,
            }
        ).encode("utf-8")
        source = self._moka_source()
        source["origin"] = "https://apply.careers.dji.com"

        MokaCampusCollector(source)._post(
            MagicMock(),
            source["url"],
            {"orgId": source["org_id"]},
            "de7c21ed8d6f50fe",
            "岗位列表",
        )

        request = request_url.call_args.args[0]
        self.assertEqual(
            request.get_header("Origin"),
            "https://apply.careers.dji.com",
        )

    @patch("job_radar.collectors.urlopen_with_retry")
    def test_moka_rejects_portal_without_required_cycle(self, request_url):
        source = self._moka_source()
        source["portal_required_keywords"] = ["2027拓疆者校园招聘"]
        init_data = {
            "org": {
                "id": source["org_id"],
                "name": source["company"],
            },
            "siteId": source["site_id"],
            "mode": "campus",
            "aesIv": "de7c21ed8d6f50fe",
            "pages": [{"title": "2026校园招聘"}],
        }
        response = request_url.return_value.__enter__.return_value
        response.read.return_value = (
            '<input id="init-data" value="{}">'.format(
                html.escape(
                    json.dumps(init_data, ensure_ascii=False),
                    quote=True,
                )
            )
        ).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "未匹配目标招聘届别"):
            MokaCampusCollector(source)._portal_data(MagicMock())

    @patch("job_radar.collectors.fetch_bytes")
    def test_moka_rejects_campaign_page_without_required_cycle(self, fetch):
        fetch.return_value = (
            "<title>中兴通讯2026届未来领军人才招聘</title>"
        ).encode("utf-8")
        source = self._moka_source()
        source["campaign_url"] = "https://example.com/campus-news"
        source["campaign_required_keywords"] = [
            "中兴通讯2027届未来领军人才招聘正式启动"
        ]

        with self.assertRaisesRegex(ValueError, "未匹配目标招聘届别"):
            MokaCampusCollector(source)._validate_campaign()

        fetch.assert_called_once_with(
            source["campaign_url"],
            timeout=20,
        )

    def test_moka_maps_configured_city_ids(self):
        source = self._moka_source()
        source["city_id_map"] = {
            "440100": "广州市",
            "440300": "深圳市",
        }

        location = MokaCampusCollector(source)._locations(
            [{"cityId": 440100}, {"cityId": 440300}]
        )

        self.assertEqual(location, "广州市、深圳市")

    @patch.object(MokaCampusCollector, "_detail")
    @patch.object(MokaCampusCollector, "_positions")
    @patch.object(MokaCampusCollector, "_portal_data")
    def test_moka_filters_internships_and_maps_target_job(
        self,
        portal,
        positions,
        detail,
    ):
        portal.return_value = {"aesIv": "de7c21ed8d6f50fe"}
        positions.return_value = [
            {
                "id": "formal-ai",
                "title": "【2027届校园招聘】AI应用开发工程师",
            },
            {
                "id": "intern-dev",
                "title": "【2027届实习生】中台研发",
            },
            {
                "id": "previous-data",
                "title": "【2026届校园招聘】数据分析师",
            },
        ]
        detail.return_value = {
            "id": "formal-ai",
            "orgId": "vipshophr",
            "status": "open",
            "title": "【2027届校园招聘】AI应用开发工程师",
            "commitment": "全职",
            "education": "硕士",
            "locations": [{"id": 1, "name": "广州市"}],
            "department": {"id": 2, "name": "人工智能部"},
            "zhineng": {"id": 3, "name": "技术类"},
            "jobDescription": (
                "<p>负责大模型、Agent与数据平台研发。</p>"
            ),
            "publishedAt": "2026-08-01T09:30:00",
        }

        jobs = MokaCampusCollector(self._moka_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "formal-ai")
        self.assertEqual(jobs[0].company, "唯品会（中国）有限公司")
        self.assertEqual(jobs[0].location, "广州市")
        self.assertEqual(jobs[0].education, "硕士")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            (
                "https://app-tc.mokahr.com/campus-recruitment/"
                "vipshophr/10039#/job/formal-ai"
            ),
        )
        self.assertIn("招聘性质：全职", jobs[0].description)
        self.assertIn("大模型、Agent与数据平台研发", jobs[0].description)
        detail.assert_called_once()

    @patch.object(MokaCampusCollector, "_detail")
    @patch.object(MokaCampusCollector, "_positions")
    @patch.object(MokaCampusCollector, "_portal_data")
    def test_moka_returns_empty_for_current_internship_only_jobs(
        self,
        portal,
        positions,
        detail,
    ):
        portal.return_value = {"aesIv": "de7c21ed8d6f50fe"}
        positions.return_value = [
            {
                "id": "intern-1",
                "title": "【2027届实习生】中台研发",
            },
            {
                "id": "intern-2",
                "title": "【2027届实习生】鸿蒙开发",
            },
            {
                "id": "intern-3",
                "title": "【2027届实习生】IOS开发",
            },
        ]

        jobs = MokaCampusCollector(self._moka_source()).collect()

        self.assertEqual(jobs, [])
        detail.assert_not_called()

    @patch.object(MokaCampusCollector, "_detail")
    @patch.object(MokaCampusCollector, "_positions")
    @patch.object(MokaCampusCollector, "_portal_data")
    def test_moka_can_use_complete_list_items_and_prefilter_titles(
        self,
        portal,
        positions,
        detail,
    ):
        portal.return_value = {"aesIv": "de7c21ed8d6f50fe"}
        positions.return_value = [
            {
                "id": "ai-1",
                "orgId": "vipshophr",
                "status": "open",
                "title": "AI 算法工程师（深圳）",
                "commitment": "全职",
                "locations": [{"name": "深圳市"}],
                "jobDescription": "负责人工智能算法研发。",
                "projectFolder": {"id": 100120257, "name": "未来领军"},
            },
            {"id": "sales-1", "title": "渠道销售（深圳）"},
        ]
        source = self._moka_source()
        source.pop("target_cycle_keywords")
        source["prefilter_title_keywords"] = ["AI", "数据", "软件"]
        source["details_in_list"] = True
        source["target_project_ids"] = [100120257]

        jobs = MokaCampusCollector(source).collect()

        self.assertEqual([job.external_id for job in jobs], ["ai-1"])
        detail.assert_not_called()

    @staticmethod
    def _cvte_source():
        return {
            "id": "cvte",
            "name": "视源股份（CVTE）",
            "type": "cvte_campus",
            "projects_url": "https://campus.cvte.com/api/project",
            "url": "https://campus.cvte.com/api/position",
            "company": "视源股份（CVTE）",
            "company_type": "私企",
            "location_keywords": ["广州", "上海", "深圳", "北京", "全国"],
            "target_project_keywords": ["2027届"],
            "full_time_property_names": ["全职岗位"],
            "include_keywords": [
                "AI",
                "人工智能",
                "智能体",
                "大模型",
                "数据",
                "算法",
                "机器学习",
                "软件",
            ],
            "exclude_title_keywords": ["博士", "博士后"],
            "exclude_keywords": ["销售", "市场营销"],
            "graduation_years": [2027],
            "education": "具体学历及海外毕业时间要求以岗位为准",
        }

    @patch("job_radar.collectors.fetch_bytes")
    def test_cvte_returns_empty_without_target_cycle_project(self, fetch):
        fetch.return_value = json.dumps(
            {
                "projects": [
                    {
                        "id": "previous",
                        "name": "CVTE2026届校园招聘",
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")

        jobs = CvteCampusCollector(self._cvte_source()).collect()

        self.assertEqual(jobs, [])
        fetch.assert_called_once_with(
            "https://campus.cvte.com/api/project"
        )

    @patch("job_radar.collectors.fetch_bytes")
    def test_cvte_filters_full_time_target_jobs_and_maps_fields(self, fetch):
        fetch.side_effect = [
            json.dumps(
                {
                    "projects": [
                        {
                            "id": "project-2027",
                            "name": "CVTE2027届实习生项目",
                            "endTime": 1786345501403,
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            json.dumps(
                {
                    "projectPositions": [
                        {
                            "id": "ai-app",
                            "name": "AI 应用工程师",
                            "duty": "负责 RAG 与智能体应用落地。",
                            "requirement": "计算机相关专业，熟练 Python。",
                            "projectId": "project-2027",
                            "projectName": "CVTE2027届实习生项目",
                            "typeName": "软件类",
                            "propertyName": "全职岗位",
                            "areaViews": [{"cityName": "广州"}],
                            "updatedTime": 1782095833791,
                        },
                        {
                            "id": "ai-intern",
                            "name": "AI 算法实习生",
                            "duty": "负责模型训练。",
                            "requirement": "计算机相关专业。",
                            "projectId": "project-2027",
                            "projectName": "CVTE2027届实习生项目",
                            "typeName": "算法类",
                            "propertyName": "实习岗位",
                            "areaViews": [{"cityName": "广州"}],
                        },
                        {
                            "id": "ai-phd",
                            "name": "多模态大模型高级研究员 - 博士",
                            "duty": "负责大模型研发。",
                            "requirement": "博士学历。",
                            "projectId": "project-2027",
                            "projectName": "CVTE2027届实习生项目",
                            "typeName": "研究院",
                            "propertyName": "全职岗位",
                            "areaViews": [{"cityName": "广州"}],
                        },
                        {
                            "id": "sales",
                            "name": "销售工程师",
                            "duty": "负责市场销售。",
                            "requirement": "沟通能力强。",
                            "projectId": "project-2027",
                            "projectName": "CVTE2027届实习生项目",
                            "typeName": "商务类",
                            "propertyName": "全职岗位",
                            "areaViews": [{"cityName": "广州"}],
                        },
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        jobs = CvteCampusCollector(self._cvte_source()).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "ai-app")
        self.assertEqual(jobs[0].title, "AI 应用工程师")
        self.assertEqual(jobs[0].location, "广州")
        self.assertEqual(
            jobs[0].url,
            "https://campus.cvte.com/position/ai-app",
        )
        self.assertEqual(jobs[0].published_at, "2026-06-22 10:37:13")
        self.assertEqual(jobs[0].deadline, "2026-08-10 15:05:01")
        self.assertIn("招聘性质：全职岗位", jobs[0].description)
        self.assertIn("RAG 与智能体", jobs[0].description)

    @patch("job_radar.collectors.fetch_bytes")
    def test_cvte_rejects_changed_position_schema(self, fetch):
        fetch.side_effect = [
            json.dumps(
                {
                    "projects": [
                        {
                            "id": "project-2027",
                            "name": "CVTE2027届校园招聘",
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            json.dumps(
                {"positions": []},
                ensure_ascii=False,
            ).encode("utf-8"),
        ]

        with self.assertRaisesRegex(
            ValueError, "缺少 projectPositions 数组"
        ):
            CvteCampusCollector(self._cvte_source()).collect()

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
    def test_hotjob_campus_accepts_fresh_ey_ai_graduate_job(self, fetch):
        source = {
            **self._hotjob_source(),
            "id": "ey_china",
            "name": "安永中国",
            "company": "安永中国",
            "company_type": "外企",
            "prefer_source_company": True,
            "omit_post_type_name": True,
            "description": "安永中国大陆 AI 创新中心应届生岗位",
            "target_keywords": ["2027届", "应届生招聘", "毕业生招聘"],
            "include_keywords": ["AI", "人工智能", "数据", "技术"],
            "exclude_keywords": ["实习", "2025-2026"],
            "graduation_years": [],
        }
        fetch.return_value = self._hotjob_page(
            [
                {
                    "postId": "ey-ai-graduate",
                    "postName": "安永中国AI创新中心应届生招聘",
                    "projectName": "安永中国AI创新中心应届生招聘",
                    "postTypeName": "应届生招聘",
                    "company": "安永校园招聘",
                    "workPlaceStr": "上海市",
                    "educationStr": "本科及以上",
                    "publishFirstDate": "2026-07-13 00:00:00",
                    "endDate": "2026-11-21 23:59:59",
                },
                {
                    "postId": "ey-spring-2026",
                    "postName": "2025-2026年安永春季应届毕业生校园招聘项目",
                    "projectName": "2025-2026年安永春季应届毕业生校园招聘项目",
                    "company": "安永校园招聘",
                    "workPlaceStr": "全部地区",
                    "publishFirstDate": "2026-04-10 00:00:00",
                },
            ]
        )

        jobs = HotjobCampusCollector(source).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "ey-ai-graduate")
        self.assertEqual(jobs[0].company, "安永中国")
        self.assertEqual(jobs[0].location, "上海市")
        self.assertEqual(
            jobs[0].description,
            "安永中国大陆 AI 创新中心应届生岗位",
        )
        self.assertEqual(jobs[0].graduation_years, [])
        self.assertEqual(jobs[0].deadline, "2026-11-21 23:59:59")

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

    @staticmethod
    def _liepin_static_source():
        return {
            "id": "guangdong_guangwu_liepin",
            "name": "广东省广物控股集团猎聘校招",
            "type": "liepin_static_campus",
            "homepage": "https://xy.example.com/guangwu/job.html",
            "url": "https://xy.example.com/guangwu/js/job3.json",
            "required_text": "招聘岗位",
            "company_type": "国企",
            "location_keywords": ["广州", "上海", "深圳", "北京"],
            "include_keywords": [
                "AI",
                "人工智能",
                "数据",
                "软件",
                "数字化",
            ],
            "exclude_keywords": ["销售", "客服", "实习"],
            "target_campaign_keywords": [
                "广物控股集团2027届校园招聘"
            ],
            "previous_campaign_keywords": [
                "广物控股集团2026届校园招聘"
            ],
            "graduation_years": [2027],
            "deadline": "招满即止",
        }

    @staticmethod
    def _liepin_static_jobs():
        return [
            {
                "data-id": 20633,
                "所属企业": "广物金属",
                "company": "广东广物金属产业集团有限公司",
                "Department": "数字化中心",
                "jobName": "AI解决方案专家",
                "Category": "技术类",
                "recruits": 1,
                "edu": "硕士及以上",
                "major": "计算机、人工智能、数据科学",
                "Salary": "8000-10000",
                "address": "广州市",
                "job_requirements": "熟悉大模型及机器学习。",
                "job_description": "负责AI解决方案与数据分析。",
                "link": (
                    "https://www.duomian.com/job/"
                    "target-agent.shtml"
                ),
            },
            {
                "data-id": 20643,
                "所属企业": "广物汽贸",
                "company": "广物汽贸股份有限公司",
                "Department": "业务部",
                "jobName": "数据产品销售岗",
                "Category": "业务类",
                "edu": "本科及以上",
                "major": "不限",
                "address": "深圳市",
                "job_requirements": "负责软件产品销售。",
                "job_description": "客户拓展。",
                "link": (
                    "https://www.duomian.com/job/"
                    "sales-data.shtml"
                ),
            },
            {
                "data-id": 20609,
                "所属企业": "电子口岸",
                "company": "广东省电子口岸管理有限公司",
                "Department": "研发部",
                "jobName": "软件开发岗",
                "Category": "技术类",
                "edu": "本科及以上",
                "major": "计算机",
                "address": "东莞市",
                "job_requirements": "熟悉Python。",
                "job_description": "负责系统开发。",
                "link": (
                    "https://www.duomian.com/job/"
                    "other-city.shtml"
                ),
            },
        ]

    @patch("job_radar.collectors.fetch_bytes")
    def test_liepin_static_campus_filters_cycle_and_maps_jobs(self, fetch):
        fetch.side_effect = [
            "<main>招聘岗位</main>".encode("utf-8"),
            json.dumps(
                self._liepin_static_jobs(), ensure_ascii=False
            ).encode("utf-8"),
            (
                "<main><a href='/project'>"
                "广物控股集团2027届校园招聘</a></main>"
            ).encode("utf-8"),
        ]

        jobs = LiepinStaticCampusCollector(
            self._liepin_static_source()
        ).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "target-agent")
        self.assertEqual(jobs[0].title, "AI解决方案专家")
        self.assertEqual(
            jobs[0].company,
            "广东广物金属产业集团有限公司",
        )
        self.assertEqual(jobs[0].location, "广州市")
        self.assertEqual(jobs[0].education, "硕士及以上")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(jobs[0].deadline, "招满即止")
        self.assertIn("大模型", jobs[0].description)
        self.assertEqual(fetch.call_count, 3)

    @patch("job_radar.collectors.fetch_bytes")
    def test_liepin_static_campus_returns_empty_for_previous_cycle(self, fetch):
        fetch.side_effect = [
            "<main>招聘岗位</main>".encode("utf-8"),
            json.dumps(
                self._liepin_static_jobs(), ensure_ascii=False
            ).encode("utf-8"),
            (
                "<main><a href='/project'>"
                "广物控股集团2026届校园招聘</a></main>"
            ).encode("utf-8"),
        ]

        jobs = LiepinStaticCampusCollector(
            self._liepin_static_source()
        ).collect()

        self.assertEqual(jobs, [])
        self.assertEqual(fetch.call_count, 3)

    @patch("job_radar.collectors.fetch_bytes")
    def test_liepin_static_campus_rejects_changed_schema(self, fetch):
        fetch.side_effect = [
            "<main>招聘岗位</main>".encode("utf-8"),
            json.dumps(
                [{"jobName": "数据分析岗"}], ensure_ascii=False
            ).encode("utf-8"),
        ]

        with self.assertRaisesRegex(ValueError, "缺少必要字段"):
            LiepinStaticCampusCollector(
                self._liepin_static_source()
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
    def test_pony_ai_campaign_watch_waits_for_official_2027_marker(self, fetch):
        fetch.return_value = (
            "<main><h1>小马智行校园招聘</h1>"
            "<p>面试9月上旬开始，Offer预计11月发放</p></main>"
            '<script>const nextCampaign = "2027届校园招聘"</script>'
        ).encode("utf-8")
        source = {
            "id": "pony_ai",
            "name": "小马智行",
            "type": "campaign_watch",
            "homepage": "https://campus.pony.ai/",
            "required_text": "校园招聘",
            "target_keywords": ["2027届校园招聘", "2027届秋招"],
            "title": "小马智行2027届校园招聘已启动",
        }

        jobs = CampaignWatchCollector(source).collect()

        self.assertEqual(jobs, [])

    @patch("job_radar.collectors.fetch_bytes")
    def test_pony_ai_campaign_watch_emits_official_2027_link(self, fetch):
        fetch.return_value = (
            "<main><h1>小马智行2027届校园招聘</h1>"
            '<a href="https://ponyai.jobs.feishu.cn/ponycampus">'
            "2027届校园招聘职位</a></main>"
        ).encode("utf-8")
        source = {
            "id": "pony_ai",
            "name": "小马智行",
            "type": "campaign_watch",
            "homepage": "https://campus.pony.ai/",
            "required_text": "校园招聘",
            "target_keywords": ["2027届校园招聘", "2027届秋招"],
            "link_keywords": ["2027", "校园招聘"],
            "external_id": "pony-ai-campus-2027-launch",
            "title": "小马智行2027届校园招聘已启动",
            "company": "小马智行",
            "company_type": "私企",
            "location": "广州、上海、深圳、北京等（以具体岗位为准）",
            "graduation_years": [2027],
        }

        jobs = CampaignWatchCollector(source).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "pony-ai-campus-2027-launch")
        self.assertEqual(
            jobs[0].url, "https://ponyai.jobs.feishu.cn/ponycampus"
        )
        self.assertEqual(jobs[0].graduation_years, [2027])

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
    def test_weride_web_notice_maps_official_2027_campaign(self, fetch):
        fetch.return_value = (
            "<h1>文远知行2027届秋季校招启动</h1>"
            "<p>文远知行2027届秋季校园招聘已全面启动！</p>"
        ).encode("utf-8")
        source = {
            "id": "weride",
            "name": "文远知行",
            "type": "web_notice",
            "homepage": "https://www.weride.ai/zh/posts/campus-2027",
            "url": "https://app.mokahr.com/campus_apply/jingchi/2137",
            "required_text": "文远知行2027届秋季校园招聘已全面启动",
            "external_id": "weride-campus-2027-launch",
            "title": "文远知行2027届秋季校园招聘已全面启动",
            "company": "文远知行",
            "company_type": "私企",
            "location": "广州、北京等（以具体岗位为准）",
            "graduation_years": [2027],
        }

        jobs = WebNoticeCollector(source).collect()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "weride-campus-2027-launch")
        self.assertEqual(jobs[0].company, "文远知行")
        self.assertEqual(jobs[0].graduation_years, [2027])
        self.assertEqual(
            jobs[0].url,
            "https://app.mokahr.com/campus_apply/jingchi/2137",
        )
        fetch.assert_called_once_with(source["homepage"])

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
