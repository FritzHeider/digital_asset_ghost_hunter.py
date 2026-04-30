from __future__ import annotations

import socket
import unittest
from unittest import mock

from cashtube_utils import DnsStatus, classify_domain, extract_urls, get_domain, is_interesting_domain


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


if __name__ == "__main__":
    unittest.main()
