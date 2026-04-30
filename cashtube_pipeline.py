"""
cashtube_pipeline.py
====================

Master Orchestrator:
Integrates:
- Phase 1 → Smart Discovery
- Phase 2 → Dead Link Detection

Goal:
1. Discover qualifying legacy YouTube channels
2. Scan those channels for abandoned domains
3. Produce:
   - phase1_results.csv
   - phase2_results.csv

Architecture:
Modular Monolith
- phase1_smart_discovery.py
- phase2_dead_link_detection.py

Example:
python cashtube_pipeline.py \
    --api-key YOUR_API_KEY \
    --published-before 2016-01-01T00:00:00Z \
    --min-video-count 50 \
    --recent-days 180 \
    --max-channels 100 \
    --top-n-videos 20 \
    --channels-output phase1_results.csv \
    --dead-links-output phase2_results.csv
"""

from __future__ import annotations

import argparse
from typing import List

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


# =========================
# HELPERS
# =========================

def channel_id_to_url(channel_id: str) -> str:
    """
    Convert YouTube channel ID to canonical URL.
    """
    return f"https://www.youtube.com/channel/{channel_id}"


# =========================
# PIPELINE
# =========================

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
) -> None:
    """
    Run full Cashtube pipeline.

    Phase 1:
    - Discover channels

    Phase 2:
    - Extract dead links
    """

    # -----------------------------------
    # Phase 1
    # -----------------------------------
    print("=" * 60)
    print("PHASE 1: SMART DISCOVERY")
    print("=" * 60)

    channels: List[ChannelRecord] = discover_channels(
        api_key=api_key,
        published_before=published_before,
        min_video_count=min_video_count,
        recent_days=recent_days,
        max_channels=max_channels,
        min_views=min_views,
        keywords=keywords,
    )

    print(f"Phase 1 Complete → {len(channels)} qualifying channels found.")

    write_channels_to_csv(
        channels=channels,
        output_file=channels_output,
    )

    print(f"Phase 1 CSV saved → {channels_output}")

    # -----------------------------------
    # Phase 2
    # -----------------------------------
    print("\n" + "=" * 60)
    print("PHASE 2: DEAD LINK DETECTION")
    print("=" * 60)

    all_dead_links: List[DeadLinkEntry] = []

    for idx, channel in enumerate(channels, start=1):
        channel_url = channel_id_to_url(channel.channel_id)

        print(
            f"[{idx}/{len(channels)}] Processing: "
            f"{channel.title} ({channel_url})"
        )

        try:
            dead_links = process_channel(
                channel_url=channel_url,
                top_n_videos=top_n_videos,
            )

            all_dead_links.extend(dead_links)

            print(
                f"→ Dead Links Found: {len(dead_links)}"
            )

        except Exception as exc:
            print(
                f"→ ERROR processing {channel_url}: {exc}"
            )

    # -----------------------------------
    # Output
    # -----------------------------------
    write_dead_links_to_csv(
        dead_links=all_dead_links,
        output_path=dead_links_output,
    )

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)

    print(
        f"Total Dead Links Found: {len(all_dead_links)}"
    )

    print(
        f"Phase 2 CSV saved → {dead_links_output}"
    )


# =========================
# CLI
# =========================

def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Cashtube Full Pipeline"
    )

    # ---------------------
    # Phase 1
    # ---------------------
    parser.add_argument(
        "--api-key",
        required=True,
        help="YouTube Data API key",
    )

    parser.add_argument(
        "--published-before",
        default="2016-01-01T00:00:00Z",
        help="Only channels created before this date",
    )

    parser.add_argument(
        "--min-video-count",
        type=int,
        default=50,
        help="Minimum uploaded videos",
    )

    parser.add_argument(
        "--recent-days",
        type=int,
        default=180,
        help="Recent upload threshold",
    )

    parser.add_argument(
        "--max-channels",
        type=int,
        default=100,
        help="Maximum qualifying channels",
    )

    parser.add_argument(
        "--min-views",
        type=int,
        default=0,
        help="Minimum total views",
    )

    parser.add_argument(
        "--keywords",
        default=None,
        help="Optional niche targeting (e.g. tech review)",
    )

    # ---------------------
    # Phase 2
    # ---------------------
    parser.add_argument(
        "--top-n-videos",
        type=int,
        default=20,
        help="Top videos per channel to scan",
    )

    # ---------------------
    # Outputs
    # ---------------------
    parser.add_argument(
        "--channels-output",
        default="phase1_results.csv",
        help="Phase 1 output CSV",
    )

    parser.add_argument(
        "--dead-links-output",
        default="phase2_results.csv",
        help="Phase 2 output CSV",
    )

    return parser.parse_args()


# =========================
# ENTRYPOINT
# =========================

def main() -> None:
    """
    Main execution.
    """
    args = parse_args()

    run_pipeline(
        api_key=args.api_key,
        published_before=args.published_before,
        min_video_count=args.min_video_count,
        recent_days=args.recent_days,
        max_channels=args.max_channels,
        top_n_videos=args.top_n_videos,
        channels_output=args.channels_output,
        dead_links_output=args.dead_links_output,
        min_views=args.min_views,
        keywords=args.keywords,
    )


if __name__ == "__main__":
    main()