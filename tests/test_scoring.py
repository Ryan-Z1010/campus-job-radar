import json
import unittest
from pathlib import Path

from job_radar.models import JobPosting
from job_radar.scoring import evaluate_eligibility, recruitment_window, score_job


ROOT = Path(__file__).resolve().parents[1]


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(
            (ROOT / "configs/profile.example.json").read_text(encoding="utf-8")
        )

    def test_city_does_not_add_score_to_state_owned_job(self):
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
        self.assertEqual(score, 84)
        self.assertEqual(job.eligibility, "符合")
        self.assertFalse(any("城市" in reason for reason in reasons))

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

    def test_bare_2027_cohort_is_eligible(self):
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
        self.assertEqual(job.eligibility, "符合")
        self.assertGreater(score, self.profile["minimum_score"])

    def test_accepted_spring_window_makes_2027_role_eligible(self):
        job = JobPosting(
            title="数据开发工程师（2027春招）",
            company="测试央企",
            company_type="央企",
            location="广州",
            url="https://example.com/4",
            source_name="测试",
            description="面向2027春季校园招聘",
            graduation_years=[2027],
        )

        self.assertEqual(recruitment_window(job), "2027春招")
        self.assertEqual(evaluate_eligibility(job, self.profile), "符合")

    def test_accepted_2027_campus_window_makes_2027_role_eligible(self):
        job = JobPosting(
            title="大模型应用开发工程师（2027届校园招聘）",
            company="测试央企",
            company_type="央企",
            location="广州",
            url="https://example.com/4-campus",
            source_name="测试",
            graduation_years=[2027],
        )

        self.assertEqual(recruitment_window(job), "2027校招")
        self.assertEqual(evaluate_eligibility(job, self.profile), "符合")

    def test_source_name_can_provide_official_campus_window_evidence(self):
        job = JobPosting(
            title="算法工程师",
            company="测试央企",
            company_type="央企",
            location="广州",
            url="https://example.com/4-source",
            source_name="测试央企2027届校园招聘",
            graduation_years=[2027],
        )

        self.assertEqual(recruitment_window(job), "2027校招")
        self.assertEqual(evaluate_eligibility(job, self.profile), "符合")

    def test_publication_date_can_identify_2027_spring_window(self):
        job = JobPosting(
            title="算法工程师（2027届）",
            company="测试外企",
            company_type="外企",
            location="上海",
            url="https://example.com/5",
            source_name="测试",
            description="面向应届毕业生",
            graduation_years=[2027],
            published_at="2027-03-15T09:00:00+08:00",
        )

        self.assertEqual(recruitment_window(job), "2027春招")
        self.assertEqual(evaluate_eligibility(job, self.profile), "符合")

    def test_unaccepted_2027_autumn_window_stays_for_review(self):
        job = JobPosting(
            title="机器学习工程师（2027秋招）",
            company="测试公司",
            company_type="私企",
            location="深圳",
            url="https://example.com/6",
            source_name="测试",
            description="2027秋季校园招聘",
            graduation_years=[2027],
        )

        self.assertEqual(recruitment_window(job), "2027秋招")
        self.assertEqual(evaluate_eligibility(job, self.profile), "需核对")


if __name__ == "__main__":
    unittest.main()
