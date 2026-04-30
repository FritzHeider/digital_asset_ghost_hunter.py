from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
from dataclasses import asdict
from typing import List

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

from cashtube_utils import (
    SQLiteCache,
    configure_dns_timeout,
    configure_logging,
    load_checkpoint,
    load_config,
    normalize_tlds,
    parse_csv_set,
    prompt_for_keywords,
    save_checkpoint,
    validate_published_before,
    write_json,
    write_markdown_report,
)

from phase1_smart_discovery import (
    ChannelRecord,
    discover_channels,
    write_channels_to_csv,
)

from phase2_dead_link_detection import (
    DeadLinkEntry,
    process_channel,
    write_dead_links_to_csv,
)

LOGGER = logging.getLogger(__name__)


def _channel_id_to_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}"


def run_pipeline(
    api_key: str,
    published_before: str,
    min_video_count: int,
    recent_days: int,
    max_channels: int,
    top_n_videos: int,
    channels_output: str,
    dead_links_output: str,
    min_views: int = 0,
    keywords: str | None = None,
    keyword_list: list[str] | None = None,
    published_after: str | None = None,
    dry_run: bool = False,
    json_output: str | None = None,
    report_output: str | None = None,
    checkpoint_file: str | None = ".cashtube_channels_seen.json",
    scan_checkpoint_file: str | None = ".cashtube_phase2_checkpoint.json",
    youtube_delay: float = 0.0,
    phase2_config: dict | None = None,
    include_domains: set[str] | None = None,
    exclude_domains: set[str] | None = None,
    cache_db: str | None = ".cashtube_cache.sqlite3",
    cache_ttl_seconds: int = 86400,
    yt_dlp_delay: float = 0.0,
    yt_dlp_retries: int = 3,
    channel_timeout: float | None = None,
    enrich_http: bool = False,
    check_rdap: bool = False,
    check_wayback: bool = False,
    check_trademark: bool = False,
    max_channel_workers: int = 4,
) -> None:
    LOGGER.info("PHASE 1: SMART DISCOVERY")

    channels = discover_channels(
        api_key=api_key,
        published_before=published_before,
        min_video_count=min_video_count,
        recent_days=recent_days,
        max_channels=max_channels,
        min_views=min_views,
        keywords=keywords,
        keyword_list=keyword_list,
        published_after=published_after,
        checkpoint_file=checkpoint_file,
        youtube_delay=youtube_delay,
    )

    LOGGER.info("Phase 1 complete: %s qualifying channels found", len(channels))
    write_channels_to_csv(channels=channels, output_file=channels_output)

    LOGGER.info("PHASE 2: DEAD LINK DETECTION")

    ignore_domains = set((phase2_config or {}).get("ignore_domains", [])) or None
    allowed_tlds = normalize_tlds((phase2_config or {}).get("allowed_tlds", [])) or None
    cache = SQLiteCache(cache_db, cache_ttl_seconds)
    processed_channels = load_checkpoint(scan_checkpoint_file)

    all_dead_links: list[DeadLinkEntry] = []
    seen_pairs: set[tuple[str, str]] = set()
    total = len(channels)

    def scan_one(args_tuple: tuple[int, ChannelRecord]) -> tuple[str, list[DeadLinkEntry]]:
        idx, channel = args_tuple
        url = _channel_id_to_url(channel.channel_id)
        LOGGER.info("[%s/%s] Scanning %s", idx, total, channel.title)
        try:
            links = process_channel(
                channel_url=url,
                top_n_videos=top_n_videos,
                dry_run=dry_run,
                ignore_domains=ignore_domains,
                allowed_tlds=allowed_tlds,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                cache=cache,
                yt_dlp_delay=yt_dlp_delay,
                yt_dlp_retries=yt_dlp_retries,
                channel_timeout=channel_timeout,
                enrich_http=enrich_http,
                check_rdap=check_rdap,
                check_wayback=check_wayback,
                check_trademark=check_trademark,
            )
            LOGGER.info("[%s/%s] Dead links found: %s", idx, total, len(links))
            return url, links
        except Exception:
            LOGGER.exception("Scan failed for %s", channel.title)
            return url, []

    pending = [
        (idx, ch)
        for idx, ch in enumerate(channels, 1)
        if _channel_id_to_url(ch.channel_id) not in processed_channels
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_channel_workers) as executor:
        for url, links in executor.map(scan_one, pending):
            for link in links:
                pair = (url, link.dead_domain)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    all_dead_links.append(link)
            processed_channels.add(url)
            save_checkpoint(scan_checkpoint_file, processed_channels)

    write_dead_links_to_csv(dead_links=all_dead_links, output_path=dead_links_output)
    rows = [asdict(link) for link in all_dead_links]
    if json_output:
        write_json(rows, json_output)
    if report_output:
        write_markdown_report(rows, report_output, "Cashtube Pipeline Summary")
    cache.close()
    LOGGER.info("Pipeline complete: %s total rows saved to %s", len(all_dead_links), dead_links_output)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Cashtube Full Pipeline")
    parser.add_argument("--api-key", help="YouTube Data API key")
    parser.add_argument("--published-before", default="2016-01-01T00:00:00Z")
    parser.add_argument("--published-after", default=None,
                        help="Narrow to channels created after this ISO-8601 date")
    parser.add_argument("--min-video-count", type=int, default=50)
    parser.add_argument("--recent-days", type=int, default=180)
    parser.add_argument("--max-channels", type=int, default=100)
    parser.add_argument("--min-views", type=int, default=0)
    parser.add_argument("--keywords", default=None)
    parser.add_argument("--keywords-file", default=None)
    parser.add_argument("--top-n-videos", type=int, default=20)
    parser.add_argument("--channels-output", default="phase1_results.csv")
    parser.add_argument("--dead-links-output", default="phase2_results.csv")
    parser.add_argument("--json-output", default=None)
    parser.add_argument("--report-output", default=None)
    parser.add_argument("--checkpoint-file", default=".cashtube_channels_seen.json")
    parser.add_argument("--scan-checkpoint-file", default=".cashtube_phase2_checkpoint.json")
    parser.add_argument("--config", default=None)
    parser.add_argument("--include-domain", default=None)
    parser.add_argument("--exclude-domain", default=None)
    parser.add_argument("--cache-db", default=".cashtube_cache.sqlite3")
    parser.add_argument("--cache-ttl-seconds", type=int, default=86400)
    parser.add_argument("--youtube-delay", type=float, default=0.0)
    parser.add_argument("--yt-dlp-delay", type=float, default=0.0)
    parser.add_argument("--yt-dlp-retries", type=int, default=3)
    parser.add_argument("--channel-timeout", type=float, default=None)
    parser.add_argument("--max-channel-workers", type=int, default=4,
                        help="Parallel threads for Phase 2 channel scanning")
    parser.add_argument("--enrich-http", action="store_true")
    parser.add_argument("--check-rdap", action="store_true")
    parser.add_argument("--check-wayback", action="store_true")
    parser.add_argument("--check-trademark", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--json-logs", action="store_true")
    args = parser.parse_args()
    configure_logging(json_logs=args.json_logs, level=args.log_level)
    configure_dns_timeout()

    for date_arg in filter(None, [args.published_before, args.published_after]):
        try:
            validate_published_before(date_arg)
        except ValueError as exc:
            parser.error(str(exc))

    api_key = args.api_key or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        parser.error("Provide API key via --api-key or .env YOUTUBE_API_KEY")

    keyword_config = load_config(args.keywords_file)
    phase2_config = load_config(args.config)
    keyword_list = keyword_config.get("keywords") if keyword_config else None

    if not keyword_list and not args.keywords:
        keyword_list = prompt_for_keywords()

    run_pipeline(
        api_key=api_key,
        published_before=args.published_before,
        published_after=args.published_after,
        min_video_count=args.min_video_count,
        recent_days=args.recent_days,
        max_channels=args.max_channels,
        top_n_videos=args.top_n_videos,
        channels_output=args.channels_output,
        dead_links_output=args.dead_links_output,
        min_views=args.min_views,
        keywords=args.keywords,
        keyword_list=keyword_list,
        dry_run=args.dry_run,
        json_output=args.json_output,
        report_output=args.report_output,
        checkpoint_file=args.checkpoint_file,
        scan_checkpoint_file=args.scan_checkpoint_file,
        youtube_delay=args.youtube_delay,
        phase2_config=phase2_config,
        include_domains=parse_csv_set(args.include_domain),
        exclude_domains=parse_csv_set(args.exclude_domain),
        cache_db=args.cache_db,
        cache_ttl_seconds=args.cache_ttl_seconds,
        yt_dlp_delay=args.yt_dlp_delay,
        yt_dlp_retries=args.yt_dlp_retries,
        channel_timeout=args.channel_timeout,
        enrich_http=args.enrich_http,
        check_rdap=args.check_rdap,
        check_wayback=args.check_wayback,
        check_trademark=args.check_trademark,
        max_channel_workers=args.max_channel_workers,
    )


if __name__ == "__main__":
    main()
