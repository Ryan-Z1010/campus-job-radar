import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LlmWorkflowConfigTests(unittest.TestCase):
    def test_scheduled_workflow_defaults_to_fifty_llm_jobs(self):
        workflow = (
            ROOT / ".github/workflows/llm-gated-monitor.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("default: 50", workflow)
        self.assertIn("MAX_JOBS: ${{ inputs.max_jobs || 50 }}", workflow)
        self.assertIn('--max-jobs "${MAX_JOBS:-50}"', workflow)
        self.assertIn('cron: "0 6 * * *"', workflow)
        self.assertIn('cron: "0 7 * * *"', workflow)
        self.assertIn('TZ=Australia/Sydney date +%H%M', workflow)
        self.assertIn('sydney_time" -lt 1645', workflow)
        self.assertIn("timeout-minutes: 60", workflow)
        self.assertIn("--collection-workers 16", workflow)


if __name__ == "__main__":
    unittest.main()
