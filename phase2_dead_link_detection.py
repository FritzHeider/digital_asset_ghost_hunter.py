from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import List

from cashtube_utils import (
    DnsStatus,
    SQLiteCache,
    check_http_domain,
    classify_domain,
    configure_logging,
    extract_urls,
    get_domain,
    is_interesting_domain,
    load_config,
    make_session,
    normalize_tlds,
    parse_csv_set,
    rdap_lookup,
    trademark_risk,
    utc_now_iso,
    wayback_lookup,
    write_dicts_to_csv,
    write_json,
    write_markdown_report,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeadLinkEntry:
    channel_url: str
    video_url: str
    dead_domain: str
    status: str
    error_category: str
    http_status: int | None
    ssl_ok: bool | None
    parking_detected: bool
    availability_signal: str
    rdap_status: str
    wayback_status: str
    trademark_status: str
    first_seen_at: str
    source_description_snippet: str


def _snippet(description: str, url: str, width: int = 180) -> str:
    index = description.find(url)
    if index < 0:
        return description[:width].replace("\n", " ")
    start = max(index - 60, 0)
    end = min(index + len(url) + 60, len(description))
    return description[start:end].replace("\n", " ")


def process_channel(
    channel_url: str,
    top_n_videos: int,
    dry_run: bool = False,
    max_dns_workers: int = 10,
    ignore_domains: set[str] | None = None,
    allowed_tlds: set[str] | None = None,
    include_domains: set[str] | None = None,
    exclude_domains: set[str] | None = None,
    cache: SQLiteCache | None = None,
    yt_dlp_delay: float = 0.0,
    yt_dlp_retries: int = 3,
    channel_timeout: float | None = None,
    enrich_http: bool = False,
    check_rdap: bool = False,
    check_wayback: bool = False,
    check_trademark: bool = False,
) -> List[DeadLinkEntry]:
    """Scrape top video descriptions and return dead-domain candidates."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("Install yt-dlp before scanning channels: pip install -r requirements.txt") from exc

    discovered: dict[str, dict[str, str]] = {}
    deadline = time.monotonic() + channel_timeout if channel_timeout else None
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "logger": None,
    }

    def timed_out() -> bool:
        return bool(deadline and time.monotonic() > deadline)

    def extract_info(ydl, url: str):
        cached = cache.get("video_metadata", url) if cache else None
        if cached is not None:
            return cached
        last_exc: Exception | None = None
        for attempt in range(yt_dlp_retries):
            if timed_out():
                raise TimeoutError(f"Timed out scanning {channel_url}")
            try:
                if yt_dlp_delay:
                    time.sleep(yt_dlp_delay)
                info = ydl.extract_info(url, download=False)
                if cache:
                    cache.set("video_metadata", url, info)
                return info
            except Exception as exc:
                last_exc = exc
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"yt-dlp failed after {yt_dlp_retries} attempts") from last_exc

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = extract_info(ydl, f"{channel_url}/videos?view=0&sort=p")
            videos = info.get("entries", [])[:top_n_videos]

            for video in videos:
                if timed_out():
                    LOGGER.warning("Timed out scanning %s", channel_url)
                    break
                video_id = video.get("id")
                if not video_id:
                    continue

                video_url = f"https://www.youtube.com/watch?v={video_id}"
                video_info = extract_info(ydl, video_url)
                description = video_info.get("description", "")

                for url in extract_urls(description):
                    domain = get_domain(url)
                    if include_domains and domain not in include_domains:
                        continue
                    if exclude_domains and domain in exclude_domains:
                        continue
                    if is_interesting_domain(domain, ignore_domains, allowed_tlds) and domain not in discovered:
                        discovered[domain] = {
                            "video_url": video_url,
                            "source_description_snippet": _snippet(description, url),
                        }
        except Exception:
            LOGGER.exception("yt-dlp failed while scanning %s", channel_url)

    if dry_run:
        return [
            DeadLinkEntry(
                channel_url=channel_url,
                video_url=meta["video_url"],
                dead_domain=domain,
                status="unchecked",
                error_category="dry_run",
                http_status=None,
                ssl_ok=None,
                parking_detected=False,
                availability_signal="unchecked",
                rdap_status="unchecked",
                wayback_status="unchecked",
                trademark_status="unchecked",
                first_seen_at=utc_now_iso(),
                source_description_snippet=meta["source_description_snippet"],
            )
            for domain, meta in sorted(discovered.items())
        ]

    def cached_classify(domain: str):
        cached = cache.get("dns", domain) if cache else None
        if cached is not None:
            return SimpleNamespace(**cached)
        check = classify_domain(domain)
        payload = {"domain": check.domain, "status": check.status.value}
        if cache:
            cache.set("dns", domain, payload)
        return check

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_dns_workers) as executor:
        checks = list(executor.map(cached_classify, sorted(discovered)))

    results: list[DeadLinkEntry] = []
    session = make_session()
    for check in checks:
        status = check.status.value if isinstance(check.status, DnsStatus) else check.status
        if status != DnsStatus.NXDOMAIN.value:
            continue
        domain = check.domain
        meta = discovered[domain]
        http_check = check_http_domain(session, domain) if enrich_http else None
        rdap_status = rdap_lookup(session, domain) if check_rdap else "unchecked"
        wayback_status = wayback_lookup(session, domain) if check_wayback else "unchecked"
        trademark_status = (
            trademark_risk(session, domain.split(".", 1)[0]) if check_trademark else "unchecked"
        )
        results.append(
            DeadLinkEntry(
                channel_url=channel_url,
                video_url=meta["video_url"],
                dead_domain=domain,
                status=status,
                error_category="dns_nxdomain",
                http_status=http_check.http_status if http_check else None,
                ssl_ok=http_check.ssl_ok if http_check else None,
                parking_detected=http_check.parked if http_check else False,
                availability_signal=http_check.signal if http_check else "dns_nxdomain",
                rdap_status=rdap_status,
                wayback_status=wayback_status,
                trademark_status=trademark_status,
                first_seen_at=utc_now_iso(),
                source_description_snippet=meta["source_description_snippet"],
            )
        )
    return results


def write_dead_links_to_csv(dead_links: List[DeadLinkEntry], output_path: str) -> None:
    fieldnames = list(DeadLinkEntry("", "", "", "", "", None, None, False, "", "", "", "", "", "").__dict__.keys())
    rows = sorted(dead_links, key=lambda item: (item.channel_url, item.dead_domain, item.video_url))
    write_dicts_to_csv([asdict(link) for link in rows], output_path, fieldnames)


def _channel_url(row: dict[str, str]) -> str:
    if row.get("channel_url"):
        return row["channel_url"]
    return f"https://www.youtube.com/channel/{row['channel_id']}"


def _load_processed_channels(path: str | None) -> set[str]:
    if not path or not Path(path).exists():
        return set()
    try:
        return set(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Could not read Phase 2 checkpoint %s; starting fresh", path)
        return set()


def _save_processed_channels(path: str | None, channel_urls: set[str]) -> None:
    if path:
        Path(path).write_text(json.dumps(sorted(channel_urls), indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cashtube Phase 2: Dead Link Detection")
    parser.add_argument("--channels-file", default="phase1_results.csv")
    parser.add_argument("--top-n-videos", type=int, default=20)
    parser.add_argument("--output", default="phase2_results.csv")
    parser.add_argument("--json-output", default=None)
    parser.add_argument("--report-output", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-dns-workers", type=int, default=10)
    parser.add_argument("--config", default=None)
    parser.add_argument("--include-domain", default=None)
    parser.add_argument("--exclude-domain", default=None)
    parser.add_argument("--cache-db", default=".cashtube_cache.sqlite3")
    parser.add_argument("--cache-ttl-seconds", type=int, default=86400)
    parser.add_argument("--checkpoint-file", default=".cashtube_phase2_checkpoint.json")
    parser.add_argument("--yt-dlp-delay", type=float, default=0.0)
    parser.add_argument("--yt-dlp-retries", type=int, default=3)
    parser.add_argument("--channel-timeout", type=float, default=None)
    parser.add_argument("--enrich-http", action="store_true")
    parser.add_argument("--check-rdap", action="store_true")
    parser.add_argument("--check-wayback", action="store_true")
    parser.add_argument("--check-trademark", action="store_true")
    parser.add_argument("--json-logs", action="store_true")
    args = parser.parse_args()
    configure_logging(json_logs=args.json_logs)

    all_dead_links: list[DeadLinkEntry] = []
    config = load_config(args.config)
    ignore_domains = set(config.get("ignore_domains", [])) or None
    allowed_tlds = normalize_tlds(config.get("allowed_tlds", [])) or None
    include_domains = parse_csv_set(args.include_domain)
    exclude_domains = parse_csv_set(args.exclude_domain)
    cache = SQLiteCache(args.cache_db, args.cache_ttl_seconds)
    processed_channels = _load_processed_channels(args.checkpoint_file)
    with open(args.channels_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = _channel_url(row)
            if url in processed_channels:
                LOGGER.info("Skipping checkpointed channel %s", url)
                continue
            LOGGER.info("Scanning %s", row.get("title") or url)
            links = process_channel(
                channel_url=url,
                top_n_videos=args.top_n_videos,
                dry_run=args.dry_run,
                max_dns_workers=args.max_dns_workers,
                ignore_domains=ignore_domains,
                allowed_tlds=allowed_tlds,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                cache=cache,
                yt_dlp_delay=args.yt_dlp_delay,
                yt_dlp_retries=args.yt_dlp_retries,
                channel_timeout=args.channel_timeout,
                enrich_http=args.enrich_http,
                check_rdap=args.check_rdap,
                check_wayback=args.check_wayback,
                check_trademark=args.check_trademark,
            )
            all_dead_links.extend(links)
            processed_channels.add(url)
            _save_processed_channels(args.checkpoint_file, processed_channels)
            LOGGER.info("Found %s candidate links", len(links))

    write_dead_links_to_csv(all_dead_links, args.output)
    rows = [asdict(link) for link in all_dead_links]
    if args.json_output:
        write_json(rows, args.json_output)
    if args.report_output:
        write_markdown_report(rows, args.report_output, "Cashtube Phase 2 Summary")
    cache.close()
    LOGGER.info("Wrote %s rows to %s", len(all_dead_links), args.output)


if __name__ == "__main__":
    main()
