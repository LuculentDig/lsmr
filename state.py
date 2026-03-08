"""Persistent trade-history state for the LSMR bot."""

import json
import os
from datetime import datetime, timezone

from constants import STATE_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"WARNING: Could not load {path}: {e}")
    return default


def _save(path: str, data) -> None:
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"WARNING: Could not save {path}: {e}")


class TradeState:
    """Manages trade history and cycle logs."""

    def __init__(self):
        self._data = _load(STATE_FILE, {"cycles": [], "trades": [], "total_cycles": 0})

    def _save(self):
        _save(STATE_FILE, self._data)

    def record_cycle(self, cycle_number: int, balance: float,
                     recommendations: list, executed_results: list,
                     skipped: list) -> None:
        entry = {
            "cycle":  cycle_number,
            "ts":     _now_iso(),
            "balance": balance,
            "recs_count": len(recommendations),
            "executed_count": len(executed_results),
            "skipped_count":  len(skipped),
            "recs_summary": [
                {
                    "market":       r.get("market", "")[:60],
                    "side":         r.get("side", "?"),
                    "action":       r.get("action", "?"),
                    "amount":       r.get("amount", 0),
                    "market_price": r.get("market_price", 0),
                    "my_estimate":  r.get("my_estimate", 0),
                    "ev":           r.get("ev", 0),
                    "llr":          r.get("llr", 0),
                    "token_id":     r.get("token_id", ""),
                }
                for r in recommendations
            ],
        }
        self._data.setdefault("cycles", []).append(entry)
        self._data["cycles"] = self._data["cycles"][-30:]
        self._data["total_cycles"] = self._data.get("total_cycles", 0) + 1
        self._save()

    def record_trade(self, rec: dict, order_result: dict) -> None:
        trade = {
            "ts":           _now_iso(),
            "market":       rec.get("market", "")[:80],
            "side":         rec.get("side", ""),
            "action":       rec.get("action", ""),
            "token_id":     rec.get("token_id", ""),
            "amount":       rec.get("amount", 0),
            "entry_price":  rec.get("market_price", 0),
            "my_estimate":  rec.get("my_estimate", 0),
            "ev":           rec.get("ev", 0),
            "llr":          rec.get("llr", 0),
            "order_id":     order_result.get("orderID", ""),
            "status":       "open",
            "exit_price":   None,
            "exit_ts":      None,
            "pnl":          None,
        }
        self._data.setdefault("trades", []).append(trade)
        self._data["trades"] = self._data["trades"][-100:]
        self._save()

    def update_trade_status(self, token_id: str, exit_price: float, pnl: float) -> None:
        for t in reversed(self._data.get("trades", [])):
            if t["token_id"] == token_id and t["status"] == "open":
                t["status"]     = "closed"
                t["exit_price"] = exit_price
                t["exit_ts"]    = _now_iso()
                t["pnl"]        = pnl
                break
        self._save()

    def get_cycle_count(self) -> int:
        return self._data.get("total_cycles", len(self._data.get("cycles", [])))

    def get_recent_cycles(self, n: int = 10) -> list:
        return self._data.get("cycles", [])[-n:]

    def get_open_trades(self) -> list:
        return [t for t in self._data.get("trades", []) if t.get("status") == "open"]

    def get_all_trades(self) -> list:
        return self._data.get("trades", [])

    def get_realised_pnl(self) -> float:
        return sum(
            t.get("pnl") or 0
            for t in self._data.get("trades", [])
            if t.get("status") == "closed"
        )
