from __future__ import annotations

import json
import logging
import re
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import tldextract
except ImportError:  # pragma: no cover - exercised when optional dependency is absent
    tldextract = None

YOUTUBE_API_BASE_URL = "https://www.googleapis.com/youtube/v3"
DEFAULT_TIMEOUT = 15

URL_PATTERN = re.compile(
    r"https?://(?:[a-zA-Z0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F]{2}))+"
)

IGNORE_DOMAINS = {
    "amazon.com",
    "amzn.to",
    "apple.com",
    "bestbuy.com",
    "bit.ly",
    "discord.gg",
    "ebay.com",
    "facebook.com",
    "fb.me",
    "github.com",
    "goo.gl",
    "google.com",
    "instagram.com",
    "linkedin.com",
    "medium.com",
    "microsoft.com",
    "t.co",
    "tinyurl.com",
    "twitch.tv",
    "twitter.com",
    "walmart.com",
    "youtu.be",
    "youtube.com",
}

ALLOWED_TLDS = {".com", ".io", ".net"}
COMMON_MULTIPART_SUFFIXES = {
    "co.uk",
    "com.au",
    "com.br",
    "com.mx",
    "co.jp",
    "co.nz",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True)


def configure_logging(json_logs: bool = False, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(levelname)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=4,
        backoff_factor=0.75,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def validate_published_before(value: str) -> str:
    candidate = value
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            "--published-before must be ISO-8601, for example 2016-01-01T00:00:00Z"
        ) from exc
    return value


def chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def youtube_get(
    session: requests.Session,
    endpoint: str,
    params: dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    response = session.get(
        f"{YOUTUBE_API_BASE_URL}/{endpoint}",
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def extract_urls(text: str) -> list[str]:
    return URL_PATTERN.findall(text or "")


def get_domain(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    hostname = (parsed.hostname or "").lower().strip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if not hostname or "." not in hostname:
        return ""

    if tldextract:
        extracted = tldextract.extract(hostname)
        if extracted.domain and extracted.suffix:
            return f"{extracted.domain}.{extracted.suffix}"

    parts = hostname.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in COMMON_MULTIPART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_interesting_domain(
    domain: str,
    ignore_domains: set[str] | None = None,
    allowed_tlds: set[str] | None = None,
) -> bool:
    ignored = ignore_domains or IGNORE_DOMAINS
    tlds = allowed_tlds or ALLOWED_TLDS
    return bool(domain and domain not in ignored and any(domain.endswith(tld) for tld in tlds))


class DnsStatus(str, Enum):
    LIVE = "live"
    NXDOMAIN = "nxdomain"
    SERVFAIL = "servfail"
    TIMEOUT = "timeout"
    TEMPORARY_ERROR = "temporary_error"


@dataclass(frozen=True)
class DomainCheck:
    domain: str
    status: DnsStatus

    @property
    def is_dead(self) -> bool:
        return self.status == DnsStatus.NXDOMAIN


def classify_domain(domain: str, timeout: float = 5.0) -> DomainCheck:
    if not domain:
        return DomainCheck(domain=domain, status=DnsStatus.TEMPORARY_ERROR)

    original_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        socket.getaddrinfo(domain, None)
        return DomainCheck(domain=domain, status=DnsStatus.LIVE)
    except socket.gaierror as exc:
        error_code = exc.errno if exc.errno is not None else exc.args[0]
        if error_code == socket.EAI_NONAME:
            return DomainCheck(domain=domain, status=DnsStatus.NXDOMAIN)
        if error_code == socket.EAI_AGAIN:
            return DomainCheck(domain=domain, status=DnsStatus.SERVFAIL)
        return DomainCheck(domain=domain, status=DnsStatus.TEMPORARY_ERROR)
    except TimeoutError:
        return DomainCheck(domain=domain, status=DnsStatus.TIMEOUT)
    except OSError:
        return DomainCheck(domain=domain, status=DnsStatus.TEMPORARY_ERROR)
    finally:
        socket.setdefaulttimeout(original_timeout)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
