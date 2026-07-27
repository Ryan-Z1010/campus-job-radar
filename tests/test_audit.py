import unittest
from unittest.mock import MagicMock, patch

from job_radar.audit import audit_sources


class AuditTests(unittest.TestCase):
    @patch("job_radar.audit.urlopen")
    def test_audit_uses_browser_compatible_accept_header(self, urlopen):
        response = MagicMock()
        response.status = 200
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        response.geturl.return_value = "https://example.com/campus"
        urlopen.return_value.__enter__.return_value = response

        results = audit_sources(
            [
                {
                    "id": "campus",
                    "name": "校园招聘",
                    "homepage": "https://example.com/campus",
                    "enabled": True,
                }
            ]
        )

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.get_header("Accept"),
            "text/html,application/json;q=0.9,*/*;q=0.8",
        )
        self.assertEqual(results[0]["status"], "200")


if __name__ == "__main__":
    unittest.main()
