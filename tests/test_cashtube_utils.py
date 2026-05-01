from __future__ import annotations

import socket
import threading
import time
import unittest
from unittest import mock

import requests

from cashtube_utils import (
    DnsStatus,
    RateLimiter,
    classify_domain,
    extract_urls,
    get_domain,
    is_interesting_domain,
    wayback_lookup,
)


class DomainUtilitiesTest(unittest.TestCase):
    def test_get_domain_normalizes_common_urls(self) -> None:
        self.assertEqual(get_domain("https://www.example.com/path?q=1"), "example.com")
        self.assertEqual(get_domain("http://blog.example.co.uk:8080/x"), "example.co.uk")
        self.assertEqual(get_domain("example.io/path"), "example.io")

    def test_extract_urls_and_filter_domains(self) -> None:
        urls = extract_urls("See https://example.com/a and https://youtube.com/watch?v=1")
        domains = [get_domain(url) for url in urls]
        self.assertIn("example.com", domains)
        self.assertTrue(is_interesting_domain("example.com"))
        self.assertFalse(is_interesting_domain("youtube.com"))
        self.assertFalse(is_interesting_domain("example.org"))

    def test_classify_domain_distinguishes_nxdomain(self) -> None:
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror(socket.EAI_NONAME, "")):
            self.assertEqual(classify_domain("missing.example").status, DnsStatus.NXDOMAIN)

    def test_classify_domain_distinguishes_temporary_dns_error(self) -> None:
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror(socket.EAI_AGAIN, "")):
            self.assertEqual(classify_domain("flaky.example").status, DnsStatus.SERVFAIL)


class RateLimiterTest(unittest.TestCase):
    def test_concurrent_threads_each_respect_delay(self) -> None:
        limiter = RateLimiter(delay_seconds=0.05)
        call_times: list[float] = []
        lock = threading.Lock()

        def worker() -> None:
            limiter.wait()
            with lock:
                call_times.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        call_times.sort()
        for i in range(1, len(call_times)):
            gap = call_times[i] - call_times[i - 1]
            self.assertGreaterEqual(
                gap, 0.03,
                f"Thread {i} fired too soon ({gap:.3f}s after previous); RateLimiter not thread-safe",
            )


class ExtractUrlsTest(unittest.TestCase):
    def test_strips_trailing_punctuation(self) -> None:
        text = "Visit https://example.com. Also (https://other.io)! And https://third.net,"
        urls = extract_urls(text)
        self.assertIn("https://example.com", urls)
        self.assertIn("https://other.io", urls)
        self.assertIn("https://third.net", urls)
        for url in urls:
            self.assertFalse(
                url[-1] in ".,;:!?)",
                f"URL {url!r} has trailing punctuation",
            )

    def test_preserves_query_strings(self) -> None:
        text = "See https://example.com/path?foo=1&bar=2 for details."
        urls = extract_urls(text)
        self.assertTrue(any("foo=1" in u for u in urls))


class WaybackLookupTest(unittest.TestCase):
    def test_non_list_json_returns_none(self) -> None:
        session = mock.Mock(spec=requests.Session)
        resp = mock.Mock()
        resp.ok = True
        resp.json.return_value = {"error": "unexpected format"}
        session.get.return_value = resp
        result = wayback_lookup(session, "example.com")
        self.assertEqual(result, "none")

    def test_snapshot_found_when_list_has_rows(self) -> None:
        session = mock.Mock(spec=requests.Session)
        resp = mock.Mock()
        resp.ok = True
        resp.json.return_value = [["urlkey", "timestamp"], ["example.com", "20150101"]]
        session.get.return_value = resp
        result = wayback_lookup(session, "example.com")
        self.assertEqual(result, "snapshot_found")


if __name__ == "__main__":
    unittest.main()
