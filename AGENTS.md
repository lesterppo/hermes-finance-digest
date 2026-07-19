# YouTube Finance Daily Digest — Agent Setup Guide

AI-agent-oriented documentation. Read this to set up the full pipeline from zero.

## What This Is

A cron-driven pipeline that:
1. Scrapes YouTube channels for recent videos via page scraping
2. Sends each video individually to **Gemini** (Flash Extended Thinking, normal chat) for deep analysis
3. Compiles all analyses into an email report
4. Analysis persona is loaded from `GEM_SYSTEM_PROMPT.md` and injected inline

## Architecture

```
GitHub Actions (daily 10am HKT)
  ├── page scraping (lockupViewModel) — parallel per channel
  ├── per-video Gemini analysis (max 3 concurrent)
  └── SMTP email delivery
```

## Prerequisites

### 1. Gemini Cookies (webapi, no API key)
Get `__Secure-1PSID` and `__Secure-1PSIDTS` cookies from a Google account
signed into gemini.google.com:
```bash
pip install gemini-webapi browser-cookie3 loguru
python gemini.py --init
# → Creates ~/.gemini-cli/auth.json
```
Or set `GEMINI_SID` + `GEMINI_TS` env vars directly.

### 2. Gmail SMTP
Enable 2FA → generate app password at https://myaccount.google.com/apppasswords

### 3. Analysis Persona
Customize `GEM_SYSTEM_PROMPT.md` — this is prepended to every analysis prompt.
Works with any language, any domain (finance is just an example).

## Setup Steps (for an AI agent)

### Step 1: Clone
```bash
git clone https://github.com/lesterppo/yt-gem-digest.git
cd yt-gem-digest
```

### Step 2: Configure
Edit these files:
- `channels.txt` — one YouTube URL per line
- `GEM_SYSTEM_PROMPT.md` — the analysis persona (any language, any domain)

### Step 3: Set GitHub Secrets
Repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|--------|-------|
| `GEMINI_SID` | `__Secure-1PSID` from `~/.gemini-cli/auth.json` |
| `GEMINI_TS` | `__Secure-1PSIDTS` from `~/.gemini-cli/auth.json` |
| `YT_GEM_SMTP_USER` | Gmail address |
| `YT_GEM_SMTP_PASS` | Gmail app password |
| `YT_GEM_RECIPIENT` | Destination email |

### Step 4: Test
Actions → YouTube Finance Daily Digest → Run workflow

### Step 5: Auto-Refresh Cookies (optional)
```bash
# Every Sunday at 3am — syncs local cookies to GitHub Secrets
python refresh_gh_secrets.py owner/repo
```

## Files

| File | Purpose |
|------|---------|
| `yt_gem_daily.py` | Main script — scraping, Gemini analysis, email |
| `gemini.py` | Bundled Gemini CLI (gemini-webapi, normal chat) |
| `yt_gem_watchdog.py` | Alerts if main script silent >48h |
| `refresh_gh_secrets.py` | Syncs auth.json cookies to GitHub Secrets |
| `channels.txt` | YouTube channel URLs (user-editable) |
| `GEM_SYSTEM_PROMPT.md` | Analysis persona (user-editable) |
| `CONFIG.md` | Environment variable reference |
| `.github/workflows/daily.yml` | GitHub Actions schedule |

## Customization

- **Different language**: edit `GEM_SYSTEM_PROMPT.md`
- **Different domain**: change the persona prompt + channels
- **Different schedule**: edit `cron:` in `.github/workflows/daily.yml`
- **Different model**: set `YT_GEM_MODEL=pro` in workflow env

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `auth.json not found` | GEMINI_SID/TS secrets missing or expired |
| `gemini-cli exit=1` | Cookies expired (~30 days) — refresh and update secrets |
| `SMTP not configured` | YT_GEM_SMTP_* secrets missing |
| No videos scraped | Check channels.txt, check YouTube page structure |
