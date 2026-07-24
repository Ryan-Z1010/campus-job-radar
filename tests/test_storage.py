import tempfile
import unittest
from pathlib import Path

from job_radar.models import JobPosting
from job_radar.storage import JobStore


class StorageTests(unittest.TestCase):
    def test_upsert_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(str(Path(directory) / "jobs.db"))
            try:
                job = JobPosting(
                    "数据工程师",
                    "测试公司",
                    "广州",
                    "https://example.com/job",
                    "测试官网",
                    external_id="abc",
                )
                new_first, updated_first = store.upsert([job])
                new_second, updated_second = store.upsert([job])
                self.assertEqual((len(new_first), updated_first), (1, 0))
                self.assertEqual((len(new_second), updated_second), (0, 1))
                self.assertEqual(len(store.list_jobs()), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
