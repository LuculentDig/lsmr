#!/usr/bin/env python3
"""LSMR + Bayesian Signal Polymarket Trading Bot.

Strategy derived from QR-PM-2026-0041 (February 2026):

  Page 1 -- LSMR pricing mechanics:
    C(q) = b * ln( sum e^(qi/b) )         cost function
    p_i  = softmax(q/b)_i                 instantaneous price
    Cost = C(q + delta*e_i) - C(q)        cost of a trade

  Page 3 -- Bayesian decision architecture:
    P(H|D) = P(D|H)*P(H) / P(D)           Bayes theorem
    log P(H|D) = logP(H) + sum logP(Dk|H) - logZ   sequential update
    EV = p_hat - p                         entry signal

  Entry condition:  |EV| >= MIN_EV
  Position sizing:  fractional Kelly
    YES: kelly_frac = (p_hat - p) / (1 - p) * KELLY_FRACTION
    NO:  kelly_frac = (p_hat_no - p_no) / (1 - p_no) * KELLY_FRACTION
    [Document annotation: "NEVER full Kelly on 5min markets!"]
    Additional scale-down applied when days_to_expiry < 1.

Run:
    python bot.py
    DRY_RUN=1 python bot.py          # simulate, no orders placed
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

from polymarket_sdk.api import (
    get_balance,
    fetch_markets,
    fetch_markets_for_tokens,
    fetch_positions,
    execute_trades,
)
from polymarket_sdk.telegram import send_telegram, escape_md
from constants import (
    CYCLE_INTERVAL_HOURS,
    EMERGENCY_STOP_FRACTION,
    MIN_BALANCE_FRACTION,
    MAX_OPEN_POSITIONS,
    MIN_EV,
    KELLY_FRACTION,
    MAX_TRADE_FRACTION,
    MIN_TRADE_AMOUNT,
    MIN_VOLUME_24H,
    MIN_LIQUIDITY,
    STOP_LOSS_FRAC,
    EXIT_EV_THRESHOLD,
    LSMR_B,
)
from state import TradeState
from lsmr_engine import inefficiency_ev, infer_quantities, trade_cost
from bayesian_engine import MarketSignals, compute_posterior

DRY_RUN = os.getenv("DRY_RUN", "0").strip() == "1"


# ---------------------------------------------------------------------------
# Kelly sizing helpers
# ---------------------------------------------------------------------------

def _kelly_size(p_hat: float, market_price: float, balance: float,
                kelly_scale: float = 1.0) -> float:
    """Fractional Kelly bet size for a binary market.

    YES bet kelly fraction = (p_hat - p) / (1 - p)
    (Derived from standard Kelly: f* = (p*b - (1-p)) / b, b = 1/p - 1)

    kelly_scale allows additional scaling for near-expiry / volatile markets.
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0
    ev = p_hat - market_price
    if ev <= 0:
        return 0.0
    raw_frac = ev / (1.0 - market_price) * KELLY_FRACTION * kelly_scale
    raw_amt = raw_frac * balance
    capped = min(raw_amt, balance * MAX_TRADE_FRACTION)
    return round(max(capped, 0.0), 2)


def _kelly_scale_for_expiry(days_to_expiry) -> float:
    """Apply additional Kelly reduction for short-dated / near-expiry markets.

    "NEVER full Kelly on 5min markets!" (document annotation, p.3)
    Scale approaches 0.1 as days -> 0 (very short-dated or intraday).
    """
    if days_to_expiry is None or days_to_expiry >= 30:
        return 1.0
    if days_to_expiry < 1:
        return 0.10   # near-resolution: very conservative
    if days_to_expiry < 7:
        return 0.50   # week-out: half the usual bet
    return 1.0


# ---------------------------------------------------------------------------
# Exit signal detection
# ---------------------------------------------------------------------------

def _check_exits(positions: list, held_market_data: dict = None) -> list:
    """Return SELL recommendations for any positions that hit an exit rule.

    Exit rules:
      1. Stop-loss:        cur_price < avg_price * STOP_LOSS_FRAC
      2. Near-expiry cut:  days_left <= 3  AND  pnl_pct < -20
      3. EV-gap closed:    Bayesian p_hat - cur_price < EXIT_EV_THRESHOLD
                           Same signal that triggered entry — exit when edge is gone.
    """
    exits = []
    print(f"  Checking exits: {len(positions)} positions, market data fetched for {len(held_market_data or {})} tokens")
    for p in positions:
        cur   = p.get("cur_price", 0)
        avg   = p.get("avg_price", 0)
        pnl_p = p.get("pnl_pct", 0)
        value = p.get("current_value", 0)
        token = p.get("token_id", "")

        if avg <= 0 or cur <= 0 or value < 0.50:
            continue

        reason = None

        # Rule 1: Stop-loss
        if cur < avg * STOP_LOSS_FRAC:
            reason = f"STOP-LOSS: cur={cur:.3f} < {avg * STOP_LOSS_FRAC:.3f}"
        else:
            # Compute days_to_expiry for rules 2 and 3
            days_to_expiry = None
            end_date = p.get("end_date", "")
            if end_date:
                try:
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    days_to_expiry = max(0.0, (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400)
                except Exception:
                    pass

            # Rule 2: Near-expiry cut
            if days_to_expiry is not None and days_to_expiry <= 3 and pnl_p < -20:
                reason = f"NEAR-EXPIRY-CUT: {days_to_expiry:.0f}d left, PnL={pnl_p:+.1f}%"

            # Rule 3: EV-gap closed — re-run Bayesian model on current price
            if reason is None:
                mkt = (held_market_data or {}).get(token)
                vol_ratio  = mkt["volume24h"] / max(MIN_VOLUME_24H, 1) if mkt else None
                liq_ratio  = mkt["liquidity"] / max(MIN_LIQUIDITY, 1)  if mkt else None
                price_chg  = mkt.get("price_change_1d")                if mkt else None
                sig = MarketSignals(
                    market_price    = cur,
                    volume_ratio    = vol_ratio,
                    price_change_1d = price_chg,
                    days_to_expiry  = days_to_expiry,
                    liquidity_ratio = liq_ratio,
                )
                p_hat, _ = compute_posterior(sig)
                ev = p_hat - cur
                signals_label = "full" if mkt else "price+expiry only"
                print(f"    {p.get('market','?')[:45]} | p={cur:.3f} p̂={p_hat:.3f} ev={ev:+.3f} [{signals_label}]")
                if ev < EXIT_EV_THRESHOLD:
                    reason = f"EV-GAP-CLOSED: ev={ev:+.3f}, p={cur:.3f} p̂={p_hat:.3f}"

        if reason:
            exits.append({
                "action":     "SELL",
                "market":     p.get("market", "")[:60],
                "token_id":   token,
                "side":       p.get("outcome", "YES"),
                "amount":     round(value, 2),
                "confidence": reason,
            })
            print(f"  EXIT: {p.get('market','?')[:50]} — {reason}")

    return exits


# ---------------------------------------------------------------------------
# Bayesian market analysis
# ---------------------------------------------------------------------------

def _analyse_market(market: dict, balance: float,
                    held_tokens: set) -> list:
    """Run the LSMR + Bayesian pipeline on one market.

    Steps:
      1. Read LSMR prices (p) from Gamma API data
      2. Infer implied quantity vector q = infer_quantities(prices, b)
      3. Compute trade cost estimate for normalisation reference
      4. Compute Bayesian posterior p_hat via compute_posterior()
      5. EV = p_hat - p  (Eq.4, p.3)
      6. If |EV| >= MIN_EV, generate a BUY recommendation with Kelly size

    Returns a list of 0, 1 or 2 recommendation dicts (YES and/or NO).
    """
    yes_price  = float(market["prices"][0])
    no_price   = float(market["prices"][1])
    yes_token  = market["token_ids"][0]
    no_token   = market["token_ids"][1]

    # Skip markets where we already hold a token
    if yes_token in held_tokens or no_token in held_tokens:
        return []

    # --- Compute signals for Bayesian update ---
    days_to_expiry = None
    if market.get("end_date"):
        try:
            end_dt = datetime.fromisoformat(market["end_date"].replace("Z", "+00:00"))
            days_to_expiry = max(0.0, (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400)
        except Exception:
            pass

    volume_ratio  = market["volume24h"] / MAX(MIN_VOLUME_24H, 1)
    liq_ratio     = market["liquidity"] / MAX(MIN_LIQUIDITY, 1)
    price_chg_1d  = market.get("price_change_1d")   # fractional YES price change

    kelly_scale = _kelly_scale_for_expiry(days_to_expiry)

    # --- LSMR quantity recovery (reference) ---
    qs = infer_quantities([yes_price, no_price], LSMR_B)
    # trade_cost reference: cost to buy 1 share of YES at current state
    cost_1_yes = trade_cost(qs, LSMR_B, 0, 1.0)

    recs = []

    # ── YES analysis ────────────────────────────────────────────────────
    sig_yes = MarketSignals(
        market_price    = yes_price,
        volume_ratio    = volume_ratio,
        price_change_1d = price_chg_1d,
        days_to_expiry  = days_to_expiry,
        liquidity_ratio = liq_ratio,
    )
    p_hat_yes, llr_yes = compute_posterior(sig_yes)
    ev_yes = inefficiency_ev(yes_price, p_hat_yes)   # p_hat - p

    if ev_yes >= MIN_EV:
        size = _kelly_size(p_hat_yes, yes_price, balance, kelly_scale)
        if size >= MIN_TRADE_AMOUNT:
            recs.append({
                "action":        "BUY",
                "market":        market["question"],
                "token_id":      yes_token,
                "side":          "YES",
                "amount":        size,
                "market_price":  yes_price,
                "my_estimate":   round(p_hat_yes, 4),
                "ev":            round(ev_yes, 4),
                "llr":           round(llr_yes, 4),
                "cost_1_share":  round(cost_1_yes, 4),
                "kelly_scale":   kelly_scale,
                "slug":          market.get("slug", ""),
                "source":        market.get("source", ""),
            })

    # ── NO analysis ─────────────────────────────────────────────────────
    sig_no = MarketSignals(
        market_price    = no_price,
        volume_ratio    = volume_ratio,
        price_change_1d = (-price_chg_1d if price_chg_1d is not None else None),
        days_to_expiry  = days_to_expiry,
        liquidity_ratio = liq_ratio,
    )
    p_hat_no, llr_no = compute_posterior(sig_no)
    ev_no = inefficiency_ev(no_price, p_hat_no)

    if ev_no >= MIN_EV:
        cost_1_no = trade_cost(qs, LSMR_B, 1, 1.0)
        size = _kelly_size(p_hat_no, no_price, balance, kelly_scale)
        if size >= MIN_TRADE_AMOUNT:
            recs.append({
                "action":        "BUY",
                "market":        market["question"],
                "token_id":      no_token,
                "side":          "NO",
                "amount":        size,
                "market_price":  no_price,
                "my_estimate":   round(p_hat_no, 4),
                "ev":            round(ev_no, 4),
                "llr":           round(llr_no, 4),
                "cost_1_share":  round(cost_1_no, 4),
                "kelly_scale":   kelly_scale,
                "slug":          market.get("slug", ""),
                "source":        market.get("source", ""),
            })

    return recs


def MAX(a, b):
    return a if a > b else b


def _apply_constraints(candidates: list, balance: float,
                       current_open: int) -> list:
    """Select trades that fit within slot and budget limits.

    Sorted by |EV| descending so highest-EV trades have first priority.
    """
    candidates = sorted(candidates, key=lambda r: -abs(r.get("ev", 0)))
    slots_left = MAX_OPEN_POSITIONS - current_open
    budget = balance * 0.90      # keep 10% cash reserve
    spent = 0.0
    used_tokens: set = set()
    result = []

    for rec in candidates:
        if len(result) >= slots_left:
            break
        tid = rec["token_id"]
        if tid in used_tokens:
            continue
        amt = rec["amount"]
        if spent + amt > budget:
            remaining = round(budget - spent, 2)
            if remaining >= MIN_TRADE_AMOUNT:
                rec = dict(rec)
                rec["amount"] = remaining
                amt = remaining
            else:
                continue
        result.append(rec)
        spent += amt
        used_tokens.add(tid)

    return result


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def _fmt_cycle_msg(cycle: int, balance: float, positions: list,
                   exits: list, new_trades: list) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    unreal = sum(p.get("pnl", 0) for p in positions)
    invested = sum(p.get("current_value", 0) for p in positions)

    lines = [
        f"📊 *LSMR Bot — Cycle \\#{cycle}*",
        f"🕐 `{now}`",
        f"",
        f"💰 Balance: `${balance:.2f}`",
        f"📈 Open: `{len(positions)}/{MAX_OPEN_POSITIONS}` \\| "
        f"Unrealised: `${unreal:+.2f}`",
        f"💼 Invested: `${invested:.2f}`",
    ]

    if exits:
        lines += ["", "🔴 *EXITS*"]
        for ex in exits:
            icon = "⛔" if "STOP-LOSS" in ex.get("confidence", "") else "✅"
            lines.append(f"  {icon} {escape_md(ex['market'][:50])}")
            lines.append(f"    _{escape_md(ex['confidence'])}_")

    if new_trades:
        lines += ["", "🟢 *NEW TRADES*"]
        for rec in new_trades:
            side = rec["side"]
            p    = rec["market_price"]
            ph   = rec["my_estimate"]
            ev   = rec["ev"]
            amt  = rec["amount"]
            icon = "🟢" if side == "YES" else "🔵"
            lines.append(f"  {icon} {escape_md(rec['market'][:55])}")
            lines.append(
                f"    {side} `{p:.3f}` → p̂ `{ph:.3f}` "
                f"EV `{ev:+.3f}` \\$ `{amt:.2f}`"
            )

    if not exits and not new_trades:
        lines += ["", "⬛ No trades this cycle\\."]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle(state: TradeState, cycle_num: int, starting_balance: float) -> None:
    print(f"\n{'='*60}")
    print(f"Cycle #{cycle_num}  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    if DRY_RUN:
        print("  [DRY RUN — no real orders]")

    # 1. Account state
    balance   = get_balance()
    positions = fetch_positions()
    print(f"  Balance: ${balance:.2f}  |  Open positions: {len(positions)}")

    # 2. Exit signals — fetch live market data for held positions to re-run EV model
    held_token_ids = [p["token_id"] for p in positions]
    held_market_data = fetch_markets_for_tokens(held_token_ids) if held_token_ids else {}
    exits = _check_exits(positions, held_market_data)

    # 3. New trade analysis
    emergency_stop   = max(starting_balance * EMERGENCY_STOP_FRACTION, MIN_TRADE_AMOUNT)
    min_bal_to_trade = max(starting_balance * MIN_BALANCE_FRACTION, MIN_TRADE_AMOUNT * 2)

    new_trades: list = []
    if balance < emergency_stop:
        print(f"  EMERGENCY STOP: balance ${balance:.2f} < ${emergency_stop:.2f} "
              f"({EMERGENCY_STOP_FRACTION*100:.0f}% of ${starting_balance:.2f})")
    elif balance < min_bal_to_trade:
        print(f"  Low balance ${balance:.2f} < ${min_bal_to_trade:.2f} — skipping new buys")
    elif len(positions) >= MAX_OPEN_POSITIONS:
        print(f"  Max positions reached ({len(positions)}/{MAX_OPEN_POSITIONS})")
    else:
        markets = fetch_markets()
        held = {p["token_id"] for p in positions}
        raw_candidates: list = []
        top_evs: list = []
        for m in markets:
            recs = _analyse_market(m, balance, held)
            raw_candidates.extend(recs)
            # Track best EV seen (even below threshold) for diagnostics
            yes_price = float(m["prices"][0])
            from bayesian_engine import MarketSignals, compute_posterior
            from lsmr_engine import inefficiency_ev
            vol_ratio = m["volume24h"] / max(MIN_VOLUME_24H, 1)
            liq_ratio = m["liquidity"] / max(MIN_LIQUIDITY, 1)
            sig = MarketSignals(
                market_price=yes_price,
                volume_ratio=vol_ratio,
                price_change_1d=m.get("price_change_1d"),
                days_to_expiry=None,
                liquidity_ratio=liq_ratio,
            )
            p_hat, _ = compute_posterior(sig)
            ev = inefficiency_ev(yes_price, p_hat)
            top_evs.append((ev, m["question"][:50], yes_price, p_hat, m.get("price_change_1d", 0)))
        top_evs.sort(key=lambda x: -x[0])
        print(f"  Bayesian analysis: {len(raw_candidates)} candidate signals found")
        print(f"  Top EVs this cycle (threshold={MIN_EV}):")
        for ev, q, p, ph, chg in top_evs[:5]:
            print(f"    EV={ev:+.4f} p={p:.3f}→p̂={ph:.3f} Δ1d={chg:+.3f} | {q}")
        new_trades = _apply_constraints(raw_candidates, balance, len(positions))
        print(f"  After constraints: {len(new_trades)} trades to execute")

    # 4. Execute
    all_recs = exits + new_trades
    if not DRY_RUN:
        results, executed_ids, skipped = execute_trades(
            all_recs, balance, positions,
        )
    else:
        results, executed_ids, skipped = [], set(), []
        for rec in all_recs:
            print(f"  DRY_RUN: would execute {rec.get('action')} "
                  f"{rec.get('side','')} {rec.get('market','')[:40]}")

    # 5. Persist
    state.record_cycle(cycle_num, balance, new_trades, results, skipped)

    # 6. Telegram
    executed_trades = [r for r in new_trades if r.get("token_id") in executed_ids]
    if all_recs or cycle_num % 8 == 0:   # always notify every 8 cycles
        try:
            msg = _fmt_cycle_msg(cycle_num, balance, positions, exits, executed_trades)
            send_telegram(msg)
        except Exception as e:
            print(f"WARNING: Telegram notification failed: {e}")

    print(f"Cycle #{cycle_num} complete — exits={len(exits)} new={len(new_trades)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    state     = TradeState()
    cycle_num = state.get_cycle_count() + 1
    interval  = CYCLE_INTERVAL_HOURS * 3600

    # Record starting balance on first-ever run; reuse it on restarts so that
    # the emergency-stop and min-balance thresholds don't shrink after losses.
    starting_balance = state.get_starting_balance()
    if starting_balance is None:
        starting_balance = get_balance()
        state.set_starting_balance(starting_balance)
        print(f"  Starting balance recorded: ${starting_balance:.2f}")
    else:
        print(f"  Starting balance (from state): ${starting_balance:.2f}")

    emergency_stop   = max(starting_balance * EMERGENCY_STOP_FRACTION, MIN_TRADE_AMOUNT)
    min_bal_to_trade = max(starting_balance * MIN_BALANCE_FRACTION, MIN_TRADE_AMOUNT * 2)

    print(f"LSMR + Bayesian Polymarket Bot starting")
    print(f"  Cycle interval: {CYCLE_INTERVAL_HOURS}h  |  MIN_EV: {MIN_EV}")
    print(f"  Kelly fraction: {KELLY_FRACTION}  |  Max positions: {MAX_OPEN_POSITIONS}")
    print(f"  Emergency stop: ${emergency_stop:.2f}  |  Min to trade: ${min_bal_to_trade:.2f}")
    print(f"  DRY_RUN: {DRY_RUN}")

    send_telegram(
        f"🚀 *LSMR Bot started*\n"
        f"Cycle \\#{cycle_num} \\| interval\\=`{CYCLE_INTERVAL_HOURS}h` "
        f"\\| MIN\\_EV\\=`{MIN_EV}` \\| Kelly\\=`{KELLY_FRACTION}`"
    )

    while True:
        try:
            run_cycle(state, cycle_num, starting_balance)
        except KeyboardInterrupt:
            print("\nStopped by user.")
            sys.exit(0)
        except Exception as e:
            print(f"ERROR in cycle #{cycle_num}: {e}")
            traceback.print_exc()
            send_telegram(f"⚠️ Bot error cycle \\#{cycle_num}: {escape_md(str(e)[:200])}")

        cycle_num += 1
        print(f"Sleeping {CYCLE_INTERVAL_HOURS}h until next cycle…")
        time.sleep(interval)


if __name__ == "__main__":
    main()
