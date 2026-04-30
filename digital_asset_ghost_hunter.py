from __future__ import annotations

import argparse
import csv
import logging
import os

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

from cashtube_utils import (
    configure_dns_timeout,
    configure_logging,
    validate_published_before,
)
from phase1_smart_discovery import discover_channels, write_channels_to_csv
from phase2_dead_link_detection import DeadLinkEntry, process_channel

LOGGER = logging.getLogger(__name__)

GHOST_KEYWORDS = [
    "tech review",
    "unboxing",
    "hands-on review",
    "kickstarter gadget",
    "startup demo",
    "saas tutorial",
]


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Digital Asset Ghost Hunter")
    parser.add_argument("--api-key", help="YouTube API Key")
    parser.add_argument("--published-before", default="2016-01-01T00:00:00Z")
    parser.add_argument("--min-views", type=int, default=2_000_000)
    parser.add_argument("--min-video-count", type=int, default=50)
    parser.add_argument("--recent-days", type=int, default=180)
    parser.add_argument("--top-n-videos", type=int, default=20)
    parser.add_argument("--max-channels", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--json-logs", action="store_true")
    parser.add_argument("--output", default="ghost_results.csv")
    args = parser.parse_args()
    configure_logging(json_logs=args.json_logs, level=args.log_level)
    configure_dns_timeout()

    try:
        validate_published_before(args.published_before)
    except ValueError as exc:
        parser.error(str(exc))

    api_key = args.api_key or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        parser.error("YouTube API key not found. Set YOUTUBE_API_KEY or pass --api-key.")

    channels = discover_channels(
        api_key=api_key,
        published_before=args.published_before,
        min_video_count=args.min_video_count,
        recent_days=args.recent_days,
        max_channels=args.max_channels,
        min_views=args.min_views,
        keyword_list=GHOST_KEYWORDS,
    )
    LOGGER.info("Found %s candidate channels", len(channels))

    all_results: list[dict] = []
    total = len(channels)
    for idx, channel in enumerate(channels, 1):
        LOGGER.info("[%s/%s] Scanning %s", idx, total, channel.title)
        entries: list[DeadLinkEntry] = process_channel(
            channel_url=f"https://www.youtube.com/channel/{channel.channel_id}",
            top_n_videos=args.top_n_videos,
            dry_run=args.dry_run,
        )
        for entry in entries:
            all_results.append(
                {
                    "channel_id": channel.channel_id,
                    "channel_title": channel.title,
                    "view_count": channel.view_count,
                    "video_url": entry.video_url,
                    "dead_domain": entry.dead_domain,
                    "status": entry.status,
                    "priority_score": entry.priority_score,
                    "first_seen_at": entry.first_seen_at,
                    "source_description_snippet": entry.source_description_snippet,
                }
            )
            LOGGER.info("Ghost domain found: %s (score %s)", entry.dead_domain, entry.priority_score)

    all_results.sort(key=lambda row: (
        -row["priority_score"],
        row["channel_id"],
        row["dead_domain"],
    ))

    fieldnames = [
        "channel_id",
        "channel_title",
        "view_count",
        "video_url",
        "dead_domain",
        "status",
        "priority_score",
        "first_seen_at",
        "source_description_snippet",
    ]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    LOGGER.info("Hunt complete: wrote %s leads to %s", len(all_results), args.output)


if __name__ == "__main__":
    main()
