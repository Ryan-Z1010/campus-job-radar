import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

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

    @patch("job_radar.network.time.sleep")
    @patch("job_radar.audit.urlopen")
    def test_audit_retries_one_transient_failure(self, urlopen, sleep):
        response = MagicMock()
        response.status = 200
        response.headers = {"Content-Type": "text/html"}
        response.geturl.return_value = "https://example.com/campus"
        response_context = MagicMock()
        response_context.__enter__.return_value = response
        urlopen.side_effect = [URLError("temporary reset"), response_context]

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

        self.assertEqual(results[0]["status"], "200")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.5)


if __name__ == "__main__":
    unittest.main()
