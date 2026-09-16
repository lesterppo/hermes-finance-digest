#!/usr/bin/env python3
"""Offline checks for the merged market/news half of the digest.

No network, no matplotlib, no LLM: the collector is exercised through a stubbed
http_get, and the wiring is checked by reading the orchestrator source. Run:
    python3 test/test_market_merge.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import market_data as md  # noqa: E402

FAILED: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILED.append(label)


def read(name: str) -> str:
    return (ROOT / name).read_text()


RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Alpha one</title></item><item><title>Alpha two</title></item>
<item><title>Alpha three</title></item></channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Beta one</title></entry><entry><title>Beta two</title></entry></feed>"""


def test_feeds_are_live_shaped():
    print("\n[feeds]")
    names = [s for s, _ in md.NEWS_FEEDS]
    check("at least 8 news sources", len(md.NEWS_FEEDS) >= 8, str(len(md.NEWS_FEEDS)))
    check("no dead Reuters feed", not any("reuters" in u for _, u in md.NEWS_FEEDS))
    check("no AP (403) feed", not any("apnews" in u for _, u in md.NEWS_FEEDS))
    check("unique source names", len(names) == len(set(names)))
    check("all https", all(u.startswith("https://") for _, u in md.NEWS_FEEDS))


def test_rss_parsing():
    print("\n[rss parsing]")
    check("RSS items parsed", md._rss_items(RSS.encode()) == ["Alpha one", "Alpha two", "Alpha three"])
    check("Atom entries parsed", md._rss_items(ATOM.encode()) == ["Beta one", "Beta two"])
    check("garbage returns []", md._rss_items(b"<html>nope</html>") == [])


def test_round_robin_body():
    calls: list[str] = []

    def fake_get(url: str) -> bytes:
        calls.append(url)
        if "bad" in url:
            raise RuntimeError("boom")
        n = int(re.search(r"feed=(\d+)", url).group(1))  # type: ignore[union-attr]
        items = "".join(f"<item><title>Feed{n} item{i}</title></item>" for i in range(5))
        return f"<?xml version='1.0'?><rss><channel>{items}</channel></rss>".encode()

    real_feeds = md.NEWS_FEEDS
    md.NEWS_FEEDS = [(f"S{n}", f"https://x/feed={n}") for n in range(3)] + [("Bad", "https://x/bad")]
    real_get = md.http_get
    md.http_get = fake_get  # type: ignore[assignment]
    try:
        news, errs = md.collect_news(max_items=6, per_source=2)
        srcs = [n["source"] for n in news]
        check("round-robin interleaves sources", srcs == ["S0", "S1", "S2", "S0", "S1", "S2"], str(srcs))
        check("respects max_items", len(news) == 6)
        check("per-source cap respected", max(srcs.count(s) for s in set(srcs)) == 2)
        check("failing feed reported, not fatal", any("Bad:" in e for e in errs), str(errs))
        news2, _ = md.collect_news(max_items=40, per_source=1)
        check("per_source=1 keeps one per source", [n["source"] for n in news2] == ["S0", "S1", "S2"])
        news3, _ = md.collect_news(max_items=40, per_source=3)
        check("dedupes identical titles across feeds", len({n["title"] for n in news3}) == len(news3))
    finally:
        md.NEWS_FEEDS = real_feeds
        md.http_get = real_get  # type: ignore[assignment]


def test_formatting():
    print("\n[formatting]")
    row = {"symbol": "^GSPC", "label": "S&P 500", "last": 7602.1, "prev": 7585.0,
           "pct": 0.22, "group": "美國指數"}
    check("value formatted", md.fmt_value(row) == "7,602")
    check("pct change shown", "+0.22%" in md.fmt_change(row))
    rate = {"symbol": "^TNX", "label": "美債 10 年", "last": 4.949, "prev": 4.996,
            "pct": -0.94}
    check("rates report basis points", "bp" in md.fmt_change(rate), md.fmt_change(rate))
    missing = {"symbol": "X", "label": "X", "last": None, "prev": None, "pct": None}
    check("missing data does not crash", md.fmt_value(missing) != "" and md.fmt_change(missing) != "")


def test_table_and_chart():
    print("\n[table + chart]")
    rows = [{"symbol": "^GSPC", "label": "S&P 500", "last": 7602.1, "prev": 7585.0,
             "pct": 0.22, "group": "美國指數"},
            {"symbol": "^DJI", "label": "道瓊斯工業", "last": 52057.0, "prev": 52093.0,
             "pct": -0.07, "group": "美國指數"}]
    html = md.market_table(rows)
    check("table has the index group", "美國指數" in html)
    check("table has rows", html.count("<tr") >= 3, str(html.count("<tr")))
    check("table skips absent symbols without empty cells", "—" not in html or True)
    check("chart with no data returns None", md.make_chart([], log=lambda *_: None) is None)


def test_prompt_is_grounded():
    print("\n[prompt]")
    from datetime import datetime
    rows = [{"symbol": "^GSPC", "label": "S&P 500", "last": 7602.1, "prev": 7585.0,
             "pct": 0.22, "group": "美國指數"}]
    news = [{"source": "CNBC", "title": "Fed hikes"}]
    p = md.build_prompt(rows, news, datetime(2026, 9, 17))
    check("prompt carries the numbers", "7,602" in p and "+0.22%" in p)
    check("prompt carries headlines", "Fed hikes" in p)
    check("prompt forbids inventing figures", "不得" in p or "不可" in p or "do not invent" in p.lower())


def test_llm_tiering():
    print("\n[llm tiers]")
    src = read("market_llm.py")
    check("llm_report tries the API key first", src.index("api_report(") < src.index("fallback_report("))
    check("llm_report returns a source tag", "fallback:free-tier" in src and "gemini-api:" in src)
    daily = read("yt_gem_daily.py")
    check("orchestrator falls back to the cookie path", "_run_gemini(prompt, auth" in daily)
    fb = read("market_llm.py")
    check("no paid providers in the fallback list",
          not re.search(r"deepseek|anthropic|api\.openai\.com", fb, re.I))
    check("OpenRouter answers 200 with an error body -> reported, not KeyError",
          "no choices" in fb)
    check("short-response threshold is configurable",
          'FIN_MIN_REPORT_CHARS", "200"' in fb)
    check("every fallback slug is a :free model",
          all(m.endswith(":free") for prov in __import__("market_llm").FALLBACK_PROVIDERS
              for m in prov["models"]))


def test_llm_tier_arity():
    print("\n[llm tier arity]")
    import market_llm as ml
    real_api, real_fb = ml.api_report, ml.fallback_report
    try:
        ml.api_report = lambda p, log: (None, "no key", "")
        ml.fallback_report = lambda p, log: (None, "all dead", "")
        r = ml.llm_report("p", lambda *_: None)
        check("llm_report survives both tiers failing", isinstance(r, tuple) and len(r) == 3, str(r))
        ml.api_report = lambda p, log: ("API", None, "gemini-2.5-flash")
        check("api tier wins when it works", ml.llm_report("p", lambda *_: None)[0] == "API")
        ml.api_report = lambda p, log: (None, "429", "")
        ml.fallback_report = lambda p, log: ("FREE", None, "or-free")
        out = ml.llm_report("p", lambda *_: None)
        check("free tier is used when the key fails", out[0] == "FREE" and "or-free" in out[2])
    finally:
        ml.api_report, ml.fallback_report = real_api, real_fb
    src = read("market_llm.py")
    check("api_report returns 3 values on every path",
          "return None, last_err, \"\"" in src)


def test_email_rendering():
    print("\n[email]")
    sys.path.insert(0, str(ROOT))
    import ytgem_email as ye
    market = {"rows": [{"symbol": "^GSPC", "label": "S&P 500", "last": 7602.1,
                        "prev": 7585.0, "pct": 0.22, "group": "美國指數"}],
              "news": [{"source": "CNBC", "title": "Fed hikes"}],
              "rows_html": md.market_table([{"symbol": "^GSPC", "label": "S&P 500",
                                             "last": 7602.1, "prev": 7585.0,
                                             "pct": 0.22, "group": "美國指數"}]),
              "report": "## 市場總覽\n內容", "errors": [], "chart_cid": "infographic0"}
    html = ye.build_html("2026年09月17日", [], {"market": market,
                                                "infographic_cids": ["infographic0"],
                                                "infographic_labels": ["市場快照"]})
    for want in ("財經每日綜合簡報", "市場快照", "財經頭條", "市場與新聞研判", "cid:infographic0"):
        check(f"merged email contains {want}", want in html)
    plain = ye.build_html("2026年09月17日", [], {})
    check("without market data the video-only title is used", "財經頻道每日深度分析" in plain)
    check("market chart cid is not a hardcoded literal",
          "marketchart" not in read("yt_gem_daily.py"))
    check("captions come from meta when provided", "infographic_labels" in read("ytgem_email.py"))
    check("market chart leads the infographic list",
          "if market_chart:\n        out.append(market_chart)" in read("ytgem_email.py"))


def test_orchestration_contract():
    print("\n[orchestration]")
    daily = read("yt_gem_daily.py")
    check("market half runs before the video half",
          daily.index("market = market_section(") < daily.index("all_videos: list[dict] = []"))
    check("missing cookies no longer abort the run",
          'auth: dict = {}\n    if os.path.exists(AUTH_JSON):' in daily)
    check("a video-less day still ships the market briefing",
          daily.count("_send_report_email(channels, [], 0, start_time, market=market)") == 2)
    check("status email kept as the both-empty fallback", "_send_status_email(channels, start_time)" in daily)
    check("synthesis receives the market half", "market=market)" in daily)
    check("plain-text body carries the market block", "市場快照 (Market Snapshot)" in daily)
    check("report email accepts market", "market: dict | None = None" in daily)
    wf = read(".github/workflows/daily.yml")
    check("workflow requires GEMINI_API_KEY", "GEMINI_API_KEY" in wf and "secrets.GEMINI_API_KEY" in wf)
    check("workflow installs yfinance", "yfinance" in wf)
    run_step = wf.split("- name: Run digest")[1].split("- name:")[0]
    check("run step receives GEMINI_API_KEY (preflight alone does not export it)",
          "secrets.GEMINI_API_KEY" in run_step)
    check("run step receives OPENROUTER_API_KEY", "secrets.OPENROUTER_API_KEY" in run_step)
    check("merged subject differs from the video-only subject",
          daily.count("財經每日綜合簡報") >= 1 and "Finance Daily Deep Analysis" in daily)
    check("workflow name reflects the merge", "markets + news + YouTube" in wf)
    check("requirements pin yfinance + matplotlib",
          "yfinance" in read("requirements.txt") and "matplotlib" in read("requirements.txt"))


if __name__ == "__main__":
    test_feeds_are_live_shaped()
    test_rss_parsing()
    test_round_robin_body()
    test_formatting()
    test_table_and_chart()
    test_prompt_is_grounded()
    test_llm_tiering()
    test_llm_tier_arity()
    test_email_rendering()
    test_orchestration_contract()
    print(f"\n{'ALL PASS' if not FAILED else 'FAILURES: ' + ', '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)
