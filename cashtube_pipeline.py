from __future__ import annotations
import argparse
import os
from typing import List
from dotenv import load_dotenv

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
) -> None:
    print("\n" + "=" * 60)
    print("PHASE 1: SMART DISCOVERY")
    print("=" * 60)

    channels = discover_channels(
        api_key=api_key,
        published_before=published_before,
        min_video_count=min_video_count,
        recent_days=recent_days,
        max_channels=max_channels,
        min_views=min_views,
        keywords=keywords,
    )

    print(f"[*] Phase 1 Complete → {len(channels)} qualifying channels found.")
    write_channels_to_csv(channels=channels, output_file=channels_output)

    print("\n" + "=" * 60)
    print("PHASE 2: DEAD LINK DETECTION")
    print("=" * 60)

    all_dead_links: List[DeadLinkEntry] = []
    for idx, channel in enumerate(channels, start=1):
        url = channel_id_to_url(channel.channel_id)
        print(f"[{idx}/{len(channels)}] Scanning: {channel.title}")
        try:
            links = process_channel(channel_url=url, top_n_videos=top_n_videos)
            all_dead_links.extend(links)
            print(f"   → Dead Links Found: {len(links)}")
        except Exception as e:
            print(f"   → ERROR: {e}")

    write_dead_links_to_csv(dead_links=all_dead_links, output_path=dead_links_output)
    
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print(f"Total Dead Links Found: {len(all_dead_links)}")
    print(f"Results saved to: {dead_links_output}")
    print("=" * 60)

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
    args = parser.parse_args()

    api_key = args.api_key or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("[-] Error: Provide API Key via --api-key or .env YOUTUBE_API_KEY")
        return

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
    )

if __name__ == "__main__":
    main()