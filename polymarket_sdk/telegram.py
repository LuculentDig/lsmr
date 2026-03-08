"""Telegram utility functions for Polymarket SDK.

Bots can import ``send_telegram`` and ``escape_md`` from here and rely on
configuration values from :mod:`polymarket_sdk.config`.
"""

import urllib.request
import urllib.parse
import ssl

from .config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def escape_md(text: str) -> str:
    """Escape a string for Telegram MarkdownV2.

    The input may be any object; ``str()`` will be called on it.
    """
    for c in r'_*[]()~`>#+-=|{}.!':
        text = str(text).replace(c, f"\\{c}")
    return text


def send_telegram(text: str) -> None:
    """Send a message to the configured Telegram chat.

    Falls back to plain-text if MarkdownV2 parsing fails.
    """
    if not TELEGRAM_TOKEN:
        print("WARNING: TELEGRAM_TOKEN not set, skipping alert")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "MarkdownV2",
        }).encode("utf-8")
        with urllib.request.urlopen(url, data=data, timeout=10, context=SSL_CTX) as resp:
            resp.read()
    except Exception as e:
        print(f"WARNING: Telegram MarkdownV2 failed: {e}, retrying as plaintext")
        try:
            plain = text.replace("\\", "")
            for c in "*_~`":
                plain = plain.replace(c, "")
            data = urllib.parse.urlencode({
                "chat_id": TELEGRAM_CHAT_ID,
                "text": plain,
            }).encode("utf-8")
            with urllib.request.urlopen(url, data=data, timeout=10, context=SSL_CTX) as resp:
                resp.read()
        except Exception as e2:
            print(f"WARNING: Telegram plaintext also failed: {e2}")
