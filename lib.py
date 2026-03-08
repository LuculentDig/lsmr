"""Backward‑compatibility shim that re‑exports the SDK.

The implementation of the API/Telegram helpers has been moved into
``polymarket_sdk`` so that they can be shared across multiple bot
projects.  This module simply re-exports the public symbols so that existing
code that still imports ``lib`` continues to work for the time being.
"""

from polymarket_sdk.api import *
from polymarket_sdk.telegram import send_telegram, escape_md
