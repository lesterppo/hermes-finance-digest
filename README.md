# Finance Daily Digest — markets + news + YouTube

**One automated daily email combining a market snapshot, financial headlines, an institutional market brief, and buy-side desk notes on the day's finance videos — zero API cost.**

[![GitHub Actions](https://github.com/lesterppo/hermes-finance-digest/actions/workflows/daily.yml/badge.svg)](https://github.com/lesterppo/hermes-finance-digest/actions/workflows/daily.yml)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A fully automated pipeline that each morning pulls 31 market instruments and
headlines from ten financial RSS feeds, scrapes your YouTube finance channels
for new videos, sends the videos **directly to Google Gemini** (Flash Extended
Thinking) for desk-note analysis, writes a market/news brief from the day's own
numbers, synthesises everything into one decision briefing, and delivers it as
a single HTML email with charts and a NotebookLM dashboard.

> This repo is the merge of two earlier pipelines: `hermes-daily-finance`
> (market data + headlines) and `yt-finance-digest` (the YouTube half). The
> YouTube half is the survivor because its email/infographic stack and CI were
> the more mature part; the market half was ported in, and the retired repo is
> archived.

## What one run produces

| Section | Source | Notes |
|---|---|---|
| 市場快照 | Yahoo Finance (`yfinance`) | US indices, Magnificent 7, rates (in bp), commodities, FX, crypto, Asia/Europe |
| Chart | matplotlib (local render) | grouped horizontal bars; ASCII labels because the default font has no CJK glyphs |
| 財經頭條 | 10 RSS feeds, round-robin | per-source cap, so one high-volume feed cannot fill the whole quota |
| 市場與新聞研判 | Gemini (3-tier) | institutional brief; may only cite the supplied numbers |
| 影片分析 | Gemini + YouTube URLs | per-channel desk notes, ranked by their own score |
| 今日綜合研判 | Gemini | synthesis across market data + headlines + videos |
| Infographics | matplotlib + NotebookLM | market chart, direction pulse panel, NotebookLM dashboard |

## Features

- **Merged market + video digest** — one email, not two. A day with no new videos still delivers the market snapshot and brief.
- **Degrades, never cancels** — if every LLM tier fails, the email still ships the raw market table and headlines. The numbers are the part that must never be wrong.
- **URL-Direct video analysis** — YouTube URLs go straight to Gemini. No transcript extraction — Gemini resolves video content natively.
- **Desk-Note Standard** — Desk Take (stance / conviction / horizon / instruments / catalyst / invalidation / edge) → Thesis Deconstruction → Data & Evidence Audit → Market Context & Pricing → Risk Matrix → Actionable Insights with instrument mapping → Scored Assessment. `[mm:ss]` timestamps become clickable links.
- **Cross-source briefing** — one extra pass over the day's notes *and* the market data produces the top of the email: core messages, agreement vs contradiction, an actionable list with real tickers, upcoming catalysts, market posture, and the day's shared blind spot.
- **Zero API cost** — free AI Studio key, Gemini web cookies, and OpenRouter `:free` slugs only. Every fallback slug must end in `:free`.
- **Fully Configurable** — channels, persona, language, model, look-back window, schedule: all environment variables.
- **GitHub Actions Ready** — daily schedule included; set 5 secrets and you're done.
- **Privacy-Safe** — no hardcoded credentials, paths, or personal identifiers; a preflight step names any missing secret without printing values.

## Quick Start (GitHub Actions)

```bash
# 1. Fork this repo
gh repo fork lesterppo/hermes-finance-digest --clone
cd hermes-finance-digest

# 2. Gemini API key (free AI Studio tier) — the CI-safe tier
#    https://aistudio.google.com/apikey

# 3. Gemini cookies (video analysis + cookie tier)
pip install gemini-webapi browser-cookie3 loguru
python gemini.py --init
cat ~/.gemini-cli/auth.json  # copy __Secure-1PSID and __Secure-1PSIDTS

# 4. Set GitHub Secrets
#    GEMINI_API_KEY, GEMINI_SID, GEMINI_TS,
#    YT_GEM_SMTP_USER, YT_GEM_SMTP_PASS, YT_GEM_RECIPIENT
#    optional: OPENROUTER_API_KEY (free fallback tier), NLM_STORAGE_STATE_GZ

# 5. Customize
#    channels.txt — your YouTube channel URLs
#    GEM_SYSTEM_PROMPT.md — analysis persona (optional)

# 6. Test, then schedule
gh workflow run daily.yml -f dry_run=1
```

## How It Works

```
yfinance + 10 RSS feeds ─┐
                         ├─→ market/news brief (Gemini, 3 tiers) ─┐
YouTube pages ─→ per-video desk notes (Gemini, URL-direct) ───────┼─→ synthesis ─→ HTML email + charts
                                                                  ┘
```

1. Collects the market snapshot (yfinance) and headlines (round-robin RSS) — no LLM needed for the numbers.
2. Scrapes `@handle/videos` pages for videos published in the look-back window (works from any IP, unlike RSS).
3. Sends each video URL individually to Gemini, which reads the video and writes a buy-side desk note.
4. Writes the market/news brief from the collected table + headlines, with an explicit instruction not to introduce numbers.
5. Ranks the notes by their own composite score, then runs one synthesis pass across market + news + videos.
6. Assembles the HTML email (market chart, pulse chart, NotebookLM dashboard as inline images) and sends it over SMTP.

## LLM tiers (all $0)

1. **Gemini API key** — `GEMINI_API_KEY` (free AI Studio tier). CI-safe: Gemini web cookies are refused from GitHub runner IPs (consent wall), so a runner needs this key.
2. **Gemini web cookies** — the vendored `gemini.py` (`GEMINI_SID`/`GEMINI_TS`); also the path used for video analysis.
3. **OpenRouter `:free` slugs** — `OPENROUTER_API_KEY`. If a slug retires, probe `/api/v1/models` for another `:free` id; replacing it with a paid model is not an acceptable fix.

## Customization

| What | How |
|------|-----|
| **Channels** | Edit `channels.txt` — one YouTube URL per line |
| **Analysis Style** | Edit `GEM_SYSTEM_PROMPT.md` — any language, any domain |
| **Model** | `YT_GEM_MODEL=pro` for deeper analysis, `flash` for speed (default) |
| **Schedule** | Edit `cron:` in `.github/workflows/daily.yml` |
| **Recipient** | `YT_GEM_RECIPIENT` env var |
| **Headline count** | `FIN_NEWS_ITEMS` (default 40) |
| **Cross-source briefing** | `YT_GEM_SYNTHESIS=0` to turn it off; `YT_GEM_SYNTHESIS_CHARS` caps each note's contribution |

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
| `GEMINI_API_KEY` | Yes | — | Free AI Studio API key — primary LLM tier |
| `GEMINI_SID` | Yes* | — | `__Secure-1PSID` cookie |
| `GEMINI_TS` | Yes* | — | `__Secure-1PSIDTS` cookie |
| `YT_GEM_SMTP_USER` | Yes | — | Gmail address |
| `YT_GEM_SMTP_PASS` | Yes | — | Gmail app password |
| `YT_GEM_RECIPIENT` | Yes | — | Destination email |
| `OPENROUTER_API_KEY` | No | — | Free fallback tier (all slugs `:free`) |
| `NLM_STORAGE_STATE_GZ` | No | — | NotebookLM dashboard (gzip+base64 storage state) |
| `FIN_NEWS_ITEMS` | No | `40` | Headlines to collect |
| `FIN_MIN_REPORT_CHARS` | No | `200` | Reject a fallback response shorter than this |
| `FIN_API_TIMEOUT` | No | `240` | Per-call timeout for the API tiers |
| `YT_GEM_MODEL` | No | `flash` | `flash`, `pro`, or `lite` |
| `YT_GEM_HOURS_BACK` | No | `24` | Video look-back window |
| `YT_GEM_SYNTHESIS` | No | `1` | Cross-source briefing (`0` disables) |
| `YT_GEM_SYNTHESIS_CHARS` | No | `2500` | Max chars per note fed to the briefing |
| `YT_GEM_SEEN_FILE` | No | `~/.hermes/yt_gem_seen.json` | Dedup database |
| `DIGEST_DRY_RUN` | No | — | `1` builds the email but sends nothing |

*For GitHub Actions: set as repository secrets.

## Files

| File | Purpose |
|------|---------|
| `yt_gem_daily.py` | Main script — market half, scraping, Gemini desk notes, synthesis, email |
| `market_data.py` | Market snapshot, round-robin RSS headlines, matplotlib chart, HTML table, LLM prompt |
| `market_llm.py` | Free LLM tiers for the market brief (AI Studio key → OpenRouter `:free`) |
| `gemini.py` | Bundled Gemini CLI (gemini-webapi, cookie auth) |
| `ytgem_email.py` | HTML email builder + SMTP sender (markdown renderer, infographic CIDs) |
| `digest_infographic.py` | matplotlib chart + NotebookLM infographic generation |
| `nlm.py` | NotebookLM CLI wrapper used by the infographic step |
| `nlm_cookie_sync.py` | Refreshes the NotebookLM session secret from a live browser |
| `yt_gem_watchdog.py` | Silent-failure alert if no email for >48h |
| `refresh_gh_secrets.py` | Auto-refresh cookies to GitHub Secrets |
| `test/test_market_merge.py` | Offline tests for the merged market half (43 checks) |
| `channels.txt` | YouTube channel URLs (user-editable) |
| `GEM_SYSTEM_PROMPT.md` | Analysis persona (user-editable) |
| `CONFIG.md` | Environment variable reference |
| `AGENTS.md` | Full setup guide for AI agents |

## AI Agent Setup

AI coding agents (Claude Code, Codex, Hermes Agent, Cursor, etc.) should read `AGENTS.md` for a complete zero→deployed walkthrough with exact commands and configuration steps.

### Operational notes for maintainers

- **Feeds rot** — the RSS list was re-probed on 2026-09-17: Reuters' feed is 404/DNS-dead and AP returns 403, so they were replaced. Before blaming the parser, probe the feed.
- **Round-robin headlines** — appending feed-by-feed and truncating at the end let one high-volume feed fill the entire quota, so the collector interleaves sources with a per-source cap.
- **Chart labels are ASCII** — matplotlib's default font has no CJK glyphs; CJK labels render as boxes.
- **Infographic language** — the NotebookLM prompt must state the language rule in both English and Chinese. With an English-only instruction NotebookLM renders Chinese headlines in English ("BlackRock View", "One-Hammer Tone?").
- **Artifact reuse** — NotebookLM caps artifact generation per notebook per day (~3). The pipeline reuses an artifact already completed today; use `NLM_NOTEBOOK_TAG` (workflow input `nlm_notebook_tag`) to force a fresh notebook when verifying a changed prompt.
- **Testing without an inbox** — `ignore_seen=1` re-analyses the window, and the emailed HTML plus the images are uploaded as run artifacts, so the delivered report can be reviewed without opening the mailbox.
- **Scoring order** — notes are ranked by the score they state (`綜合評級：x.x / 10`) before the email is built, and the briefing runs after ranking so its "影片 N" references line up.
- **No cookies is not fatal** — a missing/stale `auth.json` now disables only the video half; the market half runs on the API key.

## Keywords

market digest, stock market email, financial news summary, YouTube video analysis, Gemini AI, automated email digest, Google Gemini, GitHub Actions cron, yfinance market snapshot, RSS financial headlines, institutional research automation, daily finance briefing, video content analyzer, AI-powered newsletter

## License

MIT — see [LICENSE](LICENSE)
