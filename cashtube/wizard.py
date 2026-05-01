"""Interactive wizard for the Cashtube pipeline."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:
        return None

LOGGER = logging.getLogger(__name__)

_DIVIDER = "─" * 52


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return value or default


def _ask_int(prompt: str, default: int) -> int:
    while True:
        raw = _ask(prompt, str(default))
        try:
            return int(raw)
        except ValueError:
            print(f"    Please enter a whole number.")


def _ask_bool(prompt: str, default: bool = False) -> bool:
    yn = "Y/n" if default else "y/N"
    answer = _ask(f"{prompt} [{yn}]").lower()
    if not answer:
        return default
    return answer.startswith("y")


def _choose(options: list[tuple[str, str]], default: int = 1) -> str:
    for i, (_, label) in enumerate(options, 1):
        marker = " (default)" if i == default else ""
        print(f"    {i}) {label}{marker}")
    while True:
        raw = _ask("Choice", str(default))
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        print(f"    Enter 1–{len(options)}")


def _collect_keywords() -> list[str]:
    print("  Enter search terms one per line. Blank line when done.")
    keywords: list[str] = []
    while True:
        kw = _ask(f"  keyword {len(keywords) + 1}").strip()
        if not kw:
            if keywords:
                break
            print("    Enter at least one keyword.")
        else:
            keywords.append(kw)
    return keywords


def _header(text: str) -> None:
    print(f"\n{_DIVIDER}")
    print(f"  {text}")
    print(_DIVIDER)


def run() -> None:
    if not sys.stdin.isatty():
        sys.exit("Wizard requires an interactive terminal. Use 'cashtube --help' instead.")

    load_dotenv()

    _header("Cashtube Wizard")
    print("  Press Enter to accept defaults shown in [brackets].\n")

    # ── API key ──────────────────────────────────────────────────────────────
    env_key = os.getenv("YOUTUBE_API_KEY", "")
    if env_key:
        print(f"  YouTube API key loaded from environment.")
        api_key = env_key
    else:
        print("  YouTube API key")
        api_key = ""
        while not api_key:
            api_key = _ask("API key")
            if not api_key:
                print("    API key is required.")

    # ── Mode ─────────────────────────────────────────────────────────────────
    print("\n  What do you want to run?")
    mode = _choose([
        ("pipeline", "Full pipeline  — Phase 1 discovery → Phase 2 dead-link scan"),
        ("phase1",   "Phase 1 only   — find legacy channels, write phase1_results.csv"),
        ("phase2",   "Phase 2 only   — scan an existing channels CSV for dead links"),
        ("ghost",    "Ghost hunter   — high-view tech/startup niche (preset keywords)"),
    ])

    # ── Channels file (phase2 only) ───────────────────────────────────────────
    channels_file = "phase1_results.csv"
    if mode == "phase2":
        print("\n  Input channels CSV")
        channels_file = _ask("Channels file", "phase1_results.csv")
        if not Path(channels_file).exists():
            print(f"    Warning: {channels_file!r} not found — make sure it exists before running.")

    # ── Keywords ─────────────────────────────────────────────────────────────
    keywords: list[str] = []
    if mode == "ghost":
        from digital_asset_ghost_hunter import GHOST_KEYWORDS
        keywords = list(GHOST_KEYWORDS)
        print(f"\n  Ghost hunter uses preset keywords:")
        for kw in keywords:
            print(f"    • {kw}")
        if _ask_bool("  Customise keywords?", False):
            keywords = _collect_keywords()
    elif mode != "phase2":
        print("\n  Search keywords")
        keywords = _collect_keywords()

    # ── Date range ────────────────────────────────────────────────────────────
    published_before = "2016-01-01T00:00:00Z"
    if mode != "phase2":
        print("\n  Date filter  (find channels whose videos were published before this date)")
        published_before = _ask("Published before", "2016-01-01T00:00:00Z")

    # ── Channel filters ───────────────────────────────────────────────────────
    min_video_count = 50
    min_views = 0
    max_channels = 100
    recent_days = 180
    if mode not in ("phase2",):
        print("\n  Channel quality filters")
        min_video_count = _ask_int("Min video count", 50)
        min_views       = _ask_int("Min total views (0 = no minimum)", 0)
        max_channels    = _ask_int("Max channels to qualify", 100)
        recent_days     = _ask_int("Ignore channels active within N days (0 = keep all)", 180)

    # ── Scan settings ─────────────────────────────────────────────────────────
    top_n_videos = 20
    enrich_http  = False
    check_rdap   = False
    check_wayback = False
    max_dns_workers = 10
    if mode != "phase1":
        print("\n  Scan settings")
        top_n_videos    = _ask_int("Videos to scan per channel", 20)
        max_dns_workers = _ask_int("Parallel DNS workers", 10)
        print("  Enrichment  (each flag costs extra HTTP calls per dead domain)")
        enrich_http   = _ask_bool("  HTTP reachability / parking check?", False)
        check_rdap    = _ask_bool("  RDAP domain availability check?", False)
        check_wayback = _ask_bool("  Wayback Machine snapshot check?", False)

    # ── Misc ──────────────────────────────────────────────────────────────────
    print("\n  Run options")
    dry_run = _ask_bool("Dry run (skip DNS — just list candidate domains)?", False)
    log_level = _ask("Log level", "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        log_level = "INFO"

    # ── Summary ───────────────────────────────────────────────────────────────
    _header("Summary")
    print(f"  Mode            {mode}")
    if mode != "phase2":
        print(f"  Keywords        {', '.join(keywords)}")
        print(f"  Published before {published_before}")
        print(f"  Max channels    {max_channels}  (min {min_video_count} vids, min {min_views:,} views)")
        print(f"  Recent days     {recent_days}")
    else:
        print(f"  Channels file   {channels_file}")
    if mode != "phase1":
        enrichment = [x for x, flag in [("HTTP", enrich_http), ("RDAP", check_rdap), ("Wayback", check_wayback)] if flag]
        print(f"  Videos/channel  {top_n_videos}  DNS workers: {max_dns_workers}")
        print(f"  Enrichment      {', '.join(enrichment) or 'none'}")
    if dry_run:
        print("  DRY RUN         yes — no DNS checks")
    print()

    if not _ask_bool("Run now?", True):
        print("\n  Aborted.")
        return

    # ── Show equivalent command ───────────────────────────────────────────────
    kw_file = ".wizard_keywords.json"
    if keywords and mode != "phase2":
        Path(kw_file).write_text(json.dumps({"keywords": keywords}, indent=2) + "\n")

    cmd = _build_command(
        mode, kw_file if (keywords and mode != "phase2") else None, api_key,
        published_before, min_video_count, min_views, max_channels, recent_days,
        top_n_videos, max_dns_workers, enrich_http, check_rdap, check_wayback,
        dry_run, log_level, channels_file,
    )
    print(f"\n  Equivalent command:\n    {cmd}\n")

    # ── Execute ───────────────────────────────────────────────────────────────
    from cashtube_utils import configure_logging, configure_dns_timeout
    configure_logging(level=log_level)
    configure_dns_timeout()

    start = time.time()

    if mode == "pipeline":
        _run_pipeline(
            api_key, keywords, published_before, min_video_count, min_views,
            max_channels, recent_days, top_n_videos, max_dns_workers,
            enrich_http, check_rdap, check_wayback, dry_run,
        )
    elif mode == "phase1":
        _run_phase1(
            api_key, keywords, published_before, min_video_count, min_views,
            max_channels, recent_days,
        )
    elif mode == "phase2":
        _run_phase2(
            channels_file, top_n_videos, max_dns_workers,
            enrich_http, check_rdap, check_wayback, dry_run,
        )
    elif mode == "ghost":
        _run_ghost(
            api_key, keywords, published_before, min_video_count, min_views,
            max_channels, recent_days, top_n_videos, dry_run,
        )

    print(f"\n  Done in {time.time() - start:.1f}s")


# ── Runners ───────────────────────────────────────────────────────────────────

def _run_pipeline(
    api_key: str, keywords: list[str], published_before: str,
    min_video_count: int, min_views: int, max_channels: int, recent_days: int,
    top_n_videos: int, max_dns_workers: int,
    enrich_http: bool, check_rdap: bool, check_wayback: bool, dry_run: bool,
) -> None:
    from cashtube_pipeline import run_pipeline
    run_pipeline(
        api_key=api_key,
        published_before=published_before,
        min_video_count=min_video_count,
        recent_days=recent_days,
        max_channels=max_channels,
        top_n_videos=top_n_videos,
        channels_output="phase1_results.csv",
        dead_links_output="phase2_results.csv",
        min_views=min_views,
        keyword_list=keywords,
        dry_run=dry_run,
        enrich_http=enrich_http,
        check_rdap=check_rdap,
        check_wayback=check_wayback,
    )


def _run_phase1(
    api_key: str, keywords: list[str], published_before: str,
    min_video_count: int, min_views: int, max_channels: int, recent_days: int,
) -> None:
    from phase1_smart_discovery import discover_channels, write_channels_to_csv
    channels = discover_channels(
        api_key=api_key,
        published_before=published_before,
        min_video_count=min_video_count,
        recent_days=recent_days,
        max_channels=max_channels,
        min_views=min_views,
        keyword_list=keywords,
    )
    LOGGER.info("Found %s qualifying channels", len(channels))
    write_channels_to_csv(channels, "phase1_results.csv")
    LOGGER.info("Wrote phase1_results.csv")


def _run_phase2(
    channels_file: str, top_n_videos: int, max_dns_workers: int,
    enrich_http: bool, check_rdap: bool, check_wayback: bool, dry_run: bool,
) -> None:
    import csv
    import concurrent.futures
    from dataclasses import asdict
    from cashtube_utils import SQLiteCache, load_checkpoint, save_checkpoint, make_session
    from phase2_dead_link_detection import (
        DeadLinkEntry, _channel_url, process_channel, write_dead_links_to_csv,
    )

    cache = SQLiteCache(".cashtube_cache.sqlite3")
    processed = load_checkpoint(".cashtube_phase2_checkpoint.json")
    enrich_session = make_session() if (enrich_http or check_rdap or check_wayback) else None

    with open(channels_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    pending = [(i, r) for i, r in enumerate(rows, 1) if _channel_url(r) not in processed]
    total = len(rows)
    all_links: list[DeadLinkEntry] = []
    seen: set[tuple[str, str]] = set()

    def scan(idx_row: tuple[int, dict]) -> tuple[str, list[DeadLinkEntry]]:
        idx, row = idx_row
        url = _channel_url(row)
        LOGGER.info("[%s/%s] Scanning %s", idx, total, row.get("title") or url)
        try:
            links = process_channel(
                channel_url=url,
                top_n_videos=top_n_videos,
                dry_run=dry_run,
                max_dns_workers=max_dns_workers,
                cache=cache,
                enrich_http=enrich_http,
                check_rdap=check_rdap,
                check_wayback=check_wayback,
                session=enrich_session,
            )
        except Exception:
            LOGGER.exception("Scan failed for %s", row.get("title") or url)
            links = []
        return url, links

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            for url, links in executor.map(scan, pending):
                for link in links:
                    pair = (url, link.dead_domain)
                    if pair not in seen:
                        seen.add(pair)
                        all_links.append(link)
                processed.add(url)
                save_checkpoint(".cashtube_phase2_checkpoint.json", processed)
    finally:
        cache.close()

    write_dead_links_to_csv(all_links, "phase2_results.csv")
    LOGGER.info("Wrote %s rows to phase2_results.csv", len(all_links))


def _run_ghost(
    api_key: str, keywords: list[str], published_before: str,
    min_video_count: int, min_views: int, max_channels: int, recent_days: int,
    top_n_videos: int, dry_run: bool,
) -> None:
    import csv
    from phase1_smart_discovery import discover_channels
    from phase2_dead_link_detection import DeadLinkEntry, process_channel

    channels = discover_channels(
        api_key=api_key,
        published_before=published_before,
        min_video_count=min_video_count,
        recent_days=recent_days,
        max_channels=max_channels,
        min_views=min_views,
        keyword_list=keywords,
    )
    LOGGER.info("Found %s candidate channels", len(channels))

    all_results: list[dict] = []
    for idx, channel in enumerate(channels, 1):
        LOGGER.info("[%s/%s] Scanning %s", idx, len(channels), channel.title)
        url = f"https://www.youtube.com/channel/{channel.channel_id}"
        entries: list[DeadLinkEntry] = process_channel(
            channel_url=url, top_n_videos=top_n_videos, dry_run=dry_run,
        )
        for entry in entries:
            all_results.append({
                "channel_id": channel.channel_id,
                "channel_title": channel.title,
                "view_count": channel.view_count,
                "video_url": entry.video_url,
                "dead_domain": entry.dead_domain,
                "status": entry.status,
                "priority_score": entry.priority_score,
                "first_seen_at": entry.first_seen_at,
                "source_description_snippet": entry.source_description_snippet,
            })

    all_results.sort(key=lambda r: (-r["priority_score"], r["channel_id"], r["dead_domain"]))
    fieldnames = ["channel_id", "channel_title", "view_count", "video_url",
                  "dead_domain", "status", "priority_score", "first_seen_at",
                  "source_description_snippet"]
    with open("ghost_results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_results)
    LOGGER.info("Wrote %s leads to ghost_results.csv", len(all_results))


# ── Command builder (for display only) ───────────────────────────────────────

def _build_command(
    mode: str, kw_file: str | None, api_key: str,
    published_before: str, min_video_count: int, min_views: int,
    max_channels: int, recent_days: int, top_n_videos: int, max_dns_workers: int,
    enrich_http: bool, check_rdap: bool, check_wayback: bool,
    dry_run: bool, log_level: str, channels_file: str,
) -> str:
    parts = [f"cashtube {mode}"]
    if mode != "phase2":
        parts += [f"--api-key $YOUTUBE_API_KEY"]
    if kw_file:
        parts += [f"--keywords-file {kw_file}"]
    if mode != "phase2":
        parts += [f"--published-before {published_before}"]
    if mode not in ("phase2", "ghost"):
        parts += [f"--min-video-count {min_video_count}",
                  f"--min-views {min_views}",
                  f"--max-channels {max_channels}",
                  f"--recent-days {recent_days}"]
    elif mode == "ghost":
        parts += [f"--min-video-count {min_video_count}",
                  f"--min-views {min_views}",
                  f"--max-channels {max_channels}",
                  f"--recent-days {recent_days}"]
    if mode == "phase2":
        parts += [f"--channels-file {channels_file}"]
    if mode != "phase1":
        parts += [f"--top-n-videos {top_n_videos}"]
    if mode in ("pipeline", "phase2"):
        parts += [f"--max-dns-workers {max_dns_workers}"]
    if enrich_http:
        parts.append("--enrich-http")
    if check_rdap:
        parts.append("--check-rdap")
    if check_wayback:
        parts.append("--check-wayback")
    if dry_run:
        parts.append("--dry-run")
    if log_level != "INFO":
        parts += [f"--log-level {log_level}"]
    return " ".join(parts)
