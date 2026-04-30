from __future__ import annotations
import csv
import requests
from dataclasses import dataclass, asdict
from typing import List, Dict

@dataclass
class ChannelRecord:
    channel_id: str
    title: str
    view_count: int
    video_count: int
    published_at: str

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
    Improved Discovery: Search for VIDEOS created before the date, 
    then identify their parent channels.
    """
    base_url = "https://www.googleapis.com/youtube/v3"
    query = keywords or "pokemon cards"
    
    # We search for videos because the API indexes them better than channel objects
    search_params = {
        "part": "snippet",
        "type": "video",
        "publishedBefore": published_before,
        "q": query,
        "maxResults": 50,
        "key": api_key,
    }

    try:
        print(f"[*] Searching for legacy videos matching: '{query}'")
        response = requests.get(f"{base_url}/search", params=search_params, timeout=15)
        response.raise_for_status()
        items = response.json().get("items", [])
        
        # Extract unique channel IDs from the video results
        unique_cids = list(set([item["snippet"]["channelId"] for item in items]))
        
        if not unique_cids:
            print("[!] No channels found in search results.")
            return []

        print(f"[*] Found {len(unique_cids)} potential channels. Fetching statistics...")

        # Batch detail lookup for statistics
        detail_params = {
            "part": "snippet,statistics",
            "id": ",".join(unique_cids[:50]),
            "key": api_key
        }
        detail_res = requests.get(f"{base_url}/channels", params=detail_params, timeout=15)
        detail_res.raise_for_status()
        
        results = []
        for item in detail_res.json().get("items", []):
            stats = item["statistics"]
            v_count = int(stats.get("videoCount", 0))
            views = int(stats.get("viewCount", 0))
            
            # Apply your specific filters
            if v_count >= min_video_count and views >= min_views:
                results.append(ChannelRecord(
                    channel_id=item["id"],
                    title=item["snippet"]["title"],
                    view_count=views,
                    video_count=v_count,
                    published_at=item["snippet"]["publishedAt"]
                ))
        
        return results[:max_channels]
    except Exception as e:
        print(f"   [!] Discovery Error: {e}")
        return []

def write_channels_to_csv(channels: List[ChannelRecord], output_file: str):
    if not channels:
        return
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(channels[0]).keys()))
        writer.writeheader()
        for c in channels:
            writer.writerow(asdict(c))