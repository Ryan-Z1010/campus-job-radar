import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from job_radar.agents import AgentStatus
from job_radar.cli import main
from job_radar.llm import (
    LlmAnalysisCache,
    LlmRecruitmentOrchestrator,
    LlmResponse,
    OpenAIResponsesClient,
)
from job_radar.llm.agents import sanitize_profile
from job_radar.models import JobPosting


class ScriptedClient:
    def __init__(self, responses, model="test-structured-model"):
        self.responses = list(responses)
        self.model = model
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return LlmResponse(
            data=self.responses.pop(0),
            response_id="response-{}".format(len(self.calls)),
            model=self.model,
            usage={"input_tokens": 10, "output_tokens": 5},
        )


def jd_response():
    return {
        "role_summary": "建设招聘数据智能体",
        "role_family": "AI应用开发",
        "responsibilities": ["开发并维护AI智能体"],
        "required_skills": ["Python", "大模型应用"],
        "preferred_skills": ["SQL"],
        "education_requirements": ["硕士优先"],
        "graduation_requirements": ["2027届"],
        "experience_requirements": [],
        "work_locations": ["广州"],
        "risk_flags": ["海外院校毕业时间需核对"],
        "evidence": [
            {"field": "职责", "quote": "开发并维护AI智能体"},
            {"field": "届别", "quote": "2027届"},
        ],
        "confidence": 0.9,
    }


def match_response(score=82):
    return {
        "score": score,
        "verdict": "match" if score >= 70 else "possible_match",
        "matched_requirements": [
            {"requirement": "Python", "profile_evidence": "技能字段包含Python"}
        ],
        "gaps": ["缺少生产部署证据"],
        "hard_constraint_risks": ["2027届覆盖范围需官网核对"],
        "recommendation": "核对届别后投递",
        "evidence_quality": "medium",
        "confidence": 0.8,
    }


def critic_response(verdict="accept"):
    return {
        "verdict": verdict,
        "confidence": 0.85,
        "issues": [] if verdict == "accept" else ["分数偏高"],
        "revision_instructions": []
        if verdict == "accept"
        else ["降低分数并保留届别风险"],
        "factuality_checks": [
            {
                "claim": "候选人掌握Python",
                "supported": True,
                "reason": "来自skills字段",
            }
        ],
    }


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class LlmAgentTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "graduation": "2026-11",
            "review_graduation_years": [2027],
            "education": "硕士",
            "target_roles": ["AI智能体", "数据开发"],
            "preferred_cities": ["广州", "上海"],
            "company_type_priority": ["央企", "国企"],
            "positive_keywords": {"Python": 8, "大模型": 20},
            "negative_keywords": {"销售": -50},
            "skills": ["Python", "SQL"],
            "experience_highlights": [
                "开发过可审计的招聘信息智能体，联系 test@example.com，电话13800138000"
            ],
            "email": "should-not-leave@example.com",
            "name": "不应发送的姓名",
        }
        self.job = JobPosting(
            title="AI智能体开发工程师（2027届）",
            company="测试国企",
            company_type="国企",
            location="广州",
            url="https://example.com/job/1",
            source_name="测试招聘官网",
            description="开发并维护AI智能体，要求Python，硕士优先，面向2027届。",
            graduation_years=[2027],
            score=80,
            eligibility="需核对",
        )

    def test_profile_is_allowlisted_before_llm_call(self):
        safe = sanitize_profile(self.profile)
        self.assertNotIn("email", safe)
        self.assertNotIn("name", safe)
        self.assertEqual(safe["skills"], ["Python", "SQL"])
        self.assertIn("Python", safe["preference_keywords"])
        self.assertNotIn("test@example.com", safe["experience_highlights"][0])
        self.assertNotIn("13800138000", safe["experience_highlights"][0])

    def test_openai_client_sends_strict_schema_and_parses_output(self):
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeHttpResponse(
                {
                    "id": "resp_test",
                    "model": "test-model",
                    "status": "completed",
                    "usage": {"input_tokens": 4},
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps({"answer": "ok"}),
                                }
                            ],
                        }
                    ],
                }
            )

        client = OpenAIResponsesClient(
            "test-secret", "test-model", timeout=12, opener=opener
        )
        response = client.complete(
            agent_name="TestAgent",
            instructions="Return structured data.",
            input_data={"text": "hello"},
            schema_name="test_schema",
            schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        )
        request_payload = json.loads(captured["request"].data.decode("utf-8"))
        self.assertEqual(captured["timeout"], 12)
        self.assertTrue(request_payload["text"]["format"]["strict"])
        self.assertEqual(request_payload["text"]["format"]["type"], "json_schema")
        self.assertFalse(request_payload["store"])
        self.assertNotIn("tools", request_payload)
        self.assertEqual(response.data, {"answer": "ok"})

    def test_orchestrator_accepts_grounded_result(self):
        client = ScriptedClient([jd_response(), match_response(), critic_response()])
        result = LlmRecruitmentOrchestrator(client).run(
            [self.job], self.profile, max_jobs=1
        )
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(result.analyzed, 1)
        self.assertEqual(len(client.calls), 3)
        analysis = result.analyses[0]
        self.assertEqual(analysis["semantic_match"]["score"], 82)
        self.assertEqual(analysis["critic_review"]["verdict"], "accept")
        sent_profile = client.calls[1]["input_data"]["sanitized_profile"]
        self.assertNotIn("email", sent_profile)

    def test_critic_can_request_exactly_one_revision(self):
        client = ScriptedClient(
            [
                jd_response(),
                match_response(86),
                critic_response("revise"),
                match_response(72),
                critic_response("accept"),
            ]
        )
        result = LlmRecruitmentOrchestrator(client).run(
            [self.job], self.profile, max_jobs=1
        )
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(result.analyses[0]["revisions"], 1)
        self.assertEqual(result.analyses[0]["semantic_match"]["score"], 72)
        self.assertEqual(len(client.calls), 5)
        self.assertIn("critic_feedback", client.calls[3]["input_data"])

    def test_second_revision_request_becomes_manual_review(self):
        client = ScriptedClient(
            [
                jd_response(),
                match_response(86),
                critic_response("revise"),
                match_response(70),
                critic_response("revise"),
            ]
        )
        result = LlmRecruitmentOrchestrator(client).run(
            [self.job], self.profile, max_jobs=1
        )
        self.assertEqual(result.status, AgentStatus.NEEDS_REVIEW)
        self.assertEqual(result.needs_review, 1)
        self.assertIn("一次修订上限", result.analyses[0]["review_reason"])
        self.assertEqual(len(client.calls), 5)

    def test_transient_critic_failure_is_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = LlmAnalysisCache(str(Path(directory) / "llm.sqlite3"))
            failing_client = ScriptedClient([jd_response(), match_response(), {}])
            first = LlmRecruitmentOrchestrator(
                failing_client, cache=cache
            ).run([self.job], self.profile, max_jobs=1)
            retry_client = ScriptedClient(
                [jd_response(), match_response(), critic_response()]
            )
            second = LlmRecruitmentOrchestrator(retry_client, cache=cache).run(
                [self.job], self.profile, max_jobs=1
            )
        self.assertEqual(first.status, AgentStatus.NEEDS_REVIEW)
        self.assertFalse(first.analyses[0]["cacheable"])
        self.assertEqual(second.cache_hits, 0)
        self.assertEqual(len(retry_client.calls), 3)

    def test_cache_skips_unchanged_job_and_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = LlmAnalysisCache(str(Path(directory) / "llm.sqlite3"))
            first_client = ScriptedClient(
                [jd_response(), match_response(), critic_response()]
            )
            first = LlmRecruitmentOrchestrator(first_client, cache=cache).run(
                [self.job], self.profile, max_jobs=1
            )
            second_client = ScriptedClient([])
            second = LlmRecruitmentOrchestrator(second_client, cache=cache).run(
                [self.job], self.profile, max_jobs=1
            )
        self.assertEqual(first.cache_hits, 0)
        self.assertEqual(second.cache_hits, 1)
        self.assertEqual(second.analyzed, 1)
        self.assertEqual(second_client.calls, [])
        self.assertTrue(second.analyses[0]["cached"])

    def test_changed_job_description_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = LlmAnalysisCache(str(Path(directory) / "llm.sqlite3"))
            first_client = ScriptedClient(
                [jd_response(), match_response(), critic_response()]
            )
            LlmRecruitmentOrchestrator(first_client, cache=cache).run(
                [self.job], self.profile, max_jobs=1
            )
            self.job.description += "新增要求：熟悉向量数据库。"
            second_client = ScriptedClient(
                [jd_response(), match_response(), critic_response()]
            )
            second = LlmRecruitmentOrchestrator(second_client, cache=cache).run(
                [self.job], self.profile, max_jobs=1
            )
        self.assertEqual(second.cache_hits, 0)
        self.assertEqual(len(second_client.calls), 3)

    def test_new_job_is_prioritized_over_higher_scored_cache_hit(self):
        new_job = JobPosting(
            title="数据开发工程师",
            company="另一家国企",
            company_type="国企",
            location="上海",
            url="https://example.com/job/new",
            source_name="测试招聘官网",
            description="负责数据平台开发，要求Python和SQL。",
            score=60,
            eligibility="符合",
        )
        with tempfile.TemporaryDirectory() as directory:
            cache = LlmAnalysisCache(str(Path(directory) / "llm.sqlite3"))
            cached_client = ScriptedClient(
                [jd_response(), match_response(), critic_response()]
            )
            LlmRecruitmentOrchestrator(cached_client, cache=cache).run(
                [self.job], self.profile, max_jobs=1
            )
            fresh_client = ScriptedClient(
                [jd_response(), match_response(70), critic_response()]
            )
            result = LlmRecruitmentOrchestrator(fresh_client, cache=cache).run(
                [self.job, new_job], self.profile, max_jobs=1
            )
        self.assertEqual(result.selected, 1)
        self.assertEqual(result.cache_hits, 0)
        self.assertEqual(len(fresh_client.calls), 3)
        self.assertEqual(result.analyses[0]["job"]["title"], "数据开发工程师")

    def test_cli_without_api_key_fails_before_writing_llm_artifacts(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "llm.json"
            cache = Path(directory) / "llm.sqlite3"
            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                with redirect_stderr(StringIO()) as stderr:
                    exit_code = main(
                        [
                            "llm-analyze",
                            "--env-file",
                            str(Path(directory) / "missing.env"),
                            "--profile",
                            str(root / "configs/profile.example.json"),
                            "--sources",
                            str(root / "configs/sources.json"),
                            "--include-demo",
                            "--source",
                            "demo_official_jobs",
                            "--output",
                            str(output),
                            "--cache-database",
                            str(cache),
                        ]
                    )
            self.assertEqual(exit_code, 2)
            self.assertIn("OPENAI_API_KEY 未配置", stderr.getvalue())
            self.assertFalse(output.exists())
            self.assertFalse(cache.exists())


if __name__ == "__main__":
    unittest.main()
