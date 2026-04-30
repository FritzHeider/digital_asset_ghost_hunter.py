from __future__ import annotations

import argparse
import csv
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import List

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

from cashtube_utils import (
    chunked,
    configure_logging,
    make_session,
    validate_published_before,
    youtube_get,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class ChannelRecord:
    channel_id: str
    title: str
    view_count: int
    video_count: int
    published_at: str


def _search_legacy_video_channels(
    api_key: str,
    query: str,
    published_before: str,
    max_channels: int,
) -> list[str]:
    session = make_session()
    channel_ids: list[str] = []
    seen: set[str] = set()
    page_token: str | None = None

    while len(channel_ids) < max_channels:
        params = {
            "part": "snippet",
            "type": "video",
            "publishedBefore": published_before,
            "q": query,
            "maxResults": 50,
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token

        data = youtube_get(session, "search", params)
        for item in data.get("items", []):
            channel_id = item.get("snippet", {}).get("channelId")
            if channel_id and channel_id not in seen:
                seen.add(channel_id)
                channel_ids.append(channel_id)
                if len(channel_ids) >= max_channels:
                    break

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return channel_ids


def _has_recent_upload(
    session,
    api_key: str,
    channel_id: str,
    recent_days: int,
) -> bool:
    if recent_days <= 0:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
    published_after = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
    data = youtube_get(
        session,
        "search",
        {
            "part": "id",
            "type": "video",
            "channelId": channel_id,
            "publishedAfter": published_after,
            "maxResults": 1,
            "key": api_key,
        },
    )
    return bool(data.get("items"))


def discover_channels(
    api_key: str,
    published_before: str,
    min_video_count: int,
    recent_days: int,
    max_channels: int,
    min_views: int = 0,
    keywords: str | None = None,
) -> List[ChannelRecord]:
    """
    Search for legacy videos, then qualify their parent channels.

    recent_days excludes channels that uploaded recently; pass 0 to disable that
    inactivity filter.
    """
    validate_published_before(published_before)
    query = keywords or "pokemon cards"
    LOGGER.info("Searching legacy videos matching %r", query)

    try:
        candidate_ids = _search_legacy_video_channels(
            api_key=api_key,
            query=query,
            published_before=published_before,
            max_channels=max(max_channels * 3, 50),
        )
        if not candidate_ids:
            LOGGER.warning("No channels found in search results")
            return []

        LOGGER.info("Found %s potential channels; fetching statistics", len(candidate_ids))
        session = make_session()
        results: list[ChannelRecord] = []
        for batch in chunked(candidate_ids, 50):
            data = youtube_get(
                session,
                "channels",
                {
                    "part": "snippet,statistics",
                    "id": ",".join(batch),
                    "key": api_key,
                },
            )
            for item in data.get("items", []):
                stats = item.get("statistics", {})
                video_count = int(stats.get("videoCount", 0))
                view_count = int(stats.get("viewCount", 0))
                if video_count < min_video_count or view_count < min_views:
                    continue
                if _has_recent_upload(session, api_key, item["id"], recent_days):
                    continue

                results.append(
                    ChannelRecord(
                        channel_id=item["id"],
                        title=item.get("snippet", {}).get("title", ""),
                        view_count=view_count,
                        video_count=video_count,
                        published_at=item.get("snippet", {}).get("publishedAt", ""),
                    )
                )
                if len(results) >= max_channels:
                    break
            if len(results) >= max_channels:
                break

        return sorted(results, key=lambda c: (-c.view_count, c.title.lower(), c.channel_id))
    except Exception:
        LOGGER.exception("Discovery failed")
        return []


def write_channels_to_csv(channels: List[ChannelRecord], output_file: str) -> None:
    if not channels:
        return
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(channels[0]).keys()))
        writer.writeheader()
        for channel in channels:
            writer.writerow(asdict(channel))


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Cashtube Phase 1: Smart Discovery")
    parser.add_argument("--api-key", help="YouTube Data API key")
    parser.add_argument("--published-before", default="2016-01-01T00:00:00Z")
    parser.add_argument("--min-video-count", type=int, default=50)
    parser.add_argument("--recent-days", type=int, default=180)
    parser.add_argument("--max-channels", type=int, default=100)
    parser.add_argument("--min-views", type=int, default=0)
    parser.add_argument("--keywords", default=None)
    parser.add_argument("--output", default="phase1_results.csv")
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

    channels = discover_channels(
        api_key=api_key,
        published_before=args.published_before,
        min_video_count=args.min_video_count,
        recent_days=args.recent_days,
        max_channels=args.max_channels,
        min_views=args.min_views,
        keywords=args.keywords,
    )
    write_channels_to_csv(channels, args.output)
    LOGGER.info("Wrote %s channels to %s", len(channels), args.output)


if __name__ == "__main__":
    main()
