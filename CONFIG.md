# YouTube Gem Daily Digest — Configuration
# Copy this file to .env and fill in your values.
# All variables are read at runtime by yt_gem_daily.py.

# ── REQUIRED ────────────────────────────────────────────────────────────────
# Gmail SMTP credentials (use an app password, not your real password)
YT_GEM_SMTP_USER=your.email@gmail.com
YT_GEM_SMTP_PASS=your16charapppassword
YT_GEM_RECIPIENT=recipient@email.com

# ── OPTIONAL (auto-detected) ────────────────────────────────────────────────
# Gemini Gem ID — set after first run (the script creates one automatically)
# YT_GEM_GEMINI_GEM_ID=xxxxxxxxxxxx

# Paths — defaults are relative to the script directory
# YT_GEM_CHANNELS_FILE=channels.txt
# YT_GEM_PROMPT_FILE=GEM_SYSTEM_PROMPT.md
# YT_GEM_AUTH_JSON=~/.gemini-cli/auth.json

# ── GEMINI GEM ──────────────────────────────────────────────────────────────
# Model: flash (fast), pro (deeper), thinking (reasoning)
# YT_GEM_MODEL=flash
# YT_GEM_THINKING=extended

# ── TIMING ──────────────────────────────────────────────────────────────────
# YT_GEM_HOURS_BACK=24          # hours to look back for new videos
# YT_GEM_TIMEOUT=300            # seconds per gem-cli call
# YT_GEM_MAX_CONCURRENT=3       # parallel gem-cli calls
# YT_GEM_RETRIES=2              # retries on transient failures
# YT_GEM_TOTAL_TIMEOUT=900      # hard script timeout (seconds)

# ── DEDUP ───────────────────────────────────────────────────────────────────
# YT_GEM_SEEN_FILE=~/.hermes/yt_gem_seen.json
# YT_GEM_SEEN_WINDOW_HOURS=48   # skip videos seen in this window
# YT_GEM_SEEN_PRUNE_DAYS=7      # auto-clean entries older than this

# ── MONITORING ──────────────────────────────────────────────────────────────
# YT_GEM_HEARTBEAT_FILE=~/.hermes/yt_gem_heartbeat
# YT_GEM_COOKIE_WARN_DAYS=25    # warn when auth cookies approach expiry
