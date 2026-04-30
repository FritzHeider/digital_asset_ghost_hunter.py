from __future__ import annotations
import re
import socket
import csv
import yt_dlp
import concurrent.futures
from dataclasses import dataclass, asdict
from typing import List, Set

@dataclass
class DeadLinkEntry:
    channel_url: str
    dead_domain: str

# Standard regex to catch URLs
URL_PATTERN = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')

# Platforms to ignore (unlikely to be expired assets)
IGNORE_LIST = {
    "amazon.com", "amzn.to", "twitter.com", "t.co", "youtube.com", 
    "youtu.be", "facebook.com", "apple.com", "instagram.com", 
    "bit.ly", "tinyurl.com", "google.com"
}

def get_domain(url: str) -> str:
    """Cleans a URL into a base domain."""
    try:
        domain = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except:
        return ""

def is_dead(domain: str) -> bool:
    """Checks for NXDOMAIN via DNS resolution."""
    if not domain or domain in IGNORE_LIST or "." not in domain:
        return False
    try:
        socket.gethostbyname(domain)
        return False
    except socket.gaierror:
        # This domain is a ghost candidate
        return True

def process_channel(channel_url: str, top_n_videos: int) -> List[DeadLinkEntry]:
    """Scrapes top video descriptions for dead links."""
    found_dead = set()
    found_domains = set()
    
    ydl_opts = {
        "quiet": True, 
        "no_warnings": True, 
        "extract_flat": "in_playlist", 
        "skip_download": True, 
        "logger": None
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            # Sort by view count to find highest-traffic legacy videos
            info = ydl.extract_info(f"{channel_url}/videos?view=0&sort=p", download=False)
            videos = info.get("entries", [])[:top_n_videos]
            
            for v in videos:
                v_id = v.get('id')
                if not v_id: continue
                
                v_info = ydl.extract_info(f"https://www.youtube.com/watch?v={v_id}", download=False)
                desc = v_info.get("description", "")
                urls = URL_PATTERN.findall(desc)
                
                for url in urls:
                    d = get_domain(url)
                    if d:
                        found_domains.add(d)
        except Exception:
            pass

    # Resolve domains in parallel to avoid bottlenecks
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(lambda d: (d, is_dead(d)), found_domains)
        for domain, dead_status in results:
            if dead_status:
                found_dead.add(domain)

    return [DeadLinkEntry(channel_url, d) for d in found_dead]

def write_dead_links_to_csv(dead_links: List[DeadLinkEntry], output_path: str):
    if not dead_links:
        return
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["channel_url", "dead_domain"])
        writer.writeheader()
        for link in dead_links:
            writer.writerow(asdict(link))