#!/usr/bin/env python3
"""
Infographic generator for the email digests (arxiv-gem-digest, yt-gem-daily).

Generates a conclusion infographic via Gemini web image gen (gemini.py --img,
cookie auth, no API key) and returns a PNG path. Prompt is built
deterministically from the digest results — no extra LLM round-trip.

Design rules (learned from live tests):
  - Keep labels SHORT (<= 6 words); long text garbles in image models.
  - Dark navy + teal flat style reads well in email clients.
  - Never fail the digest: generate() returns None on any problem.
"""

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

GEMINI_SCRIPT = os.environ.get(
    "ARXIV_GEMINI_SCRIPT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini.py"))
if not os.path.exists(GEMINI_SCRIPT):
    GEMINI_SCRIPT = os.path.expanduser("~/.local/bin/gemini-cli")

OUT_ROOT = os.environ.get("DIGEST_IMG_DIR",
                          os.path.expanduser("~/.hermes/digest_images"))

STYLE = ("flat vector infographic, dark navy background, teal and white "
         "accents, thin rounded card panels, minimal clean line icons, "
         "crisp small sans-serif text, 16:9 wide banner")


def _shorten(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip(" ,;:.—-") + "…"


def gen_image(prompt: str, out_dir: str, timeout: int = 280,
              retries: int = 2) -> str | None:
    """Run gemini.py --img, return newest PNG path or None.

    Google rotates __Secure-1PSIDTS server-side frequently (esp. after
    image-gen bursts). Before each attempt, re-scan the local Firefox cookie
    DB (browser_cookie3, no browser launch) so every attempt uses fresh
    cookies. On CI there is no browser — falls back to the env cookies that
    the workflow just pushed."""
    os.makedirs(out_dir, exist_ok=True)

    def _fresh_env() -> dict:
        env = dict(os.environ)
        try:
            import browser_cookie3
            cj = browser_cookie3.firefox(domain_name=".google.com")
            d = {c.name: c.value for c in cj
                 if c.name in ("__Secure-1PSID", "__Secure-1PSIDTS")}
            if d.get("__Secure-1PSID"):
                env["GEMINI_SID"] = d["__Secure-1PSID"]
                env["GEMINI_TS"] = d.get("__Secure-1PSIDTS",
                                         env.get("GEMINI_TS", ""))
        except Exception:
            pass  # CI: keep env cookies
        return env

    before = set(glob.glob(os.path.join(out_dir, "*.png")))
    args = [sys.executable if GEMINI_SCRIPT.endswith(".py") else GEMINI_SCRIPT,
            GEMINI_SCRIPT,
            "--img", prompt, "--save-images", out_dir, "--json", "-q"]
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  timeout=timeout, env=_fresh_env())
            try:
                parsed = json.loads(proc.stdout.strip() or "{}")
            except json.JSONDecodeError:
                parsed = {}
            if parsed.get("ok"):
                time.sleep(0.5)
                new = sorted(
                    set(glob.glob(os.path.join(out_dir, "*.png"))) - before,
                    key=os.path.getmtime)
                if new:
                    return new[-1]
            if parsed.get("err"):
                print(f"[infographic] gemini err={parsed.get('err')}: "
                      f"{parsed.get('msg', '')[:120]}", file=sys.stderr)
        except (subprocess.TimeoutExpired, Exception) as e:  # noqa: BLE001
            if attempt >= retries:
                print(f"[infographic] gen failed: {e}", file=sys.stderr)
        time.sleep(3)
    return None


# ── arXiv digest infographic ───────────────────────────────────────────────

def build_arxiv_prompt(score_lines: list[str], n_recommended: int = 0,
                       papers: list[dict] | None = None,
                       digest_text: str = "") -> str:
    """Rich-context prompt for the arXiv conclusion infographic.

    papers: [{id,title,agent,tune,summary}] — summaries give Gemini real
    content to visualize instead of inventing. digest_text: optional
    recommended-section notes for takeaway phrasing. Demands an analytical
    dashboard composition (ranked leaderboard + score bars + takeaways),
    NOT decorative text cards.
    """
    if not papers:
        papers = []
        for ln in score_lines[:5]:
            m = re.match(
                r"[\s*>#-]*\*{0,2}\s*(?:arxiv:?\s*)?(\d{4}\.\d{4,5})\S*\s*\*{0,2}"
                r"\s*[—–-]+\s*(.+?)\s*[—–-]+\s*\*{0,2}\s*AGENT\s*(\d)\s*/\s*5"
                r"\s*\*{0,2}\s*[—–-]+\s*\*{0,2}\s*TUNE\s*(\d)", ln, re.I)
            if m:
                pid, t, a, tu = m.groups()
                papers.append({"id": pid, "title": t.strip("* "),
                               "agent": int(a), "tune": int(tu), "summary": ""})
    papers = sorted((papers or [])[:5],
                    key=lambda p: max(p.get("agent", 0), p.get("tune", 0)),
                    reverse=True)
    if not papers:
        return ""

    blocks = []
    for rank, p in enumerate(papers, 1):
        b = (f"#{rank} {p['id']} | title: {p['title']} | "
             f"AGENT score {p.get('agent', '?')}/5 | "
             f"TUNE score {p.get('tune', '?')}/5")
        if p.get("summary"):
            b += f"\n   what it does: {p['summary'][:200]}"
        blocks.append(b)
    n_rec = n_recommended or sum(
        1 for p in papers if max(p.get("agent", 0), p.get("tune", 0)) >= 4)
    digest_note = ""
    if digest_text:
        digest_note = ("\nExtra context (recommended-paper notes — use only "
                       "for takeaway phrasing):\n" + digest_text[:900])

    return (
        "A wide 16:9 analytics dashboard infographic titled "
        "'arXiv cs.AI Daily Radar — what matters today'. "
        "This is a DATA-DENSE editorial graphic for an AI research digest, "
        "NOT a decorative card layout. Required elements:\n"
        "1. LEFT half: a ranked leaderboard of these papers, one row each, "
        "showing rank number, arXiv id, short title, and a horizontal score "
        "bar pair (teal bar = AGENT applicability, green bar = "
        "TUNE/trainability), sorted best first, top row visually highlighted "
        "with an amber rank badge:\n"
        + "\n".join("  " + b for b in blocks) + "\n"
        "2. RIGHT half: a 'Why it matters' panel with one ultra-short "
        "takeaway phrase (max 8 words) for each of the top-3 papers, "
        "derived ONLY from the context above — do not invent claims.\n"
        f"3. TOP-RIGHT corner: two stat chips: '{len(papers)} screened' and "
        f"'{n_rec} recommended'.\n"
        "4. Color-code scores red/amber/green (high=green, low=red). Flat "
        "vector, dark navy background, thin dividers, small crisp text, "
        "Bloomberg-terminal dashboard aesthetic. All text legible and "
        "exactly as given — no lorem ipsum, no extra papers, no invented "
        "numbers." + digest_note)


# ── yt-gem / finance digest infographic ────────────────────────────────────

def parse_score_lines(score_lines: list[str]) -> list[dict]:
    """Parse 'id — title — AGENT n/5 — TUNE n/5' lines (markdown tolerant)
    into [{id,title,agent,tune,summary:''}]."""
    out = []
    for ln in score_lines:
        m = re.match(
            r"[\s*>#-]*\*{0,2}\s*(?:arxiv:?\s*)?(\d{4}\.\d{4,5})\S*\s*\*{0,2}"
            r"\s*[—–-]+\s*(.+?)\s*[—–-]+\s*\*{0,2}\s*AGENT\s*(\d)\s*/\s*5"
            r"\s*\*{0,2}\s*[—–-]+\s*\*{0,2}\s*TUNE\s*(\d)", ln, re.I)
        if m:
            pid, t, a, tu = m.groups()
            out.append({"id": pid, "title": t.strip("* "), "agent": int(a),
                        "tune": int(tu), "summary": ""})
    return out


def build_videos_prompt(channel_label: str, items: list[dict],
                        date_label: str = "") -> str:
    """Rich-context prompt for the finance-video digest infographic.
    items: [{channel,title,url,verdict,direction}] with real takeaways."""
    rows = []
    for it in items[:6]:
        d = it.get("direction")
        if d == 1:
            mark, word = "▲", "bullish"
        elif d == -1:
            mark, word = "▼", "bearish"
        else:
            mark, word = "◆", "neutral/event"
        rows.append(
            f"{mark} {word} | {it.get('channel', '')} | takeaway: "
            f"{_shorten(it.get('verdict') or it.get('title', ''), 42)}")
    if not rows:
        return ""
    n_bull = sum(1 for r in rows if "▲" in r)
    n_bear = sum(1 for r in rows if "▼" in r)
    n_flat = len(rows) - n_bull - n_bear
    date_note = f"Data date: {date_label}. " if date_label else ""
    return (
        "A wide 16:9 financial market-pulse dashboard infographic titled "
        f"'{channel_label}'. DATA-DENSE morning-brief style, NOT decorative "
        "cards. Required elements:\n"
        "1. LEFT: 'Market direction' panel — a horizontal 100% stacked bar "
        f"showing today's video sentiment split: bullish {n_bull} (green), "
        f"bearish {n_bear} (red), neutral/event {n_flat} (grey), with the "
        "counts printed inside the segments.\n"
        "2. RIGHT: 'Today's takeaways' list — one row per video, each with "
        "its direction symbol (▲ green / ▼ red / ◆ grey), the channel name, "
        "and the short takeaway text EXACTLY as given (do not rewrite the "
        "financial claims):\n"
        + "\n".join("  " + r for r in rows) + "\n"
        f"3. {date_note}Footer: a ticker-style strip listing the channels "
        "covered.\n"
        "Flat vector, dark navy background, Bloomberg-terminal dashboard "
        "aesthetic, crisp legible text, thin separators, red/green market "
        "color semantics. No invented tickers, numbers, or takeaways.")


def arxiv_notebook_context(papers: list[dict], digest_text: str = "") -> str:
    """Context/instruction block for NotebookLM infographic generation:
    ranked paper list with scores + summaries."""
    lines = []
    for rank, p in enumerate(papers[:8], 1):
        lines.append(
            f"{rank}. {p['id']} — {p['title']} "
            f"(AGENT {p.get('agent', '?')}/5, TUNE {p.get('tune', '?')}/5)"
            + (f" — {p.get('summary', '')[:180]}" if p.get("summary") else ""))
    inst = (
        "Create a data-dense dark-navy dashboard infographic of today's "
        "arXiv cs.AI screening for an AI engineer audience. Show a ranked "
        "leaderboard with one row per paper: rank, arXiv id, short title, "
        "and two colored score bars (AGENT = agent-harness applicability, "
        "TUNE = trainability on a free Colab T4), best paper highlighted. "
        "Add a 'Why it matters' panel with one short takeaway per top paper "
        "and summary chips (papers screened / recommended). Use the paper "
        "list below exactly — no invented papers, numbers, or claims.\n\n"
        + "\n".join(lines))
    if digest_text:
        inst += "\n\nSupporting notes:\n" + digest_text[:1200]
    return inst


def videos_notebook_context(items: list[dict], date_label: str = "") -> str:
    """Context/instruction block for NotebookLM infographic generation for
    the finance video digest: youtube links + takeaways + sentiment split."""
    rows = []
    for it in items[:8]:
        d = it.get("direction")
        word = {1: "bullish", -1: "bearish"}.get(d, "neutral/event")
        rows.append(f"- [{word}] {it.get('channel', '')}: "
                    f"{_shorten(it.get('verdict') or it.get('title', ''), 60)}"
                    f" ({it.get('url', '')})")
    n_bull = sum(1 for r in rows if "[bullish]" in r)
    n_bear = sum(1 for r in rows if "[bearish]" in r)
    n_flat = len(rows) - n_bull - n_bear
    return (
        "Create a dark-navy financial market-pulse dashboard infographic "
        "summarizing today's Chinese finance YouTube digest. Include: "
        "(1) a left panel 'Market direction' with a stacked bar: bullish "
        f"{n_bull}, bearish {n_bear}, neutral/event {n_flat}; (2) a right "
        "panel 'Today's takeaways' listing each video with its sentiment "
        "symbol, channel name, short takeaway, and its YouTube URL in small "
        "text; (3) a footer listing the channels covered. Bloomberg-terminal "
        "aesthetic, red/green market semantics, crisp legible text. Use only "
        "the videos below"
        + (f" — data date {date_label}" if date_label else "") + ":\n\n"
        + "\n".join(rows))


def extract_verdicts_from_analyses(analyses: list[dict]) -> list[dict]:
    """Pull a short takeaway per video analysis: first bold/header line or
    first sentence clause. analyses: [{title, analysis, ok}]"""
    out = []
    for a in analyses:
        if not a.get("ok"):
            continue
        text = a.get("analysis") or ""
        verdict = ""
        for pat in (r"^#+\s*(.+)$", r"\*\*(.{4,60}?)\*\*"):
            m = re.search(pat, text, re.M)
            if m:
                verdict = m.group(1)
                break
        if not verdict:
            m = re.search(r"([^。！？\n]{6,40})[。！？]?", text)
            verdict = m.group(1) if m else a.get("title", "")
        out.append({"title": a.get("title", ""), "verdict": verdict})
    return out


def extract_conclusion_lines(report_text: str, max_lines: int = 4) -> list[str]:
    """For the finance Pro report: take short bullet-ish lines from the
    final recommendation section (四/建議/啟示) as verdict labels."""
    section = ""
    m = re.search(r"(?:四|投資啟示|建議與?策略|結論)[^\n]*\n(.*?)(?:\n#|\Z)",
                  report_text, re.S)
    if m:
        section = m.group(1)
    else:
        section = report_text[-1200:]
    lines = []
    for raw in section.splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        if len(line) < 6 or line.startswith("#"):
            continue
        lines.append(_shorten(line.split("：")[0].split(":")[0], 22))
        if len(lines) >= max_lines:
            break
    return lines


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# ── Data-driven infographic renderer (matplotlib) ───────────────────────────
# Upgrades content from text-card tiles to REAL data visualization: score bars,
# rank ordering, value labels, summary stats. Rendered locally = exact text,
# no image-model garble. Use prefers: matplotlib output; fail back to the
# Gemini decorative banner only if matplotlib is unavailable.

NAVY = "#0f1b2d"
TEAL = "#14b8a6"
GREEN = "#22c55e"
AMBER = "#f59e0b"
REDX = "#ef4444"
CARD = "#f1f5f9"
GRID = "#334155"

_FONTS = None


def _setup_fonts():
    """Register preferred CJK font for Chinese labels (yt digest)."""
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    pref = (["Noto Sans CJK TC", "Noto Sans CJK SC", "Noto Sans CJK HK",
             "Noto Sans CJK JP", "Droid Sans Fallback",
             "DejaVu Sans"] + sorted(have))
    chosen = next((f for f in dict.fromkeys(pref) if f in have), "DejaVu Sans")
    _FONTS = chosen
    return _FONTS


def _to_png(fig, out, dpi=200, tight=None):
    """Save figure. NOTE: no tight_layout/bbox-tight — they clip y-tick labels
    and suptitles that extend outside the axes box. Margins are set explicitly
    by the render functions instead. fig.patch alpha is NOT touched: the navy
    facecolor must stay opaque or the whole figure renders white."""
    import matplotlib.pyplot as plt
    fig.savefig(out, dpi=dpi, facecolor=fig.get_facecolor(),
                transparent=False)
    plt.close(fig)
    return out


def render_arxiv_chart(score_lines: list[str], n_recommended: int = 1,
                       filedate: str = "") -> str | None:
    """Parse AGENT/TUNE score lines, draw a horizontal grouped bar chart sorted
    high→low by the max of the two scores, export a temp PNG path."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    _setup_fonts()
    plt.rcParams["font.family"] = _FONTS
    plt.rcParams["axes.unicode_minus"] = False

    parsed = []
    for ln in score_lines:
        m = re.match(
            r"[\s*>#-]*\*{0,2}\s*(?:arxiv:?\s*)?(\d{4}\.\d{4,5})\S*\s*\*{0,2}"
            r"\s*(.+?)\s*[—–-]+\s*\*{0,2}\s*AGENT\s*(\d(?:\.\d)?)\s*/\s*5?"
            r"\s*\*{0,2}\s*[—–-]+\s*\*{0,2}\s*TUNE\s*(\d(?:\.\d)?)\s*/\s*5?",
            ln, re.I)
        if not m:
            continue
        pid, title, agent, tune = m.groups()
        parsed.append({"id": pid, "title": title.strip("* "),
                       "agent": float(agent), "tune": float(tune)})
    if len(parsed) < 1:
        return None
    # screen to the ones shown (all), cap legend length
    parsed = parsed[:8]
    parsed.sort(key=lambda p: max(p["agent"], p["tune"]), reverse=True)

    labels = [f"{p['id'][-2:]}" if False else _shorten(p["title"], 34)
              for p in parsed]
    agent = [p["agent"] for p in parsed]
    tune = [p["tune"] for p in parsed]
    y = list(range(len(parsed)))

    # canvas + header
    h = 3.2 + 0.62 * len(parsed)
    fig, ax = plt.subplots(figsize=(9.2, h), facecolor=NAVY)
    ax.set_facecolor(NAVY)
    fig.suptitle("arXiv cs.AI Daily Picks — AGENT vs TUNE",
                 x=0.02, y=0.99, ha="left", fontsize=17,
                 fontweight="bold", color="white")
    subtitle = filedate or datetime.now().strftime("%Y-%m-%d")
    ax.text(-0.06, 1.02 + 0.18, subtitle, transform=ax.transAxes,
            fontsize=9, color=GRID)

    ax.barh([y[i] + 0.2 for i in range(len(parsed))], agent, height=0.38,
            color=TEAL, label="AGENT", alpha=0.95, zorder=3)
    ax.barh([y[i] - 0.2 for i in range(len(parsed))], tune, height=0.38,
            color=GREEN, label="TUNE", alpha=0.85, zorder=3)
    for i in range(len(parsed)):
        ax.text(agent[i] + 0.08, y[i] + 0.2, f"{agent[i]:g}", va="center",
                fontsize=10, color="white", fontweight="bold")
        ax.text(tune[i] + 0.08, y[i] - 0.2, f"{tune[i]:g}", va="center",
                fontsize=10, color="white", fontweight="bold")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=11, color="white")
    ax.tick_params(axis="y", colors="white", labelcolor="white")
    for lbl in ax.get_yticklabels():
        lbl.set_color("white")
        lbl.set_fontsize(11)
    # ensure labels have room: reserve left margin explicitly
    fig.subplots_adjust(left=0.30)
    ax.set_xlim(0, 5.6)
    ax.set_xticks([0, 1, 2, 3, 4, 5])
    ax.set_xticklabels(["0", "1", "2", "3", "4", "5"], fontsize=10, color=GRID)
    ax.set_xlabel("score / 5", fontsize=10, color=GRID)
    ax.grid(axis="x", color=GRID, alpha=0.35, linestyle="--", zorder=0)
    ax.tick_params(axis="y", colors="white")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(GRID)

    # rank callout on top-1 + recommendation count band
    n_rec_disp = int(n_recommended) if n_recommended else 1
    rank_txt = (f"#{1}  {parsed[0]['id']}  ·  AGENT {parsed[0]['agent']:g} / "
                f"TUNE {parsed[0]['tune']:g}  —  top pick")
    ax.text(-0.06, 1.02 + 0.42, rank_txt, transform=ax.transAxes,
            fontsize=11, color=AMBER, fontweight="bold")
    ax.text(-0.06, -0.06 + 0.0,
            f"{len(parsed)} papers · {n_rec_disp} recommended · "
            "ag = agent-harness use · tune = trainable on a T4",
            transform=ax.transAxes, fontsize=9, color=GRID)
    ax.legend(loc="upper right", fontsize=9, frameon=False,
              labelcolor="white", ncol=2)

    out = os.path.join(OUT_ROOT, "arxiv", f"chart_{stamp()}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return _to_png(fig, out)


def verdict_direction(text: str) -> int:
    """Classify a takeaway text as bullish (+1) / bearish (-1) / neutral (0)."""
    up = ("利好", "上升", "上漲", "漲", "升", "反彈", "走強", "突破", "看多",
          "樂觀", "買入", "加倉", "強勢", "回暖", "上揚", "bullish", "rally",
          "surge", "gain", "beat", "看漲")
    down = ("利空", "下跌", "下挫", "跌", "走弱", "承壓", "回落", "看空",
            "悲觀", "賣出", "減倉", "逃離", "風險", "受壓", "bearish", "crash",
            "drop", "sink", "fear", "大跌", "壓抑", "壓力", "高企")
    ups = sum(1 for k in up if k in text)
    downs = sum(1 for k in down if k in text)
    if ups > downs:
        return 1
    if downs > ups:
        return -1
    return 0


def render_videos_chart(items: list[dict], title: str = "",
                        filedate: str = "") -> str | None:
    """Pulse panel: sentiment split bar (bull/bear/neutral, from
    verdict_direction) + takeaway list with direction marks."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    _setup_fonts()
    plt.rcParams["font.family"] = _FONTS
    plt.rcParams["axes.unicode_minus"] = False

    if not items:
        return None
    items = items[:6]
    for it in items:
        it.setdefault("direction", verdict_direction(
            (it.get("verdict") or "") + " " + (it.get("title") or "")))
    n_bull = sum(1 for it in items if it.get("direction") == 1)
    n_bear = sum(1 for it in items if it.get("direction") == -1)
    n_neut = len(items) - n_bull - n_bear

    fig, (axb, axl) = plt.subplots(
        1, 2, figsize=(9.4, 3.6), dpi=200,
        gridspec_kw={"width_ratios": [2.3, 3.2]}, facecolor=NAVY)
    for ax in (axb, axl):
        ax.set_facecolor(NAVY)
    cats = [(GREEN, n_bull, "bullish"),
            (REDX, n_bear, "bearish"),
            (GRID, n_neut, "neutral")]
    tot = sum(v for _, v, _ in cats) or 1
    left = 0
    for c, v, label in cats:
        if v <= 0:
            continue
        axb.barh(0.5, v / tot * 0.9, left=left, color=c, height=0.5,
                 edgecolor=NAVY, linewidth=1.2)
        axb.text(left + (v / tot * 0.45), 0.5, str(v), ha="center",
                 va="center", fontsize=16, fontweight="bold", color="white")
        if v / tot > 0.18:
            axb.text(left + (v / tot * 0.45), 0.84, label, ha="center",
                     va="bottom", fontsize=9, color="#8ab4c8")
        left += v / tot * 0.9
    axb.set_xlim(0, 0.9)
    axb.set_ylim(0, 1.3)
    axb.axis("off")
    axb.text(0.45, 0.08, f"{n_bull} bull · {n_bear} bear · {n_neut} flat",
             ha="center", color="white", fontsize=9.5)
    axb.set_title("今日方向分佈", color="white", fontsize=13,
                  fontweight="bold", loc="left", pad=14)

    axl.axis("off")
    axl.text(0, 1.06, title or "財經影片今日重點", transform=axl.transAxes,
             color="white", fontsize=14, fontweight="bold", va="top")
    shown = items[:5]
    step = 0.92 / max(len(shown), 1)
    for idx, it in enumerate(shown):
        if it.get("direction") == 1:
            sym, col = "▲", GREEN
        elif it.get("direction") == -1:
            sym, col = "▼", REDX
        else:
            sym, col = "◆", TEAL
        lbl = _shorten(it.get("verdict") or it.get("title") or "", 30)
        axl.text(0, 0.90 - idx * step, f"{sym}  {lbl}",
                 transform=axl.transAxes, color=col, fontsize=10.5, va="top")
    if filedate:
        axl.text(0, -0.10, f"◆ neutral/event · data {filedate}",
                 transform=axl.transAxes, color="#8ab4c8", fontsize=8.5)

    out = os.path.join(OUT_ROOT, "ytgem", f"chart_{stamp()}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return _to_png(fig, out)


def matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


def _noop():
    import matplotlib  # noqa: F401 — keep pyflakes quiet on renderer imports
    matplotlib.use("Agg")


# ── NotebookLM infographic path ─────────────────────────────────────────────

NLM_CLI = os.environ.get("NLM_CLI", "")
if not NLM_CLI or not os.path.exists(NLM_CLI):
    _c = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nlm.py")
    NLM_CLI = _c if os.path.exists(_c) else os.path.expanduser(
        "~/.hermes/scripts/nlm.py")


def _nlm(args: list[str], timeout: int = 120) -> dict:
    """Run nlm.py with JSON output; raise on error."""
    import subprocess
    r = subprocess.run([sys.executable, NLM_CLI] + args,
                       capture_output=True, text=True, timeout=timeout)
    try:
        d = json.loads(r.stdout.strip() or "{}")
    except json.JSONDecodeError:
        d = {"s": "err", "e": (r.stdout + r.stderr)[:300]}
    if d.get("s") != "ok":
        msg = d.get("e") or d.get("message") or (r.stdout + r.stderr)[:250]
        raise RuntimeError(f"nlm {args[0]}: {str(msg)[:250]}")
    return d


def nlm_ensure_notebook(title: str) -> str:
    """Create a NotebookLM notebook if absent (idempotent per title)."""
    d = _nlm(["notebook", "list"])
    for nb in d.get("notebooks", []):
        if nb.get("title") == title:
            return nb["id"]
    d = _nlm(["notebook", "create", title])
    return d["id"]


def nlm_add_text_sources(nb: str, sources: list[dict]) -> int:
    """Add text sources; returns count added. Skips on per-source failure."""
    n = 0
    for s in sources:
        try:
            _nlm(["src", "add", "--type", "text", "--title", s["title"][:90],
                  s["text"], "-n", nb], timeout=120)
            n += 1
        except RuntimeError as e:
            print(f"[nlm] source skip: {e}", file=sys.stderr)
    return n


def nlm_add_youtube_sources(nb: str, sources: list[dict]) -> int:
    n = 0
    for s in sources:
        try:
            _nlm(["src", "add", "--type", "youtube",
                  "--title", s["title"][:90], s["url"], "-n", nb], timeout=120)
            n += 1
        except RuntimeError as e:
            print(f"[nlm] source skip: {e}", file=sys.stderr)
    return n


def nlm_generate_infographic(nb: str, instructions: str,
                             timeout: int = 420) -> str:
    """Generate an infographic artifact; returns the CDN image URL.
    Re-authenticates once on session expiry. Idempotent per notebook per
    run-cycle: if today's artifact already exists, return its URL instead
    of regenerating."""
    import subprocess
    # reuse: check for an existing completed Infographic created today
    try:
        d = _nlm(["art", "list", "-n", nb])
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y-%m-%d")
        for a in d.get("artifacts", []):
            if (a.get("type") == "Infographic"
                    and a.get("status") == "completed"
                    and str(a.get("created_at", "")).startswith(today)):
                g = _nlm(["art", "get", a["id"], "-n", nb])
                # art get doesn't return url; fall through to generate only
                # if we can't obtain one
                url = g.get("url", "")
                if url:
                    return url
    except Exception:
        pass
    for attempt in (1, 2):
        try:
            d = _nlm(["art", "generate", "infographic", instructions,
                      "--wait", "-n", nb], timeout=timeout)
            return d.get("url", "")
        except RuntimeError as e:
            if attempt == 1 and ("Authentication" in str(e) or
                                 "UNEXPECTED_ERROR" in str(e)):
                try:
                    _nlm(["auth", "init"], timeout=180)
                except Exception:
                    pass
                continue
            raise
    return ""


def nlm_download_image(url: str, out_path: str) -> str:
    """Download a NotebookLM CDN image (auth + multi-hop redirects) via
    Playwright APIRequestContext with real browser session cookies.
    (curl_cffi/urllib hit a Google consent/login wall.)"""
    if not url:
        raise RuntimeError("empty url")
    import asyncio
    try:
        import browser_cookie3  # noqa: F401 — fallback only
    except ImportError:
        browser_cookie3 = None

    def norm_cookies():
        """storage_state.json (CI + local NLM session) first; browser
        cookies only as fallback."""
        ss = os.environ.get(
            "NOTEBOOKLM_STORAGE_STATE",
            os.path.expanduser(
                "~/.notebooklm/profiles/default/storage_state.json"))
        raw = []
        try:
            with open(ss) as f:
                raw = json.load(f).get("cookies", [])
        except Exception:
            raw = []
        if not raw:
            try:
                cj = browser_cookie3.firefox(domain_name=".google.com")
                raw = [{"name": c.name, "value": c.value, "domain": c.domain,
                        "path": c.path, "expires": c.expires,
                        "secure": bool(c.secure)}
                       for c in cj if c.domain.endswith("google.com")]
            except Exception:
                raw = []
        cookies = []
        for c in raw:
            if not c.get("domain", "").endswith("google.com"):
                continue
            e = c.get("expires")
            exp = -1
            if e:
                try:
                    exp = int(e)
                except Exception:
                    exp = -1
                if exp > 253402300799:
                    exp //= 1000
                if exp <= 0:
                    exp = -1
            cookies.append({"name": c.get("name"), "value": c.get("value"),
                            "domain": c.get("domain"), "path": c.get("path") or "/",
                            "expires": exp, "secure": bool(c.get("secure")),
                            "httpOnly": False})
        return cookies

    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = await b.new_context()
            await ctx.add_cookies(norm_cookies())
            r = await ctx.request.get(url, max_redirects=10, timeout=90000)
            body = await r.body()
            await b.close()
            if r.status != 200 or body[:4] != b"\x89PNG":
                raise RuntimeError(f"download failed: HTTP {r.status} "
                                   f"({body[:6]!r})")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(body)
            return out_path

    return asyncio.run(run())


def nlm_arxiv_sources(papers: list[dict]) -> list[dict]:
    """Text sources from parsed paper dicts (URL adds fail rpc_code=9)."""
    return [{"title": f"{p['id']} — {_shorten(p['title'], 55)}",
             "text": (f"arXiv paper {p['id']}: {p['title']}\n"
                      f"AGENT score (agent-harness applicability): "
                      f"{p.get('agent', '?')}/5\n"
                      f"TUNE score (trainability on free Colab T4): "
                      f"{p.get('tune', '?')}/5\n"
                      f"Link: https://arxiv.org/abs/{p['id']}\n"
                      f"Summary: {p.get('summary', '')[:600]}")}
            for p in papers]
