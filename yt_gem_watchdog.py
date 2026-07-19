#!/usr/bin/env python3
"""
Watchdog: checks that the YouTube Finance Daily Digest has run successfully
in the last N hours. Sends an alert email if silent.

Configure via environment variables (same as yt_gem_daily.py).
"""

import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

HEARTBEAT_FILE = os.path.expanduser(
    os.environ.get("YT_GEM_HEARTBEAT_FILE", "~/.hermes/yt_gem_heartbeat"))
SILENCE_HOURS = int(os.environ.get("YT_GEM_WATCHDOG_SILENCE_HOURS", "48"))

SMTP_USER = os.environ.get("YT_GEM_SMTP_USER", "")
SMTP_PASS = os.environ.get("YT_GEM_SMTP_PASS", "")
SMTP_SERVER = os.environ.get("YT_GEM_SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("YT_GEM_SMTP_PORT", "465"))
RECIPIENT = os.environ.get("YT_GEM_RECIPIENT", "")


def main() -> int:
    now = datetime.now(timezone.utc)

    if not SMTP_USER or not SMTP_PASS or not RECIPIENT:
        print("ERROR: SMTP not configured — set YT_GEM_SMTP_USER, YT_GEM_SMTP_PASS, YT_GEM_RECIPIENT")
        return 1

    if not os.path.exists(HEARTBEAT_FILE):
        _alert("心跳檔案不存在", "從未成功執行過，或檔案被刪除。")
        return 1

    try:
        with open(HEARTBEAT_FILE) as f:
            ts_str = f.read().strip()
        last_beat = datetime.fromisoformat(ts_str)
    except (ValueError, IOError):
        _alert("心跳檔案損壞", f"無法讀取時間戳: {HEARTBEAT_FILE}")
        return 1

    silence = now - last_beat
    if silence > timedelta(hours=SILENCE_HOURS):
        hours = silence.total_seconds() / 3600
        _alert(
            f"⚠️ 財經頻道分析系統靜默 {hours:.0f} 小時",
            f"最後成功執行: {last_beat.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"當前時間: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"靜默時長: {hours:.0f} 小時（閾值: {SILENCE_HOURS}h）\n\n"
            f"可能原因: auth cookie 過期、gemini-cli 故障、網路問題、排程器停擺\n"
            f"修復: gemini-cli --init 更新 cookie，然後手動運行 yt-gem-daily.py"
        )
        return 1

    print(f"Watchdog OK — last heartbeat {silence.total_seconds()/3600:.1f}h ago")
    return 0


def _alert(subject: str, detail: str) -> None:
    body = f"""YouTube Finance Daily Digest — 警報

{detail}

系統名稱: YouTube Finance Daily Digest (yt_gem_daily.py)
引擎: gemini.py webapi (Flash Extended Thinking)
"""
    try:
        msg = MIMEText(body, _charset="utf-8", _subtype="plain")
        msg["From"] = SMTP_USER
        msg["To"] = RECIPIENT
        msg["Subject"] = subject
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        print(f"Alert sent: {subject}")
    except Exception as e:
        print(f"ERROR sending alert: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
