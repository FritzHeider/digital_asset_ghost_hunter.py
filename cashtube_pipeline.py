from __future__ import annotations
import argparse
import logging
import os
from typing import List

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

from cashtube_utils import configure_logging, validate_published_before

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

def channel_id_to_url(channel_id: str) -> str:
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
    dry_run: bool = False,
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
    )

    LOGGER.info("Phase 1 complete: %s qualifying channels found", len(channels))
    write_channels_to_csv(channels=channels, output_file=channels_output)

    LOGGER.info("PHASE 2: DEAD LINK DETECTION")

    all_dead_links: List[DeadLinkEntry] = []
    seen_pairs: set[tuple[str, str]] = set()
    for idx, channel in enumerate(channels, start=1):
        url = channel_id_to_url(channel.channel_id)
        LOGGER.info("[%s/%s] Scanning %s", idx, len(channels), channel.title)
        try:
            links = process_channel(channel_url=url, top_n_videos=top_n_videos, dry_run=dry_run)
            for link in links:
                pair = (channel.channel_id, link.dead_domain)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    all_dead_links.append(link)
            LOGGER.info("Dead links found: %s", len(links))
        except Exception:
            LOGGER.exception("Scan failed for %s", channel.title)

    write_dead_links_to_csv(dead_links=all_dead_links, output_path=dead_links_output)
    LOGGER.info("Pipeline complete: %s total rows saved to %s", len(all_dead_links), dead_links_output)

def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Cashtube Full Pipeline")
    parser.add_argument("--api-key", help="YouTube Data API key")
    parser.add_argument("--published-before", default="2016-01-01T00:00:00Z")
    parser.add_argument("--min-video-count", type=int, default=50)
    parser.add_argument("--recent-days", type=int, default=180)
    parser.add_argument("--max-channels", type=int, default=100)
    parser.add_argument("--min-views", type=int, default=0)
    parser.add_argument("--keywords", default=None)
    parser.add_argument("--top-n-videos", type=int, default=20)
    parser.add_argument("--channels-output", default="phase1_results.csv")
    parser.add_argument("--dead-links-output", default="phase2_results.csv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-logs", action="store_true")
    args = parser.parse_args()
    configure_logging(json_logs=args.json_logs)

    try:
        validate_published_before(args.published_before)
    except ValueError as exc:
        parser.error(str(exc))

    api_key = args.api_key or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        parser.error("Provide API key via --api-key or .env YOUTUBE_API_KEY")

    run_pipeline(
        api_key=api_key, 
        published_before=args.published_before,
        min_video_count=args.min_video_count, 
        recent_days=args.recent_days,
        max_channels=args.max_channels, 
        top_n_videos=args.top_n_videos,
        channels_output=args.channels_output, 
        dead_links_output=args.dead_links_output,
        min_views=args.min_views, 
        keywords=args.keywords,
        dry_run=args.dry_run,
    )

if __name__ == "__main__":
    main()
