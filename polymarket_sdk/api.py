"""Polymarket API helpers for the SDK.

This module provides a thin wrapper around the Polymarket CLOB client plus
some convenient functions for fetching markets, balances, positions and
placing orders.  It is mostly a straight port of the code previously living
in ``lib.py`` of the LSMR bot.

Clients of the SDK may import individual helpers such as ``fetch_markets`` or
``execute_trades`` without worrying about the underlying initialization.
"""

import json
import os
import sys
import ssl
import requests
from dotenv import load_dotenv
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.order_builder import builder as _ob
from py_clob_client.order_builder.helpers import (
    round_down, round_normal, round_up, decimal_places, to_token_decimals,
)
from py_order_utils.model import BUY as _UtilsBuy, SELL as _UtilsSell

from .config import PRIVATE_KEY, FUNDER, CHAIN_ID, CLOB_HOST

load_dotenv()  # ensure .env is processed if user only imported this module

# ---------------------------------------------------------------------------
# Patch: fix rounding bug in py_clob_client OrderBuilder
# ---------------------------------------------------------------------------

def _patched_get_market_order_amounts(self, side, amount, price, round_config):
    raw_price = round_normal(price, round_config.price)

    if side == BUY:
        raw_taker_amt = round_down(amount / raw_price, 2)
        raw_maker_amt = raw_taker_amt * raw_price
        if decimal_places(raw_maker_amt) > round_config.amount:
            raw_maker_amt = round_up(raw_maker_amt, round_config.amount)
        return _UtilsBuy, to_token_decimals(raw_maker_amt), to_token_decimals(raw_taker_amt)

    elif side == SELL:
        raw_maker_amt = round_down(amount, 2)
        raw_taker_amt = raw_maker_amt * raw_price
        if decimal_places(raw_taker_amt) > round_config.amount:
            raw_taker_amt = round_up(raw_taker_amt, round_config.amount + 4)
            if decimal_places(raw_taker_amt) > round_config.amount:
                raw_taker_amt = round_down(raw_taker_amt, round_config.amount)
        return _UtilsSell, to_token_decimals(raw_maker_amt), to_token_decimals(raw_taker_amt)

    raise ValueError(f"side must be 'BUY' or 'SELL'")


_ob.OrderBuilder.get_market_order_amounts = _patched_get_market_order_amounts

# --- Validate private key --------------------------------------------------
private_key = PRIVATE_KEY
if not private_key or private_key.strip() == "":
    sys.exit("ERROR: PRIVATE_KEY not set in environment")

# --- SSL context for the tiny Telegram helper that used to live here; kept for
# backward compatibility with utilities that may copy adapatively.  (the
# Telegram code itself has been moved to ``telegram.py``.)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# --- Polymarket client setup ------------------------------------------------
acct = None
client = None

_DUMMY_KEYS = {
    "0x0",
    "0x0000000000000000000000000000000000000000000000000000000000000000",
}
if private_key and private_key not in _DUMMY_KEYS:
    try:
        acct = Account.from_key(private_key)
        client = ClobClient(
            host=CLOB_HOST,
            key=private_key,
            chain_id=CHAIN_ID,
            signature_type=1,
            funder=FUNDER,
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
    except Exception as e:
        print(f"WARNING: failed to initialise Polymarket client: {e}")
        client = None


# ---------------------------------------------------------------------------
# Market-fetching helpers (Gamma API)
# ---------------------------------------------------------------------------

def _raw_fetch_markets(order, limit=60):
    """Fetch raw market dicts from Gamma API sorted by ``order`` (descending)."""
    try:
        resp = requests.get("https://gamma-api.polymarket.com/markets", params={
            "closed": "false",
            "active": "true",
            "limit": limit,
            "order": order,
            "ascending": "false",
        }, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"WARNING: Gamma API ({order}) request failed: {e}")
        return []
    except (json.JSONDecodeError, ValueError) as e:
        print(f"WARNING: Gamma API ({order}) parse error: {e}")
        return []


def _parse_market(m, source_label):
    """Parse a raw Gamma API market dict into a structured dict."""
    try:
        prices = (
            json.loads(m.get("outcomePrices", "[]"))
            if isinstance(m.get("outcomePrices"), str)
            else m.get("outcomePrices", [])
        )
        outcomes = (
            json.loads(m.get("outcomes", "[]"))
            if isinstance(m.get("outcomes"), str)
            else m.get("outcomes", [])
        )
        token_ids = (
            json.loads(m.get("clobTokenIds", "[]"))
            if isinstance(m.get("clobTokenIds"), str)
            else m.get("clobTokenIds", [])
        )

        if not prices or len(prices) < 2 or not token_ids or len(token_ids) < 2:
            return None

        return {
            "question":       m["question"],
            "outcomes":       outcomes,
            "prices":         prices,
            "token_ids":      token_ids,
            "volume24h":      float(m.get("volume24hr", 0)),
            "liquidity":      float(m.get("liquidity", 0)),
            "price_change_1d": float(m.get("oneDayPriceChange") or 0),
            "end_date":       m.get("endDate", ""),
            "slug":           m.get("slug", ""),
            "source":         source_label,
        }
    except (KeyError, ValueError, TypeError) as e:
        print(f"WARNING: Skipping malformed market: {e}")
        return None


def fetch_markets():
    """Return a filtered list of active markets (same behaviour as before).

    The function keeps the same bucketing logic as the original bot
    (top-volume, new, breaking) and applies the same noise and liquidity
    filters.  Constants such as ``MIN_VOLUME_24H`` are intentionally left
    outside the SDK so that individual bots may set their own thresholds.
    """
    seen_slugs: set = set()
    result: list = []

    _NOISE = ("up or down -", "updown", "upmove", "downmove")

    def _is_noise(m):
        q = (m.get("question") or "").lower()
        s = (m.get("slug") or "").lower()
        return any(p in q or p in s for p in _NOISE)

    def _add_batch(raw, label, max_count, min_vol=0, min_liq=0):
        added = 0
        for m in raw:
            if added >= max_count:
                break
            if _is_noise(m):
                continue
            slug = m.get("slug", "")
            if slug and slug in seen_slugs:
                continue
            parsed = _parse_market(m, label)
            if parsed is None:
                continue
            if slug:
                seen_slugs.add(slug)
            result.append(parsed)
            added += 1
        return added

    n_vol = _add_batch(_raw_fetch_markets("volume24hr", 60), "top-volume", 10)
    print(f"  top-volume bucket: {n_vol} markets")

    n_new = _add_batch(_raw_fetch_markets("startDate", 200), "new", 5, min_vol=0, min_liq=15_000)
    print(f"  new bucket: {n_new} markets")

    brk_raw = _raw_fetch_markets("oneDayPriceChange", 100)
    n_brk = _add_batch(brk_raw, "breaking", 5, min_vol=5_000, min_liq=5_000) if brk_raw else 0
    print(f"  breaking bucket: {n_brk} markets")

    print(f"Fetched {len(result)} candidate markets ({n_vol} vol, {n_new} new, {n_brk} breaking)")
    return result


def get_balance():
    """Fetch available USDC balance from Polymarket and return as float."""
    if client is None:
        print("WARNING: get_balance called but Polymarket client is unavailable")
        return 0.0
    try:
        result = client.get_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
        ))
        return float(result.get("balance", 0)) / 1e6
    except Exception as e:
        print(f"ERROR fetching balance: {e}")
        return 0.0


def fetch_positions():
    """Fetch current open positions from the data API."""
    if client is None:
        print("WARNING: fetch_positions called but Polymarket client is unavailable; returning []")
        return []
    try:
        resp = requests.get("https://data-api.polymarket.com/positions", params={
            "user": FUNDER.lower(),
            "sizeThreshold": 0.1,
            "limit": 100,
        }, timeout=30)
        resp.raise_for_status()
        positions = resp.json()
    except requests.RequestException as e:
        print(f"ERROR fetching positions: {e}")
        return []
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR parsing positions response: {e}")
        return []

    result = []
    for p in positions:
        try:
            if p.get("size", 0) <= 0:
                continue
            result.append({
                "market": p.get("market", ""),
                "outcome": p.get("outcome", ""),
                "token_id": p.get("tokenId", ""),
                "size": float(p.get("size", 0)),
                "avg_price": float(p.get("avgPrice", 0)),
                "cur_price": float(p.get("currentPrice", 0)),
                "pnl_pct": float(p.get("pnlPct", 0)),
                "current_value": float(p.get("currentValue", 0)),
                "end_date": p.get("endDate", ""),
            })
        except Exception as e:
            print(f"WARNING: Skipping malformed position: {e}")
    return result


def execute_trades(recommendations, balance, positions=None, markets=None):
    """Execute a list of BUY/SELL recommendations via the Polymarket CLOB.

    Returns ``(results, executed_token_ids, skipped_trades)`` exactly as the
    previous bot version did.  ``recommendations`` should be a list of dicts
    with the same schema that :mod:`bot` produces.
    """
    if client is None:
        print("WARNING: execute_trades called but Polymarket client is unavailable; no orders placed")
        return [], set(), [(r, "client unavailable") for r in recommendations]

    results = []
    executed_token_ids = set()
    skipped_trades = []
    total_buy_spend = 0.0

    pos_lookup = {p["token_id"]: p["size"] for p in (positions or [])}

    market_tokens = {}
    if markets:
        for m in markets:
            market_tokens[m["question"]] = {
                "YES": m["token_ids"][0],
                "NO":  m["token_ids"][1],
            }

    for rec in recommendations:
        action = rec.get("action", "BUY").upper()

        if action in ("HOLD", "PASS"):
            continue

        token_id = rec.get("token_id")
        if not token_id:
            skipped_trades.append((rec, "missing token_id"))
            continue

        # Correct garbled token_ids from upstream
        mkt_name = rec.get("market", "")
        side_str = rec.get("side", "YES").upper()
        if mkt_name in market_tokens:
            correct_id = market_tokens[mkt_name].get(side_str, "")
            if correct_id and token_id != correct_id:
                print(f"  FIX: correcting token_id for {mkt_name} ({side_str})")
                token_id = correct_id

        try:
            amount = round(float(rec.get("amount", 0)), 2)
        except (ValueError, TypeError):
            skipped_trades.append((rec, "invalid amount"))
            continue

        if amount <= 0 and action != "SELL":
            skipped_trades.append((rec, f"amount must be > 0, got {amount}"))
            continue

        if action == "BUY":
            if amount < 1.0:
                skipped_trades.append((rec, f"below $1 min (${amount:.2f})"))
                continue
            if round(total_buy_spend + amount, 2) > round(balance, 2):
                skipped_trades.append((rec, f"would exceed balance"))
                continue
            remaining = round(balance - total_buy_spend - amount, 2)
            if 0 < remaining < 1.0:
                amount = round(balance - total_buy_spend, 2)
            total_buy_spend += amount

        if action == "SELL":
            shares = pos_lookup.get(token_id, 0)
            if shares <= 0:
                skipped_trades.append((rec, "no shares found for token_id"))
                continue
            order_amount = round(shares, 2)
            print(f"\n  SELL: {rec.get('market','?')[:60]} — {rec.get('confidence','')}")
        else:
            order_amount = amount
            print(f"\n  BUY {rec.get('side','?')}: {rec.get('market','?')[:60]}")
            print(f"    p={rec.get('market_price',0):.3f} p_hat={rec.get('my_estimate',0):.3f}"
                  f" EV={rec.get('ev',0):+.3f} amt=${amount:.2f}")

        order_amount = round(order_amount, 4)
        side = BUY if action == "BUY" else SELL

        try:
            if action == "SELL":
                client.update_balance_allowance(BalanceAllowanceParams(
                    asset_type=AssetType.CONDITIONAL, token_id=token_id,
                ))
            else:
                client.update_balance_allowance(BalanceAllowanceParams(
                    asset_type=AssetType.COLLATERAL,
                ))
        except Exception as e:
            print(f"  WARNING: allowance update failed: {e}")

        try:
            order = client.create_market_order(MarketOrderArgs(
                token_id=token_id,
                amount=order_amount,
                side=side,
            ))
            result = client.post_order(order)
            print(f"  Order result: {json.dumps(result, indent=2, default=str)}")
            results.append(result)
            executed_token_ids.add(token_id)
        except Exception as e:
            print(f"  ERROR executing trade: {e}")
            skipped_trades.append((rec, str(e)))

    return results, executed_token_ids, skipped_trades
