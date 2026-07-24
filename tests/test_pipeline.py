import json
import tempfile
import unittest
from pathlib import Path

from job_radar.pipeline import run_pipeline


ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def test_demo_pipeline_and_deduplication(self):
        profile = json.loads(
            (ROOT / "configs/profile.example.json").read_text(encoding="utf-8")
        )
        sources = [
            {
                "id": "fixture",
                "name": "演示岗位数据",
                "type": "fixture_json",
                "enabled": True,
                "demo": True,
                "path": str(ROOT / "data/demo_jobs.json"),
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "jobs.db")
            report_dir = str(Path(directory) / "report")
            first = run_pipeline(
                profile,
                sources,
                database,
                report_dir,
                dry_run=True,
                include_demo=True,
            )
            second = run_pipeline(
                profile,
                sources,
                database,
                report_dir,
                dry_run=True,
                include_demo=True,
            )
            self.assertEqual(first.collected, 3)
            self.assertEqual(first.inserted, 3)
            self.assertEqual(first.alerted, 2)
            self.assertEqual(second.inserted, 0)
            self.assertTrue((Path(report_dir) / "digest.html").exists())
            self.assertTrue((Path(report_dir) / "jobs.csv").exists())


if __name__ == "__main__":
    unittest.main()
