#!/usr/bin/env python3
"""
YouTube Finance Daily Digest — Configurable Edition

Scrapes YouTube channels for recent videos, sends each video URL directly
to Gemini (Flash Extended Thinking) for deep analysis — Gemini resolves
transcripts itself from the URL. No local transcript extraction needed.

Fully configurable via environment variables and local files.
No hardcoded credentials, paths, or API keys.

Backend: gemini.py (gemini-webapi) — normal chat session, no Gem.
System prompt from GEM_SYSTEM_PROMPT.md is injected as inline persona.

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

import requests


# ── Configuration (all overridable via environment) ────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent

def _env_path(key: str, default_rel: str) -> str:
    if os.environ.get(key):
        return os.path.expanduser(os.environ[key])
    return str(SCRIPT_DIR / default_rel)

CHANNELS_FILE    = _env_path("YT_GEM_CHANNELS_FILE", "channels.txt")
PROMPT_FILE      = _env_path("YT_GEM_PROMPT_FILE", "GEM_SYSTEM_PROMPT.md")

def _find_gemcli() -> str:
    for p in [os.environ.get("YT_GEM_GEMINI_CLI", ""),
              os.path.expanduser("~/.local/bin/gemini-cli"),
              "gemini-cli"]:
        if p and (Path(p).exists() or p == "gemini-cli"):
            return p
    return "gemini-cli"

GEMINI_CLI = _find_gemcli()

AUTH_JSON = os.path.expanduser(
    os.environ.get("YT_GEM_AUTH_JSON", "~/.gemini-cli/auth.json"))

SMTP_USER = os.environ.get("YT_GEM_SMTP_USER", "")
SMTP_PASS = os.environ.get("YT_GEM_SMTP_PASS", "")
SMTP_SERVER = os.environ.get("YT_GEM_SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("YT_GEM_SMTP_PORT", "465"))
RECIPIENT = os.environ.get("YT_GEM_RECIPIENT", "")

MODEL = os.environ.get("YT_GEM_MODEL", "flash")
THINKING = os.environ.get("YT_GEM_THINKING", "extended")

HOURS_BACK = int(os.environ.get("YT_GEM_HOURS_BACK", "24"))
GEMINI_TIMEOUT = int(os.environ.get("YT_GEM_TIMEOUT", "300"))
MAX_CONCURRENT = int(os.environ.get("YT_GEM_MAX_CONCURRENT", "3"))
GEMINI_RETRIES = int(os.environ.get("YT_GEM_RETRIES", "2"))
TOTAL_TIMEOUT = int(os.environ.get("YT_GEM_TOTAL_TIMEOUT", "900"))
COOKIE_WARN_DAYS = int(os.environ.get("YT_GEM_COOKIE_WARN_DAYS", "25"))

SEEN_FILE = os.path.expanduser(
    os.environ.get("YT_GEM_SEEN_FILE", "~/.hermes/yt_gem_seen.json"))
SEEN_WINDOW_HOURS = int(os.environ.get("YT_GEM_SEEN_WINDOW_HOURS", "48"))
SEEN_PRUNE_DAYS = int(os.environ.get("YT_GEM_SEEN_PRUNE_DAYS", "7"))

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
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(pruned, f, indent=2)


def _filter_duplicates(videos, seen):
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
        log(f"  Skipped {skipped} duplicate videos")
    return new_videos, seen


def _touch_heartbeat() -> None:
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except IOError:
        pass


def load_channels(path: str) -> dict[str, str]:
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


# ── Persona ────────────────────────────────────────────────────────────────

def load_persona(path: str) -> str:
    """Load analysis persona from markdown file. Falls back to built-in 7-dimension prompt."""
    if not os.path.exists(path):
        log(f"WARNING: Persona file not found: {path} — using built-in prompt")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if content.startswith("---"):
        parts = content.split("---", 2)
        content = parts[2] if len(parts) > 2 else content
    return content.strip()


# ── Gemini Analysis ────────────────────────────────────────────────────────

# Built-in 7-dimension institutional analyst prompt (used when no persona file).
# Gemini resolves the YouTube video transcript directly from the URL.
_BUILTIN_PROMPT = """You are a senior institutional financial analyst with expertise spanning global macroeconomics, equity research, fixed income, commodities, FX, and geopolitical risk assessment.

Analyze the following YouTube video with institutional rigor. Access the video directly via the URL — watch the full video, read the transcript, and examine all data presented.

VIDEO URL: {url}
CHANNEL: {channel}
TITLE: {title}
PUBLISHED: {published}

Deliver in Traditional Chinese (繁體中文), structured as:

## 一、執行摘要 (Executive Summary)
6-10句概括核心論點、框架與結論。標示投資方向（看多/看空/中性）及時間維度。資訊價值評級。

## 二、核心論點逐項拆解 (Thesis Deconstruction)
逐一拆解每個主要論點：邏輯鏈條、嚴謹性評估、反方觀點。標記為：強力支撐/部分支撐/證據不足。

## 三、數據與證據稽核 (Data & Evidence Audit)
列出所有關鍵數據，評估時效性與來源可靠性。補充遺漏數據。識別混淆變數。

## 四、市場背景與宏觀框架 (Market Context)
當前宏觀環境定位。資產類別技術面/資金面/情緒面。政策動向影響。市場定價是否已反映觀點。

## 五、風險矩陣 (Risk Matrix)
| 風險類別 | 具體風險 | 發生概率 | 影響程度 |
|---------|---------|---------|---------|
分類為宏觀/政策/市場/基本面/流動性風險。區分短期波動vs長期結構性風險。提供風險監控指標。

## 六、可行動洞察 (Actionable Insights)
分級建議：若同意→實施方案；若部分同意→調整策略；若不同意→替代策略。具體止損/止盈框架。

## 七、綜合評分 (Overall Assessment)
| 維度 | 評分 |
|-----|-----|
| 分析深度/邏輯嚴謹度/數據可靠性/實用性/時效性/原創性 | ★★★★★ |

綜合評級：___/10。推薦閱讀：是/有保留/否。一句話總結。"""


def analyze_video(video: dict, persona: str, auth: dict,
                  timeout: int, max_retries: int) -> dict:
    """Send one video URL to Gemini — it resolves the transcript directly from the URL."""
    env = os.environ.copy()
    env["GEMINI_SID"] = auth.get("__Secure-1PSID", "")
    env["GEMINI_TS"] = auth.get("__Secure-1PSIDTS", "")

    if persona:
        prompt = f"{persona}\n\n影片連結：{video['url']}\n頻道：{video['channel']}\n標題：{video['title']}\n發布時間：{video['published']}"
    else:
        prompt = _BUILTIN_PROMPT.format(
            url=video["url"], channel=video["channel"],
            title=video["title"], published=video["published"])

    last_error = ""

    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                [GEMINI_CLI, "-p", prompt,
                 "-m", MODEL, "--thinking", THINKING,
                 "--json", "--brief", "-q"],
                capture_output=True, text=True,
                timeout=timeout, env=env,
            )

            if result.returncode == 0:
                try:
                    stdout_json = json.loads(result.stdout.strip())
                    if stdout_json.get("ok") and stdout_json.get("text"):
                        analysis = stdout_json["text"]
                        if len(analysis) > 80:
                            return {"video_id": video["video_id"], "title": video["title"],
                                    "channel": video["channel"], "url": video["url"],
                                    "analysis": analysis, "ok": True}
                except (json.JSONDecodeError, KeyError):
                    pass

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
            "analysis": f"Analysis failed ({max_retries + 1} attempts): {last_error}",
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
    subject = f"📊 Finance Digest Status — {date_str} (no new videos)"
    body = f"""YouTube Finance Daily Digest — Status Report
Date: {date_str}
Engine: Gemini {MODEL} + {THINKING} thinking
Method: URL-direct (Gemini resolves transcripts from YouTube links)

Monitored Channels ({len(channels)}):
{channel_list}

New Videos Today: 0 (none in last {HOURS_BACK}h)

System Status: Running normally ✓
Check Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
    _send_email(subject, body)


def _send_report_email(channels: dict, results: list[dict],
                       ok_count: int, start_time: datetime) -> None:
    date_str = start_time.strftime("%Y年%m月%d日")
    channel_list = "\n".join(f"  • {h}" for h in channels)

    video_sections: list[str] = []
    for i, r in enumerate(results, 1):
        status = "✓" if r["ok"] else "✗"
        video_sections.append(
            f"\n{'─' * 60}\n"
            f"[Video {i}] {status} {r['channel']}\n"
            f"Title: {r['title']}\n"
            f"URL: {r['url']}\n"
            f"{'─' * 60}\n"
            f"{r['analysis']}\n"
        )

    body = f"""YouTube Finance Daily Deep Analysis Report
Date: {date_str}
Engine: Gemini — {MODEL} + {THINKING} thinking
Method: Direct URL — Gemini resolves video transcripts natively

Monitored Channels ({len(channels)}):
{channel_list}

Videos Today: {len(results)} ({ok_count}/{len(results)} analyzed successfully)

{''.join(video_sections)}

{'=' * 60}

Notes:
• Analysis engine: Gemini {MODEL} ({THINKING} thinking) via gemini-webapi
• Content source: YouTube URLs passed directly — no transcript extraction needed
• Each video analyzed individually with 7-dimension framework:
  Executive Summary → Thesis Deconstruction → Data Audit → Market Context →
  Risk Matrix → Actionable Insights → Overall Assessment
• Analysis persona: {'GEM_SYSTEM_PROMPT.md (custom)' if os.path.exists(PROMPT_FILE) and os.path.getsize(PROMPT_FILE) > 10 else 'built-in institutional analyst'}
• Schedule: daily automated via GitHub Actions
"""
    subject = f"📊 Finance Daily Deep Analysis — {date_str}"
    _send_email(subject, body)


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    start_time = datetime.now()
    log("Starting YouTube Finance Daily Digest")

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

    # 2. Load analysis persona
    persona = ""
    if os.path.exists(PROMPT_FILE):
        persona = load_persona(PROMPT_FILE)
        if persona:
            log(f"Loaded persona from {PROMPT_FILE} ({len(persona)} chars)")
    if not persona:
        log("Using built-in 7-dimension institutional analyst prompt")

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
        log("No new videos — sending status email")
        _send_status_email(channels, start_time)
        _touch_heartbeat()
        return 0

    # 5. Filter duplicates
    seen = _load_seen_videos()
    all_videos, seen = _filter_duplicates(all_videos, seen)
    _save_seen_videos(seen)

    if not all_videos:
        log("All videos already analyzed — sending status email")
        _send_status_email(channels, start_time)
        _touch_heartbeat()
        return 0

    log(f"Total: {len(all_videos)} videos to analyze")

    # 6. Analyze each video with Gemini (URL-direct, no transcript extraction)
    log(f"Calling Gemini ({MODEL} + {THINKING} thinking) for each video (max {MAX_CONCURRENT} concurrent)")
    log("Gemini resolves YouTube transcripts directly from URLs — no local extraction needed")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as ex:
        futures = {
            ex.submit(analyze_video, v, persona, auth, GEMINI_TIMEOUT, GEMINI_RETRIES): v
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

    # 7. Email report
    ok_count = sum(1 for r in results if r["ok"])
    _send_report_email(channels, results, ok_count, start_time)
    _touch_heartbeat()

    elapsed = (datetime.now() - start_time).total_seconds()
    log(f"Done — {ok_count}/{len(results)} analyses OK ({elapsed:.0f}s)")

    for r in results:
        print(f"\n{'='*60}")
        print(f"[{'✓' if r['ok'] else '✗'}] {r['channel']}: {r['title']}")
        print(f"URL: {r['url']}")
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
