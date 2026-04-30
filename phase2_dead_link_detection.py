"""
phase2_dead_link_detection.py
=============================

Phase 2 of Cashtube:
Deep Link Extraction + Dead Link Detection

Goal:
For each discovered YouTube channel:
1. Pull channel metadata
2. Pull top N most viewed videos
3. Extract external links from:
   - Channel About page
   - Video descriptions
4. Filter out:
   - Shorteners
   - Major platforms
5. DNS resolve domains
6. Flag dead / abandoned domains

Outputs:
CSV of potentially abandoned domains.

Requirements:
- yt-dlp
- socket
- regex

Example:
python phase2_dead_link_detection.py \
    --channels-file phase1_results.csv \
    --top-n-videos 20 \
    --output phase2_results.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import socket
from dataclasses import dataclass, field
from typing import Iterable, List

import yt_dlp


# =========================
# CONFIG
# =========================

URL_PATTERN = re.compile(r"(https?://[^\s>]+)", re.IGNORECASE)

EXCLUDED_SHORTLINK_DOMAINS = {
    "bit.ly",
    "goo.gl",
    "t.co",
    "tinyurl.com",
    "ow.ly",
    "buff.ly",
    "cutt.ly",
    "is.gd",
    "rebrand.ly",
}

EXCLUDED_MAJOR_DOMAINS = {
    "amazon.com",
    "apple.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "google.com",
    "bestbuy.com",
}


# =========================
# DATA MODEL
# =========================

@dataclass
class DeadLinkEntry:
    channel_url: str
    video_url: str
    dead_link: str
    status: str
    total_channel_views: int = 0
    domain_backlink_count: int = 0
    priority_score: float = field(init=False)

    def __post_init__(self) -> None:
        """
        Priority Score Formula:
        (Total Channel Views / 1M) + Backlink Count
        """
        self.priority_score = (
            self.total_channel_views / 1_000_000
        ) + self.domain_backlink_count


# =========================
# URL EXTRACTION
# =========================

def extract_urls(text: str) -> List[str]:
    """
    Extract all URLs from text.
    """
    return URL_PATTERN.findall(text or "")


def get_domain_from_url(url: str) -> str:
    """
    Normalize URL → root domain
    """
    domain = url.split("://", 1)[-1]
    domain = domain.split("/", 1)[0]
    domain = domain.split(":", 1)[0]
    return domain.lower().replace("www.", "")


def is_excluded_domain(domain: str) -> bool:
    """
    Exclude:
    - Shorteners
    - Major non-target domains
    """
    return (
        domain in EXCLUDED_SHORTLINK_DOMAINS
        or domain in EXCLUDED_MAJOR_DOMAINS
    )


# =========================
# DNS CHECK
# =========================

def is_domain_dead(url: str) -> bool:
    """
    DNS resolution check.

    Returns:
        True → NXDOMAIN / likely abandoned
        False → Resolves
    """
    domain = get_domain_from_url(url)

    if is_excluded_domain(domain):
        return False

    try:
        socket.gethostbyname(domain)
        return False
    except socket.gaierror:
        return True


# =========================
# YT-DLP FETCHERS
# =========================

def fetch_channel_metadata(channel_url: str) -> dict:
    """
    Pull channel metadata using yt-dlp.
    """
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(
            f"{channel_url}/videos?view=0&sort=p",
            download=False,
        )


def fetch_top_video_descriptions(
    channel_metadata: dict,
    top_n_videos: int = 20,
) -> Iterable[dict]:
    """
    Pull full metadata for top videos.
    """
    entries = channel_metadata.get("entries", [])

    for video in entries[:top_n_videos]:
        video_url = video.get("url")

        if not video_url:
            continue

        try:
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "skip_download": True,
                }
            ) as ydl:
                full_info = ydl.extract_info(
                    video_url,
                    download=False,
                )

            yield {
                "video_url": full_info.get(
                    "webpage_url",
                    video_url,
                ),
                "description": full_info.get(
                    "description",
                    "",
                ),
            }

        except Exception:
            continue


# =========================
# CORE PROCESSOR
# =========================

def process_channel(
    channel_url: str,
    top_n_videos: int = 20,
) -> List[DeadLinkEntry]:
    """
    Process one channel:
    - About page
    - Top videos
    """
    dead_links: List[DeadLinkEntry] = []

    channel_metadata = fetch_channel_metadata(channel_url)

    total_views = channel_metadata.get("view_count", 0)

    # -------------------------
    # Channel Description
    # -------------------------
    channel_desc = channel_metadata.get("description", "")

    for url in extract_urls(channel_desc):
        if is_domain_dead(url):
            dead_links.append(
                DeadLinkEntry(
                    channel_url=channel_url,
                    video_url=channel_url,
                    dead_link=url,
                    status="Available/DNS_Fail",
                    total_channel_views=total_views,
                )
            )

    # -------------------------
    # Top Videos
    # -------------------------
    for video in fetch_top_video_descriptions(
        channel_metadata,
        top_n_videos,
    ):
        for url in extract_urls(video["description"]):
            if is_domain_dead(url):
                dead_links.append(
                    DeadLinkEntry(
                        channel_url=channel_url,
                        video_url=video["video_url"],
                        dead_link=url,
                        status="Available/DNS_Fail",
                        total_channel_views=total_views,
                    )
                )

    return dead_links


# =========================
# CSV HELPERS
# =========================

def read_channel_urls_from_csv(csv_path: str) -> List[str]:
    """
    Read Phase 1 CSV
    """
    urls: List[str] = []

    with open(
        csv_path,
        newline="",
        encoding="utf-8",
    ) as csvfile:
        reader = csv.DictReader(csvfile)

        for row in reader:
            channel_id = row.get("channel_id", "").strip()

            if channel_id:
                urls.append(
                    f"https://www.youtube.com/channel/{channel_id}"
                )

    return urls


def write_dead_links_to_csv(
    dead_links: Iterable[DeadLinkEntry],
    output_path: str,
) -> None:
    """
    Output dead links to CSV.
    """
    fieldnames = [
        "channel_url",
        "video_url",
        "dead_link",
        "status",
        "total_channel_views",
        "domain_backlink_count",
        "priority_score",
    ]

    with open(
        output_path,
        "w",
        newline="",
        encoding="utf-8",
    ) as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for entry in dead_links:
            writer.writerow(
                {
                    "channel_url": entry.channel_url,
                    "video_url": entry.video_url,
                    "dead_link": entry.dead_link,
                    "status": entry.status,
                    "total_channel_views": entry.total_channel_views,
                    "domain_backlink_count": entry.domain_backlink_count,
                    "priority_score": entry.priority_score,
                }
            )


# =========================
# CLI
# =========================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cashtube Phase 2 Dead Link Detection"
    )

    parser.add_argument(
        "--channel-url",
        help="Single YouTube channel URL",
    )

    parser.add_argument(
        "--channels-file",
        help="CSV from Phase 1",
    )

    parser.add_argument(
        "--top-n-videos",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--output",
        default="phase2_results.csv",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    channels: List[str] = []

    if args.channel_url:
        channels.append(args.channel_url)

    if args.channels_file:
        channels.extend(
            read_channel_urls_from_csv(
                args.channels_file
            )
        )

    if not channels:
        raise ValueError(
            "Provide either --channel-url or --channels-file"
        )

    all_dead_links: List[DeadLinkEntry] = []

    for channel in channels:
        print(f"Processing channel: {channel}")

        try:
            dead_links = process_channel(
                channel,
                top_n_videos=args.top_n_videos,
            )

            all_dead_links.extend(dead_links)

            print(
                f"Found {len(dead_links)} dead links"
            )

        except Exception as exc:
            print(
                f"Error processing {channel}: {exc}"
            )

    write_dead_links_to_csv(
        all_dead_links,
        args.output,
    )

    print(
        f"Completed. Total dead links: {len(all_dead_links)}"
    )
    print(f"Results written to: {args.output}")


if __name__ == "__main__":
    main()