# YouTube Gem Daily Digest — Agent Setup Guide

AI-agent-oriented documentation. Read this to set up the full pipeline from zero.

## What This Is

A cron-driven pipeline that:
1. Scrapes YouTube channels for recent videos via page scraping
2. Sends each video individually to a **Gemini Gem** (Flash Extended Thinking) for deep analysis
3. Compiles all analyses into an email report
4. Auto-creates the Gem on first run from `GEM_SYSTEM_PROMPT.md`

## Architecture

```
GitHub Actions (daily 10am HKT)
  ├── page scraping (lockupViewModel) — 8 channels parallel
  ├── per-video Gemini Gem analysis (max 3 concurrent)
  └── SMTP email delivery
```

## Prerequisites

### 1. gem-cli
This project bundles `gem-cli.py` (from [lesterppo/hermes-gem-cli](https://github.com/lesterppo/hermes-gem-cli))
which is copied to `~/.local/bin/gem-cli` in the workflow. The script auto-detects it.

### 2. Gemini Cookies
Get `__Secure-1PSID` and `__Secure-1PSIDTS` cookies from a Google account
signed into gemini.google.com:
```bash
git clone https://github.com/lesterppo/hermes-gem-cli.git
python hermes-gem-cli/gem-cli.py --init
# → Creates ~/.gemini-cli/auth.json
```

### 3. Gmail SMTP
Enable 2FA → generate app password at https://myaccount.google.com/apppasswords

### 4. Gemini Gem
Create one manually or let the script auto-create it on first run:
```bash
echo "You are a financial analyst..." | gem-cli --create-gem "Finance Analyst"
# → Save the Gem ID for YT_GEM_GEMINI_GEM_ID
```

## Setup Steps (for an AI agent)

### Step 1: Clone
```bash
git clone https://github.com/lesterppo/yt-gem-digest.git
cd yt-gem-digest
```

### Step 2: Configure
Edit these files:
- `channels.txt` — one YouTube URL per line
- `GEM_SYSTEM_PROMPT.md` — the Gem's system instruction (any language, any domain)

### Step 3: Set GitHub Secrets
Repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|--------|-------|
| `GEMINI_SID` | `__Secure-1PSID` from `~/.gemini-cli/auth.json` |
| `GEMINI_TS` | `__Secure-1PSIDTS` from `~/.gemini-cli/auth.json` |
| `YT_GEM_SMTP_USER` | Gmail address |
| `YT_GEM_SMTP_PASS` | Gmail app password |
| `YT_GEM_RECIPIENT` | Destination email |
| `YT_GEM_GEMINI_GEM_ID` | Gem ID |

### Step 4: Test
Actions → YouTube Gem Daily Digest → Run workflow

### Step 5: Auto-Refresh Cookies (optional)
```bash
# Every Sunday at 3am — syncs local cookies to GitHub Secrets
python refresh_gh_secrets.py owner/repo
```

## Files

| File | Purpose |
|------|---------|
| `yt_gem_daily.py` | Main script — scraping, Gem analysis, email |
| `gem-cli.py` | Bundled gem-cli (positional GEM ID, `--json-out`) |
| `yt_gem_watchdog.py` | Alerts if main script silent >48h |
| `refresh_gh_secrets.py` | Syncs auth.json cookies to GitHub Secrets |
| `channels.txt` | YouTube channel URLs (user-editable) |
| `GEM_SYSTEM_PROMPT.md` | Gem system instruction (user-editable) |
| `CONFIG.md` | Environment variable reference |
| `.github/workflows/daily.yml` | GitHub Actions schedule |

## Customization

- **Different language**: edit `GEM_SYSTEM_PROMPT.md`
- **Different domain**: change the Gem prompt + channels
- **Different schedule**: edit `cron:` in `.github/workflows/daily.yml`
- **Different model**: set `YT_GEM_MODEL=pro` in workflow env

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `auth.json not found` | GEMINI_SID/TS secrets missing or expired |
| `gem-cli exit=1` | Cookies expired (~30 days) — refresh and update secrets |
| `SMTP not configured` | YT_GEM_SMTP_* secrets missing |
| No videos scraped | Check channels.txt, check YouTube page structure |
