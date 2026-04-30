from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

from cashtube_utils import (
    RateLimiter,
    chunked,
    configure_dns_timeout,
    configure_logging,
    load_checkpoint,
    load_config,
    make_session,
    prompt_for_keywords,
    save_checkpoint,
    validate_published_before,
    write_dicts_to_csv,
    write_json,
    write_markdown_report,
    youtube_get,
)

LOGGER = logging.getLogger(__name__)

FATAL_YOUTUBE_403_REASONS = {
    "accessNotConfigured",
    "dailyLimitExceeded",
    "ipRefererBlocked",
    "keyInvalid",
    "quotaExceeded",
    "rateLimitExceeded",
}


@dataclass
class ChannelRecord:
    channel_id: str
    title: str
    view_count: int
    video_count: int
    published_at: str
    last_upload: str = ""
    source_keyword: str = ""


def _search_legacy_video_channels(
    session: requests.Session,
    api_key: str,
    query: str,
    published_before: str,
    published_after: str | None,
    max_channels: int,
    rate_limiter: RateLimiter | None = None,
) -> list[str]:
    channel_ids: list[str] = []
    seen: set[str] = set()
    page_token: str | None = None

    while len(channel_ids) < max_channels:
        params: dict = {
            "part": "snippet",
            "type": "video",
            "publishedBefore": published_before,
            "q": query,
            "maxResults": 50,
            "key": api_key,
        }
        if published_after:
            params["publishedAfter"] = published_after
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


def _handle_recent_upload_403(
    exc: requests.HTTPError,
    label: str,
) -> None:
    """Re-raise fatal 403s; log and swallow non-fatal ones."""
    response = exc.response
    if response is None or response.status_code != 403:
        raise exc
    reason, message = _youtube_error_details(response)
    if reason in FATAL_YOUTUBE_403_REASONS:
        raise exc
    LOGGER.warning(
        "Could not check recent uploads for %s due to YouTube 403%s; keeping channel eligible",
        label,
        f" ({reason}: {message})" if reason or message else "",
    )


def _has_recent_upload(
    session: requests.Session,
    api_key: str,
    channel_id: str,
    recent_days: int,
    rate_limiter: RateLimiter | None = None,
    uploads_playlist_id: str | None = None,
) -> Optional[str]:
    """Return the most recent upload date (ISO string) if the channel uploaded
    within ``recent_days``, or None if it has not.

    When ``uploads_playlist_id`` is supplied (from a prior channels.list
    contentDetails fetch) this uses ``playlistItems.list`` (1 quota unit).
    Otherwise it falls back to ``search`` (100 quota units).

    A non-fatal YouTube 403 returns None; a fatal 403 re-raises.
    """
    if recent_days <= 0:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)

    if uploads_playlist_id:
        try:
            data = youtube_get(
                session,
                "playlistItems",
                {
                    "part": "contentDetails",
                    "playlistId": uploads_playlist_id,
                    "maxResults": 1,
                    "key": api_key,
                },
                rate_limiter=rate_limiter,
            )
        except requests.HTTPError as exc:
            _handle_recent_upload_403(exc, f"playlist {uploads_playlist_id}")
            return None

        items = data.get("items", [])
        if not items:
            return None
        published_at = items[0].get("contentDetails", {}).get("videoPublishedAt", "")
        if not published_at:
            return None
        try:
            upload_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        return published_at if upload_dt >= cutoff else None

    # Fallback: search API (costs 100 quota units — avoid when possible)
    published_after = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        data = youtube_get(
            session,
            "search",
            {
                "part": "snippet",
                "type": "video",
                "channelId": channel_id,
                "publishedAfter": published_after,
                "maxResults": 1,
                "key": api_key,
            },
            rate_limiter=rate_limiter,
        )
    except requests.HTTPError as exc:
        _handle_recent_upload_403(exc, f"channel {channel_id}")
        return None

    items = data.get("items", [])
    if not items:
        return None
    return items[0].get("snippet", {}).get("publishedAt", "")


def _youtube_error_details(response: requests.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except ValueError:
        return "", ""

    error = payload.get("error", {})
    message = str(error.get("message", ""))
    errors = error.get("errors", [])
    if errors:
        first_error = errors[0]
        return str(first_error.get("reason", "")), message
    return "", message


def discover_channels(
    api_key: str,
    published_before: str,
    min_video_count: int,
    recent_days: int,
    max_channels: int,
    min_views: int = 0,
    keywords: str | None = None,
    keyword_list: list[str] | None = None,
    published_after: str | None = None,
    checkpoint_file: str | None = None,
    youtube_delay: float = 0.0,
) -> List[ChannelRecord]:
    """Search for legacy videos, then qualify their parent channels.

    ``recent_days`` skips channels that uploaded recently; pass 0 to disable.
    Raises ValueError when no keywords are provided.
    """
    validate_published_before(published_before)

    queries = keyword_list or ([keywords] if keywords else None)
    if not queries:
        raise ValueError(
            "At least one keyword is required. Pass --keywords or --keywords-file."
        )

    rate_limiter = RateLimiter(youtube_delay)
    seen_channel_ids = load_checkpoint(checkpoint_file)
    session = make_session()

    candidates_by_keyword: dict[str, str] = {}
    for query in queries:
        LOGGER.info("Searching legacy videos matching %r", query)
        candidate_ids = _search_legacy_video_channels(
            session=session,
            api_key=api_key,
            query=query,
            published_before=published_before,
            published_after=published_after,
            max_channels=max(max_channels * 3, 50),
            rate_limiter=rate_limiter,
        )
        for channel_id in candidate_ids:
            if channel_id not in seen_channel_ids:
                candidates_by_keyword.setdefault(channel_id, query)

    candidate_ids = list(candidates_by_keyword)
    if not candidate_ids:
        LOGGER.warning("No new channels found in search results")
        return []

    LOGGER.info("Found %s candidate channels; fetching statistics", len(candidate_ids))
    results: list[ChannelRecord] = []
    for batch in chunked(candidate_ids, 50):
        data = youtube_get(
            session,
            "channels",
            {
                "part": "snippet,statistics,contentDetails",
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

            uploads_playlist_id = (
                item.get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads", "")
            )
            last_upload = _has_recent_upload(
                session,
                api_key,
                item["id"],
                recent_days,
                rate_limiter,
                uploads_playlist_id=uploads_playlist_id or None,
            )
            if last_upload is not None:
                # Channel uploaded recently — still active, not a ghost target
                continue

            results.append(
                ChannelRecord(
                    channel_id=item["id"],
                    title=item.get("snippet", {}).get("title", ""),
                    view_count=view_count,
                    video_count=video_count,
                    published_at=item.get("snippet", {}).get("publishedAt", ""),
                    last_upload=last_upload or "",
                    source_keyword=candidates_by_keyword.get(item["id"], ""),
                )
            )
            if len(results) >= max_channels:
                break
        if len(results) >= max_channels:
            break

    # Checkpoint tracks only channels that were fully qualified and processed
    qualified_ids = seen_channel_ids | {r.channel_id for r in results}
    save_checkpoint(checkpoint_file, qualified_ids)

    return sorted(results, key=lambda c: (-c.view_count, c.title.lower(), c.channel_id))


def write_channels_to_csv(channels: List[ChannelRecord], output_file: str) -> None:
    fieldnames = [f.name for f in dataclasses.fields(ChannelRecord)]
    write_dicts_to_csv([asdict(channel) for channel in channels], output_file, fieldnames)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Cashtube Phase 1: Smart Discovery")
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
    parser.add_argument("--output", default="phase1_results.csv")
    parser.add_argument("--json-output", default=None)
    parser.add_argument("--report-output", default=None)
    parser.add_argument("--checkpoint-file", default=".cashtube_channels_seen.json")
    parser.add_argument("--youtube-delay", type=float, default=0.0)
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

    config = load_config(args.keywords_file)
    keyword_list = config.get("keywords") if config else None

    if not keyword_list and not args.keywords:
        keyword_list = prompt_for_keywords()

    start = time.time()
    channels = discover_channels(
        api_key=api_key,
        published_before=args.published_before,
        published_after=args.published_after,
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
