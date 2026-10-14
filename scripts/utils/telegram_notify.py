"""
Centralized Telegram notification utility.

Usage (Python):
    from scripts.utils.telegram_notify import send

    send("Hello from pipeline")                    # plain text
    send("*Bold* text", parse_mode="Markdown")     # markdown (auto-retries plain if 400)

Usage (CLI from GitHub Actions / shell):
    python -m scripts.utils.telegram_notify "Pipeline complete"
    python -m scripts.utils.telegram_notify "Step failed" --chat-id "$CHAT_ID"

Pipeline Auto-Fix Rule (CLAUDE.md):
    Telegram HTTP 400 fires when message contains unescaped Markdown special chars.
    Fix: catch 400, retry without parse_mode. Plain text never fails on special chars.
"""
import os
import sys
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_DEFAULT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_DEFAULT_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


def send(
    text: str,
    parse_mode: str | None = "Markdown",
    token: str = "",
    chat_id: str = "",
) -> bool:
    """
    Send a Telegram message. Returns True on success, False on failure.

    Auto-retries without parse_mode on HTTP 400 (unescaped Markdown chars).
    """
    token = token or _DEFAULT_TOKEN
    chat_id = chat_id or _DEFAULT_CHAT
    if not token or not chat_id:
        print("[telegram_notify] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping", file=sys.stderr)
        return False

    url = _API_BASE.format(token=token)
    payload: dict = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 400 and parse_mode:
            # Markdown parse failed — retry as plain text
            payload.pop("parse_mode", None)
            resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"[telegram_notify] Error: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send a Telegram message")
    parser.add_argument("text", help="Message text")
    parser.add_argument("--chat-id", default="", help="Override TELEGRAM_CHAT_ID")
    parser.add_argument("--token", default="", help="Override TELEGRAM_BOT_TOKEN")
    parser.add_argument("--no-markdown", action="store_true", help="Send as plain text")
    args = parser.parse_args()

    ok = send(
        args.text,
        parse_mode=None if args.no_markdown else "Markdown",
        token=args.token,
        chat_id=args.chat_id,
    )
    sys.exit(0 if ok else 1)
