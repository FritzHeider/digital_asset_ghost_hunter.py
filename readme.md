# Cashtube / Digital Asset Ghost Hunter

## Overview

Cashtube is a modular YouTube intelligence and digital asset discovery pipeline designed to identify **legacy high-traffic YouTube channels** that may contain **abandoned domains** hidden in:

- Channel About pages
- Video descriptions
- Defunct sponsor links
- Legacy SaaS / startup promotions
- Old affiliate funnels

Its purpose is to uncover “Digital Ghost Assets”:
expired `.com`, `.io`, or `.net` domains still receiving evergreen traffic from old YouTube content.

---

# Core Concept

Many creators from **2010–2019** linked to:

- Startups that failed
- Sponsored products that vanished
- Custom landing pages they abandoned
- SaaS tools that shut down
- Affiliate microsites that expired

If those domains are now dead and legally safe, they may present opportunities for:

### Monetization Paths
- Affiliate redirects
- Domain flips
- Niche site rebuilds
- SEO authority capture
- Archive reconstruction

---

# Project Architecture

## Phase 1 → Smart Discovery
Discovers qualifying YouTube channels via API.

### Filters:
- Created before `2016-01-01`
- Tech/Gadget niche keywords
- Sorted by view count
- Minimum video count
- Minimum total views
- Recent upload activity

### Files:
- `phase1_smart_discovery.py`

### Output:
- `phase1_results.csv`

---

## Phase 2 → Dead Link Detection
Scans discovered channels for dead external domains.

### Process:
- Pull top videos with `yt-dlp`
- Regex extract URLs
- Ignore:
  - Amazon
  - Apple
  - Twitter
  - Facebook
  - Shorteners
- DNS resolution via `socket.gethostbyname`
- NXDOMAIN → Candidate

### Files:
- `phase2_dead_link_detection.py`

### Output:
- `phase2_results.csv`

---

## Phase 3 (Planned) → Validation
### Planned:
- USPTO Trademark Risk
- GoDaddy / Namecheap availability
- Wayback relevance
- Google Spam safety

---

## Ghost Hunter Edition
### File:
- `digital_asset_ghost_hunter.py`

### Purpose:
A niche-focused sponsor graveyard hunter optimized for:
- Tech Review
- Unboxing
- Hardware startups
- VC failures

---

# Installation

## 1. Clone / Extract
```bash
unzip cashtube_full_project.zip
cd cashtube
```

## 2. Create Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```

## 3. Install Requirements
```bash
pip install -r requirements.txt
```

---

# Environment Setup

Create `.env`

```env
YOUTUBE_API_KEY=YOUR_API_KEY
```

---

# Google API Setup

## Required:
### Enable:
**YouTube Data API v3**

### Use:
**Public Data**

### Important:
If you receive `403 Forbidden`, verify:

- API enabled
- Billing enabled
- API key unrestricted (for testing)
- Correct project selected
- Quota available

---

# Usage

---

## Run Phase 1 Only
```bash
python phase1_smart_discovery.py \
    --api-key YOUR_API_KEY \
    --published-before 2016-01-01T00:00:00Z \
    --min-video-count 50 \
    --recent-days 180 \
    --max-channels 100 \
    --output phase1_results.csv
```

---

## Run Phase 2 Only
```bash
python phase2_dead_link_detection.py \
    --channels-file phase1_results.csv \
    --top-n-videos 20 \
    --output phase2_results.csv
```

---

## Run Full Pipeline
```bash
python cashtube_pipeline.py \
    --api-key YOUR_API_KEY \
    --published-before 2016-01-01T00:00:00Z \
    --min-video-count 50 \
    --recent-days 180 \
    --max-channels 100 \
    --top-n-videos 20 \
    --channels-output phase1_results.csv \
    --dead-links-output phase2_results.csv
```

---

## Run Ghost Hunter
```bash
python digital_asset_ghost_hunter.py \
    --api-key YOUR_API_KEY \
    --published-before 2016-01-01T00:00:00Z \
    --min-views 2000000 \
    --top-n-videos 20 \
    --output ghost_results.csv
```

---

# Output Schema

## Phase 1 CSV
| Field | Description |
|------|-------------|
| channel_id | YouTube channel ID |
| title | Channel title |
| created_at | Channel creation date |
| video_count | Uploaded videos |
| view_count | Total channel views |
| last_upload | Most recent upload |

---

## Phase 2 CSV
| Field | Description |
|------|-------------|
| channel_url | Channel URL |
| video_url | Source video |
| dead_link | Dead external domain |
| status | DNS fail |
| priority_score | Basic ROI score |

---

# Security Notes

## Recommended:
- Restrict API key by IP
- Rotate secrets
- Move keys to Secrets Manager
- Use PostgreSQL cache
- Add rate limiting
- Add retry logic
- Add OpenTelemetry

---

# Known Risks

## False Positives:
A DNS failure may mean:
- Temporary outage
- Parking
- Registrar hold
- Geo DNS issue

### Solution:
Phase 3 domain registrar validation required.

---

# Ethical Guardrails

## Do:
- Analyze public metadata
- Evaluate domain history
- Check legal safety

## Do Not:
- Attempt CAPTCHA bypass
- Harvest protected emails
- Impersonate trademarks
- Abuse registrar systems

---

# Best Niches (2026)
### Highest ROI:
## Tech Review / Startup Graveyard
Examples:
- Hardware startups
- Smart home failures
- Kickstarter collapses
- SaaS shutdowns

---

# Future Enhancements

## Planned:
- USPTO API
- GoDaddy API
- Namecheap API
- Wayback Machine
- PostgreSQL
- Dashboard UI
- OpenTelemetry
- Async queue workers

---

# Troubleshooting

## 403 Forbidden:
Check Google Cloud setup.

## yt-dlp Failure:
```bash
pip install --upgrade yt-dlp
```

## DNS False Positives:
Test manually:
```bash
nslookup domain.com
```

---

# Disclaimer

This project is for:
### Research, SEO intelligence, and lawful domain investment only.

Always validate:
- Trademark
- Legal ownership
- Brand safety
- Historical use

---

# Final Thought

The internet forgets surprisingly expensive things.

Cashtube exists to find them before someone else does.# digital_asset_ghost_hunter.py
