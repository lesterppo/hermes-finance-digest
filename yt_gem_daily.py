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

def _env_int(name: str, default: str) -> int:
    """int() from env, tolerating unset OR empty values (a workflow_dispatch
    input that isn't filled in arrives as an empty string, not unset)."""
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw else int(default)


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
SMTP_PORT = _env_int("YT_GEM_SMTP_PORT", "465")
RECIPIENT = os.environ.get("YT_GEM_RECIPIENT", "")

MODEL = os.environ.get("YT_GEM_MODEL", "flash")
THINKING = os.environ.get("YT_GEM_THINKING", "extended")

HOURS_BACK = _env_int("YT_GEM_HOURS_BACK", "24")
GEMINI_TIMEOUT = _env_int("YT_GEM_TIMEOUT", "300")
MAX_CONCURRENT = _env_int("YT_GEM_MAX_CONCURRENT", "3")
GEMINI_RETRIES = _env_int("YT_GEM_RETRIES", "2")
TOTAL_TIMEOUT = _env_int("YT_GEM_TOTAL_TIMEOUT", "900")
COOKIE_WARN_DAYS = _env_int("YT_GEM_COOKIE_WARN_DAYS", "25")

SEEN_FILE = os.path.expanduser(
    os.environ.get("YT_GEM_SEEN_FILE", "~/.hermes/yt_gem_seen.json"))
SEEN_WINDOW_HOURS = _env_int("YT_GEM_SEEN_WINDOW_HOURS", "48")
SEEN_PRUNE_DAYS = _env_int("YT_GEM_SEEN_PRUNE_DAYS", "7")

HEARTBEAT_FILE = os.path.expanduser(
    os.environ.get("YT_GEM_HEARTBEAT_FILE", "~/.hermes/yt_gem_heartbeat"))


# ── Helpers ────────────────────────────────────────────────────────────────

import market_data as md
import market_llm as ml


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
    d = os.path.dirname(SEEN_FILE)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(pruned, f, indent=2)


def _filter_duplicates(videos, seen):
    now = datetime.now(timezone.utc)
    window_cutoff = (now - timedelta(hours=SEEN_WINDOW_HOURS)).isoformat()
    new_videos = []
    skipped = 0
    if os.environ.get("YT_GEM_IGNORE_SEEN", "").lower() in ("1", "true", "yes"):
        log("IGNORE_SEEN set — dedup disabled for this run (testing)")
        return list(videos), seen
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


def _channel_ref(line: str) -> Optional[tuple[str, str]]:
    """Parse one channels.txt line into (label, url-path-segment).

    Accepts `https://www.youtube.com/@Handle` (percent-encoded handles are
    decoded) and `https://www.youtube.com/channel/UCxxxx` — some channels keep
    a channel-ID URL reachable after their @handle stops resolving.
    """
    m = re.search(r"/@([A-Za-z0-9_%.~-]+)", line) or \
        re.search(r"@([A-Za-z0-9_%.~-]+)", line)
    if m:
        label = urllib.parse.unquote(m.group(1))
        return label, f"@{m.group(1)}"
    m = re.search(r"/channel/(UC[A-Za-z0-9_-]{10,})", line)
    if m:
        return m.group(1), f"channel/{m.group(1)}"
    m = re.search(r"/c/([A-Za-z0-9_%.-]+)", line)
    if m:
        label = urllib.parse.unquote(m.group(1))
        return label, f"c/{m.group(1)}"
    return None


def load_channels(path: str) -> dict[str, str]:
    """Returns {label: url-path-segment} for every configured channel."""
    channels: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parsed = _channel_ref(line)
            if parsed:
                channels[parsed[0]] = parsed[1]
            else:
                log(f"  WARNING: ignoring unparseable channel line: {line[:60]}")
    return channels


def parse_relative_time(text: str) -> Optional[datetime]:
    """'3 hours ago' / '8h ago' / '1d ago' -> aware datetime. None if unparseable.

    YouTube serves BOTH long forms ('10 hours ago', '2 weeks ago') and short
    forms ('8h ago', '1d ago', '2w ago') on channel video grids, varying by
    channel. A video whose time doesn't parse is silently dropped from the
    digest — the no-miss guarantee requires both forms to parse.
    """
    if not text:
        return None
    now = datetime.now(timezone.utc)
    t = text.lower().replace("streamed ", "").replace("premiered ", "").strip()
    m = re.match(r"(\d+)\s*([a-z]+)\s*ago$", t)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit.endswith("s") and len(unit) > 1:
        unit = unit[:-1]  # plural -> singular, but keep bare "s" (seconds)
    unit = {
        "sec": "second", "s": "second",
        "min": "minute", "mins": "minute", "m": "minute",
        "h": "hour", "hr": "hour", "hrs": "hour",
        "d": "day",
        "w": "week", "wk": "week", "wks": "week",
        "mo": "month", "mon": "month", "mons": "month",
        "y": "year", "yr": "year", "yrs": "year",
    }.get(unit, unit)
    deltas = {
        "second": timedelta(seconds=n), "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n), "day": timedelta(days=n),
        "week": timedelta(weeks=n), "month": timedelta(days=n * 30),
        "year": timedelta(days=n * 365),
    }
    delta = deltas.get(unit)
    return now - delta if delta is not None else None


def scrape_channel_videos(channel_ref: str, cutoff: datetime) -> list[dict]:
    """channel_ref: url path segment — '@handle', 'channel/UCxxxx' or 'c/name'."""
    ref = channel_ref if "/" in channel_ref or channel_ref.startswith("@") \
        else f"@{channel_ref}"
    # Labels may be percent-encoded in channels.txt — decode for display.
    label = urllib.parse.unquote(ref.split("/")[-1].lstrip("@"))
    url = f"https://www.youtube.com/{ref}/videos"
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
        log(f"  {label}: ytInitialData not found in page")
        return []

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        log(f"  {label}: JSON decode error: {e}")
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
                    "channel": label,
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

# Built-in institutional desk-note prompt (used when no persona file).
# Gemini resolves the YouTube video transcript directly from the URL.
_BUILTIN_PROMPT = """You are a senior buy-side analyst covering global macro, equities, rates, FX and commodities, writing a pre-trade desk note for a multi-asset portfolio manager.

Analyze the following YouTube video. Access it directly via the URL — watch the full video, read the transcript, and examine every chart and number shown.

VIDEO URL: {url}
CHANNEL: {channel}
TITLE: {title}
PUBLISHED: {published}
TODAY: {today}

Hard rules: ground everything in the video plus verifiable fact (mark anything unverifiable as 未經核實); cite timestamps as [[mm:ss](URL&t=SECONDS)]; no p-values (use effect sizes, ranges, ratios); instruments must be real tradable symbols or "無直接工具"; separate video claims from verifiable fact from what is already priced in; no filler or disclaimers; 900-1400 words of Traditional Chinese.

Deliver in Traditional Chinese (繁體中文):

### 0. 交易摘要 (Desk Take)
表格：立場（看多/看空/中性偏多/中性偏空）、信心（1-5）、時間框、主要工具（真實代號）、關鍵催化劑（事件+日期）、失效條件（可觀測數字）、資訊優勢 Edge（相對共識的增量或「無增量」）。

### 1. 執行摘要 (Executive Summary)
6-10 句核心論點、框架與結論。標示投資方向及時間維度。資訊價值評級 A/B/C 並說明理由。

### 2. 核心論點逐項拆解 (Thesis Deconstruction)
每論點四行：邏輯鏈條、嚴謹度（強力支撐/部分支撐/證據不足）、最強反方觀點、可驗證性。

### 3. 數據與證據稽核 (Data & Evidence Audit)
表格：影片數據 | 時間戳 | 稽核結論（準確/大致準確但有偏差/需脈絡化/無法核實）| 遺漏或混淆變數。

### 4. 市場背景與定價 (Market Context & Pricing)
宏觀定位、資產類別技術/資金/情緒面。關鍵：區分「已定價」與「未定價」的部分，指出市場共識所在。

### 5. 風險矩陣 (Risk Matrix)
表格：風險類別 | 具體風險 | 發生概率 | 影響程度 | 時期。末行給風險監控指標。

### 6. 可行動洞察 (Actionable Insights)
先給工具對映表（想法 | 工具代號 | 方向 | 觸發條件 | 失效條件），再分「同意/部分同意（基準）/不同意」三情境給具體做法；無可執行內容寫「無操作」。

### 7. 綜合評分 (Overall Assessment)
表格：分析深度/邏輯嚴謹度/數據可靠性/可交易性/時效性/原創性，各 x.x/5 附一句理由。綜合評級 x.x/10。結論：值得關注/一般/可略過。最後一句「若只能記住一件事」。"""


# Digest-level synthesis: one extra pass over today's per-video notes.
_SYNTHESIS_PROMPT = """You are the head of research at a multi-asset fund. Below are today's analyst notes
on finance videos, each written independently, PLUS today's market snapshot.
Hierarchy of evidence: market data (highest) > headlines > videos (lowest).
A video claim that contradicts market data loses; say so explicitly.

Synthesize into a one-page desk briefing in Traditional Chinese (繁體中文), then stop.
Rules: never invent facts, tickers or dates; if evidence is thin, say so;
keep it short and decision-oriented; no disclaimers; max 600 words total.

## 1. 今日核心訊息
3–5條，每條 ≤35字，只寫對倉位有影響的訊息。數字若來自影片而無法獨立核實，標註（未經核實）。

## 2. 跨影片一致性與矛盾
Cite EVERY video as 【channel《title前20字》】— never「影片1/2/3」alone.
A synthesis containing「影片 [0-9]」without a channel name is INVALID and will be rejected.
哪些指向同一方向，哪些互相矛盾並說明分歧點。

## 3. 可執行清單
表格：想法｜工具（真實代號）｜方向｜理由｜催化劑/日期｜失效條件。
- 有高信心想法就列 Top 3。
- 沒有高信心交易時，仍然列出「條件式觀察名單」最多3條，並在表上方寫明「今日無高信心交易」。

## 4. 需要追蹤的數據與日期
只列今日或之後的事件（今天日期見上）。已過去的日期標為「已過去」或省略。

## 5. 整體市場姿態
一行：risk-on / risk-off / 觀望 ＋ 一句理由，必須與市場數據（VIX、指數、債息方向）一致。

## 6. 今日最大盲點
所有影片共同忽略或共同假設錯了的變數（若無，寫「無明顯共同盲點」）。
必须点名至少一个市场数据中可见但影片未讨论的变量（如债息/油价/VIX异动），除非确实无关。

=== 今日市場快照 ===
{market_block}

=== 今日各影片分析 ===
{analyses}
"""


def _synthesis_needs_retry(synthesis: str) -> bool:
    """Validator: bare '影片 N' refs without any 【channel《title》】cite → retry once."""
    if not synthesis:
        return False
    has_bare = re.search(r"影片\s*[0-9]", synthesis) is not None
    has_cite = re.search(r"【.+《.+》】", synthesis) is not None
    return bool(has_bare and not has_cite)



def linkify_timestamps(text: str, video_url: str) -> str:
    """Turn bare [mm:ss] / [hh:mm:ss] markers into markdown timestamp links.

    Gemini sometimes emits the plain-bracket form even when asked for links;
    deterministic post-processing keeps the citations clickable either way.
    Idempotent: existing markdown links [..](..) are left untouched.
    """
    if not text or not video_url:
        return text
    base = video_url.split("&t=")[0]

    def _to_secs(ts: str) -> int:
        parts = [int(p) for p in ts.split(":")]
        secs = 0
        for p in parts:
            secs = secs * 60 + p
        return secs

    def _sub(m: re.Match) -> str:
        ts = m.group(1)
        return f"[[{ts}]({base}&t={_to_secs(ts)})]"

    # Split on existing markdown links so we never double-linkify inside them.
    parts = re.split(r"(\[.*?\]\(.*?\))", text)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\](?!\()", _sub, parts[i])
    return "".join(parts)


def _run_gemini(prompt: str, auth: dict, timeout: int,
                max_retries: int, label: str = "") -> tuple[Optional[str], str]:
    """One Gemini webapi call via the bundled CLI. Returns (analysis, last_error).

    gemini.py emits a pointer JSON on stdout: {"ok":true,"f":"<path>","s":N};
    the full text lives in that file. Falls back to an inline "text" field.
    """
    env = os.environ.copy()
    env["GEMINI_SID"] = auth.get("__Secure-1PSID", "")
    env["GEMINI_TS"] = auth.get("__Secure-1PSIDTS", "")
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
                    if stdout_json.get("ok"):
                        analysis = None
                        fpath = stdout_json.get("f")
                        if fpath:
                            fp = Path(fpath)
                            if fp.exists():
                                raw = fp.read_text(encoding="utf-8", errors="replace")
                                try:
                                    file_json = json.loads(raw)
                                    analysis = file_json.get("text") or raw
                                except json.JSONDecodeError:
                                    analysis = raw
                        if not analysis:
                            analysis = stdout_json.get("text")
                        if analysis and len(analysis) > 80:
                            return analysis, ""
                        last_error = (f"ok but analysis short/missing "
                                      f"({len(analysis or '')} chars)")
                    else:
                        # Structured failure from gemini.py (e.g. AUTH_EXPIRED,
                        # RATE_LIMIT) — surface the err category, don't swallow it.
                        last_error = f"gemini err={stdout_json.get('err') or 'unknown'}"
                except (json.JSONDecodeError, KeyError):
                    last_error = f"unparseable stdout: {result.stdout[:150]}"

            if result.returncode != 0:
                last_error = f"exit={result.returncode} stderr: {result.stderr[:200]}"

        except subprocess.TimeoutExpired:
            last_error = "timeout"
        except Exception as e:  # noqa: BLE001
            last_error = str(e)

        if attempt < max_retries and "AUTH_EXPIRED" not in last_error:
            delay = (attempt + 1) * 10
            log(f"  Retry {attempt + 1}/{max_retries} for {label[:40]}... "
                f"({last_error[:80]}, waiting {delay}s)")
            time.sleep(delay)

    return None, last_error


def analyze_video(video: dict, persona: str, auth: dict,
                  timeout: int, max_retries: int) -> dict:
    """Send one video URL to Gemini — it resolves the transcript directly from the URL."""
    today = datetime.now().strftime("%Y-%m-%d")
    if persona:
        prompt = (f"{persona}\n\n今日日期：{today}\n影片連結：{video['url']}"
                  f"\n頻道：{video['channel']}\n標題：{video['title']}"
                  f"\n發布時間：{video['published']}")
    else:
        prompt = _BUILTIN_PROMPT.format(
            url=video["url"], channel=video["channel"],
            title=video["title"], published=video["published"], today=today)

    analysis, last_error = _run_gemini(prompt, auth, timeout, max_retries,
                                       label=video["title"])
    if analysis:
        return {"video_id": video["video_id"], "title": video["title"],
                "channel": video["channel"], "url": video["url"],
                "analysis": linkify_timestamps(analysis, video["url"]),
                "ok": True}

    return {"video_id": video["video_id"], "title": video["title"],
            "channel": video["channel"], "url": video["url"],
            "analysis": f"Analysis failed ({max_retries + 1} attempts): {last_error}",
            "ok": False}


def extract_rating(analysis: str) -> Optional[float]:
    """Pull the overall score out of a note ('綜合評級：6.5 / 10' or '6.5/10')."""
    if not analysis:
        return None
    pats = [
        r"綜合評級[^0-9]{0,20}(\d{1,2}(?:\.\d)?)\s*/\s*10",
        r"綜合評分[^0-9]{0,20}(\d{1,2}(?:\.\d)?)\s*/\s*10",
        r"(\d{1,2}(?:\.\d)?)\s*/\s*10",
    ]
    for p in pats:
        m = re.search(p, analysis)
        if m:
            try:
                v = float(m.group(1))
                if 0 <= v <= 10:
                    return v
            except ValueError:
                pass
    return None


def _rank(results: list[dict]) -> list[dict]:
    """Highest-scoring notes first; failures last. Keeps the email decision-first."""
    return sorted(
        results,
        key=lambda r: (0 if r.get("ok") else 1,
                       -(extract_rating(r.get("analysis", "")) or 0.0),
                       r.get("channel", "")),
    )


# ── Market half (merged from the retired hermes-daily-finance pipeline) ────

def market_section(auth: dict, timeout: int) -> dict:
    """Collect the market snapshot + headlines and write the briefing.

    ALWAYS returns a dict, and the email ships the raw market table even when
    every LLM tier fails — a data-only briefing beats no briefing (that was the
    retired pipeline's contract, and its numbers are the only source the prose
    is allowed to use).
    """
    rows, merr = md.collect_market()
    if merr:
        log(f"[market] collect error: {merr}")
    news: list[dict] = []
    nerrs: list[str] = []
    if rows:
        news, nerrs = md.collect_news(max_items=_env_int("FIN_NEWS_ITEMS", "40"))
    log(f"[market] {len(rows)} instruments, {len(news)} headlines, "
        f"{len(nerrs)} feed error(s)")
    chart = None
    if rows:
        try:
            chart = md.make_chart(rows, log)
            if chart:
                log(f"[market] chart -> {chart}")
        except Exception as e:  # noqa: BLE001
            log(f"[market] chart failed: {e}")

    report, source = "", "none"
    if rows:
        prompt = md.build_prompt(rows, news, datetime.now())
        # tier 1: Gemini API key ladder · tier 3: OpenAI-compatible free slugs
        report, err, source = ml.llm_report(prompt, log)
        if not report:
            # tier 2: the vendored gemini.py cookie path (the video path)
            log(f"[market] key/free tiers failed ({err}) — trying the cookie path")
            report, err2 = _run_gemini(prompt, auth, timeout, 1,
                                       label="market briefing")
            source = "gemini-web" if report else "none"
            if not report:
                log(f"[market] cookie tier failed too: {err2}")
    if report:
        log(f"[market] briefing OK via {source} ({len(report)} chars)")
    else:
        log("[market] NO briefing — email ships the raw table + headlines only")

    return {"rows": rows, "news": news, "errors": nerrs,
            "rows_html": md.market_table(rows) if rows else "",
            "report": report or "", "source": source,
            "chart": str(chart) if chart else None}


def synthesize_digest(results: list[dict], auth: dict,
                      timeout: int, market: dict | None = None) -> str:
    """One extra pass over today's notes -> cross-video desk briefing.

    Returns "" on failure (the email degrades to per-video notes only).
    """
    if os.environ.get("YT_GEM_SYNTHESIS", "1").lower() in ("0", "false", "no"):
        return ""
    ok = [r for r in results if r.get("ok")]
    has_market = bool((market or {}).get("rows"))
    if len(ok) < 2 and not has_market:
        log("Synthesis skipped (fewer than 2 successful analyses and no market data)")
        return ""
    chunk_cap = _env_int("YT_GEM_SYNTHESIS_CHARS", "2500")
    blocks = []
    for i, r in enumerate(ok, 1):
        blocks.append(
            f"\n--- 影片 {i} [{r['channel']}] {r['title']}\n"
            f"URL: {r['url']}\n{r['analysis'][:chunk_cap]}\n")
    market_block = ""
    if has_market:
        lines = []
        for group, members in md.MARKET_GROUPS.items():
            lines.append(f"[{group}]")
            for sym in members:
                r0 = next((x for x in market["rows"] if x["symbol"] == sym), None)
                if r0 and r0.get("pct") is not None:
                    lines.append(f"  {r0['label']} ({sym}): {md.fmt_value(r0)}  "
                                 f"{md.fmt_change(r0)}")
        heads = "\n".join(f"- [{n['source']}] {n['title']}"
                          for n in (market.get("news") or [])[:20]) or "(none)"
        market_block = (
            "今日市場數據（唯一可引用的數字來源，不得自行補充）：\n"
            + "\n".join(lines) + "\n\n今日財經頭條：\n" + heads + "\n\n")
    prompt = _SYNTHESIS_PROMPT.format(analyses="".join(blocks),
                                      market_block=market_block or "(無市場數據)")
    prompt = f"今日日期：{datetime.now().strftime('%Y-%m-%d')}\n\n{prompt}"
    log(f"Synthesizing cross-video briefing ({len(ok)} videos, "
        f"{len(prompt)} prompt chars)")
    analysis, err = _run_gemini(prompt, auth, timeout, 1, label="digest synthesis")
    if not analysis:
        log(f"Synthesis failed: {err}")
        return ""
    if _synthesis_needs_retry(analysis):
        log("Synthesis validator: bare 影片 N refs without channel cites — retrying once")
        retry, err2 = _run_gemini(prompt, auth, timeout, 1, label="digest synthesis (retry)")
        if retry and not _synthesis_needs_retry(retry):
            analysis = retry
        elif retry:
            log("Synthesis retry still has bare refs — keeping retry output")
            analysis = retry
        else:
            log(f"Synthesis retry failed: {err2} — keeping first output")
    log(f"Synthesis OK ({len(analysis)} chars)")
    return analysis


# ── Email ──────────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str) -> None:
    if os.environ.get("DIGEST_DRY_RUN", "").lower() in ("1", "true", "yes"):
        log(f"DRY RUN — email suppressed (would send: {subject})")
        return
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
                       ok_count: int, start_time: datetime,
                       summary: str = "", market: dict | None = None) -> None:
    date_str = start_time.strftime("%Y年%m月%d日")

    # Conclusion infographic (Gemini web image gen) — skip on any failure
    img_path = None
    try:
        import ytgem_email
        img_paths = ytgem_email.make_infographics(
            results, market_chart=(market or {}).get("chart"))
        # CID names must match the MIME parts exactly; the market chart is
        # prepended by make_infographics, so it owns infographic0.
        cids = [f"infographic{i}" for i in range(len(img_paths))]
        mchart = (market or {}).get("chart")
        chart_cid = cids[img_paths.index(mchart)] if mchart and mchart in img_paths else None
        labels = []
        for p in img_paths:
            if p == mchart:
                labels.append("市場快照 — 指數 / 商品 / 利率 / 加密貨幣（matplotlib 本地渲染，"
                              "標籤為 ASCII 以免 CJK 字型缺字）")
            elif "pulse" in os.path.basename(p):
                labels.append("影片方向分佈圖 — 由當日影片分析數據本地渲染："
                              "▲ 看多 · ▼ 看空 · ◆ 中性/事件")
            else:
                labels.append("市場脈搏儀表板 — Gemini NotebookLM 依 attached 來源生成")
        log("Infographics: " + (", ".join(img_paths) if img_paths else "skipped"))
    except Exception as e:  # noqa: BLE001
        img_paths = []
        log(f"Infographic skipped: {e}")

    subject = (f"📊 財經每日綜合簡報 — {date_str}" if (market or {}).get("rows")
               else f"📊 Finance Daily Deep Analysis — {date_str}")
    try:
        import ytgem_email
        html = ytgem_email.build_html(
            date_str, results,
            {"infographic_cids": cids,
             "infographic_labels": labels,
             "channel_count": len(channels),
             "digest_summary": summary,
             "market": ({**market, "chart_cid": chart_cid} if market else None)})
        sent = ytgem_email.send_html(subject, html, image_paths=img_paths)
        if sent:
            log("HTML email sent"
                + (f" (+{len(img_paths)} infographics)" if img_paths else ""))
            return
        log("HTML email failed — falling back to plain text")
    except ImportError:
        log("ytgem_email module missing — plain text fallback")

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

    summary_block = f"\n{'=' * 60}\n今日綜合研判 (Cross-source Desk Briefing)\n{'=' * 60}\n{summary}\n" if summary else ""

    market_block = ""
    if market and market.get("rows"):
        heads = "\n".join(f"  - [{n['source']}] {n['title']}"
                          for n in (market.get("news") or [])[:20])
        market_block = (
            f"\n{'=' * 60}\n市場快照 (Market Snapshot)\n{'=' * 60}\n"
            + "\n".join(f"  {r['label']} ({r['symbol']}): {md.fmt_value(r)}  "
                        f"{md.fmt_change(r)}"
                        for r in market["rows"] if r.get("pct") is not None)
            + (f"\n\n財經頭條 (Headlines)\n{heads}" if heads else "")
            + (f"\n\n{'=' * 60}\n市場與新聞研判 (Market & News Briefing)\n"
               f"{'=' * 60}\n{market['report']}"
               if (market.get("report") or "").strip() else
               "\n\n(市場研判未能生成 — 以上數字為原始數據)")
        )

    body = f"""{('財經每日綜合簡報 — 市場、新聞與影片分析' if (market or {}).get('rows') else 'YouTube Finance Daily Deep Analysis')}
Date: {date_str}
Engine: Gemini — {MODEL} + {THINKING} thinking (video) / free key→cookie→:free (market)
Method: Direct URL — Gemini resolves video transcripts natively

Monitored Channels ({len(channels)}):
{channel_list}

Videos Today: {len(results)} ({ok_count}/{len(results)} analyzed successfully)
{market_block}
{summary_block}
{''.join(video_sections)}

{'=' * 60}

Notes:
• Analysis engine: Gemini {MODEL} ({THINKING} thinking) via gemini-webapi
• Content source: YouTube URLs passed directly — no transcript extraction needed
• Each video analyzed individually against the buy-side desk-note standard
  (Desk Take → Thesis → Data Audit → Market Pricing → Risk Matrix →
   Actionable Insights → Scored Assessment), then synthesized across videos
• Analysis persona: {'GEM_SYSTEM_PROMPT.md (custom)' if os.path.exists(PROMPT_FILE) and os.path.getsize(PROMPT_FILE) > 10 else 'built-in institutional analyst'}
• Schedule: daily automated via GitHub Actions
"""
    subject = (f"📊 財經每日綜合簡報 — {date_str}" if (market or {}).get("rows")
               else f"📊 Finance Daily Deep Analysis — {date_str}")
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

    # 3. Load auth. NOT fatal any more: the video half needs the cookies, but the
    #    market half runs on GEMINI_API_KEY, so a stale/missing cookie file must
    #    degrade the digest, not cancel it.
    auth: dict = {}
    if os.path.exists(AUTH_JSON):
        with open(AUTH_JSON) as f:
            auth = json.load(f)
        if not auth.get("__Secure-1PSID"):
            log("WARNING: __Secure-1PSID missing from auth.json — video analysis disabled")
            auth = {}
        else:
            auth_age_days = (time.time() - os.path.getmtime(AUTH_JSON)) / 86400
            if auth_age_days > COOKIE_WARN_DAYS:
                log(f"WARNING: auth.json is {auth_age_days:.0f} days old — run: gemini-cli --init")
    else:
        log(f"WARNING: {AUTH_JSON} not found — video analysis disabled "
            f"(market briefing still runs on GEMINI_API_KEY)")

    # 3b. Market half — always runs, independent of the cookie path
    market = market_section(auth, GEMINI_TIMEOUT)

    # 4. Scrape channels (parallel) — skipped without cookie auth
    all_videos: list[dict] = []
    if not auth:
        log("Skipping channel scrape (no Gemini cookie auth)")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    _unused_all: list[dict] = []

    def _fetch_one(label: str, ref: str) -> list[dict]:
        """Scrape one channel, retrying empty/failed fetches.

        YouTube intermittently serves a page without ytInitialData (or with a
        consent shell); a single silent empty result silently drops the channel
        from the report, so retry before accepting "no videos".
        """
        for attempt in range(3):
            try:
                vids = scrape_channel_videos(ref, cutoff)
            except Exception as e:  # noqa: BLE001
                log(f"  {label}: attempt {attempt + 1} ERROR — {e}")
                vids = []
            if vids:
                return vids
            if attempt < 2:
                time.sleep(4 * (attempt + 1))
        log(f"  {label}: WARNING — 0 new videos after 3 attempts "
            f"(throttled/empty page, or genuinely quiet channel)")
        return []

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_fetch_one, label, ref): label
                   for label, ref in channels.items()}
        for fut in as_completed(futures):
            label = futures[fut]
            vids = fut.result()
            all_videos.extend(vids)
            log(f"  {label}: {len(vids)} new videos")

    if not all_videos:
        if market.get("rows"):
            log("No new videos — sending the market/news briefing on its own")
            _send_report_email(channels, [], 0, start_time, market=market)
        else:
            log("No new videos and no market data — sending status email")
            _send_status_email(channels, start_time)
        _touch_heartbeat()
        return 0

    # 5. Filter duplicates
    seen = _load_seen_videos()
    all_videos, seen = _filter_duplicates(all_videos, seen)
    _save_seen_videos(seen)

    if not all_videos:
        if market.get("rows"):
            log("All videos already analysed — sending the market/news briefing")
            _send_report_email(channels, [], 0, start_time, market=market)
        else:
            log("All videos already analysed and no market data — sending status email")
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

    # Rank decision-first: best-scoring note at the top, failures last.
    # (Applied before synthesis so the briefing's 影片 N references match the email.)
    results = _rank(results)

    # 7. Email report (with cross-video synthesis)
    ok_count = sum(1 for r in results if r["ok"])
    dump = os.environ.get("YT_GEM_DUMP_ANALYSES")
    if dump:
        try:
            Path(dump).write_text(json.dumps(results, ensure_ascii=False),
                                  encoding="utf-8")
            log(f"Dumped analyses -> {dump}")
        except OSError as e:
            log(f"Dump failed: {e}")
    summary = synthesize_digest(results, auth, GEMINI_TIMEOUT, market=market)
    _send_report_email(channels, results, ok_count, start_time, summary,
                       market=market)
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
