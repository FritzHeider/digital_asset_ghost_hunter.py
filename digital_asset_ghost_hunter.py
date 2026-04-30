from __future__ import annotations

import argparse
import csv
import re
import socket
import os
import concurrent.futures
from dataclasses import dataclass
from typing import Dict, List, Set

import requests
import yt_dlp
from dotenv import load_dotenv

# =========================
# CONFIG & PATTERNS
# =========================

YOUTUBE_API_BASE_URL = "https://www.googleapis.com/youtube/v3"

TECH_KEYWORDS = [
    "tech review",
    "unboxing",
    "hands-on",
]

MAJOR_DOMAINS = {
    "amazon.com", "amzn.to", "apple.com", "twitter.com", "t.co", 
    "facebook.com", "fb.me", "instagram.com", "youtube.com", "youtu.be",
    "google.com", "bestbuy.com", "walmart.com", "ebay.com", "bit.ly", 
    "goo.gl", "tinyurl.com", "microsoft.com", "github.com", "medium.com",
    "linkedin.com", "twitch.tv", "discord.gg"
}

ALLOWED_TLDS = {".com", ".io", ".net"}

URL_PATTERN = re.compile(
    r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
)

@dataclass
class Channel:
    channel_id: str
    title: str
    view_count: int

# =========================
# DOMAIN UTILITIES
# =========================

def get_domain(url: str) -> str:
    """Extracts and normalizes the root domain from a URL."""
    try:
        domain = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        domain = domain.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except (IndexError, AttributeError):
        return ""

def is_interesting_domain(domain: str) -> bool:
    """Filters out big tech and keeps only targeted TLDs."""
    if not domain or domain in MAJOR_DOMAINS:
        return False
    return any(domain.endswith(tld) for tld in ALLOWED_TLDS)

def domain_is_dead(domain: str) -> bool:
    """Returns True if the domain fails to resolve (NXDOMAIN)."""
    try:
        socket.gethostbyname(domain)
        return False
    except socket.gaierror:
        return True

# =========================
# PHASE 2: VIDEO SCANNING
# =========================

def extract_dead_links(channel_id: str, top_n_videos: int) -> List[str]:
    """Scrapes descriptions and checks for dead domains."""
    dead_candidates: Set[str] = set()
    all_extracted_domains: Set[str] = set()
    
    channel_url = f"https://www.youtube.com/channel/{channel_id}/videos?view=0&sort=p"
    
    # Updated options to silence warnings and avoid JS challenge solving
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist", # Don't dive into formats, just get metadata
        "skip_download": True,
        "logger": None
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            # Step 1: Get the list of top videos
            channel_info = ydl.extract_info(channel_url, download=False)
            video_entries = channel_info.get("entries", [])[:top_n_videos]
            
            for entry in video_entries:
                v_id = entry.get('id')
                if not v_id: continue
                
                # Step 2: Get specific video metadata (description)
                # We use a second call to ensure we get the full description
                v_url = f"https://www.youtube.com/watch?v={v_id}"
                v_info = ydl.extract_info(v_url, download=False)
                description = v_info.get("description", "")
                
                urls = URL_PATTERN.findall(description)
                for url in urls:
                    domain = get_domain(url)
                    if is_interesting_domain(domain):
                        all_extracted_domains.add(domain)
        except Exception as e:
            # Silently catch to keep the loop moving
            pass

    # Parallel DNS resolution
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        future_to_domain = {executor.submit(domain_is_dead, d): d for d in all_extracted_domains}
        for future in concurrent.futures.as_completed(future_to_domain):
            domain = future_to_domain[future]
            try:
                if future.result():
                    dead_candidates.add(domain)
            except Exception:
                continue

    return list(dead_candidates)

# =========================
# PHASE 1: CHANNEL DISCOVERY
# =========================

def search_channels(api_key: str, published_before: str, min_views: int) -> List[Channel]:
    discovered: Dict[str, Channel] = {}

    for keyword in TECH_KEYWORDS:
        print(f"[*] Querying YouTube API for: {keyword}")
        params = {
            "part": "snippet",
            "type": "channel",
            "publishedBefore": published_before,
            "order": "viewCount",
            "q": keyword,
            "maxResults": 50,
            "key": api_key,
        }
        
        try:
            r = requests.get(f"{YOUTUBE_API_BASE_URL}/search", params=params, timeout=15)
            r.raise_for_status()
            items = r.json().get("items", [])
            
            cids = [i['snippet']['channelId'] for i in items if i.get('snippet')]
            
            if cids:
                d_r = requests.get(f"{YOUTUBE_API_BASE_URL}/channels", params={
                    "part": "snippet,statistics",
                    "id": ",".join(cids),
                    "key": api_key
                })
                d_r.raise_for_status()
                for c in d_r.json().get("items", []):
                    views = int(c['statistics'].get('viewCount', 0))
                    if views >= min_views:
                        discovered[c['id']] = Channel(
                            channel_id=c['id'],
                            title=c['snippet']['title'],
                            view_count=views
                        )
        except Exception as e:
            print(f"   [!] YouTube API Error: {e}")

    return list(discovered.values())

# =========================
# MAIN ENTRY
# =========================

def main():
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="Digital Asset Ghost Hunter")
    parser.add_argument("--api-key", help="YouTube API Key")
    parser.add_argument("--published-before", default="2016-01-01T00:00:00Z")
    parser.add_argument("--min-views", type=int, default=2000000)
    parser.add_argument("--top-n-videos", type=int, default=20)
    parser.add_argument("--output", default="ghost_results.csv")
    args = parser.parse_args()

    api_key = args.api_key or os.getenv("YOUTUBE_API_KEY")

    if not api_key:
        print("[-] Error: YouTube API Key not found.")
        return

    print("\n" + "="*40)
    print(" DIGITAL ASSET GHOST HUNTER ")
    print("="*40)

    channels = search_channels(api_key, args.published_before, args.min_views)
    print(f"[*] Found {len(channels)} candidate channels.\n")

    all_results = []
    for idx, channel in enumerate(channels, 1):
        print(f"[{idx}/{len(channels)}] Scanning: {channel.title}...")
        dead_domains = extract_dead_links(channel.channel_id, args.top_n_videos)
        
        for domain in dead_domains:
            all_results.append({
                "channel_id": channel.channel_id,
                "channel_title": channel.title,
                "view_count": channel.view_count,
                "dead_domain": domain
            })
            print(f"   [+] Ghost Domain Found: {domain}")

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["channel_id", "channel_title", "view_count", "dead_domain"])
        writer.writeheader()
        writer.writerows(all_results)

    print(f"\n[*] Hunt Complete. Found {len(all_results)} total leads.")
    print(f"[*] Results saved to: {args.output}")

if __name__ == "__main__":
    main()