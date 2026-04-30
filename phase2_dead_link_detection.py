from __future__ import annotations

import argparse
import concurrent.futures
import csv
import logging
from dataclasses import asdict, dataclass
from typing import List

from cashtube_utils import (
    DnsStatus,
    classify_domain,
    configure_logging,
    extract_urls,
    get_domain,
    is_interesting_domain,
    utc_now_iso,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeadLinkEntry:
    channel_url: str
    video_url: str
    dead_domain: str
    status: str
    first_seen_at: str
    source_description_snippet: str


def _snippet(description: str, url: str, width: int = 180) -> str:
    index = description.find(url)
    if index < 0:
        return description[:width].replace("\n", " ")
    start = max(index - 60, 0)
    end = min(index + len(url) + 60, len(description))
    return description[start:end].replace("\n", " ")


def process_channel(
    channel_url: str,
    top_n_videos: int,
    dry_run: bool = False,
    max_dns_workers: int = 10,
) -> List[DeadLinkEntry]:
    """Scrape top video descriptions and return dead-domain candidates."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("Install yt-dlp before scanning channels: pip install -r requirements.txt") from exc

    discovered: dict[str, dict[str, str]] = {}
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "logger": None,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"{channel_url}/videos?view=0&sort=p", download=False)
            videos = info.get("entries", [])[:top_n_videos]

            for video in videos:
                video_id = video.get("id")
                if not video_id:
                    continue

                video_url = f"https://www.youtube.com/watch?v={video_id}"
                video_info = ydl.extract_info(video_url, download=False)
                description = video_info.get("description", "")

                for url in extract_urls(description):
                    domain = get_domain(url)
                    if is_interesting_domain(domain) and domain not in discovered:
                        discovered[domain] = {
                            "video_url": video_url,
                            "source_description_snippet": _snippet(description, url),
                        }
        except Exception:
            LOGGER.exception("yt-dlp failed while scanning %s", channel_url)

    if dry_run:
        return [
            DeadLinkEntry(
                channel_url=channel_url,
                video_url=meta["video_url"],
                dead_domain=domain,
                status="unchecked",
                first_seen_at=utc_now_iso(),
                source_description_snippet=meta["source_description_snippet"],
            )
            for domain, meta in sorted(discovered.items())
        ]

    checks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_dns_workers) as executor:
        checks = list(executor.map(classify_domain, sorted(discovered)))

    results: list[DeadLinkEntry] = []
    for check in checks:
        if check.status != DnsStatus.NXDOMAIN:
            continue
        meta = discovered[check.domain]
        results.append(
            DeadLinkEntry(
                channel_url=channel_url,
                video_url=meta["video_url"],
                dead_domain=check.domain,
                status=check.status.value,
                first_seen_at=utc_now_iso(),
                source_description_snippet=meta["source_description_snippet"],
            )
        )
    return results


def write_dead_links_to_csv(dead_links: List[DeadLinkEntry], output_path: str) -> None:
    if not dead_links:
        return
    rows = sorted(dead_links, key=lambda item: (item.channel_url, item.dead_domain, item.video_url))
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for link in rows:
            writer.writerow(asdict(link))


def _channel_url(row: dict[str, str]) -> str:
    if row.get("channel_url"):
        return row["channel_url"]
    return f"https://www.youtube.com/channel/{row['channel_id']}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Cashtube Phase 2: Dead Link Detection")
    parser.add_argument("--channels-file", default="phase1_results.csv")
    parser.add_argument("--top-n-videos", type=int, default=20)
    parser.add_argument("--output", default="phase2_results.csv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-dns-workers", type=int, default=10)
    parser.add_argument("--json-logs", action="store_true")
    args = parser.parse_args()
    configure_logging(json_logs=args.json_logs)

    all_dead_links: list[DeadLinkEntry] = []
    with open(args.channels_file, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = _channel_url(row)
            LOGGER.info("Scanning %s", row.get("title") or url)
            links = process_channel(
                channel_url=url,
                top_n_videos=args.top_n_videos,
                dry_run=args.dry_run,
                max_dns_workers=args.max_dns_workers,
            )
            all_dead_links.extend(links)
            LOGGER.info("Found %s candidate links", len(links))

    write_dead_links_to_csv(all_dead_links, args.output)
    LOGGER.info("Wrote %s rows to %s", len(all_dead_links), args.output)


if __name__ == "__main__":
    main()
