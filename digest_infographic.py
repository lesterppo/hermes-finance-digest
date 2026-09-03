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
    """Run gemini.py --img, return newest PNG path or None."""
    os.makedirs(out_dir, exist_ok=True)
    before = set(glob.glob(os.path.join(out_dir, "*.png")))
    args = [sys.executable if GEMINI_SCRIPT.endswith(".py") else GEMINI_SCRIPT,
            GEMINI_SCRIPT,
            "--img", prompt, "--save-images", out_dir, "--json", "-q"]
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  timeout=timeout)
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

def build_arxiv_prompt(score_lines: list[str], n_recommended: int) -> str:
    """score_lines: raw digest lines like
    '2609.02885 — Title — AGENT 5/5 — TUNE 4/5' (optional markdown **bold)."""
    cards = []
    for ln in score_lines[:4]:
        m = re.match(
            r"[\s*>#-]*\*{0,2}\s*(?:arxiv:?\s*)?(\d{4}\.\d{4,5})\S*\s*\*{0,2}"
            r"\s*[—–-]+\s*(.+?)\s*[—–-]+\s*\*{0,2}\s*AGENT\s*(\d)\s*/\s*5"
            r"\s*\*{0,2}\s*[—–-]+\s*\*{0,2}\s*TUNE\s*(\d)",
            ln, re.I)
        if not m:
            continue
        pid, title, agent, tune = m.groups()
        cards.append(f"card: paper {pid} '{_shorten(title.strip('* '), 30)}' "
                     f"badge AGENT {agent}/5, badge TUNE {tune}/5")
    if not cards:
        return ""
    top_n = min(n_recommended, 3)
    return (f"{STYLE}. Title: 'arXiv cs.AI Daily Picks'. "
            f"{len(cards)} paper cards in a row, each card shows the paper id, "
            f"its short title, and two small score badges (AGENT x/5, TUNE x/5). "
            f" {' '.join(cards)} "
            f"Footer ribbon: '{top_n} recommended today'. No other text.")


# ── yt-gem / finance digest infographic ────────────────────────────────────

def build_videos_prompt(channel_label: str, items: list[dict]) -> str:
    """items: [{title, verdict}] where verdict is a <=6-word takeaway."""
    cards = []
    for it in items[:4]:
        cards.append(f"card: '{_shorten(it.get('verdict') or it.get('title',''), 22)}'")
    if not cards:
        return ""
    return (f"{STYLE}. Title: '{channel_label}'. "
            f"{len(cards)} takeaway cards in a row, each with one icon and "
            f"its short label. {' '.join(cards)} No other text.")


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
