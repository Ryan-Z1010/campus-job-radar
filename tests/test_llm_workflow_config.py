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
        self.assertIn('MAX_JOBS: ${{ inputs.max_jobs || 50 }}', workflow)
        self.assertIn('cron: "0 20 * * *"', workflow)
        self.assertIn('cron: "0 21 * * *"', workflow)
        self.assertIn('TZ=Australia/Sydney date +%H%M', workflow)
        self.assertIn('sydney_time" -lt 0600', workflow)
        self.assertIn('sydney_time" -ge 0900', workflow)
        self.assertNotIn('sydney_time" -lt 0645', workflow)
        self.assertNotIn('sydney_time" -ge 0800', workflow)
        self.assertNotIn('sydney_time" -lt 1645', workflow)
        self.assertNotIn('sydney_time" -ge 1800', workflow)
        self.assertIn("timeout-minutes: 60", workflow)
        self.assertIn('AGENT_SWEEP: ${{ inputs.agent_sweep || false }}', workflow)
        self.assertIn('RESEND_ALL: ${{ inputs.resend_all || false }}', workflow)
        self.assertIn('source.get("company_type") in {"央企", "国企"}', workflow)
        self.assertIn('--job-keyword "智能体"', workflow)
        self.assertIn('agent-sweep-notification.sqlite3', workflow)
        self.assertIn("--collection-workers 64", workflow)
        self.assertIn('SOURCE_MODE: ${{ inputs.source_mode || \'live\' }}', workflow)
        self.assertIn('source.get("type") != "campaign_watch"', workflow)
        self.assertIn('--sources "$sources_path"', workflow)
        self.assertIn('echo 10 || echo "${MAX_JOBS:-50}"', workflow)


if __name__ == "__main__":
    unittest.main()
