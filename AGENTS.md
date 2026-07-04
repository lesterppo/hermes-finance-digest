# YouTube Gem Daily Digest — Agent Setup Guide

AI-agent-oriented documentation. Read this top-to-bottom to set up the full pipeline.

## What This Is

A cron-driven pipeline that:
1. Scrapes YouTube channels for recent videos (past 24h) via page scraping
2. Sends each video individually to a **Gemini Gem** (Flash Extended Thinking) for deep analysis
3. Compiles all analyses into an email report
4. Auto-creates the Gem on first run; reuses it every subsequent run

Zero external AI cost for orchestration — only the Gemini Gem is used for analysis.
The Gem has a persistent system instruction so you don't pay to re-send context.

## Architecture

```
cron (daily) → page scraping (lockupViewModel) → per-video Gem analysis → SMTP email
                   ↑                                                      ↑
              channels.txt                                    GEM_SYSTEM_PROMPT.md
```

## Prerequisites

### 1. Python + Dependencies
```bash
pip install -r requirements.txt
```
Requirements: `requests`, `youtube-transcript-api`.

### 2. gemini-cli (REQUIRED)
This project uses **[lesterppo/hermes-gem-cli](https://github.com/lesterppo/hermes-gem-cli)**
to interact with Gemini Gems programmatically.

```bash
# Install gemini-cli (clone to ~/gemini-cli or install via pip if available)
git clone https://github.com/lesterppo/hermes-gem-cli.git ~/gemini-cli
cd ~/gemini-cli
pip install -e .  # or: ln -sf ~/gemini-cli/gemini-cli.py ~/.local/bin/gemini-cli

# One-time auth: signs into gemini.google.com via browser, caches cookies
gemini-cli --init
# → Creates ~/.gemini-cli/auth.json with __Secure-1PSID + __Secure-1PSIDTS
```

Cookies expire ~30 days. Re-run `gemini-cli --init` to refresh.
The script warns when cookies are >25 days old.

### 3. Gmail SMTP (REQUIRED)
- Enable 2FA on a Gmail account
- Generate an **app password** at https://myaccount.google.com/apppasswords
- Set these env vars (copy from CONFIG.md template to `.env`):
  ```bash
  export YT_GEM_SMTP_USER=your.email@gmail.com
  export YT_GEM_SMTP_PASS=your16charapppassword
  export YT_GEM_RECIPIENT=recipient@email.com
  ```

## Configuration

### Step 1: Channels
Edit `channels.txt` — one YouTube channel URL per line.
```text
https://www.youtube.com/@ChannelHandle1
https://www.youtube.com/@ChannelHandle2
# Lines starting with # are ignored
```
Extracts the `@handle` from each URL automatically.

### Step 2: Gem System Prompt
Edit `GEM_SYSTEM_PROMPT.md` — this becomes the Gem's permanent system instruction.
The default is a Traditional Chinese finance analyst persona. Customize freely:
- Change language (English, Japanese, etc.)
- Change domain (tech, crypto, news, etc.)
- Change output format

### Step 3: Environment Variables
Copy the template and fill in:
```bash
cp CONFIG.md .env
# Edit .env with your values
# Source it before running: source .env
```

All configurable vars (with defaults):
| Variable | Default | Purpose |
|----------|---------|---------|
| `YT_GEM_SMTP_USER` | *(required)* | Gmail sender |
| `YT_GEM_SMTP_PASS` | *(required)* | Gmail app password |
| `YT_GEM_RECIPIENT` | *(required)* | Email recipient |
| `YT_GEM_GEMINI_GEM_ID` | *(auto-created)* | Gem ID to reuse |
| `YT_GEM_CHANNELS_FILE` | `channels.txt` | Path to channels list |
| `YT_GEM_PROMPT_FILE` | `GEM_SYSTEM_PROMPT.md` | Path to Gem system prompt |
| `YT_GEM_MODEL` | `flash` | Gem model: flash/pro/thinking |
| `YT_GEM_THINKING` | `extended` | Thinking tier: standard/plus/extended |
| `YT_GEM_HOURS_BACK` | `24` | Look-back window for new videos |
| `YT_GEM_MAX_CONCURRENT` | `3` | Parallel gem-cli calls |
| `YT_GEM_RETRIES` | `2` | Retries on transient failure |
| `YT_GEM_COOKIE_WARN_DAYS` | `25` | Warn when cookies near expiry |

## First Run — Auto Gem Creation

Run the script **once manually** to create the Gem:

```bash
source .env
python yt_gem_daily.py
```

On first run (no `YT_GEM_GEMINI_GEM_ID` set):
1. The script reads `GEM_SYSTEM_PROMPT.md`
2. Calls `gemini-cli --create-gem` to create a new Gem
3. Prints: `Save this Gem ID for future runs: export YT_GEM_GEMINI_GEM_ID=xxxxxxxxxxxx`
4. Proceeds to scrape channels and analyze videos

**Copy that Gem ID into your `.env`** so subsequent runs reuse the same Gem.

## Scheduling

### Option A: Hermes Cron (recommended)
```bash
hermes cron add \
  --name "YouTube Gem Daily Digest" \
  --schedule "0 10 * * *" \
  --script yt-gem-daily.py \
  --no-agent \
  --deliver local \
  --workdir /path/to/youtube-gem-digest
```

Also add the watchdog (alerts if silent >48h):
```bash
hermes cron add \
  --name "YouTube Gem Watchdog" \
  --schedule "0 */12 * * *" \
  --script yt_gem_watchdog.py \
  --no-agent \
  --deliver local \
  --workdir /path/to/youtube-gem-digest
```

**Important:** Copy `yt_gem_daily.py` and `yt_gem_watchdog.py` to `~/.hermes/scripts/`
so the cron scheduler can find them. Also copy `channels.txt` to the same directory,
or set `YT_GEM_CHANNELS_FILE` to point to the repo path.

### Option B: GitHub Actions
Edit `.github/workflows/daily.yml` to set your schedule.
Requires `GEMINI_SID` and `GEMINI_TS` as GitHub Secrets (needs refresh every ~30 days).

### Option C: Any Cron System
```bash
# crontab -e
0 10 * * * cd /path/to/youtube-gem-digest && source .env && python yt_gem_daily.py
```

## Customization Recipes

### Different Language
Edit `GEM_SYSTEM_PROMPT.md`:
```markdown
You are a senior financial analyst. Analyze videos in English...
```

### Different Domain (Tech Videos)
1. Change `GEM_SYSTEM_PROMPT.md` to a tech analyst persona
2. Change `channels.txt` to tech channels
3. Change the per-video prompt in `analyze_video_with_gem()` (line ~280)

### Different Schedule
Edit the cron expression in your cron setup. Common patterns:
- `0 10 * * *` — daily 10am
- `0 */6 * * *` — every 6 hours
- `0 9 * * 1-5` — weekdays 9am

### Multiple Topic Pipelines
Clone the repo twice with different configs:
```bash
cp -r youtube-gem-digest youtube-gem-digest-tech
# Edit channels.txt, GEM_SYSTEM_PROMPT.md, .env
# Set up separate cron jobs
```

## Files

| File | Purpose |
|------|---------|
| `yt_gem_daily.py` | Main script: scrape → analyze → email |
| `yt_gem_watchdog.py` | Watchdog: alerts if script silent >48h |
| `channels.txt` | YouTube channel URLs (user-editable) |
| `GEM_SYSTEM_PROMPT.md` | Gem system instruction (user-editable) |
| `CONFIG.md` | Environment variable template |
| `requirements.txt` | Python dependencies |
| `AGENTS.md` | This file — AI agent setup guide |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `__Secure-1PSID missing` | Cookies expired | `gemini-cli --init` |
| `gemini-cli: command not found` | gem-cli not on PATH | Install from lesterppo/hermes-gem-cli |
| `SMTP not configured` | Missing env vars | Set YT_GEM_SMTP_* in .env |
| `ytInitialData not found` | YouTube page structure changed | Check the lockupViewModel JSON path |
| `AUTH_EXPIRED` | Gem cookies stale | Re-run gemini-cli --init |
| Watchdog alerts | Script silent >48h | Check auth, network, run manually |
| Duplicate analyses | 24h window overlaps | Built-in dedup (48h window, auto-prune 7 days) |

## Privacy

- **No hardcoded credentials** — everything via env vars or config files
- **No hardcoded paths** — uses `os.path.expanduser()` and relative paths
- **No identifiers** — no author names, email addresses, or account IDs in source
- **channels.txt is plaintext** — public YouTube channel URLs
- **CONFIG.md is a template** — committed with placeholder values only
