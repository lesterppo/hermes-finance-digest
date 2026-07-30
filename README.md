# YouTube Finance Daily Digest

**Automated daily deep analysis of YouTube videos using Google Gemini AI — sent straight to your inbox.**

[![GitHub Actions](https://github.com/lesterppo/yt-finance-digest/actions/workflows/daily.yml/badge.svg)](https://github.com/lesterppo/yt-finance-digest/actions/workflows/daily.yml)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A zero-cost, fully automated pipeline that scrapes YouTube channels for new videos daily, sends each video URL directly to **Google Gemini** (Flash Extended Thinking) for institutional-quality analysis using a **7-dimension analytical framework**, and delivers the compiled report via email. No API keys needed — uses Gemini web cookies for authentication.

## Features

- **URL-Direct Analysis** — Sends YouTube video URLs directly to Gemini. No transcript extraction needed — Gemini resolves video content and transcripts natively.
- **7-Dimension Framework** — Each video gets a structured institutional analysis: Executive Summary → Thesis Deconstruction → Data & Evidence Audit → Market Context → Risk Matrix → Actionable Insights → Overall Assessment (star ratings).
- **Zero API Cost** — Uses Gemini web cookies (`__Secure-1PSID`), no API key or billing required.
- **Fully Configurable** — All settings via environment variables. Customize channels, analysis persona, language, model, schedule — everything.
- **GitHub Actions Ready** — Scheduled daily run included. Set 5 secrets and you're done.
- **Privacy-Safe** — No hardcoded credentials, paths, or personal identifiers. Suitable for public forks.

## Quick Start (GitHub Actions)

```bash
# 1. Fork this repo
gh repo fork lesterppo/yt-finance-digest --clone
cd yt-finance-digest

# 2. Get Gemini cookies
pip install gemini-webapi browser-cookie3 loguru
python gemini.py --init
cat ~/.gemini-cli/auth.json  # copy __Secure-1PSID and __Secure-1PSIDTS

# 3. Set GitHub Secrets
#    GEMINI_SID, GEMINI_TS, YT_GEM_SMTP_USER, YT_GEM_SMTP_PASS, YT_GEM_RECIPIENT

# 4. Customize
#    Edit channels.txt — add your YouTube channel URLs
#    Edit GEM_SYSTEM_PROMPT.md — customize analysis style (optional)

# 5. Run manually to test
gh workflow run daily.yml
```

First email arrives in ~2 minutes with deep analysis of every new video from your channels.

## How It Works

```
YouTube pages → lockupViewModel scraping → per-video Gemini analysis (URL-direct) → SMTP email
```

1. Scrapes `@handle/videos` pages for videos published in the last 24 hours (works from any IP, unlike RSS)
2. Sends each video URL individually to Gemini Flash Extended Thinking
3. Gemini accesses the video, reads the transcript, and produces a 7-dimension analysis
4. All analyses compiled into a single email report

## Customization

| What | How |
|------|-----|
| **Channels** | Edit `channels.txt` — one YouTube URL per line |
| **Analysis Style** | Edit `GEM_SYSTEM_PROMPT.md` — any language, any domain |
| **Language** | Set persona to your language — Financial, Tech, News, Academic |
| **Model** | `YT_GEM_MODEL=pro` for deeper analysis, `flash` for speed (default) |
| **Schedule** | Edit `cron:` in `.github/workflows/daily.yml` |
| **Recipient** | `YT_GEM_RECIPIENT` env var |

## Analysis Framework

Each video is analyzed across 7 dimensions:

1. **Executive Summary** — Core thesis, investment direction, time horizon, information value rating
2. **Thesis Deconstruction** — Point-by-point logic chain analysis with strength ratings
3. **Data & Evidence Audit** — Source reliability, timeliness check, missing data identification
4. **Market Context** — Macro positioning, sector trends, policy impact, price-in assessment
5. **Risk Matrix** — Tabular risk breakdown: macro/policy/market/fundamental/liquidity
6. **Actionable Insights** — Tiered recommendations: agree/partially agree/disagree scenarios
7. **Overall Assessment** — 6-dimensional star ratings + composite score /10

## Configuration Reference

All environment variables (see `CONFIG.md` for full list):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_SID` | Yes* | — | `__Secure-1PSID` cookie |
| `GEMINI_TS` | Yes* | — | `__Secure-1PSIDTS` cookie |
| `YT_GEM_SMTP_USER` | Yes | — | Gmail address |
| `YT_GEM_SMTP_PASS` | Yes | — | Gmail app password |
| `YT_GEM_RECIPIENT` | Yes | — | Destination email |
| `YT_GEM_MODEL` | No | `flash` | `flash`, `pro`, or `lite` |
| `YT_GEM_HOURS_BACK` | No | `24` | Look-back window |

*For GitHub Actions: set as repository secrets.

## Files

| File | Purpose |
|------|---------|
| `yt_gem_daily.py` | Main script — scraping, Gemini analysis, email |
| `gemini.py` | Bundled Gemini CLI (gemini-webapi, cookie auth) |
| `yt_gem_watchdog.py` | Silent-failure alert if no email for >48h |
| `refresh_gh_secrets.py` | Auto-refresh cookies to GitHub Secrets |
| `channels.txt` | YouTube channel URLs (user-editable) |
| `GEM_SYSTEM_PROMPT.md` | Analysis persona (user-editable) |
| `CONFIG.md` | Environment variable reference |
| `AGENTS.md` | Full setup guide for AI agents |

## AI Agent Setup

AI coding agents (Claude Code, Codex, Hermes Agent, Cursor, etc.) should read `AGENTS.md` for a complete zero→deployed walkthrough with exact commands and configuration steps.

## Keywords

YouTube video analysis, Gemini AI, automated email digest, financial news summary, YouTube transcript analysis, Google Gemini, GitHub Actions cron, video content analyzer, AI-powered newsletter, YouTube scraper, daily video digest, institutional research automation

## License

MIT — see [LICENSE](LICENSE)
