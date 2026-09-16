#!/usr/bin/env python3
"""market_llm.py — LLM tiers for the market/news briefing.

Tier 1  api_report()      GEMINI_API_KEY (AI Studio free tier) over REST, with a
                          model ladder: a 429 means that model has no free-tier
                          quota today, so fall through to the next one.
Tier 2  (in yt_gem_daily) the vendored gemini.py cookie path — used locally on
                          a residential IP, and as the pre-existing video path.
Tier 3  fallback_report() OpenAI-compatible FREE slugs (OpenRouter :free), the
                          last resort when Google's free tiers are exhausted.

The tiers must stay independent: a shared failure mode across all of them is the
one thing this chain exists to avoid. Never invent numbers — the prompt carries
the exact market values and the email always ships the raw table next to the
prose so the two can be diffed.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from market_data import env

API_MODELS = [
    "gemini-3.1-pro-preview", "gemini-pro-latest",
    "gemini-3.8-flash", "gemini-3.7-flash",
    "gemini-3.5-flash", "gemini-2.5-flash",
]

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

def api_key() -> str:
    for name in ("FIN_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        v = env(name)
        if v:
            return v
    try:
        import pathlib
        return pathlib.Path(os.path.expanduser("~/.aistudio-cli/apikey")).read_text().strip()
    except Exception:
        return ""

def api_report(prompt: str, log) -> tuple[str | None, str | None, str]:
    """generateContent against the AI Studio key: model ladder + 429/503 retry."""
    key = api_key()
    if not key:
        return None, "no API key (FIN_API_KEY/GEMINI_API_KEY/GOOGLE_API_KEY)", ""
    models = ([env("FIN_API_MODEL")] if env("FIN_API_MODEL") else []) + API_MODELS
    last_err = "no model attempted"
    for model in models:
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        for attempt in (1, 2, 3):
            req = urllib.request.Request(
                f"{API_BASE}/models/{model}:generateContent", data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": key})
            try:
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=int(env("FIN_API_TIMEOUT", "240"))) as r:
                    data = json.loads(r.read())
                text = "".join(p.get("text", "") for p in
                               data["candidates"][0]["content"]["parts"]).strip()
                if len(text) > 400:
                    log(f"api {model}: {len(text)} chars in {time.time()-t0:.0f}s")
                    return text, None, model
                last_err = f"{model}: response too short ({len(text)} chars)"
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = json.loads(exc.read()).get("error", {}).get("message", "")
                except Exception:
                    pass
                last_err = f"{model}: HTTP {exc.code} {detail[:120]}"
                # 429 = quota for this model: move on immediately (another model
                # may still be in quota). 503 = transient demand: back off, retry.
                if exc.code == 503 and attempt < 2:
                    wait = 10 * attempt
                    log(f"api {model}: HTTP 503, retry in {wait}s")
                    time.sleep(wait)
                    continue
                break
            except Exception as exc:
                last_err = f"{model}: {type(exc).__name__}: {exc}"
                break
            time.sleep(3)
        log(f"api {model} unusable — {last_err}")
    return None, last_err, ""

FALLBACK_PROVIDERS = [
    # FREE-ONLY by policy (cost constraint, not a preference) and each slug was
    # probed live before being listed. If one retires, probe OpenRouter's
    # /api/v1/models for another ":free" id — swapping in a paid provider is not
    # an acceptable fix. Probed 2026-09-17: these answered; gemma-4-*:free
    # returned 429, nemotron-3-ultra returned RemoteDisconnected, and nano-omni /
    # content-safety returned empty content (reasoning+safety models).
    {"name": "OpenRouter", "base": "https://openrouter.ai/api/v1",
     "models": ["nvidia/nemotron-3-super-120b-a12b:free",
                "dots-studio/dots-3-note-preview:free",
                "nvidia/nemotron-3.5-lightning:free"],
     "keys": ["FIN_FALLBACK_KEY", "OPENROUTER_API_KEY"]},
]

def fallback_report(prompt: str, log) -> tuple[str | None, str | None, str]:
    """Last-resort OpenAI-compatible chat completion."""
    for prov in FALLBACK_PROVIDERS:
        key = next((env(k) for k in prov["keys"] if env(k)), "")
        if not key:
            continue
        for model in prov["models"]:
            body = json.dumps({"model": model, "max_tokens": 8192,
                               "messages": [{"role": "user", "content": prompt}]}).encode()
            req = urllib.request.Request(
                f"{prov['base']}/chat/completions", data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"})
            try:
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=int(env("FIN_API_TIMEOUT", "240"))) as r:
                    data = json.loads(r.read())
                if not data.get("choices"):
                    # OpenRouter answers 200 with an {"error": ...} body for some
                    # upstream failures — report it instead of raising KeyError.
                    err = (data.get("error") or {}).get("message") or str(data)[:120]
                    log(f"fallback {prov['name']}/{model}: no choices — {err}")
                    continue
                text = (data["choices"][0]["message"].get("content") or "").strip()
                if len(text) > int(env("FIN_MIN_REPORT_CHARS", "200")):
                    log(f"fallback {prov['name']}/{model}: {len(text)} chars in {time.time()-t0:.0f}s")
                    return text, None, f"{prov['name']} ({model})"
                log(f"fallback {prov['name']}/{model}: response too short "
                    f"({len(text)} chars)")
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode()[:120]
                except Exception:
                    pass
                log(f"fallback {prov['name']}/{model}: HTTP {exc.code} {detail}")
            except Exception as exc:
                log(f"fallback {prov['name']}/{model}: {type(exc).__name__}: {exc}")
    return None, "no fallback provider produced a report", ""


def llm_report(prompt: str, log) -> tuple[str | None, str | None, str]:
    """Tier 1 (Gemini API key) -> tier 3 (OpenAI-compatible FREE slugs).

    Returns (text|None, error|None, source). The cookie tier lives in
    yt_gem_daily (it owns the gemini.py auth), so the caller falls back to it
    between these two.
    """
    txt, err, model = api_report(prompt, log)
    if txt:
        return txt, None, f"gemini-api:{model}"
    txt2, err2, src2 = fallback_report(prompt, log)
    if txt2:
        return txt2, None, src2 or "fallback:free-tier"
    return None, (err or err2), "none"
