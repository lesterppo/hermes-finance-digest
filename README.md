# YouTube Gem Daily Digest

Daily automated deep analysis of YouTube videos using a dedicated Gemini Gem with email delivery.

## How It Works

1. Scrapes your YouTube channels for videos published in the past day
2. Sends each video individually to a Gemini Gem (Flash Extended Thinking)
3. The Gem analyzes each video using its persistent system instruction
4. Compiles all analyses into an email report

## Quick Start

```bash
# 1. Install
git clone https://github.com/lesterppo/youtube-gem-digest.git
cd youtube-gem-digest
pip install -r requirements.txt

# 2. Install gem-cli (REQUIRED)
# See: https://github.com/lesterppo/hermes-gem-cli
gemini-cli --init   # browser-based auth

# 3. Configure
cp CONFIG.md .env   # edit with your Gmail SMTP + recipient
# Edit channels.txt with your YouTube channels
# Edit GEM_SYSTEM_PROMPT.md to customize analysis

# 4. Run
source .env
python yt_gem_daily.py
```

On first run, the script creates a Gemini Gem from your `GEM_SYSTEM_PROMPT.md`.
Save the printed Gem ID into your `.env` for subsequent runs.

## Customization

- **Channels**: edit `channels.txt` (one YouTube URL per line)
- **Analysis style**: edit `GEM_SYSTEM_PROMPT.md` (any language, any domain)
- **Schedule**: set up a cron job (see AGENTS.md for Hermes cron / GitHub Actions)
- **Model**: set `YT_GEM_MODEL=pro` for deeper analysis, `flash` for speed

## Dependencies

- **[lesterppo/hermes-gem-cli](https://github.com/lesterppo/hermes-gem-cli)** — Gemini Gem CLI
- Python 3.10+, requests, youtube-transcript-api
- Gmail account with app password for SMTP

Full setup guide: [AGENTS.md](AGENTS.md)
