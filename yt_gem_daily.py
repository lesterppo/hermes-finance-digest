#!/usr/bin/env python3
"""
YouTube Gem Daily Digest — Configurable Edition

Scrapes YouTube channels for recent videos, sends each video individually
to a dedicated Gemini Gem (Flash Extended Thinking) for deep analysis,
compiles all analyses into an email report.

Fully configurable via environment variables and local files.
No hardcoded credentials, paths, or Gem IDs.

Dependencies:
  - lesterppo/hermes-gem-cli (installed at ~/.local/bin/gemini-cli or PATH)
  - Python packages: requests, youtube-transcript-api
  - Gmail account with app password for SMTP

Setup: see AGENTS.md for full walkthrough.
"""

import json
import os
import re
import smtplib
import subprocess
import sys
import time
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional


# ── Configuration (all overridable via environment) ────────────────────────

# Paths — relative to this script's directory unless overridden
SCRIPT_DIR = Path(__file__).resolve().parent

def _env_path(key: str, default_rel: str) -> str:
    """Resolve a path from env var, falling back to a path relative to script dir."""
    if os.environ.get(key):
        return os.path.expanduser(os.environ[key])
    return str(SCRIPT_DIR / default_rel)

CHANNELS_FILE    = _env_path("YT_GEM_CHANNELS_FILE", "channels.txt")
GEM_PROMPT_FILE  = _env_path("YT_GEM_PROMPT_FILE", "GEM_SYSTEM_PROMPT.md")

# Gemini Gem — create one on first run if GEM_ID not set
GEM_ID = os.environ.get("YT_GEM_GEMINI_GEM_ID", "")  # set after creation

# gem-cli — auto-detect from PATH or common locations
def _find_gemcli() -> str:
    for p in [os.environ.get("YT_GEM_GEMINI_CLI", ""),
              os.path.expanduser("~/.local/bin/gemini-cli"),
              "gemini-cli"]:
        if p and (Path(p).exists() or p == "gemini-cli"):
            return p
    return "gemini-cli"  # last resort — will fail with clear error

GEMINI_CLI = _find_gemcli()

# Auth — browser cookies (preferred) or explicit env vars
AUTH_JSON = os.path.expanduser(
    os.environ.get("YT_GEM_AUTH_JSON", "~/.gemini-cli/auth.json"))

# Gmail SMTP — REQUIRED, no defaults
SMTP_USER = os.environ.get("YT_GEM_SMTP_USER", "")
SMTP_PASS = os.environ.get("YT_GEM_SMTP_PASS", "")
SMTP_SERVER = os.environ.get("YT_GEM_SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("YT_GEM_SMTP_PORT", "465"))
RECIPIENT = os.environ.get("YT_GEM_RECIPIENT", "")

# Gem model config
GEM_MODEL = os.environ.get("YT_GEM_MODEL", "flash")
GEM_THINKING = os.environ.get("YT_GEM_THINKING", "extended")

# Timing
HOURS_BACK = int(os.environ.get("YT_GEM_HOURS_BACK", "24"))
GEMINI_TIMEOUT = int(os.environ.get("YT_GEM_TIMEOUT", "300"))
MAX_CONCURRENT_GEM = int(os.environ.get("YT_GEM_MAX_CONCURRENT", "3"))
GEMINI_RETRIES = int(os.environ.get("YT_GEM_RETRIES", "2"))
TOTAL_TIMEOUT = int(os.environ.get("YT_GEM_TOTAL_TIMEOUT", "900"))
COOKIE_WARN_DAYS = int(os.environ.get("YT_GEM_COOKIE_WARN_DAYS", "25"))

# Dedup
SEEN_FILE = os.path.expanduser(
    os.environ.get("YT_GEM_SEEN_FILE", "~/.hermes/yt_gem_seen.json"))
SEEN_WINDOW_HOURS = int(os.environ.get("YT_GEM_SEEN_WINDOW_HOURS", "48"))
SEEN_PRUNE_DAYS = int(os.environ.get("YT_GEM_SEEN_PRUNE_DAYS", "7"))

# Heartbeat
HEARTBEAT_FILE = os.path.expanduser(
    os.environ.get("YT_GEM_HEARTBEAT_FILE", "~/.hermes/yt_gem_heartbeat"))


# ── Helpers ────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _load_seen_videos() -> dict[str, str]:
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_seen_videos(seen: dict[str, str]) -> None:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=SEEN_PRUNE_DAYS)).isoformat()
    pruned = {vid: ts for vid, ts in seen.items() if ts >= cutoff}
    with open(SEEN_FILE, "w") as f:
        json.dump(pruned, f, indent=2)


def _filter_duplicates(videos: list[dict], seen: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
    now = datetime.now(timezone.utc)
    window_cutoff = (now - timedelta(hours=SEEN_WINDOW_HOURS)).isoformat()
    new_videos = []
    skipped = 0
    for v in videos:
        vid = v["video_id"]
        if vid in seen and seen[vid] >= window_cutoff:
            skipped += 1
            continue
        if vid not in seen:
            seen[vid] = now.isoformat()
        new_videos.append(v)
    if skipped:
        log(f"  Skipped {skipped} duplicate videos (seen in last {SEEN_WINDOW_HOURS}h)")
    return new_videos, seen


def _touch_heartbeat() -> None:
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except IOError:
        pass


def load_channels(path: str) -> dict[str, str]:
    """Parse channels file. Returns {display_name: @handle} dict.
    One YouTube URL per line, # for comments."""
    channels: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.search(r"@([a-zA-Z0-9_%-]+)", line)
            if m:
                raw_handle = m.group(1)
                handle = urllib.parse.unquote(raw_handle)
                channels[handle] = handle
    return channels


def parse_relative_time(text: str) -> Optional[datetime]:
    if not text:
        return None
    now = datetime.now(timezone.utc)
    text = text.lower().replace("streamed ", "").replace("premiered ", "")
    m = re.match(r"(\d+)\s*(minute|hour|day|week|month|year)s?\s*ago", text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    deltas = {
        "minute": timedelta(minutes=n), "hour": timedelta(hours=n),
        "day": timedelta(days=n), "week": timedelta(weeks=n),
        "month": timedelta(days=n * 30), "year": timedelta(days=n * 365),
    }
    return now - deltas[unit]


def scrape_channel_videos(handle: str, cutoff: datetime) -> list[dict]:
    """Scrape @handle/videos page for recent videos via lockupViewModel JSON."""
    url = f"https://www.youtube.com/@{handle}/videos"
    resp = requests.get(url, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    resp.raise_for_status()
    html = resp.text

    match = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
    if not match:
        match = re.search(r"ytInitialData\s*=\s*(\{.*?\});", html, re.DOTALL)
    if not match:
        log(f"  {handle}: ytInitialData not found in page")
        return []

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        log(f"  {handle}: JSON decode error: {e}")
        return []

    tabs = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
    videos: list[dict] = []

    for tab in tabs:
        contents = tab.get("tabRenderer", {}).get("content", {}).get("richGridRenderer", {}).get("contents", [])
        for item in contents:
            rich = item.get("richItemRenderer", {})
            lvm = rich.get("content", {}).get("lockupViewModel", {})
            if not lvm:
                continue
            video_id = lvm.get("contentId", "")
            if not video_id:
                continue

            md = lvm.get("metadata", {}).get("lockupMetadataViewModel", {})
            title = md.get("title", {}).get("content", "")
            meta_rows = (md.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", []))
            published_text = ""
            for row in meta_rows:
                for part in row.get("metadataParts", []):
                    txt = part.get("text", {}).get("content", "")
                    if "ago" in txt:
                        published_text = txt
                        break
                if published_text:
                    break

            published_dt = parse_relative_time(published_text)
            if published_dt and published_dt >= cutoff:
                videos.append({
                    "channel": handle,
                    "title": title,
                    "video_id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "published": published_dt.isoformat(),
                })
    return videos


def fetch_transcript(video_id: str) -> Optional[str]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        segments = api.fetch(video_id)
        return " ".join(seg.text for seg in segments)
    except Exception:
        return None


def _fetch_video_description(video_id: str) -> Optional[str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        resp = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        match = re.search(r"var ytInitialPlayerResponse\s*=\s*(\{.*?\});\s*\n", resp.text, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
            desc = data.get("videoDetails", {}).get("shortDescription", "")
            return desc.strip() if desc else None
    except Exception:
        pass
    return None


# ── Gem Management ─────────────────────────────────────────────────────────

def load_gem_system_prompt(path: str) -> str:
    """Load the Gem system instruction from a markdown file.
    The first YAML frontmatter block (if any) is skipped — only the body is used."""
    if not os.path.exists(path):
        log(f"WARNING: Gem prompt file not found: {path}")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Skip YAML frontmatter if present
    if content.startswith("---"):
        parts = content.split("---", 2)
        content = parts[2] if len(parts) > 2 else content
    return content.strip()


def create_gemcli_gem(prompt_file: str, gem_name: str = "YT Finance Analyst") -> str:
    """Create a Gemini Gem via gem-cli and return its ID.
    Uses the content of prompt_file as the Gem's system instruction."""
    if not os.path.exists(prompt_file):
        raise FileNotFoundError(f"Gem prompt file not found: {prompt_file}")
    system_prompt = load_gem_system_prompt(prompt_file)
    if not system_prompt:
        raise ValueError(f"Gem prompt file is empty: {prompt_file}")

    log(f"Creating Gemini Gem from {prompt_file} ({len(system_prompt)} chars)...")
    result = subprocess.run(
        [GEMINI_CLI, "--create-gem", gem_name],
        input=system_prompt,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gem-cli --create-gem failed: {result.stderr[:300]}")

    try:
        data = json.loads(result.stdout.strip())
        if data.get("ok") and data.get("id"):
            gem_id = data["id"]
            log(f"Created Gem: {gem_id} ({data.get('name', gem_name)})")
            return gem_id
        raise RuntimeError(f"Unexpected gem-cli output: {result.stdout[:200]}")
    except json.JSONDecodeError:
        raise RuntimeError(f"Could not parse gem-cli output: {result.stdout[:200]}")


def analyze_video_with_gem(video: dict, gem_id: str, auth: dict,
                           timeout: int, max_retries: int) -> dict:
    """Send one video to Gemini Gem for deep analysis with retry."""
    env = os.environ.copy()
    env["GEMINI_SID"] = auth.get("__Secure-1PSID", "")
    env["GEMINI_TS"] = auth.get("__Secure-1PSIDTS", "")

    prompt = f"""請深入分析以下財經影片（繁體中文，至少300字）：

頻道：{video['channel']}
標題：{video['title']}
連結：{video['url']}
發布時間：{video['published']}

影片內容（字幕/描述）：
{video.get('content', '(無內容)')[:4000]}

請提供：
1. 核心觀點與邏輯鏈條
2. 數據支撐與市場背景
3. 投資含義與風險提示"""

    output_file = f"/tmp/gem_video_{video['video_id'][:8]}.md"
    last_error = ""

    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                [GEMINI_CLI, "-g", gem_id,
                 "-m", GEM_MODEL, "--thinking", GEM_THINKING,
                 "-o", output_file, "--json", "--brief"],
                input=prompt, capture_output=True, text=True,
                timeout=timeout, env=env,
            )

            if result.returncode == 0:
                try:
                    stdout_json = json.loads(result.stdout.strip())
                    if stdout_json.get("ok") and stdout_json.get("f"):
                        out_path = stdout_json["f"]
                        if os.path.exists(out_path):
                            with open(out_path, "r", encoding="utf-8") as f:
                                analysis = f.read()
                            if len(analysis) > 50:
                                return {"video_id": video["video_id"], "title": video["title"],
                                        "channel": video["channel"], "url": video["url"],
                                        "analysis": analysis, "ok": True}
                except (json.JSONDecodeError, KeyError):
                    pass

                if os.path.exists(output_file):
                    with open(output_file, "r", encoding="utf-8") as f:
                        analysis = f.read()
                    if len(analysis) > 50:
                        return {"video_id": video["video_id"], "title": video["title"],
                                "channel": video["channel"], "url": video["url"],
                                "analysis": analysis, "ok": True}

            last_error = f"exit={result.returncode} stderr: {result.stderr[:200]}"

        except subprocess.TimeoutExpired:
            last_error = "timeout"
        except Exception as e:
            last_error = str(e)

        if attempt < max_retries and "AUTH_EXPIRED" not in last_error:
            delay = (attempt + 1) * 10
            log(f"  Retry {attempt + 1}/{max_retries} for {video['title'][:40]}... "
                f"({last_error[:80]}, waiting {delay}s)")
            time.sleep(delay)

    return {"video_id": video["video_id"], "title": video["title"],
            "channel": video["channel"], "url": video["url"],
            "analysis": f"分析失敗（{max_retries + 1}次嘗試）: {last_error}",
            "ok": False}


# ── Email ──────────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str) -> None:
    if not SMTP_USER or not SMTP_PASS or not RECIPIENT:
        log("ERROR: SMTP not configured — set YT_GEM_SMTP_USER, YT_GEM_SMTP_PASS, YT_GEM_RECIPIENT")
        return
    try:
        msg = MIMEText(body, _charset="utf-8", _subtype="plain")
        msg["From"] = SMTP_USER
        msg["To"] = RECIPIENT
        msg["Subject"] = subject
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        log(f"Email sent to {RECIPIENT}")
    except Exception as e:
        log(f"ERROR: Email send failed: {e}")
        traceback.print_exc()


def _send_status_email(channels: dict, start_time: datetime) -> None:
    date_str = start_time.strftime("%Y年%m月%d日")
    channel_list = "\n".join(f"  • {h}" for h in channels)
    subject = f"📊 財經頻道每日狀態 — {date_str} (無新影片)"
    body = f"""財經頻道每日狀態報告
日期：{date_str}
分析引擎：Gemini Gem ({GEM_MODEL} + {GEM_THINKING} thinking, ID: {GEM_ID or '(auto-created)'})

監控頻道（{len(channels)}個）：
{channel_list}

今日影片：0 部（過去24小時無新影片）

系統狀態：正常運行 ✓
檢查時間：{start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
    _send_email(subject, body)
    log(f"Status email sent to {RECIPIENT}")


def _send_report_email(channels: dict, results: list[dict],
                       ok_count: int, start_time: datetime) -> None:
    date_str = start_time.strftime("%Y年%m月%d日")
    channel_list = "\n".join(f"  • {h}" for h in channels)

    video_sections: list[str] = []
    for i, r in enumerate(results, 1):
        status = "✓" if r["ok"] else "✗"
        video_sections.append(
            f"\n{'─' * 60}\n"
            f"【影片{i}】{status} {r['channel']}\n"
            f"標題：{r['title']}\n"
            f"連結：{r['url']}\n"
            f"{'─' * 60}\n"
            f"{r['analysis']}\n"
        )

    body = f"""財經頻道每日深度分析報告
日期：{date_str}
分析引擎：Gemini Gem — {GEM_MODEL} + {GEM_THINKING} thinking (ID: {GEM_ID or '(auto-created)'})
分析方式：逐片獨立深度分析

監控頻道（{len(channels)}個）：
{channel_list}

今日影片：{len(results)} 部（成功分析 {ok_count}/{len(results)}）

{''.join(video_sections)}

{'=' * 60}

📌 說明：
• 分析引擎：Gemini Flash Extended Thinking（經由 Gem {GEM_ID or '(auto-created)'}）
• 內容來源：YouTube 頁面爬取（字幕 + 影片描述）
• 每個影片獨立發送至 Gem 進行深度分析
"""
    subject = f"📊 財經頻道每日深度分析 — {date_str}"
    _send_email(subject, body)


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    start_time = datetime.now()
    log("Starting YouTube Gem Daily Digest")

    # 1. Validate config
    if not os.path.exists(CHANNELS_FILE):
        log(f"ERROR: Channels file not found: {CHANNELS_FILE}")
        log("Create channels.txt with one YouTube channel URL per line.")
        return 1

    channels = load_channels(CHANNELS_FILE)
    if not channels:
        log("ERROR: No channels found in channels file")
        return 1
    log(f"Loaded {len(channels)} channels")

    # 2. Load or create Gem
    global GEM_ID
    if not GEM_ID:
        if not os.path.exists(GEM_PROMPT_FILE):
            log(f"ERROR: Gem prompt file not found: {GEM_PROMPT_FILE}")
            log("Create GEM_SYSTEM_PROMPT.md with the Gem's system instruction.")
            return 1
        try:
            GEM_ID = create_gemcli_gem(GEM_PROMPT_FILE)
            log(f"Save this Gem ID for future runs: export YT_GEM_GEMINI_GEM_ID={GEM_ID}")
        except Exception as e:
            log(f"ERROR creating Gem: {e}")
            return 1

    # 3. Load auth
    if not os.path.exists(AUTH_JSON):
        log(f"ERROR: {AUTH_JSON} not found — run: gemini-cli --init")
        return 1
    with open(AUTH_JSON) as f:
        auth = json.load(f)
    if not auth.get("__Secure-1PSID"):
        log("ERROR: __Secure-1PSID missing from auth.json")
        return 1

    auth_mtime = os.path.getmtime(AUTH_JSON)
    auth_age_days = (time.time() - auth_mtime) / 86400
    if auth_age_days > COOKIE_WARN_DAYS:
        log(f"WARNING: auth.json is {auth_age_days:.0f} days old — run: gemini-cli --init")

    # 4. Scrape channels (parallel)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    all_videos: list[dict] = []

    def _fetch_one(handle: str) -> list[dict]:
        try:
            return scrape_channel_videos(handle, cutoff)
        except Exception as e:
            log(f"  {handle}: ERROR — {e}")
            return []

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_fetch_one, h): h for h in channels}
        for fut in as_completed(futures):
            handle = futures[fut]
            vids = fut.result()
            all_videos.extend(vids)
            log(f"  {handle}: {len(vids)} new videos")

    if not all_videos:
        log("No new videos in the last 24 hours — sending status email")
        _send_status_email(channels, start_time)
        _touch_heartbeat()
        return 0

    # 5. Dedup
    seen = _load_seen_videos()
    all_videos, seen = _filter_duplicates(all_videos, seen)
    _save_seen_videos(seen)

    if not all_videos:
        log("All scraped videos already analyzed — sending status email")
        _send_status_email(channels, start_time)
        _touch_heartbeat()
        return 0

    log(f"Total: {len(all_videos)} videos to analyze (after dedup)")

    # 6. Enrich (parallel)
    def _enrich_one(v: dict) -> dict:
        transcript = fetch_transcript(v["video_id"])
        if transcript:
            v["content"] = transcript[:6000]
            v["has_transcript"] = True
        else:
            try:
                desc = _fetch_video_description(v["video_id"])
                v["content"] = desc[:4000] if desc else "(無內容)"
            except Exception:
                v["content"] = "(無內容)"
            v["has_transcript"] = False
        return v

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_enrich_one, v): v for v in all_videos}
        for fut in as_completed(futures):
            fut.result()

    # 7. Analyze with Gem (parallel, limited concurrency)
    log(f"Calling Gemini Gem ({GEM_ID[:12]}...) for each video (max {MAX_CONCURRENT_GEM} concurrent)")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_GEM) as ex:
        futures = {
            ex.submit(analyze_video_with_gem, v, GEM_ID, auth, GEMINI_TIMEOUT, GEMINI_RETRIES): v
            for v in all_videos
        }
        for fut in as_completed(futures):
            v = futures[fut]
            result = fut.result()
            results.append(result)
            status = "OK" if result["ok"] else "FAIL"
            log(f"  [{status}] {v['channel']}: {v['title'][:60]}...")

    results.sort(key=lambda r: all_videos.index(
        next(v for v in all_videos if v["video_id"] == r["video_id"])))

    # 8. Email
    ok_count = sum(1 for r in results if r["ok"])
    _send_report_email(channels, results, ok_count, start_time)
    _touch_heartbeat()

    elapsed = (datetime.now() - start_time).total_seconds()
    log(f"Done — {ok_count}/{len(results)} analyses OK ({elapsed:.0f}s)")

    for r in results:
        print(f"\n{'='*60}")
        print(f"[{'✓' if r['ok'] else '✗'}] {r['channel']}: {r['title']}")
        print(f"連結: {r['url']}")
        print(f"{'='*60}")
        print(r["analysis"][:2000])

    return 0


if __name__ == "__main__":
    import signal
    def _timeout_handler(signum, frame):
        log(f"FATAL: Script timed out after {TOTAL_TIMEOUT}s")
        sys.exit(4)
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TOTAL_TIMEOUT)
    sys.exit(main())
