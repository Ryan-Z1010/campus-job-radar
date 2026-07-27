import ssl
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from job_radar.network import urlopen_with_retry


class NetworkRetryTests(unittest.TestCase):
    @patch("job_radar.network.time.sleep")
    def test_retries_tls_eof_once_then_returns_response(self, sleep):
        response = MagicMock()
        opener = MagicMock(
            side_effect=[
                URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")),
                response,
            ]
        )

        result = urlopen_with_retry("https://example.com", timeout=3, opener=opener)

        self.assertIs(result, response)
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(0.5)

    @patch("job_radar.network.time.sleep")
    def test_retries_retryable_http_status(self, sleep):
        response = MagicMock()
        opener = MagicMock(
            side_effect=[
                HTTPError(
                    "https://example.com",
                    503,
                    "Service Unavailable",
                    hdrs=None,
                    fp=None,
                ),
                response,
            ]
        )

        result = urlopen_with_retry("https://example.com", timeout=3, opener=opener)

        self.assertIs(result, response)
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(0.5)

    @patch("job_radar.network.time.sleep")
    def test_does_not_retry_deterministic_http_error(self, sleep):
        error = HTTPError(
            "https://example.com",
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )
        opener = MagicMock(side_effect=error)

        with self.assertRaises(HTTPError):
            urlopen_with_retry("https://example.com", timeout=3, opener=opener)

        opener.assert_called_once()
        sleep.assert_not_called()

    @patch("job_radar.network.time.sleep")
    def test_stops_after_second_transient_failure(self, sleep):
        opener = MagicMock(
            side_effect=[
                TimeoutError("first timeout"),
                TimeoutError("second timeout"),
            ]
        )

        with self.assertRaises(TimeoutError):
            urlopen_with_retry("https://example.com", timeout=3, opener=opener)

        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(0.5)


if __name__ == "__main__":
    unittest.main()
