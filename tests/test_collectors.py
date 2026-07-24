import json
import unittest
from unittest.mock import patch

from job_radar.collectors import JsonApiCollector, WebNoticeCollector


class CollectorTests(unittest.TestCase):
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
            "company": "广汽集团",
            "company_type": "国企",
            "location": "广州",
            "graduation_years": [2027],
        }
        jobs = WebNoticeCollector(source).collect()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].graduation_years, [2027])


if __name__ == "__main__":
    unittest.main()
