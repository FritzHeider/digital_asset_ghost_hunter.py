# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Cashtube is a two-phase YouTube intelligence pipeline for finding "digital ghost assets" — expired domains that still receive evergreen traffic from old YouTube content. Phase 1 discovers legacy channels via the YouTube Data API v3; Phase 2 scans those channels' video descriptions for dead external domains using `yt-dlp` + DNS resolution.

## Environment

```bash
# Activate the virtual environment
source venv/bin/activate

# Required env var (or pass --api-key on CLI)
# YOUTUBE_API_KEY=...
# Optional: USPTO_API_KEY=... (for trademark checks)
cp .env .env.local  # edit as needed
```

Python 3.11+ required. `pyproject.toml` defines all deps; install with:

```bash
pip install -e ".[dev]"
```

## Running the Pipeline

```bash
# Full pipeline (Phase 1 → Phase 2)
cashtube pipeline --api-key YOUR_KEY --published-before 2016-01-01T00:00:00Z --min-video-count 50 --max-channels 100

# Phase 1 only
cashtube phase1 --api-key YOUR_KEY --published-before 2016-01-01T00:00:00Z --max-channels 100

# Phase 2 only
cashtube phase2 --channels-file phase1_results.csv --top-n-videos 20

# Ghost hunter (niche-focused variant)
cashtube ghost --api-key YOUR_KEY --min-views 2000000

# Dry run (no DNS checks, just candidate extraction)
cashtube pipeline --api-key YOUR_KEY --dry-run --json-logs
```

## Linting and Type Checking

```bash
ruff check .
black --check .
mypy .
```

## Tests

```bash
# All tests
python -m pytest tests/

# Single test file
python -m pytest tests/test_cashtube_utils.py

# Single test case
python -m pytest tests/test_phase1_phase2.py::Phase2DryRunTest::test_process_channel_dry_run_schema
```

Tests use `unittest` with `mock.patch`; no live API calls are made in the test suite.

## Code Architecture

### Module Map

| File | Role |
|------|------|
| `cashtube/cli.py` | Entry point; dispatches subcommands to module `main()` functions |
| `cashtube_utils.py` | All shared utilities: HTTP session, DNS classification, SQLite cache, URL extraction, domain filtering, enrichment helpers |
| `phase1_smart_discovery.py` | YouTube API queries → `ChannelRecord` dataclasses; writes `phase1_results.csv` |
| `phase2_dead_link_detection.py` | yt-dlp scraping → `DeadLinkEntry` dataclasses; writes `phase2_results.csv` |
| `cashtube_pipeline.py` | Wires Phase 1 → Phase 2 with checkpointing, dedup, and optional enrichment |
| `digital_asset_ghost_hunter.py` | Standalone niche-focused variant (tech/startup graveyard) |
| `trademarked.py` | Trademark risk utilities |

### Key Abstractions in `cashtube_utils.py`

- **`SQLiteCache`** — namespace-keyed key-value cache with TTL, backed by `.cashtube_cache.sqlite3`. Controls which channel/domain lookups get re-used across runs.
- **`DomainCheck` / `classify_domain()`** — wraps `socket.getaddrinfo` and maps errno codes to `DnsStatus` enum (`LIVE`, `NXDOMAIN`, `SERVFAIL`, `TIMEOUT`, `TEMPORARY_ERROR`). Only `NXDOMAIN` is treated as a dead domain candidate.
- **`make_session()`** — returns a `requests.Session` with exponential-backoff retries on 429/5xx.
- **`RateLimiter`** — token-bucket style rate limiter used to space out YouTube API calls.

### Phase 1 Flow

`discover_channels()` → keyword search via YouTube Search API → batch `channels.list` to get stats → filter by `min_video_count`, `min_views`, `recent_days` → optional checkpoint file to skip already-seen channels.

YouTube 403 errors for quota/key problems are fatal; 403s on individual recent-upload checks (e.g. restricted channels) are non-fatal (channel stays eligible).

### Phase 2 Flow

`process_channel()` → yt-dlp fetches top-N video descriptions → regex extracts URLs → `get_domain()` normalizes + strips `www.` (uses `tldextract` when available) → `is_interesting_domain()` filters against `IGNORE_DOMAINS` and `ALLOWED_TLDS` → `classify_domain()` DNS check → optional enrichment (`check_http_domain`, `rdap_lookup`, `wayback_lookup`, `trademark_risk`).

Pipeline deduplicates `(channel_id, dead_domain)` pairs and writes a scan checkpoint after each channel so interrupted runs resume cleanly.

### Config Files

Keywords, ignore lists, and allowed TLDs can be loaded from JSON or YAML via `--config` / `--keywords-file`. The `load_config()` helper in `cashtube_utils.py` handles both formats.

## Outputs

- `phase1_results.csv` — discovered channels
- `phase2_results.csv` — dead domain candidates
- Optional `--json-output` and `--report-output` for JSON + Markdown summary
- `.cashtube_cache.sqlite3` — SQLite DNS/HTTP cache
- `.cashtube_channels_seen.json` / `.cashtube_phase2_checkpoint.json` — run checkpoints (gitignored)
