import base64
import json
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from job_radar.collectors import (
    AccentureEarlyCareerCollector,
    BeisenLegacyCampusCollector,
    BeisenPortalCampaignCollector,
    CampaignWatchCollector,
    ChinaSouthernPowerGridCollector,
    CvteCampusCollector,
    GdrcGroupCollector,
    GdutCampusNoticeCollector,
    GiihgCampusCollector,
    GzRecruitCompanyCollector,
    HotjobCampusCollector,
    HsbcProgrammeCollector,
    IguopinCompanyCollector,
    JsonApiCollector,
    LiepinStaticCampusCollector,
    MokaCampusCollector,
    NeteaseGameCampusCollector,
    NoticeJsonCollector,
    PwcGraduateCampaignCollector,
    SheinCampusCollector,
    WebNoticeCollector,
    ZhaopinCampusCompanyCollector,
)


class CollectorTests(unittest.TestCase):
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
