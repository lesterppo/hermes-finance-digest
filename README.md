# YouTube Finance Daily Digest

Daily automated deep analysis of YouTube videos using Gemini (Flash Extended Thinking) with email delivery. Fully configurable — no hardcoded credentials.

## How It Works

1. Scrapes your YouTube channels for videos published in the past day
2. Each video is sent individually to Gemini for deep analysis
3. Analysis persona is loaded from `GEM_SYSTEM_PROMPT.md` and injected inline
4. All analyses compiled into an email report

## Quick Start

```bash
# 1. Install
git clone https://github.com/lesterppo/yt-gem-digest.git
cd yt-gem-digest
pip install -r requirements.txt

# 2. Setup Gemini auth (one-time)
pip install gemini-webapi browser-cookie3 loguru
gemini-cli --init   # browser-based cookie extraction

# 3. Configure
cp CONFIG.md .env   # edit with your Gmail SMTP + recipient
# Edit channels.txt with your YouTube channels
# Edit GEM_SYSTEM_PROMPT.md to customize analysis style

# 4. Run
source .env
python yt_gem_daily.py
```

## Customization

- **Channels**: edit `channels.txt` (one YouTube URL per line)
- **Analysis style**: edit `GEM_SYSTEM_PROMPT.md` (any language, any domain)
- **Schedule**: set up GitHub Actions (included) or a cron job
- **Model**: set `YT_GEM_MODEL=pro` for deeper analysis, `flash` for speed (default)

## Dependencies

- **gemini.py** — bundled Gemini CLI (gemini-webapi, cookie auth, no API key)
- Python 3.10+, requests, youtube-transcript-api
- Gmail account with app password for SMTP

Full setup guide: [AGENTS.md](AGENTS.md)
