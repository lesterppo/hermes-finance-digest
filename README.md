# YouTube Finance Daily Digest

**Automated daily deep analysis of YouTube videos using Google Gemini AI — sent straight to your inbox.**

[![GitHub Actions](https://github.com/lesterppo/yt-finance-digest/actions/workflows/daily.yml/badge.svg)](https://github.com/lesterppo/yt-finance-digest/actions/workflows/daily.yml)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A zero-cost, fully automated pipeline that scrapes YouTube channels for new videos daily, sends each video URL directly to **Google Gemini** (Flash Extended Thinking) for **buy-side desk-note analysis**, cross-synthesizes the day's notes into one decision briefing, and delivers the compiled report via email. No API keys needed — uses Gemini web cookies for authentication.

> Canonical repo. The earlier private fork (`youtube-gem-digest`) is archived — this repo is the single home of the pipeline and its one daily workflow.

## Features

- **URL-Direct Analysis** — Sends YouTube video URLs directly to Gemini. No transcript extraction needed — Gemini resolves video content and transcripts natively.
- **Desk-Note Standard** — Every video gets a pre-trade style note: Desk Take (stance / conviction / horizon / instruments / catalyst / invalidation / edge) → Thesis Deconstruction → Data & Evidence Audit → Market Context & Pricing → Risk Matrix → Actionable Insights with instrument mapping → Scored Assessment with reasons. Timestamp citations are auto-linked back to the video.
- **Cross-Video Briefing** — One extra pass over the day's notes produces the top of the email: core messages, agreement vs contradiction between videos, an actionable list with real tickers (conditional watchlist when there is no high-conviction trade), upcoming catalysts and dates, market posture, and the day's shared blind spot.
- **Decision-First Ordering** — Notes are ranked by their own composite score before the email is assembled, so the highest-value note is read first.
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
3. Gemini accesses the video, reads the transcript, and writes a buy-side desk note
4. Notes are ranked by their own composite score
5. One synthesis pass turns the ranked notes into the cross-video briefing at the top of the email
6. Email is assembled as HTML (with a data-viz chart and a NotebookLM dashboard) and sent over SMTP

## Customization

| What | How |
|------|-----|
| **Channels** | Edit `channels.txt` — one YouTube URL per line |
| **Analysis Style** | Edit `GEM_SYSTEM_PROMPT.md` — any language, any domain |
| **Language** | Set persona to your language — Financial, Tech, News, Academic |
| **Model** | `YT_GEM_MODEL=pro` for deeper analysis, `flash` for speed (default) |
| **Schedule** | Edit `cron:` in `.github/workflows/daily.yml` |
| **Recipient** | `YT_GEM_RECIPIENT` env var |
| **Cross-video briefing** | `YT_GEM_SYNTHESIS=0` to turn it off; `YT_GEM_SYNTHESIS_CHARS` caps each note's contribution |

## Analysis Standard

Each video is analyzed as a pre-trade desk note (`GEM_SYSTEM_PROMPT.md`; the same
standard is embedded as a fallback prompt):

0. **Desk Take** — stance, conviction 1–5, horizon, instruments with real tickers, catalyst + date, invalidation condition, and whether the video carries any information edge versus consensus
1. **Executive Summary** — core thesis, direction, information value grade (A/B/C) with reason
2. **Thesis Deconstruction** — per argument: logic chain, rigour (strongly / partly / thinly supported), strongest counter-argument, verifiability
3. **Data & Evidence Audit** — table of claims, timestamps, audit verdict, missing or confounding variables
4. **Market Context & Pricing** — explicitly separates what is priced in from what is not
5. **Risk Matrix** — macro / policy / market / fundamental / liquidity, with monitoring indicators
6. **Actionable Insights** — instrument-mapping table (idea, ticker, direction, trigger, invalidation) plus agree / partly agree / disagree playbooks
7. **Scored Assessment** — six dimensions scored with one-line reasons, composite /10, verdict (值得關注 / 一般 / 可略過)

Timestamps written as `[mm:ss]` are converted to clickable links back to the video's moment.

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
| `YT_GEM_SYNTHESIS` | No | `1` | Cross-video briefing (`0` disables) |
| `YT_GEM_SYNTHESIS_CHARS` | No | `2500` | Max chars per note fed to the briefing |
| `YT_GEM_SEEN_FILE` | No | `~/.hermes/yt_gem_seen.json` | Dedup database |
| `DIGEST_DRY_RUN` | No | — | `1` runs everything but sends no email |

*For GitHub Actions: set as repository secrets.

## Files

| File | Purpose |
|------|---------|
| `yt_gem_daily.py` | Main script — scraping, Gemini desk notes, synthesis, email |
| `gemini.py` | Bundled Gemini CLI (gemini-webapi, cookie auth) |
| `ytgem_email.py` | HTML email builder + SMTP sender (markdown renderer, infographic CIDs) |
| `digest_infographic.py` | matplotlib chart + NotebookLM infographic generation |
| `nlm.py` | NotebookLM CLI wrapper used by the infographic step |
| `nlm_cookie_sync.py` | Refreshes the NotebookLM session secret from a live browser |
| `yt_gem_watchdog.py` | Silent-failure alert if no email for >48h |
| `refresh_gh_secrets.py` | Auto-refresh cookies to GitHub Secrets |
| `channels.txt` | YouTube channel URLs (user-editable) |
| `GEM_SYSTEM_PROMPT.md` | Analysis persona (user-editable) |
| `CONFIG.md` | Environment variable reference |
| `AGENTS.md` | Full setup guide for AI agents |

## AI Agent Setup

AI coding agents (Claude Code, Codex, Hermes Agent, Cursor, etc.) should read `AGENTS.md` for a complete zero→deployed walkthrough with exact commands and configuration steps.

### Operational notes for maintainers

- **Infographic language** — the NotebookLM prompt must state the language rule in
  both English and Chinese. With an English-only instruction NotebookLM renders
  Chinese headlines in English ("BlackRock View", "One-Hammer Tone?").
- **Artifact reuse** — NotebookLM caps artifact generation per notebook per day
  (~3). The pipeline reuses an artifact already completed today; use
  `NLM_NOTEBOOK_TAG` (workflow input `nlm_notebook_tag`) to force a fresh
  notebook when verifying a changed prompt.
- **Testing without an inbox** — `ignore_seen=1` re-analyses the window and the
  emailed HTML plus both images are uploaded as run artifacts, so the delivered
  report can be reviewed without opening the mailbox.
- **Scoring order** — notes are ranked by the score they state (`綜合評級：x.x / 10`)
  before the email is built, and the cross-video briefing runs after ranking so
  its "影片 N" references line up.

## Keywords

YouTube video analysis, Gemini AI, automated email digest, financial news summary, YouTube transcript analysis, Google Gemini, GitHub Actions cron, video content analyzer, AI-powered newsletter, YouTube scraper, daily video digest, institutional research automation

## License

MIT — see [LICENSE](LICENSE)
