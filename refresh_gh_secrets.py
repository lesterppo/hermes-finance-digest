#!/usr/bin/env python3
"""
Refresh GitHub Secrets with latest Gemini cookies from auth.json.

Scheduled: cron job (no_agent=true), every 7 days.
Usage:
    python refresh_gh_secrets.py owner/repo

Requires: pynacl, gh CLI authenticated.
"""

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

import nacl.encoding
import nacl.public

AUTH_JSON = os.path.expanduser("~/.gemini-cli/auth.json")
SECRETS = ["GEMINI_SID", "GEMINI_TS"]
AUTH_KEYS = {"GEMINI_SID": "__Secure-1PSID", "GEMINI_TS": "__Secure-1PSIDTS"}


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def get_github_token() -> str:
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"gh auth token failed: {result.stderr.strip()}")
    return result.stdout.strip()


def github_api(token: str, repo: str, method: str, path: str, data: dict | None = None):
    url = f"https://api.github.com/repos/{repo}/actions/secrets{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = json.dumps(data).encode() if data else None
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:500]
        raise RuntimeError(f"GitHub API {method} {path}: HTTP {e.code} — {err_body}")


def encrypt_secret(public_key_b64: str, value: str) -> str:
    pk_bytes = nacl.public.PublicKey(public_key_b64.encode(), nacl.encoding.Base64Encoder)
    box = nacl.public.SealedBox(pk_bytes)
    return base64.b64encode(box.encrypt(value.encode())).decode()


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python refresh_gh_secrets.py owner/repo")
        return 1

    repo = sys.argv[1]
    log(f"Refreshing secrets for {repo}")

    if not os.path.exists(AUTH_JSON):
        log(f"ERROR: {AUTH_JSON} not found")
        return 1

    with open(AUTH_JSON) as f:
        auth = json.load(f)

    token = get_github_token()

    pubkey_data = github_api(token, repo, "GET", "/public-key")
    if not pubkey_data:
        log("ERROR: Could not fetch repo public key")
        return 1

    for secret_name in SECRETS:
        auth_key = AUTH_KEYS[secret_name]
        cookie_value = auth.get(auth_key, "")
        if not cookie_value:
            log(f"WARNING: {auth_key} not found — skipping {secret_name}")
            continue

        encrypted = encrypt_secret(pubkey_data["key"], cookie_value)
        github_api(token, repo, "PUT", f"/{secret_name}", data={
            "encrypted_value": encrypted,
            "key_id": pubkey_data["key_id"],
        })
        log(f"  {secret_name} updated ({len(cookie_value)} chars)")

    log("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
