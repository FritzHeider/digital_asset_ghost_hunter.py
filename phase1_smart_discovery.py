from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

from cashtube_utils import (
    RateLimiter,
    chunked,
    configure_logging,
    load_config,
    make_session,
    validate_published_before,
    write_json,
    write_dicts_to_csv,
    write_markdown_report,
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
    source_keyword: str = ""


def _search_legacy_video_channels(
    api_key: str,
    query: str,
    published_before: str,
    max_channels: int,
    rate_limiter: RateLimiter | None = None,
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

        data = youtube_get(session, "search", params, rate_limiter=rate_limiter)
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
    rate_limiter: RateLimiter | None = None,
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
        rate_limiter=rate_limiter,
    )
    return bool(data.get("items"))


def _load_seen_channel_ids(path: str | None) -> set[str]:
    if not path or not Path(path).exists():
        return set()
    try:
        return set(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Could not read channel checkpoint %s; starting fresh", path)
        return set()


def _save_seen_channel_ids(path: str | None, channel_ids: set[str]) -> None:
    if not path:
        return
    Path(path).write_text(json.dumps(sorted(channel_ids), indent=2) + "\n", encoding="utf-8")


def discover_channels(
    api_key: str,
    published_before: str,
    min_video_count: int,
    recent_days: int,
    max_channels: int,
    min_views: int = 0,
    keywords: str | None = None,
    keyword_list: list[str] | None = None,
    checkpoint_file: str | None = None,
    youtube_delay: float = 0.0,
) -> List[ChannelRecord]:
    """
    Search for legacy videos, then qualify their parent channels.

    recent_days excludes channels that uploaded recently; pass 0 to disable that
    inactivity filter.
    """
    validate_published_before(published_before)
    queries = keyword_list or ([keywords] if keywords else ["pokemon cards"])
    rate_limiter = RateLimiter(youtube_delay)
    seen_channel_ids = _load_seen_channel_ids(checkpoint_file)
    newly_seen = set(seen_channel_ids)

    try:
        candidates_by_keyword: dict[str, str] = {}
        for query in queries:
            LOGGER.info("Searching legacy videos matching %r", query)
            candidate_ids = _search_legacy_video_channels(
                api_key=api_key,
                query=query,
                published_before=published_before,
                max_channels=max(max_channels * 3, 50),
                rate_limiter=rate_limiter,
            )
            for channel_id in candidate_ids:
                if channel_id not in seen_channel_ids:
                    candidates_by_keyword.setdefault(channel_id, query)
                newly_seen.add(channel_id)

        candidate_ids = list(candidates_by_keyword)
        if not candidate_ids:
            LOGGER.warning("No channels found in search results")
            _save_seen_channel_ids(checkpoint_file, newly_seen)
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
                rate_limiter=rate_limiter,
            )
            for item in data.get("items", []):
                stats = item.get("statistics", {})
                video_count = int(stats.get("videoCount", 0))
                view_count = int(stats.get("viewCount", 0))
                if video_count < min_video_count or view_count < min_views:
                    continue
                if _has_recent_upload(session, api_key, item["id"], recent_days, rate_limiter):
                    continue

                results.append(
                    ChannelRecord(
                        channel_id=item["id"],
                        title=item.get("snippet", {}).get("title", ""),
                        view_count=view_count,
                        video_count=video_count,
                        published_at=item.get("snippet", {}).get("publishedAt", ""),
                        source_keyword=candidates_by_keyword.get(item["id"], ""),
                    )
                )
                if len(results) >= max_channels:
                    break
            if len(results) >= max_channels:
                break

        _save_seen_channel_ids(checkpoint_file, newly_seen)
        return sorted(results, key=lambda c: (-c.view_count, c.title.lower(), c.channel_id))
    except Exception:
        LOGGER.exception("Discovery failed")
        return []


def write_channels_to_csv(channels: List[ChannelRecord], output_file: str) -> None:
    fieldnames = list(ChannelRecord("", "", 0, 0, "").__dict__.keys())
    write_dicts_to_csv([asdict(channel) for channel in channels], output_file, fieldnames)


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
    parser.add_argument("--keywords-file", default=None)
    parser.add_argument("--output", default="phase1_results.csv")
    parser.add_argument("--json-output", default=None)
    parser.add_argument("--report-output", default=None)
    parser.add_argument("--checkpoint-file", default=".cashtube_channels_seen.json")
    parser.add_argument("--youtube-delay", type=float, default=0.0)
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

    config = load_config(args.keywords_file)
    keyword_list = config.get("keywords") if config else None
    start = time.time()
    channels = discover_channels(
        api_key=api_key,
        published_before=args.published_before,
        min_video_count=args.min_video_count,
        recent_days=args.recent_days,
        max_channels=args.max_channels,
        min_views=args.min_views,
        keywords=args.keywords,
        keyword_list=keyword_list,
        checkpoint_file=args.checkpoint_file,
        youtube_delay=args.youtube_delay,
    )
    write_channels_to_csv(channels, args.output)
    rows = [asdict(channel) for channel in channels]
    if args.json_output:
        write_json(rows, args.json_output)
    if args.report_output:
        write_markdown_report(rows, args.report_output, "Cashtube Phase 1 Summary")
    LOGGER.info("Wrote %s channels to %s", len(channels), args.output)
    LOGGER.info("Phase 1 runtime: %.2fs", time.time() - start)


if __name__ == "__main__":
    main()
