from __future__ import annotations

import argparse
import csv
import logging
import os
from dataclasses import dataclass
from typing import Dict, List

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

from cashtube_utils import (
    configure_logging,
    make_session,
    validate_published_before,
    youtube_get,
)
from phase2_dead_link_detection import process_channel

LOGGER = logging.getLogger(__name__)

TECH_KEYWORDS = [
    "tech review",
    "unboxing",
    "hands-on",
]


@dataclass
class Channel:
    channel_id: str
    title: str
    view_count: int


def search_channels(
    api_key: str,
    published_before: str,
    min_views: int,
    max_channels: int = 100,
) -> List[Channel]:
    validate_published_before(published_before)
    discovered: Dict[str, Channel] = {}
    session = make_session()

    for keyword in TECH_KEYWORDS:
        LOGGER.info("Querying YouTube API for %r", keyword)
        page_token: str | None = None
        while len(discovered) < max_channels:
            params = {
                "part": "snippet",
                "type": "channel",
                "publishedBefore": published_before,
                "order": "viewCount",
                "q": keyword,
                "maxResults": 50,
                "key": api_key,
            }
            if page_token:
                params["pageToken"] = page_token

            try:
                data = youtube_get(session, "search", params)
                channel_ids = [
                    item["snippet"]["channelId"]
                    for item in data.get("items", [])
                    if item.get("snippet", {}).get("channelId")
                ]

                if channel_ids:
                    details = youtube_get(
                        session,
                        "channels",
                        {
                            "part": "snippet,statistics",
                            "id": ",".join(channel_ids),
                            "key": api_key,
                        },
                    )
                    for channel in details.get("items", []):
                        views = int(channel.get("statistics", {}).get("viewCount", 0))
                        if views >= min_views:
                            discovered[channel["id"]] = Channel(
                                channel_id=channel["id"],
                                title=channel.get("snippet", {}).get("title", ""),
                                view_count=views,
                            )

                page_token = data.get("nextPageToken")
                if not page_token:
                    break
            except Exception:
                LOGGER.exception("YouTube API query failed for %r", keyword)
                break

    return sorted(discovered.values(), key=lambda c: (-c.view_count, c.title.lower()))


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Digital Asset Ghost Hunter")
    parser.add_argument("--api-key", help="YouTube API Key")
    parser.add_argument("--published-before", default="2016-01-01T00:00:00Z")
    parser.add_argument("--min-views", type=int, default=2000000)
    parser.add_argument("--top-n-videos", type=int, default=20)
    parser.add_argument("--max-channels", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-logs", action="store_true")
    parser.add_argument("--output", default="ghost_results.csv")
    args = parser.parse_args()
    configure_logging(json_logs=args.json_logs)

    try:
        validate_published_before(args.published_before)
    except ValueError as exc:
        parser.error(str(exc))

    api_key = args.api_key or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        parser.error("YouTube API key not found")

    channels = search_channels(
        api_key=api_key,
        published_before=args.published_before,
        min_views=args.min_views,
        max_channels=args.max_channels,
    )
    LOGGER.info("Found %s candidate channels", len(channels))

    all_results = []
    for idx, channel in enumerate(channels, 1):
        LOGGER.info("[%s/%s] Scanning %s", idx, len(channels), channel.title)
        entries = process_channel(
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
                    "first_seen_at": entry.first_seen_at,
                    "source_description_snippet": entry.source_description_snippet,
                }
            )
            LOGGER.info("Ghost domain found: %s", entry.dead_domain)

    all_results.sort(key=lambda row: (row["channel_id"], row["dead_domain"], row["video_url"]))
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "channel_id",
            "channel_title",
            "view_count",
            "video_url",
            "dead_domain",
            "status",
            "first_seen_at",
            "source_description_snippet",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)

    LOGGER.info("Hunt complete: wrote %s total leads to %s", len(all_results), args.output)


if __name__ == "__main__":
    main()
