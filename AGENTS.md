# YouTube Finance Daily Digest — Agent Setup Guide

AI-agent-oriented documentation. Read this to deploy the full pipeline from zero,
whether you are Claude Code, Codex, Hermes Agent, Cursor, or any other AI coding agent.

## What This Is

A cron-driven pipeline that:
1. Scrapes YouTube channels for recent videos via page scraping (works from any IP)
2. Sends each video URL directly to **Gemini** (Flash Extended Thinking) — Gemini resolves transcripts natively
3. Produces institutional-quality 7-dimension analysis per video
4. Compiles all analyses into an email report
5. Analysis persona loaded from `GEM_SYSTEM_PROMPT.md`; built-in institutional analyst fallback

## Architecture

```
GitHub Actions (daily 02:00 UTC / 10:00 HKT)
  ├── Page scraping (lockupViewModel) — parallel per channel
  ├── Per-video Gemini analysis — URL-direct, max 3 concurrent
  │   └── 7-dimension framework: Summary → Thesis → Data → Context → Risk → Insights → Assessment
  └── SMTP email delivery
```

## Prerequisites (what the user needs)

### 1. Gemini Cookies (webapi — free, no API key)
The user needs `__Secure-1PSID` and `__Secure-1PSIDTS` cookies from a Google account
signed into gemini.google.com. These expire ~30 days.

**Extract cookies:**
```bash
pip install gemini-webapi browser-cookie3 loguru
python gemini.py --init
# Creates ~/.gemini-cli/auth.json with SID and TS values
```

### 2. Gmail SMTP
A Gmail account with 2FA enabled and an app password generated at:
https://myaccount.google.com/apppasswords

### 3. YouTube Channels
A list of YouTube channel URLs to monitor (one per line in `channels.txt`).

## Setup Steps (for AI agents to execute)

### Step 1: Fork or Clone
```bash
git clone https://github.com/lesterppo/yt-finance-digest.git
cd yt-finance-digest
```

### Step 2: Verify Python
```bash
python3 --version  # needs 3.10+
pip install -r requirements.txt
pip install gemini-webapi browser-cookie3 loguru
```

### Step 3: Extract Gemini Cookies
```bash
python gemini.py --init
cat ~/.gemini-cli/auth.json
# Output: {"__Secure-1PSID": "abc...", "__Secure-1PSIDTS": "def..."}
```
Save these two values for the next step.

### Step 4: Configure Channels
Edit `channels.txt` — add one YouTube channel URL per line:
```
# Lines starting with # are comments
https://www.youtube.com/@ChannelOne
https://www.youtube.com/@ChannelTwo
```
URLs must use the `@handle` format. Percent-encoded handles are auto-decoded.

### Step 5: Customize Analysis Persona (Optional)
Edit `GEM_SYSTEM_PROMPT.md` to change the analysis style, language, or domain.
Delete the file to use the built-in 7-dimension institutional analyst prompt.
The built-in prompt works in any domain — finance is just the default.

### Step 6: Set GitHub Secrets
Go to repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret Name | Value | Source |
|------------|-------|--------|
| `GEMINI_SID` | `__Secure-1PSID` value | From `~/.gemini-cli/auth.json` |
| `GEMINI_TS` | `__Secure-1PSIDTS` value | From `~/.gemini-cli/auth.json` |
| `YT_GEM_SMTP_USER` | Gmail address | User's Gmail |
| `YT_GEM_SMTP_PASS` | Gmail app password | 16-char from myaccount.google.com/apppasswords |
| `YT_GEM_RECIPIENT` | Destination email | Where reports are sent |

### Step 7: Test Run
```bash
# Via GitHub CLI:
gh workflow run daily.yml

# Or manually trigger in browser:
# Actions → "YouTube Finance Daily Digest" → "Run workflow"
```
Monitor progress at: Actions tab → click the running workflow.
First email arrives in ~2 minutes.

### Step 8: Auto-Refresh Cookies (Recommended)
Google cookies expire every ~30 days. Set up a cron job to refresh them:
```bash
# Every Sunday at 3am — syncs local cookies to GitHub Secrets
python refresh_gh_secrets.py owner/repo
```
Requires `GH_TOKEN` env var with repo access.

### Step 9: CLI Contract (IMPORTANT for AI agents)
The bundled `gemini.py` writes the full response to a temp file and prints a
**pointer JSON** to stdout: `{"ok":true,"f":"/tmp/gemini-<ts>.json","s":N,"model":"..."}`.
The actual text lives in the file at `f` (JSON payload with a `text` key, or raw
markdown if `--json-out` is not set). Do NOT expect an inline `text` field in
stdout. Any consumer must read the file at `f`:
```python
import json, pathlib
p = json.loads(result.stdout)          # {"ok":true,"f":"..."}
text = json.loads(pathlib.Path(p["f"]).read_text())["text"] if p["f"].endswith(".json") \
       else pathlib.Path(p["f"]).read_text()
```

## Environment Variables Reference

All configurable via `YT_GEM_*` env vars. Set in GitHub Secrets or `.env` file.

| Variable | Default | Description |
|----------|---------|-------------|
| `YT_GEM_CHANNELS_FILE` | `channels.txt` | Path to channels list |
| `YT_GEM_PROMPT_FILE` | `GEM_SYSTEM_PROMPT.md` | Analysis persona file |
| `YT_GEM_AUTH_JSON` | `~/.gemini-cli/auth.json` | Gemini cookie path |
| `YT_GEM_GEMINI_CLI` | `gemini-cli` | Path to Gemini CLI binary |
| `YT_GEM_SMTP_USER` | *(required)* | Gmail address |
| `YT_GEM_SMTP_PASS` | *(required)* | Gmail app password |
| `YT_GEM_SMTP_SERVER` | `smtp.gmail.com` | SMTP server |
| `YT_GEM_SMTP_PORT` | `465` | SMTP port (SSL) |
| `YT_GEM_RECIPIENT` | *(required)* | Report destination email |
| `YT_GEM_MODEL` | `flash` | Gemini model: `flash`, `pro`, `lite` |
| `YT_GEM_THINKING` | `extended` | Thinking tier: `basic`, `plus`, `extended` |
| `YT_GEM_HOURS_BACK` | `24` | Look-back window for videos |
| `YT_GEM_TIMEOUT` | `300` | Seconds per Gemini call |
| `YT_GEM_MAX_CONCURRENT` | `3` | Parallel Gemini calls |
| `YT_GEM_RETRIES` | `2` | Retries on transient failure |
| `YT_GEM_TOTAL_TIMEOUT` | `900` | Hard script timeout |
| `YT_GEM_SEEN_FILE` | `~/.hermes/yt_gem_seen.json` | Dedup database |
| `YT_GEM_SEEN_WINDOW_HOURS` | `48` | Skip videos in this window |
| `YT_GEM_SEEN_PRUNE_DAYS` | `7` | Auto-clean old entries |
| `YT_GEM_HEARTBEAT_FILE` | `~/.hermes/yt_gem_heartbeat` | Watchdog heartbeat |
| `YT_GEM_COOKIE_WARN_DAYS` | `25` | Cookie expiry warning threshold |

## Files

| File | Purpose |
|------|---------|
| `yt_gem_daily.py` | Main script — scraping, Gemini analysis, email |
| `gemini.py` | Bundled Gemini CLI (gemini-webapi, normal chat, cookie auth) |
| `yt_gem_watchdog.py` | Alerts if main script silent >48h |
| `refresh_gh_secrets.py` | Syncs auth.json cookies to GitHub Secrets via gh CLI |
| `channels.txt` | YouTube channel URLs (user-editable, one per line) |
| `GEM_SYSTEM_PROMPT.md` | Analysis persona (user-editable, any language/domain) |
| `CONFIG.md` | Environment variable reference |
| `AGENTS.md` | This file — complete setup guide for AI agents |

## Customization

- **Different language**: edit `GEM_SYSTEM_PROMPT.md` — change the output language
- **Different domain**: change the persona prompt + channels (tech reviews, news analysis, academic papers)
- **Different schedule**: edit `cron:` in `.github/workflows/daily.yml` (standard cron syntax)
- **Different model**: set `YT_GEM_MODEL=pro` in workflow env for deeper but slower analysis
- **No persona file**: delete `GEM_SYSTEM_PROMPT.md` — built-in 7-dimension prompt activates automatically

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| All analyses FAIL (0/4 OK, exit=0) | gemini.py outputs pointer JSON, script expected inline text | Fixed in `yt_gem_daily.py`: it now reads the response file at pointer key `f` (`{"ok":true,"f":"<path>"}`) and falls back to inline `text`. If you see this again, check the `f` key handling. |
| `auth.json not found` | GEMINI_SID/TS secrets missing | Re-run `gemini.py --init` and update secrets |
| `gemini-cli exit=1` | Cookies expired (~30 days) | Run `gemini.py --init` again, update GitHub Secrets |
| `SMTP not configured` | YT_GEM_SMTP_* secrets missing | Add SMTP_USER, SMTP_PASS, RECIPIENT to GitHub Secrets |
| No videos scraped | Channel URL format wrong | Must use `https://www.youtube.com/@Handle` format |
| `ytInitialData not found` | YouTube page structure changed | Check if page scraping still works; may need selector update |
| Email not received | Gmail app password revoked | Regenerate at myaccount.google.com/apppasswords |
| All analyses FAIL | Gemini rate-limited | Reduce MAX_CONCURRENT, check cookie validity |

## GitHub Action Schedule

Default: daily at 02:00 UTC (10:00 HKT). Edit `.github/workflows/daily.yml`:
```yaml
on:
  schedule:
    - cron: "0 2 * * *"  # Change this line
```
Also supports manual trigger (`workflow_dispatch`).

## Running Locally

```bash
# Set env vars
export GEMINI_SID="your-sid-value"
export GEMINI_TS="your-ts-value"
export YT_GEM_SMTP_USER="your.email@gmail.com"
export YT_GEM_SMTP_PASS="your16charapppassword"
export YT_GEM_RECIPIENT="recipient@email.com"

# Run
python yt_gem_daily.py
```
