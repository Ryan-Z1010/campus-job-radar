import json
import tempfile
import unittest
from pathlib import Path

from job_radar.agents import (
    AgentStatus,
    CollectionAgent,
    EligibilityAgent,
    OrchestratorAgent,
    ReviewAgent,
    write_agent_trace,
)
from job_radar.cli import main
from job_radar.models import JobPosting


ROOT = Path(__file__).resolve().parents[1]


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(
            (ROOT / "configs/profile.example.json").read_text(encoding="utf-8")
        )
        cls.demo_source = {
            "id": "demo_official_jobs",
            "name": "演示岗位数据",
            "type": "fixture_json",
            "enabled": True,
            "demo": True,
            "path": str(ROOT / "data/demo_jobs.json"),
        }

    def test_collection_agent_returns_structured_evidence(self):
        result = CollectionAgent().run(self.demo_source)
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(result.jobs), 3)
        self.assertEqual(result.metadata["source_id"], "demo_official_jobs")
        self.assertTrue(any(item.label == "采集结果" for item in result.evidence))
        self.assertEqual(result.evidence[0].locator, "demo_jobs.json")
        self.assertNotIn(str(ROOT), result.evidence[0].locator)

    def test_collection_failure_is_visible_in_result(self):
        source = dict(self.demo_source, type="missing_collector")
        result = CollectionAgent().run(source)
        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("不支持的采集器类型", result.error)

    def test_eligibility_agent_normalizes_scores_and_queues_uncertain_job(self):
        job = JobPosting(
            title="  数据   开发工程师（2027届） ",
            company="测试央企",
            company_type="央企",
            location="广州",
            url="https://example.com/jobs/1",
            source_name="测试来源",
        )
        result = EligibilityAgent().run([job], self.profile, "test")
        self.assertEqual(result.status, AgentStatus.NEEDS_REVIEW)
        self.assertEqual(result.jobs[0].title, "数据 开发工程师（2027届）")
        self.assertEqual(result.jobs[0].eligibility, "需核对")
        self.assertGreater(result.jobs[0].score, 0)
        self.assertEqual(result.metadata["eligibility_counts"]["需核对"], 1)

    def test_review_agent_deduplicates_and_rejects_unsafe_links(self):
        first = JobPosting(
            "算法岗",
            "测试公司",
            "广州",
            "https://example.com/jobs/2",
            "测试来源",
            external_id="same",
            eligibility="符合",
        )
        duplicate = JobPosting(
            "算法工程师",
            "测试公司",
            "深圳",
            "https://example.com/jobs/duplicate",
            "测试来源",
            external_id="same",
            eligibility="符合",
        )
        unsafe = JobPosting(
            "数据岗",
            "测试公司",
            "北京",
            "javascript:alert(1)",
            "测试来源",
            eligibility="符合",
        )
        result = ReviewAgent().run([first, duplicate, unsafe], "test")
        self.assertEqual(result.status, AgentStatus.PARTIAL)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.metadata["duplicate_count"], 1)
        self.assertEqual(result.metadata["unsafe_url_count"], 1)

    def test_orchestrator_runs_shadow_pipeline_and_writes_compact_trace(self):
        result = OrchestratorAgent().run(
            self.profile,
            [self.demo_source],
            include_demo=True,
            source_ids=["demo_official_jobs"],
        )
        self.assertEqual(result.source_total, 1)
        self.assertEqual(result.completed_sources, 1)
        self.assertEqual(result.collected, 3)
        self.assertEqual(result.reviewed, 3)
        self.assertEqual(len(result.traces[0].steps), 3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            write_agent_trace(result, str(path))
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "shadow")
        self.assertEqual(len(payload["jobs"]), 3)
        self.assertNotIn("jobs", payload["traces"][0]["steps"][0])

    def test_orchestrator_rejects_unknown_source_filter(self):
        with self.assertRaisesRegex(ValueError, "未找到来源 ID"):
            OrchestratorAgent().run(
                self.profile,
                [self.demo_source],
                include_demo=True,
                source_ids=["not-configured"],
            )

    def test_orchestrator_keeps_source_failure_visible(self):
        broken = dict(self.demo_source, type="missing_collector", demo=False)
        result = OrchestratorAgent().run(self.profile, [broken])
        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(result.failed_sources, 1)
        self.assertEqual(result.completed_sources, 0)
        self.assertEqual(len(result.source_errors), 1)
        self.assertEqual(result.traces[0].status, AgentStatus.FAILED)

    def test_cli_agent_run_writes_trace_without_database(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "agent-trace.json"
            exit_code = main(
                [
                    "agent-run",
                    "--include-demo",
                    "--source",
                    "demo_official_jobs",
                    "--trace-file",
                    str(trace),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(trace.exists())
            self.assertFalse((Path(directory) / "job_radar.db").exists())


if __name__ == "__main__":
    unittest.main()
