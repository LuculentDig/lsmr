"""Polymarket SDK configuration helpers.

This module reads and exposes environment variables commonly used by
Polymarket bots.  Bots should import values from here instead of accessing
`os.environ` directly.  The SDK calls ``dotenv.load_dotenv()`` so that a
`.env` file in the project root is automatically processed.

Expected environment variables:

  PRIVATE_KEY           - signer for Polymarket orders
  FUNDER                - address to use for data API queries (lowercase)
  CHAIN_ID              - Polygon chain id (default: 137)
  CLOB_HOST             - URL of the Polymarket CLOB host

  TELEGRAM_TOKEN        - bot token from @BotFather
  TELEGRAM_CHAT_ID      - numeric chat id to send alerts to

  # optional operational overrides:
  CYCLE_INTERVAL_HOURS  - default polling interval (float)
  DRY_RUN               - set to "1" to disable actual orders

  # VPN / Docker helpers (used by the provided scripts):
  NORDVPN_USERNAME
  NORDVPN_PASSWORD
  NORDVPN_TOKEN

"""
import os
from dotenv import load_dotenv

# load configuration from .env, if present
load_dotenv()

# --- Polymarket connection --------------------------------------------------
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
FUNDER = os.getenv("FUNDER", "").lower()
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")

# --- Telegram ----------------------------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# --- Operational overrides ---------------------------------------------------
CYCLE_INTERVAL_HOURS = float(os.getenv("CYCLE_INTERVAL_HOURS", "1"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip() == "1"

# --- VPN / Docker ------------------------------------------------------------
NORDVPN_USERNAME = os.getenv("NORDVPN_USERNAME")
NORDVPN_PASSWORD = os.getenv("NORDVPN_PASSWORD")
NORDVPN_TOKEN = os.getenv("NORDVPN_TOKEN")

# ---------------------------------------------------------------------------
# sanity checks
# ---------------------------------------------------------------------------
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set in environment")
