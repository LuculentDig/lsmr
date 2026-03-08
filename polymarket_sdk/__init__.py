"""Lightweight Polymarket SDK package.

Importing this package will load configuration and make the three main
submodules available::

    from polymarket_sdk import config, api, telegram

"""

from . import config, api, telegram

__all__ = ["config", "api", "telegram"]
