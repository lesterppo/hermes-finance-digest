#!/usr/bin/env python3
"""market_data.py — market snapshot + news headlines.

Merged verbatim in behaviour from the retired single-purpose daily-finance
pipeline (hermes-daily-finance). Kept as its own module so the finance digest
can compose: market table + chart + headlines + LLM briefing + video notes.

Invariants worth keeping (learned the hard way):
  * yields (^TNX/^TYX/^FVX) are quoted in BASIS POINTS, not percent;
  * chart labels stay ASCII — the GitHub runner's default matplotlib font has no
    CJK glyphs, so Chinese labels would ship as tofu boxes in the email;
  * news parsing is stdlib xml.etree (no feedparser dependency);
  * every number in the email comes from collect_market().
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import urllib.error
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MARKET_GROUPS: dict[str, dict[str, str]] = {
    "美國指數": {
        "^GSPC": "S&P 500", "^DJI": "道瓊斯工業", "^IXIC": "納斯達克綜合",
        "^RUT": "羅素 2000", "^VIX": "VIX 恐慌指數",
    },
    "七巨頭": {
        "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA",
        "GOOGL": "Alphabet", "AMZN": "Amazon", "META": "Meta", "TSLA": "Tesla",
    },
    "利率": {"^TNX": "美債 10 年", "^TYX": "美債 30 年", "^FVX": "美債 5 年"},
    "商品": {
        "GC=F": "黃金", "SI=F": "白銀", "CL=F": "WTI 原油",
        "BZ=F": "布蘭特原油", "HG=F": "銅",
    },
    "匯率與加密": {
        "HKD=X": "美元/港元", "DX-Y.NYB": "美元指數",
        "EURUSD=X": "歐元/美元", "JPY=X": "美元/日圓", "BTC-USD": "比特幣",
    },
    "亞歐市場": {
        "^N225": "日經 225", "^HSI": "恒生指數", "^KS11": "KOSPI",
        "^GDAXI": "德國 DAX", "^FCHI": "法國 CAC 40", "000001.SS": "上證綜合",
    },
}

RATE_SYMBOLS = {"^TNX", "^TYX", "^FVX"}

CHART_LABELS = {
    "^GSPC": "S&P 500", "^DJI": "Dow", "^IXIC": "Nasdaq",
    "^N225": "Nikkei", "^HSI": "Hang Seng", "^KS11": "KOSPI",
    "^GDAXI": "DAX", "AAPL": "AAPL", "MSFT": "MSFT", "NVDA": "NVDA",
    "GOOGL": "GOOGL", "AMZN": "AMZN", "META": "META", "TSLA": "TSLA",
}

NEWS_FEEDS: list[tuple[str, str]] = [
    # Verified live 2026-09-17: Reuters' RSS is 404/DNS-dead and AP returns 403,
    # so they were dropped for working equivalents (this list rots — re-probe
    # before assuming a source is broken for another reason).
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("FT", "https://www.ft.com/rss/home"),
    ("Nasdaq Markets", "https://www.nasdaq.com/feed/rssoutbound?category=Markets"),
    ("Investing.com", "https://www.investing.com/rss/news.rss"),
    ("Guardian Business", "https://www.theguardian.com/uk/business/rss"),
]

# --- constants the ported functions expect -------------------------------
HKT = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).resolve().parent

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")

def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default

def out_dir() -> Path:
    d = Path(os.path.expanduser(env("FIN_OUT_DIR", "~/.hermes/finance_daily")))
    d.mkdir(parents=True, exist_ok=True)
    return d

def http_get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def collect_market() -> tuple[list[dict], str | None]:
    """Return (rows, error). rows: one dict per symbol with last/prev/pct."""
    try:
        import yfinance as yf
    except Exception as exc:  # pragma: no cover
        return [], f"yfinance unavailable: {exc}"

    symbols = [s for g in MARKET_GROUPS.values() for s in g]
    try:
        df = yf.download(symbols, period="7d", interval="1d", progress=False,
                         auto_adjust=False, threads=True, group_by="ticker")
    except Exception as exc:
        return [], f"yfinance download failed: {exc}"

    def closes(sym: str):
        try:
            sub = df[sym]
        except Exception:
            return None
        if sub is None or "Close" not in sub:
            return None
        series = sub["Close"].dropna()
        return series if len(series) else None

    rows: list[dict] = []
    for group, members in MARKET_GROUPS.items():
        for sym, label in members.items():
            series = closes(sym)
            if series is None or len(series) < 2:
                rows.append({"group": group, "symbol": sym, "label": label,
                             "last": None, "prev": None, "pct": None})
                continue
            last = float(series.iloc[-1])
            prev = float(series.iloc[-2])
            pct = (last - prev) / prev * 100.0 if prev else 0.0
            rows.append({"group": group, "symbol": sym, "label": label,
                         "last": last, "prev": prev, "pct": pct})

    ok = [r for r in rows if r["pct"] is not None]
    if not ok:
        return rows, "no market data returned (all symbols empty)"
    return rows, None

def fmt_value(row: dict) -> str:
    if row["last"] is None:
        return "—"
    if row["symbol"] in RATE_SYMBOLS:
        return f"{row['last']:.3f}%"
    if row["last"] >= 1000:
        return f"{row['last']:,.0f}"
    return f"{row['last']:,.2f}"

def fmt_change(row: dict) -> str:
    if row["pct"] is None:
        return "—"
    if row["symbol"] in RATE_SYMBOLS:
        bps = (row["last"] - row["prev"]) * 100.0
        return f"{bps:+.1f} bp"
    return f"{row['pct']:+.2f}%"

def _rss_items(raw: bytes, limit: int = 12) -> list[str]:
    titles: list[str] = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return titles
    for item in root.iter():
        tag = item.tag.split("}")[-1].lower()
        if tag != "item" and tag != "entry":
            continue
        title = ""
        for child in item:
            ctag = child.tag.split("}")[-1].lower()
            if ctag == "title" and child.text:
                title = " ".join(child.text.split())
                break
        if title:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles

def collect_news(max_items: int = 40, per_source: int = 6) -> tuple[list[dict], list[str]]:
    """Headlines, round-robin across sources.

    Round-robin + a per-source cap is deliberate: appending feed-by-feed and
    truncating at the end let one high-volume feed (CNBC returns ~30 items) fill
    the entire quota, so every other outlet was invisible in the digest.
    """
    by_source: list[tuple[str, list[str]]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for source, url in NEWS_FEEDS:
        try:
            raw = http_get(url)
            titles = _rss_items(raw)
            if not titles:
                errors.append(f"{source}: no items")
                continue
            uniq: list[str] = []
            for t in titles:
                key = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", t.lower())[:60]
                if not key or key in seen:
                    continue
                seen.add(key)
                uniq.append(t)
            if uniq:
                by_source.append((source, uniq))
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}: {exc}")

    items: list[dict] = []
    for rank in range(per_source):
        for source, titles in by_source:
            if rank < len(titles):
                items.append({"source": source, "title": titles[rank]})
        if len(items) >= max_items:
            break
    return items[:max_items], errors

def make_chart(rows: list[dict], log) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        log(f"chart skipped: {exc}")
        return None

    picks = [("美國指數", "^GSPC"), ("美國指數", "^DJI"), ("美國指數", "^IXIC"),
             ("亞歐市場", "^N225"), ("亞歐市場", "^HSI"), ("亞歐市場", "^KS11"),
             ("亞歐市場", "^GDAXI")]
    mag7 = ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA"]

    def series(symbols):
        labels, vals = [], []
        for sym in symbols:
            r = next((x for x in rows if x["symbol"] == sym), None)
            if not r or r["pct"] is None:
                continue
            labels.append(CHART_LABELS.get(sym, sym))
            vals.append(r["pct"])
        return labels, vals

    idx_syms = [s for _, s in picks]
    mag_syms = [s for s in mag7]
    i_l, i_v = series(idx_syms)
    m_l, m_v = series(mag_syms)
    if not i_v and not m_v:
        log("chart skipped: no data")
        return None

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), dpi=150)
    fig.patch.set_facecolor("white")
    for ax, labels, vals, title in (
        (axes[0], i_l, i_v, "Indices"),
        (axes[1], m_l, m_v, "Magnificent 7"),
    ):
        if not vals:
            ax.axis("off")
            continue
        colors = ["#c0392b" if v < 0 else "#1e8449" for v in vals]
        y = range(len(labels))
        ax.barh(list(y), vals, color=colors, height=0.62)
        ax.set_yticks(list(y))
        ax.set_yticklabels(labels, fontsize=9)
        ax.invert_yaxis()
        ax.axvline(0, color="#333", linewidth=0.8)
        ax.set_title(title, fontsize=12, fontweight="bold", loc="left")
        ax.grid(axis="x", alpha=0.25, linestyle=":")
        for i, v in enumerate(vals):
            ax.text(v + (0.05 if v >= 0 else -0.05), i, f"{v:+.2f}%",
                    va="center", ha="left" if v >= 0 else "right", fontsize=8.5)
        span = max(abs(min(vals)), abs(max(vals))) * 1.35 or 1
        ax.set_xlim(-span, span)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)

    stamp = datetime.now(HKT)
    fig.suptitle(f"Daily Markets — {stamp.strftime('%Y-%m-%d')} (HKT {stamp.strftime('%H:%M')})",
                 fontsize=13, x=0.012, ha="left", fontweight="bold")
    fig.subplots_adjust(left=0.14, right=0.98, top=0.86, bottom=0.08, wspace=0.22)
    path = out_dir() / f"chart_{stamp.strftime('%Y%m%d')}.png"
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    log(f"chart written: {path}")
    return path

def market_table(rows: list[dict]) -> str:
    parts = ['<table cellspacing="0" cellpadding="6" style="border-collapse:collapse;'
             'width:100%;font-size:13px">']
    for group, members in MARKET_GROUPS.items():
        parts.append(f'<tr><td colspan="3" style="background:#0A1628;color:#C9A84C;'
                     f'font-weight:bold;padding:6px 8px">{group}</td></tr>')
        for sym in members:
            r = next((x for x in rows if x["symbol"] == sym), None)
            if not r:
                continue
            pct = r["pct"]
            color = "#6b7280" if pct is None else ("#c0392b" if pct < 0 else "#1e8449")
            parts.append(
                f'<tr style="border-bottom:1px solid #eee">'
                f'<td style="padding:4px 8px">{r["label"]} '
                f'<span style="color:#999;font-size:11px">{r["symbol"]}</span></td>'
                f'<td align="right" style="padding:4px 8px">{fmt_value(r)}</td>'
                f'<td align="right" style="padding:4px 8px;color:{color};font-weight:bold">'
                f'{fmt_change(r)}</td></tr>')
    parts.append("</table>")
    return "".join(parts)

def build_prompt(rows: list[dict], news: list[dict], now: datetime) -> str:
    table = []
    for group, members in MARKET_GROUPS.items():
        table.append(f"[{group}]")
        for sym in members:
            r = next((x for x in rows if x["symbol"] == sym), None)
            if not r or r["pct"] is None:
                continue
            table.append(f"  {r['label']} ({sym}): {fmt_value(r)}  {fmt_change(r)}")
    headlines = "\n".join(f"- [{n['source']}] {n['title']}" for n in news) or "(none)"

    return f"""你是機構級宏觀策略分析師。以下是 {now.strftime('%Y-%m-%d')} 收市後的市場數據與新聞標題。
輸出必須全部使用繁體中文（香港用語），不得混用簡體字。

【市場數據（全部來自 yfinance，必須原文引用這些數字，不得自行編造或修改）】
{chr(10).join(table)}

【新聞標題（僅標題，來自公開 RSS；可據此推斷事件，但不可虛構細節、數字或引述）】
{headlines}

請用繁體中文（香港）撰寫一份專業日報，格式為 Markdown，必須包含以下章節（無資料的章節寫「暫無資料」，不要省略標題）：

## 市場總覽
（三大指數表現、市場寬度/波動率、一句話總結當日主調）

## 核心事件
（當日最重要的 2-4 個事件，每個 2-3 句。只可基於上面標題所反映的事件；如標題不足以支撐，明確寫「標題未提供細節」）

## 七巨頭與產業
（逐一列出七巨頭當日表現，指出拖累與逆市贏家）

## 利率、債市與商品
（美債 10/30/5 年、原油、黃金、銅，用 bp / % 精確表述）

## 亞歐市場
（日經、恒生、KOSPI、DAX、CAC、上證；與美股對比）

## 後市觀察
（未來 1-2 週關鍵事件與數據，以及需要注意的風險；如無法從標題得知，就寫市場層面的通用觀察並標明為推測）

規則：
- 所有數字必須與上面提供的數據一致；未有提供的數字一律不要寫。
- 不要使用「或許」「可能」等模糊詞描述已發生的事實。
- 不要輸出任何客套話、開場白或結語，直接由「## 市場總覽」開始。
"""
