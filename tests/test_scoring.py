import json
import unittest
from pathlib import Path

from job_radar.models import JobPosting
from job_radar.scoring import score_job


ROOT = Path(__file__).resolve().parents[1]


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(
            (ROOT / "configs/profile.example.json").read_text(encoding="utf-8")
        )

    def test_guangzhou_ai_state_owned_job_scores_high(self):
        job = JobPosting(
            title="AI智能体开发工程师（2026届）",
            company="测试国企",
            company_type="国企",
            location="广州",
            url="https://example.com/1",
            source_name="测试",
            description="Python 大模型应用",
            graduation_years=[2026],
        )
        score, reasons = score_job(job, self.profile)
        self.assertGreaterEqual(score, 80)
        self.assertEqual(job.eligibility, "符合")
        self.assertTrue(any("广州" in reason for reason in reasons))

    def test_sales_role_is_penalized(self):
        job = JobPosting(
            title="AI产品销售",
            company="测试公司",
            company_type="私企",
            location="北京",
            url="https://example.com/2",
            source_name="测试",
            description="负责销售",
            graduation_years=[2026],
        )
        score, _ = score_job(job, self.profile)
        self.assertLess(score, self.profile["minimum_score"])

    def test_next_campaign_year_requires_review_without_penalty(self):
        job = JobPosting(
            title="数据开发工程师（2027届）",
            company="测试央企",
            company_type="央企",
            location="广州",
            url="https://example.com/3",
            source_name="测试",
            graduation_years=[2027],
        )
        score, _ = score_job(job, self.profile)
        self.assertEqual(job.eligibility, "需核对")
        self.assertGreater(score, self.profile["minimum_score"])


if __name__ == "__main__":
    unittest.main()
