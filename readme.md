# Cashtube — Digital Ghost Asset Hunter

Find expired domains that still receive passive SEO traffic from old YouTube content.

---

## Concept

When a YouTube creator links to an external site in a video description, that link persists forever — even after the site goes offline. Old videos about failed startups, discontinued SaaS products, expired affiliate microsites, and defunct sponsors continue ranking in search results and sending clicks to dead URLs.

Cashtube finds those links.

**Phase 1** scans YouTube for legacy channels: channels created before a cutoff date (default: 2016), with a high video count, high view count, and no recent uploads — signs the creator has moved on.

**Phase 2** scrapes the top videos from each channel, extracts every external URL from video descriptions, resolves them via DNS, and flags the ones that return `NXDOMAIN` — meaning the domain is gone.

Optional enrichment checks whether the domain is HTTP-reachable, parked-for-sale, listed in the Wayback Machine, or still registered. A priority score synthesises these signals so you know which leads to act on first.

---

## Requirements

- Python 3.11+
- A Google Cloud project with **YouTube Data API v3** enabled
- Internet access for DNS resolution and optional enrichment

---

## Installation

```bash
git clone https://github.com/FritzHeider/cashtube.git
cd cashtube

python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -e ".[dev]"
```

---

## Google Cloud Setup

You need a YouTube Data API v3 key. This takes about 5 minutes.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and sign in.
2. Click **Select a project → New Project**. Give it any name (e.g. `cashtube`).
3. In the left sidebar go to **APIs & Services → Library**.
4. Search for **YouTube Data API v3** and click **Enable**.
5. Go to **APIs & Services → Credentials → Create Credentials → API key**.
6. Copy the key. Optionally click **Restrict Key → API restrictions → YouTube Data API v3** to limit its scope.

**Daily quota:** 10,000 units. Each search page costs 100 units; each channel stats lookup costs ~1 unit; playlist checks cost 1 unit. See [Quota Management](#quota-management) below.

---

## Environment

Create a `.env` file in the project root:

```bash
YOUTUBE_API_KEY=AIzaSy...your_key_here...
USPTO_API_KEY=...          # optional — only needed for --check-trademark
```

Or pass `--api-key YOUR_KEY` on every command. The `.env` file is gitignored.

---

## Quick Start — Interactive Wizard

The easiest way to run Cashtube is the wizard. It walks you through every option and shows the equivalent CLI command so you can rerun without it.

```bash
cashtube
# or
cashtube wizard
# or
cashtube-wizard
```

Example session:

```
──────────────────────────────────────────────────────
  Cashtube Wizard
──────────────────────────────────────────────────────
  Press Enter to accept defaults shown in [brackets].

  YouTube API key loaded from environment.

  What do you want to run?
    1) Full pipeline  — Phase 1 discovery → Phase 2 dead-link scan (default)
    2) Phase 1 only   — find legacy channels, write phase1_results.csv
    3) Phase 2 only   — scan an existing channels CSV for dead links
    4) Ghost hunter   — high-view tech/startup niche (preset keywords)
  Choice [1]: 1

  Search keywords
  Enter search terms one per line. Blank line when done.
    keyword 1: saas tutorial
    keyword 2: kickstarter gadget
    keyword 3:

  Published before [2016-01-01T00:00:00Z]:
  Max channels [100]: 50
  Min video count [50]:
  Min total views (0 = no minimum) [0]: 500000
  Ignore channels active within N days (0 = keep all) [180]:

  Videos to scan per channel [20]:
  Parallel DNS workers [10]:
  HTTP reachability / parking check? [y/N]:
  RDAP domain availability check? [y/N]: y
  Wayback Machine snapshot check? [y/N]: y

  Dry run (skip DNS — just list candidate domains)? [y/N]:

──────────────────────────────────────────────────────
  Summary
──────────────────────────────────────────────────────
  Mode            pipeline
  Keywords        saas tutorial, kickstarter gadget
  Published before 2016-01-01T00:00:00Z
  Max channels    50  (min 50 vids, min 500,000 views)
  Recent days     180
  Videos/channel  20  DNS workers: 10
  Enrichment      RDAP, Wayback

  Run now? [Y/n]:

  Equivalent command:
    cashtube pipeline --api-key $YOUTUBE_API_KEY \
      --keywords-file .wizard_keywords.json \
      --published-before 2016-01-01T00:00:00Z \
      --min-video-count 50 --min-views 500000 \
      --max-channels 50 --recent-days 180 \
      --top-n-videos 20 --check-rdap --check-wayback
```

---

## CLI Reference

All commands share the `cashtube <subcommand>` prefix.

### `cashtube pipeline` — Full run (Phase 1 + Phase 2)

```bash
cashtube pipeline \
  --api-key YOUR_KEY \
  --keywords "saas tutorial" \
  --keywords-file cashtube-config.json \
  --published-before 2016-01-01T00:00:00Z \
  --published-after  2008-01-01T00:00:00Z \
  --min-video-count 50 \
  --min-views 100000 \
  --max-channels 100 \
  --recent-days 180 \
  --top-n-videos 20 \
  --max-channel-workers 4 \
  --channels-output phase1_results.csv \
  --dead-links-output phase2_results.csv \
  --json-output phase2_results.json \
  --report-output phase2_summary.md \
  --enrich-http \
  --check-rdap \
  --check-wayback \
  --check-trademark \
  --cache-db .cashtube_cache.sqlite3 \
  --cache-ttl-seconds 86400 \
  --yt-dlp-delay 0.5 \
  --yt-dlp-retries 3 \
  --channel-timeout 120 \
  --checkpoint-file .cashtube_channels_seen.json \
  --scan-checkpoint-file .cashtube_phase2_checkpoint.json \
  --youtube-delay 0.0 \
  --dry-run \
  --log-level INFO \
  --json-logs
```

| Flag | Default | Description |
|------|---------|-------------|
| `--api-key` | `$YOUTUBE_API_KEY` | YouTube Data API v3 key |
| `--keywords` | — | Single search keyword |
| `--keywords-file` | — | JSON/YAML file with a `"keywords"` list |
| `--published-before` | `2016-01-01T00:00:00Z` | Only find channels whose videos predate this |
| `--published-after` | — | Narrow the search window (ISO-8601) |
| `--min-video-count` | `50` | Skip channels with fewer videos |
| `--min-views` | `0` | Skip channels with fewer total views |
| `--max-channels` | `100` | Stop after qualifying this many channels |
| `--recent-days` | `180` | Skip channels that uploaded within N days (0 = keep all) |
| `--top-n-videos` | `20` | Videos to scrape per channel in Phase 2 |
| `--max-channel-workers` | `4` | Parallel threads for Phase 2 scanning |
| `--channels-output` | `phase1_results.csv` | Phase 1 output file |
| `--dead-links-output` | `phase2_results.csv` | Phase 2 output file |
| `--json-output` | — | Also write results as JSON |
| `--report-output` | — | Also write a Markdown summary report |
| `--enrich-http` | off | HTTP-fetch each dead domain to check reachability and parking |
| `--check-rdap` | off | RDAP lookup to see if the domain is still registered |
| `--check-wayback` | off | Check Wayback Machine for historical snapshots |
| `--check-trademark` | off | USPTO trademark risk check (requires `USPTO_API_KEY`) |
| `--cache-db` | `.cashtube_cache.sqlite3` | SQLite cache path (DNS + video metadata) |
| `--cache-ttl-seconds` | `86400` | Cache TTL in seconds (default: 24h) |
| `--yt-dlp-delay` | `0.0` | Seconds to pause between yt-dlp calls |
| `--yt-dlp-retries` | `3` | Retry count for yt-dlp failures |
| `--channel-timeout` | — | Seconds before abandoning a channel scan |
| `--checkpoint-file` | `.cashtube_channels_seen.json` | Tracks Phase 1 progress for resume |
| `--scan-checkpoint-file` | `.cashtube_phase2_checkpoint.json` | Tracks Phase 2 progress for resume |
| `--youtube-delay` | `0.0` | Seconds between YouTube API calls (rate control) |
| `--dry-run` | off | Extract URL candidates without DNS resolution |
| `--log-level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `--json-logs` | off | Emit logs as JSON lines (for log aggregators) |

---

### `cashtube phase1` — Discover channels only

Runs Phase 1 and writes `phase1_results.csv`. Use this when you want to review or filter the channel list before committing to a full Phase 2 scan.

```bash
cashtube phase1 \
  --api-key YOUR_KEY \
  --keywords "unboxing" \
  --published-before 2016-01-01T00:00:00Z \
  --min-video-count 50 \
  --min-views 100000 \
  --max-channels 200 \
  --recent-days 365 \
  --output phase1_results.csv \
  --log-level DEBUG
```

Accepts all Phase 1 flags from the pipeline table above. Does not accept Phase 2 flags.

---

### `cashtube phase2` — Scan channels for dead links

Reads a channels CSV (from Phase 1) and runs dead-link detection. Useful for re-scanning the same channel list with different enrichment settings, or resuming an interrupted scan.

```bash
cashtube phase2 \
  --channels-file phase1_results.csv \
  --top-n-videos 20 \
  --max-channel-workers 4 \
  --max-dns-workers 10 \
  --enrich-http \
  --check-rdap \
  --check-wayback \
  --cache-db .cashtube_cache.sqlite3 \
  --checkpoint-file .cashtube_phase2_checkpoint.json \
  --output phase2_results.csv
```

The `--channels-file` must have a `channel_id` column (the default Phase 1 output does).

---

### `cashtube ghost` — Niche ghost hunter

Focused scan for high-value tech/startup ghost assets using preset keywords. Outputs a combined `ghost_results.csv` sorted by priority score.

```bash
cashtube ghost \
  --api-key YOUR_KEY \
  --published-before 2016-01-01T00:00:00Z \
  --min-views 2000000 \
  --min-video-count 50 \
  --max-channels 100 \
  --recent-days 180 \
  --top-n-videos 20 \
  --dry-run \
  --output ghost_results.csv
```

Preset keywords: `tech review`, `unboxing`, `hands-on review`, `kickstarter gadget`, `startup demo`, `saas tutorial`. To use different keywords, use `cashtube pipeline` instead.

---

## Keywords and Config Files

For multiple keywords, use a JSON or YAML config file.

**`cashtube-config.json`**
```json
{
  "keywords": [
    "saas tutorial",
    "kickstarter gadget",
    "startup demo",
    "tech review 2014",
    "product launch"
  ],
  "ignore_domains": [
    "patreon.com",
    "paypal.com",
    "shopify.com"
  ],
  "allowed_tlds": [".com", ".io", ".net"]
}
```

**`cashtube-config.yaml`**
```yaml
keywords:
  - saas tutorial
  - kickstarter gadget
ignore_domains:
  - patreon.com
allowed_tlds:
  - .com
  - .io
```

Pass it with:
```bash
cashtube pipeline \
  --api-key YOUR_KEY \
  --keywords-file cashtube-config.json \
  --config cashtube-config.json
```

`--keywords-file` supplies the keyword list for Phase 1. `--config` supplies domain filter overrides for Phase 2. Both can point to the same file.

---

## Outputs

### `phase1_results.csv`

One row per qualifying channel, sorted by total views descending.

| Column | Description |
|--------|-------------|
| `channel_id` | YouTube channel ID |
| `title` | Channel display name |
| `view_count` | Total channel views |
| `video_count` | Number of uploaded videos |
| `published_at` | Channel creation date (ISO-8601) |
| `last_upload` | Date of most recent upload within `--recent-days` window; empty = no recent activity |
| `source_keyword` | The search keyword that surfaced this channel |

### `phase2_results.csv`

One row per dead-domain candidate found, sorted by channel and domain.

| Column | Description |
|--------|-------------|
| `channel_url` | Source channel URL |
| `video_url` | The specific video where the domain appeared |
| `dead_domain` | The candidate expired domain (`example.com`, no `www.`) |
| `status` | DNS result: `nxdomain` (gone), `live`, `servfail`, `timeout`, `temporary_error` |
| `error_category` | `dns_nxdomain` for confirmed dead domains; `dry_run` if `--dry-run` was set |
| `http_status` | HTTP response code (`--enrich-http` only) |
| `ssl_ok` | Whether HTTPS was valid (`--enrich-http` only) |
| `parking_detected` | `True` if the domain appears parked/for-sale (`--enrich-http` only) |
| `availability_signal` | `parked`, `reachable`, `unreachable`, `ssl_error`, or `dns_nxdomain` |
| `rdap_status` | `not_found` (unregistered), `registered`, `http_404`, `error` (`--check-rdap` only) |
| `wayback_status` | `snapshot_found` (had real content), `none`, `error` (`--check-wayback` only) |
| `trademark_status` | `clear`, `risky`, `not_configured`, `error` (`--check-trademark` only) |
| `priority_score` | Composite ROI score (see below) |
| `first_seen_at` | Timestamp this domain was first detected (UTC) |
| `source_description_snippet` | 180-character context window from the video description |

### Priority Score

Scores are additive integers. Higher is better.

| Signal | Points | Reasoning |
|--------|--------|-----------|
| RDAP `not_found` | +2 | Domain is unregistered and available to acquire |
| Wayback `snapshot_found` | +1 | Had real content; carries SEO history and link equity |
| Parking detected | −1 | Domain is held by a parking service; acquisition cost may be higher |
| Trademark `risky` | −2 | Active trademark found; legal risk to registration |

Sort `phase2_results.csv` by `priority_score` descending to see the best leads first.

---

## Quota Management

YouTube Data API v3 has a **10,000 unit/day** limit per API key. Quota resets at **midnight Pacific time**.

| Operation | Cost | When |
|-----------|------|------|
| Search page (50 results) | 100 units | Phase 1 keyword search |
| `channels.list` batch (50 channels) | 1 unit | Phase 1 stats fetch |
| `playlistItems.list` (recent upload check) | 1 unit | Phase 1 per qualifying channel |
| `search` fallback (recent upload check) | 100 units | Phase 1 — only if no uploads playlist ID |

**Estimating usage:** With `--max-channels 100` and 3 keywords, Phase 1 typically costs 600–900 units (3 keywords × 2–3 search pages × 100 units, plus cheap lookups). A full 6-keyword ghost run can exhaust the 10,000-unit budget in one pass.

**To reduce quota consumption:**
- Use fewer keywords
- Lower `--max-channels`
- Run Phase 1 once, then rerun Phase 2 many times against the saved CSV (Phase 2 uses no YouTube quota)
- Use a second Google Cloud project for a second API key

**If you see `WARNING: Quota exhausted mid-search`:** The run continues with whatever channels were found before the limit hit. Results are saved and can be resumed.

---

## Resuming Interrupted Runs

Cashtube writes checkpoint files after each channel is processed:

- `.cashtube_channels_seen.json` — channels already processed by Phase 1
- `.cashtube_phase2_checkpoint.json` — channels already scanned by Phase 2

If a run is interrupted (Ctrl-C, crash, quota exhaustion), simply rerun the same command. Already-completed channels are skipped automatically.

To start fresh, delete the checkpoint files:
```bash
rm .cashtube_channels_seen.json .cashtube_phase2_checkpoint.json
```

DNS and video metadata lookups are also cached in `.cashtube_cache.sqlite3`. Delete it to force a full re-check, or set `--cache-ttl-seconds 0` to disable caching.

---

## DNS False Positives

`NXDOMAIN` means the domain did not resolve at query time. It does **not** guarantee the domain is available to register. Common reasons for false positives:

- Temporary DNS outage or propagation delay
- Registrar redemption period (domain expired but not yet released)
- Geo-DNS (domain resolves in some regions but not others)
- Private/internal domains

Always verify availability with a registrar (Namecheap, Cloudflare Registrar, etc.) before attempting to register.

---

## Enrichment Details

### `--enrich-http`
Makes an HTTP GET request to the dead domain. Detects:
- Whether the domain is still reachable (HTTP 200) despite DNS returning NXDOMAIN (rare, but happens with CDN edge caching)
- Parking-page signals (`"buy this domain"`, `"sedo"`, `"afternic"`, `"godaddy.com/domainsearch"`, etc.)

### `--check-rdap`
Queries `rdap.org` for registration status. `not_found` means the registry has no record of the domain — the strongest signal of availability.

### `--check-wayback`
Queries the Wayback Machine CDX API. `snapshot_found` means the domain hosted real content at some point. A domain with Wayback snapshots carries more SEO history and inbound link equity than one that was never indexed.

### `--check-trademark`
Queries the USPTO TSDR API. `risky` means an active trademark registration exists for the domain name's primary word. Requires `USPTO_API_KEY` in the environment.

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Lint and format checks
ruff check .
black --check .
mypy .

# Auto-fix formatting
black .
ruff check --fix .
```

### Running a single test
```bash
python -m pytest tests/test_phase1_phase2.py::Phase2DryRunTest::test_process_channel_dry_run_schema -v
```

### Adding keywords and domain filters
Edit `cashtube-config.json` (or create it) and pass it with `--keywords-file` / `--config`. No code changes needed.

### yt-dlp updates
YouTube changes its internal API periodically. If Phase 2 produces no results or throws extraction errors, update yt-dlp:
```bash
pip install --upgrade yt-dlp
```

---

## Troubleshooting

**`403 quotaExceeded` / `WARNING: Quota exhausted mid-search`**
Your daily 10,000-unit YouTube API quota is exhausted. Wait until midnight Pacific time. The run saves progress and will resume from where it stopped.

**`403 keyInvalid`**
The API key is wrong or the YouTube Data API v3 is not enabled on your Google Cloud project. Check the key and ensure the API is enabled at console.cloud.google.com.

**Phase 2 finds zero dead domains**
- Try `--dry-run` first to see what URLs are being extracted before DNS resolution
- Check that the channels in `phase1_results.csv` actually have video descriptions with external links
- Lower `--min-views` or `--min-video-count` to broaden the channel pool
- Add more keywords or try different ones

**yt-dlp fails with extraction errors**
Update yt-dlp: `pip install --upgrade yt-dlp`. If a specific channel fails repeatedly, it may be age-restricted or unavailable in your region; those channels are skipped automatically.

**Slow Phase 2 scans**
- Increase `--max-channel-workers` (default 4) for more parallel channel scanning
- Increase `--max-dns-workers` (default 10) for more parallel DNS resolution
- Set `--channel-timeout 60` to abandon slow channels after 60 seconds
- Ensure `--cache-db` is set so DNS results are cached between runs

---

## File Reference

| File | Purpose |
|------|---------|
| `cashtube/cli.py` | Entry point; dispatches subcommands |
| `cashtube/wizard.py` | Interactive wizard |
| `cashtube_utils.py` | Shared utilities: HTTP session, DNS, SQLite cache, rate limiter, URL extraction |
| `phase1_smart_discovery.py` | YouTube API queries → `ChannelRecord` → `phase1_results.csv` |
| `phase2_dead_link_detection.py` | yt-dlp scraping → `DeadLinkEntry` → `phase2_results.csv` |
| `cashtube_pipeline.py` | Wires Phase 1 → Phase 2 with parallel scanning and checkpointing |
| `digital_asset_ghost_hunter.py` | Standalone niche-focused variant (tech/startup keywords, preset) |
| `trademarked.py` | Trademark risk utilities (USPTO API) |
| `.cashtube_channels_seen.json` | Phase 1 checkpoint (gitignored) |
| `.cashtube_phase2_checkpoint.json` | Phase 2 checkpoint (gitignored) |
| `.cashtube_cache.sqlite3` | DNS + video metadata cache (gitignored) |
| `.wizard_keywords.json` | Keywords written by the wizard for CLI reuse (gitignored) |

---

## Ethical Use

- This tool analyzes publicly available YouTube metadata only
- Verify trademark and legal status before registering any domain
- Do not attempt CAPTCHA bypass, bulk email harvesting, or registrar API abuse
- Intended for research, SEO intelligence, and lawful domain investment only

---

*The internet forgets surprisingly expensive things. Cashtube finds them.*
