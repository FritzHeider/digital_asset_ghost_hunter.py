"""
phase1_smart_discovery.py
=========================

This module implements Phase 1 of the Cashtube pipeline: Smart Discovery.

Goal:
Identify older, high-traffic YouTube channels in target niches that may contain
abandoned or expired domains in their metadata.

Core Strategy:
1. Use YouTube Data API v3 search.list
2. Search channels created before a cutoff date
3. Sort by view count
4. Batch channel IDs into groups of 50
5. Use channels.list for quota efficiency
6. Filter by:
   - Minimum total views
   - Minimum video count
   - Recent upload activity

Outputs:
CSV of qualified channels for later dead-link extraction.

Requirements:
- requests
- valid YouTube Data API key

Example:
python phase1_smart_discovery.py \
    --api-key YOUR_API_KEY \
    --published-before 2016-01-01T00:00:00Z \
    --min-video-count 50 \
    --recent-days 180 \
    --max-channels 100 \
    --output phase1_results.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from dataclasses import dataclass
from typing import Iterable, List, Optional

import requests


YOUTUBE_API_BASE_URL = "https://www.googleapis.com/youtube/v3"


@dataclass
class ChannelRecord:
    """
    Represents a qualifying YouTube channel discovered during Phase 1.
    """
    channel_id: str
    title: str
    created_at: str
    video_count: int
    view_count: int
    last_upload: str


def search_channels(
    api_key: str,
    published_before: str,
    max_results: int = 50,
    page_token: Optional[str] = None,
    keywords: Optional[str] = None,
) -> dict:
    """
    Search for YouTube channels created before a specific date.

    Parameters:
        api_key: YouTube API key
        published_before: RFC3339 cutoff date
        max_results: Max search results per call (<=50)
        page_token: Pagination token
        keywords: Optional niche keywords

    Returns:
        JSON response dict
    """
    params = {
        "part": "snippet",
        "type": "channel",
        "publishedBefore": published_before,
        "order": "viewCount",
        "maxResults": max_results,
        "key": api_key,
    }

    if page_token:
        params["pageToken"] = page_token

    if keywords:
        params["q"] = keywords

    response = requests.get(
        f"{YOUTUBE_API_BASE_URL}/search",
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def batch_get_channels(
    api_key: str,
    channel_ids: List[str],
    parts: str = "snippet,statistics",
) -> dict:
    """
    Batch fetch detailed channel data using channels.list.

    Max 50 IDs per request.

    Parameters:
        api_key: YouTube API key
        channel_ids: List of channel IDs
        parts: Resource parts

    Returns:
        JSON response
    """
    if not channel_ids:
        return {"items": []}

    params = {
        "part": parts,
        "id": ",".join(channel_ids),
        "maxResults": min(len(channel_ids), 50),
        "key": api_key,
    }

    response = requests.get(
        f"{YOUTUBE_API_BASE_URL}/channels",
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def get_latest_upload_date(
    api_key: str,
    channel_id: str,
) -> Optional[str]:
    """
    Retrieve the most recent upload date for a channel.

    Parameters:
        api_key: YouTube API key
        channel_id: Channel ID

    Returns:
        Latest upload ISO date string or None
    """
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "order": "date",
        "type": "video",
        "maxResults": 1,
        "key": api_key,
    }

    response = requests.get(
        f"{YOUTUBE_API_BASE_URL}/search",
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()
    items = data.get("items", [])

    if not items:
        return None

    return items[0].get("snippet", {}).get("publishedAt")


def filter_channels(
    api_key: str,
    channels_data: Iterable[dict],
    min_video_count: int,
    recent_days: int,
    min_views: int = 0,
    now: Optional[dt.datetime] = None,
) -> List[ChannelRecord]:
    """
    Filter channels based on:
    - Minimum videos
    - Minimum views
    - Recent upload activity

    Parameters:
        api_key
        channels_data
        min_video_count
        recent_days
        min_views
        now

    Returns:
        List of ChannelRecord
    """
    if now is None:
        now = dt.datetime.utcnow()

    qualifying_channels: List[ChannelRecord] = []

    for item in channels_data:
        channel_id = item.get("id")
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})

        if not channel_id:
            continue

        try:
            video_count = int(statistics.get("videoCount", 0))
            view_count = int(statistics.get("viewCount", 0))
        except (ValueError, TypeError):
            continue

        if video_count < min_video_count:
            continue

        if view_count < min_views:
            continue

        latest_upload = get_latest_upload_date(api_key, channel_id)

        if not latest_upload:
            continue

        try:
            latest_dt = dt.datetime.fromisoformat(
                latest_upload.replace("Z", "+00:00")
            )
        except ValueError:
            continue

        delta = now - latest_dt.replace(tzinfo=None)

        if delta.days > recent_days:
            continue

        qualifying_channels.append(
            ChannelRecord(
                channel_id=channel_id,
                title=snippet.get("title", ""),
                created_at=snippet.get("publishedAt", ""),
                video_count=video_count,
                view_count=view_count,
                last_upload=latest_upload,
            )
        )

    return qualifying_channels


def discover_channels(
    api_key: str,
    published_before: str,
    min_video_count: int = 50,
    recent_days: int = 180,
    max_channels: int = 100,
    min_views: int = 0,
    keywords: Optional[str] = None,
) -> List[ChannelRecord]:
    """
    Main discovery pipeline.

    Returns:
        Qualified channels
    """
    collected: List[ChannelRecord] = []
    next_token: Optional[str] = None

    while len(collected) < max_channels:
        search_response = search_channels(
            api_key=api_key,
            published_before=published_before,
            max_results=min(50, max_channels),
            page_token=next_token,
            keywords=keywords,
        )

        search_items = search_response.get("items", [])

        if not search_items:
            break

        channel_ids = []

        for item in search_items:
            snippet = item.get("snippet", {})
            cid = snippet.get("channelId")

            if cid:
                channel_ids.append(cid)

        if not channel_ids:
            break

        channel_details = batch_get_channels(
            api_key=api_key,
            channel_ids=channel_ids,
        )

        filtered = filter_channels(
            api_key=api_key,
            channels_data=channel_details.get("items", []),
            min_video_count=min_video_count,
            recent_days=recent_days,
            min_views=min_views,
        )

        collected.extend(filtered)

        if len(collected) >= max_channels:
            break

        next_token = search_response.get("nextPageToken")

        if not next_token:
            break

    return collected[:max_channels]


def write_channels_to_csv(
    channels: Iterable[ChannelRecord],
    output_file: str,
) -> None:
    """
    Write qualifying channels to CSV.
    """
    with open(
        output_file,
        "w",
        newline="",
        encoding="utf-8",
    ) as csvfile:
        writer = csv.writer(csvfile)

        writer.writerow(
            [
                "channel_id",
                "title",
                "created_at",
                "video_count",
                "view_count",
                "last_upload",
            ]
        )

        for channel in channels:
            writer.writerow(
                [
                    channel.channel_id,
                    channel.title,
                    channel.created_at,
                    channel.video_count,
                    channel.view_count,
                    channel.last_upload,
                ]
            )


def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Cashtube Phase 1 Smart Discovery"
    )

    parser.add_argument(
        "--api-key",
        required=True,
        help="YouTube Data API key",
    )

    parser.add_argument(
        "--published-before",
        default="2016-01-01T00:00:00Z",
        help="Only channels created before this RFC3339 date",
    )

    parser.add_argument(
        "--min-video-count",
        type=int,
        default=50,
        help="Minimum number of uploaded videos",
    )

    parser.add_argument(
        "--recent-days",
        type=int,
        default=180,
        help="Channel must have uploaded within this many days",
    )

    parser.add_argument(
        "--max-channels",
        type=int,
        default=100,
        help="Maximum qualifying channels to collect",
    )

    parser.add_argument(
        "--min-views",
        type=int,
        default=0,
        help="Minimum total channel views",
    )

    parser.add_argument(
        "--keywords",
        default=None,
        help="Optional niche keywords like 'tech review'",
    )

    parser.add_argument(
        "--output",
        default="phase1_results.csv",
        help="CSV output path",
    )

    return parser.parse_args()


def main() -> None:
    """
    Entry point.
    """
    args = parse_args()

    print("Starting Phase 1: Smart Discovery...")

    channels = discover_channels(
        api_key=args.api_key,
        published_before=args.published_before,
        min_video_count=args.min_video_count,
        recent_days=args.recent_days,
        max_channels=args.max_channels,
        min_views=args.min_views,
        keywords=args.keywords,
    )

    print(f"Qualified channels found: {len(channels)}")

    write_channels_to_csv(
        channels=channels,
        output_file=args.output,
    )

    print(f"Results written to: {args.output}")


if __name__ == "__main__":
    main()