from __future__ import annotations

import json
import logging
import re
import socket
import sqlite3
import sys
import time
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
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
DEFAULT_CACHE_TTL_SECONDS = 86400

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


class RateLimiter:
    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = max(delay_seconds, 0.0)
        self._last_call = 0.0

    def wait(self) -> None:
        if self.delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_call = time.monotonic()


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
    rate_limiter: RateLimiter | None = None,
) -> dict[str, Any]:
    if rate_limiter:
        rate_limiter.wait()
    response = session.get(
        f"{YOUTUBE_API_BASE_URL}/{endpoint}",
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}

    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() == ".json":
        return json.loads(text)

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Install PyYAML to load YAML config files, or use JSON config") from exc
    return yaml.safe_load(text) or {}


def parse_csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def normalize_tlds(values: Iterable[str]) -> set[str]:
    return {value if value.startswith(".") else f".{value}" for value in values if value}


def write_dicts_to_csv(rows: list[dict[str, Any]], output_path: str, fieldnames: list[str]) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(rows: list[dict[str, Any]], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, sort_keys=True)
        f.write("\n")


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total_rows": len(rows),
        "by_tld": dict(Counter(Path(row.get("dead_domain", "")).suffix for row in rows if row.get("dead_domain"))),
        "by_dns_status": dict(Counter(row.get("status", "unknown") for row in rows)),
        "by_channel": dict(Counter(row.get("channel_url") or row.get("channel_id") or "unknown" for row in rows)),
        "by_keyword": dict(Counter(row.get("source_keyword", "") for row in rows if row.get("source_keyword"))),
    }


def write_markdown_report(rows: list[dict[str, Any]], output_path: str, title: str = "Cashtube Summary") -> None:
    summary = summarize_rows(rows)
    lines = [f"# {title}", "", f"- Total rows: {summary['total_rows']}"]
    for section, values in (
        ("By TLD", summary["by_tld"]),
        ("By DNS status", summary["by_dns_status"]),
        ("By channel", summary["by_channel"]),
        ("By keyword", summary["by_keyword"]),
    ):
        lines.extend(["", f"## {section}"])
        if values:
            lines.extend(f"- {key or 'unknown'}: {count}" for key, count in sorted(values.items()))
        else:
            lines.append("- none")
    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


class SQLiteCache:
    def __init__(self, path: str | None, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._conn: sqlite3.Connection | None = None
        if path:
            self._conn = sqlite3.connect(path)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cache (namespace TEXT, key TEXT, value TEXT, updated_at REAL, PRIMARY KEY (namespace, key))"
            )
            self._conn.commit()

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        if not self._conn:
            return None
        row = self._conn.execute(
            "SELECT value, updated_at FROM cache WHERE namespace = ? AND key = ?",
            (namespace, key),
        ).fetchone()
        if not row:
            return None
        value, updated_at = row
        if time.time() - updated_at > self.ttl_seconds:
            return None
        return json.loads(value)

    def set(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        if not self._conn:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (namespace, key, value, updated_at) VALUES (?, ?, ?, ?)",
            (namespace, key, json.dumps(value, default=str, sort_keys=True), time.time()),
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()


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


@dataclass(frozen=True)
class HttpCheck:
    http_status: int | None
    ssl_ok: bool | None
    parked: bool
    signal: str


PARKING_MARKERS = (
    "buy this domain",
    "domain is for sale",
    "sedo",
    "afternic",
    "parkingcrew",
    "namecheap parking",
    "godaddy.com/domainsearch",
)


def check_http_domain(session: requests.Session, domain: str, timeout: int = DEFAULT_TIMEOUT) -> HttpCheck:
    for scheme in ("https", "http"):
        try:
            response = session.get(f"{scheme}://{domain}", timeout=timeout, allow_redirects=True)
            text = response.text[:5000].lower()
            parked = any(marker in text for marker in PARKING_MARKERS)
            return HttpCheck(
                http_status=response.status_code,
                ssl_ok=scheme == "https",
                parked=parked,
                signal="parked" if parked else "reachable",
            )
        except requests.exceptions.SSLError:
            if scheme == "https":
                continue
            return HttpCheck(http_status=None, ssl_ok=False, parked=False, signal="ssl_error")
        except requests.RequestException:
            continue
    return HttpCheck(http_status=None, ssl_ok=None, parked=False, signal="unreachable")


def rdap_lookup(session: requests.Session, domain: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    try:
        response = session.get(f"https://rdap.org/domain/{domain}", timeout=timeout)
        if response.status_code == 404:
            return "not_found"
        if response.ok:
            return "registered"
        return f"http_{response.status_code}"
    except requests.RequestException:
        return "error"


def wayback_lookup(session: requests.Session, domain: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    try:
        response = session.get(
            "https://web.archive.org/cdx",
            params={"url": domain, "output": "json", "limit": 1},
            timeout=timeout,
        )
        if response.ok and len(response.json()) > 1:
            return "snapshot_found"
        return "none"
    except (ValueError, requests.RequestException):
        return "error"


def trademark_risk(session: requests.Session, word: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    import os

    token = os.getenv("USPTO_API_KEY")
    if not token:
        return "not_configured"

    try:
        response = session.get(
            "https://developer.uspto.gov/api-catalog/tsdr-data-api/v1/search",
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            params={"q": f"mark_literal_text:{word} AND registration_date:[* TO *]", "rows": 1},
            timeout=timeout,
        )
        response.raise_for_status()
        return "risky" if response.json().get("count", 0) > 0 else "clear"
    except requests.RequestException:
        return "error"
