#!/usr/bin/env python3
"""Daily: refresh NLM_STORAGE_STATE_GZ secret from local NotebookLM session."""
import json, gzip, base64, os, subprocess, sys
from datetime import datetime

REPOS = ["lesterppo/arxiv-gem-digest", "lesterppo/yt-finance-digest"]
AUTH_NAMES = {"__Secure-1PSID","__Secure-1PSIDTS","__Secure-3PSID","__Secure-3PSIDTS",
              "SID","SSID","HSID","APISID","SAPISID","__Secure-1PAPISID",
              "__Secure-3PAPISID","SIDCC","__Secure-3PSIDCC","NID","LOGIN_INFO",
              "LSID","ACCOUNT_CHOOSER","OSID","__Host-1PLSID"}
SRC = os.path.expanduser("~/.notebooklm/profiles/default/storage_state.json")

def log(m): print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)

def main():
    log("NLM cookie sync starting")
    # refresh local session first (live check)
    r = subprocess.run([sys.executable, os.path.expanduser("~/.hermes/scripts/nlm.py"),
                        "auth", "init"], capture_output=True, text=True, timeout=180)
    if '"s": "ok"' not in r.stdout:
        log(f"auth init FAILED: {r.stdout[:150]}")
        return 1
    d = json.load(open(SRC))
    # keep ALL .google.com cookies — pruning to "auth" names broke NLM RPC
    # (UNEXPECTED_ERROR auth on CI even though the full set works)
    keep = [c for c in d.get("cookies", [])
            if c.get("domain", "").endswith("google.com")]
    d["cookies"] = keep
    d["origins"] = []
    b64 = base64.b64encode(gzip.compress(
        json.dumps(d, separators=(",", ":")).encode(), 9)).decode()
    rc = 0
    for repo in REPOS:
        p = subprocess.run(["gh", "secret", "set", "NLM_STORAGE_STATE_GZ",
                            "-R", repo, "--body", b64],
                           capture_output=True, text=True)
        log(f"{repo}: {'ok' if p.returncode == 0 else p.stderr[:120]}")
        rc |= p.returncode
    log("done")
    return rc

if __name__ == "__main__":
    sys.exit(main())
