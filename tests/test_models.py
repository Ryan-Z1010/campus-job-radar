import unittest

from job_radar.models import JobPosting
from job_radar.normalize import infer_graduation_years, normalize_job


class JobModelTests(unittest.TestCase):
    def test_external_id_makes_stable_fingerprint(self):
        first = JobPosting("算法岗", "甲公司", "广州", "https://a", "官网", external_id="42")
        second = JobPosting("算法工程师", "甲公司", "深圳", "https://b", "官网", external_id="42")
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_normalization_infers_year(self):
        job = JobPosting(
            "  AI   工程师（2026届） ",
            "公司",
            "广州",
            "https://example.com",
            "官网",
        )
        normalize_job(job)
        self.assertEqual(job.title, "AI 工程师（2026届）")
        self.assertEqual(job.graduation_years, [2026])
        self.assertEqual(infer_graduation_years("2026或2027届"), [2026, 2027])


if __name__ == "__main__":
    unittest.main()
