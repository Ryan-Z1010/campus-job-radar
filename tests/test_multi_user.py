import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_radar.agents.collection import CollectionAgent
from job_radar.multi_user import (
    MultiUserOrchestrator,
    load_user_monitoring,
    load_user_profile,
    load_users,
)
from job_radar.llm.multi_user import MultiUserLlmOrchestrator
from job_radar.llm.orchestrator import LlmRunResult


ROOT = Path(__file__).resolve().parents[1]


class CountingCollectionAgent:
    def __init__(self):
        self.calls = 0
        self.delegate = CollectionAgent()

    def run(self, source):
        self.calls += 1
        return self.delegate.run(source)


class MultiUserTests(unittest.TestCase):
    def _profile(self, path):
        profile = json.loads(
            (ROOT / "configs/profile.example.json").read_text(encoding="utf-8")
        )
        Path(path).write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_load_users_validates_and_resolves_private_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            self._profile(profile)
            users_path = root / "users.json"
            users_path.write_text(
                json.dumps(
                    {
                        "users": [
                            {
                                "id": "student-a",
                                "profile": str(profile),
                                "email": "a@example.com",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            users = load_users(str(users_path))
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].user_id, "student-a")
            self.assertEqual(users[0].email, "a@example.com")
            self.assertTrue(users[0].database.endswith("data/users/student-a/job_radar.db"))

    def test_collects_once_and_isolates_user_databases_and_reports(self):
        source = {
            "id": "demo_official_jobs",
            "name": "演示岗位数据",
            "type": "fixture_json",
            "enabled": True,
            "demo": True,
            "path": str(ROOT / "data/demo_jobs.json"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = []
            users = []
            for user_id in ("alice", "bob"):
                profile = root / "{}.json".format(user_id)
                self._profile(profile)
                profiles.append(profile)
                users.append(
                    {
                        "id": user_id,
                        "profile": str(profile),
                        "email": "{}@example.com".format(user_id),
                        "database": str(root / user_id / "jobs.db"),
                        "report_dir": str(root / user_id / "reports"),
                        "trace_file": str(root / user_id / "agent-trace.json"),
                    }
                )
            users_path = root / "users.json"
            users_path.write_text(
                json.dumps({"users": users}, ensure_ascii=False), encoding="utf-8"
            )

            collection = CountingCollectionAgent()
            result = MultiUserOrchestrator(collection_agent=collection).run(
                load_users(str(users_path)),
                [source],
                include_demo=True,
                source_ids=["demo_official_jobs"],
                dry_run=True,
            )

            self.assertEqual(collection.calls, 1)
            self.assertEqual(result.source_total, 1)
            self.assertEqual(result.collected, 3)
            self.assertEqual([user.inserted for user in result.users], [3, 3])
            self.assertTrue((root / "alice" / "jobs.db").exists())
            self.assertTrue((root / "bob" / "jobs.db").exists())
            self.assertTrue((root / "alice" / "reports" / "digest.html").exists())
            self.assertTrue((root / "bob" / "reports" / "digest.html").exists())
            self.assertTrue((root / "alice" / "agent-trace.json").exists())
            self.assertTrue((root / "bob" / "agent-trace.json").exists())

    def test_each_user_recipient_is_passed_to_notification(self):
        source = {
            "id": "demo_official_jobs",
            "name": "演示岗位数据",
            "type": "fixture_json",
            "enabled": True,
            "demo": True,
            "path": str(ROOT / "data/demo_jobs.json"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            users = []
            for user_id in ("alice", "bob"):
                profile = root / "{}.json".format(user_id)
                self._profile(profile)
                users.append(
                    {
                        "id": user_id,
                        "profile": str(profile),
                        "email": "{}@example.com".format(user_id),
                        "database": str(root / user_id / "jobs.db"),
                        "report_dir": str(root / user_id / "reports"),
                    }
                )
            users_path = root / "users.json"
            users_path.write_text(
                json.dumps({"users": users}, ensure_ascii=False), encoding="utf-8"
            )
            with patch("job_radar.agents.notification.send_email") as sender:
                result = MultiUserOrchestrator().run(
                    load_users(str(users_path)),
                    [source],
                    include_demo=True,
                    source_ids=["demo_official_jobs"],
                    dry_run=False,
                )

            self.assertEqual(len(result.users), 2)
            recipients = [call.kwargs["recipient"] for call in sender.call_args_list]
            self.assertEqual(sorted(recipients), ["alice@example.com", "bob@example.com"])

    def test_inline_profile_and_monitoring_are_isolated_per_user(self):
        profile = json.loads(
            (ROOT / "configs/profile.example.json").read_text(encoding="utf-8")
        )
        users_path = Path(tempfile.mkdtemp()) / "users.json"
        users_path.write_text(
            json.dumps(
                {
                    "users": [
                        {
                            "id": "ryan",
                            "profile": profile,
                            "monitoring": {
                                "excluded_company_keywords": ["腾讯"]
                            },
                            "email": "ryan@example.com",
                        },
                        {
                            "id": "friend",
                            "profile": profile,
                            "monitoring": {
                                "excluded_company_keywords": ["美团"]
                            },
                            "email": "friend@example.com",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        users = load_users(str(users_path))
        self.assertEqual(load_user_profile(users[0])["graduation"], "2026-11")
        self.assertEqual(
            load_user_monitoring(users[0])["excluded_company_keywords"], ["腾讯"]
        )
        self.assertEqual(
            load_user_monitoring(users[1])["excluded_company_keywords"], ["美团"]
        )
        self.assertNotEqual(users[0].llm_cache_database, users[1].llm_cache_database)

    def test_llm_multi_user_collects_once_and_filters_per_user(self):
        profile = json.loads(
            (ROOT / "configs/profile.example.json").read_text(encoding="utf-8")
        )
        source = {
            "id": "demo_official_jobs",
            "name": "演示岗位数据",
            "type": "fixture_json",
            "enabled": True,
            "demo": True,
            "path": str(ROOT / "data/demo_jobs.json"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            users_path = root / "users.json"
            users_path.write_text(
                json.dumps(
                    {
                        "users": [
                            {
                                "id": "alice",
                                "profile": profile,
                                "monitoring": {
                                    "excluded_company_keywords": ["某广州"]
                                },
                                "email": "alice@example.com",
                            },
                            {
                                "id": "bob",
                                "profile": profile,
                                "monitoring": {
                                    "excluded_company_keywords": ["某上海"]
                                },
                                "email": "bob@example.com",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            class CountingCollectionAgent:
                def __init__(self):
                    self.calls = 0
                    self.delegate = CollectionAgent()

                def run(self, item):
                    self.calls += 1
                    return self.delegate.run(item)

            class FakeLlmOrchestrator:
                def __init__(self, client, cache=None):
                    pass

                def run(self, jobs, profile, **kwargs):
                    jobs = list(jobs)
                    return LlmRunResult(
                        model="fake",
                        started_at="",
                        finished_at="",
                        selected=len(jobs),
                    )

            collection = CountingCollectionAgent()
            with patch(
                "job_radar.llm.multi_user.LlmRecruitmentOrchestrator",
                FakeLlmOrchestrator,
            ):
                result = MultiUserLlmOrchestrator(
                    collection_agent=collection
                ).run(
                    users=load_users(str(users_path)),
                    sources=[source],
                    client=object(),
                    include_demo=True,
                    no_cache=True,
                )

            self.assertEqual(collection.calls, 1)
            self.assertEqual(result.source_total, 1)
            self.assertEqual([item.result.selected for item in result.users], [2, 2])


if __name__ == "__main__":
    unittest.main()
