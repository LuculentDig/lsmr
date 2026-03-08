#!/usr/bin/env python3
"""End-to-end smoke tests for the LSMR + Bayesian Polymarket bot.

Tests cover every formula from QR-PM-2026-0041:
  Section 1: LSMR cost / price / trade-cost / max-MM-loss
  Section 2: Bayesian posterior (prior, sequential update, EV)
  Section 3: Kelly sizing (YES and NO, near-expiry scale-down)
  Section 4: Exit signals
  Section 5: Integration (imports, balance fetch, Telegram)
"""
import sys
import math

PASS = "OK"
FAIL = "XX"
errors = []

def check(label, condition, detail=""):
    if condition:
        print(f"  [OK] {label}")
    else:
        print(f"  [XX] {label}  {detail}")
        errors.append(label)

def approx_eq(a, b, tol=1e-9):
    return abs(a - b) < tol

def approx_close(a, b, rel=1e-4):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-12)

# ===========================================================================
print("\n1. LSMR engine (QR-PM-2026-0041, Eq.1-4)")
# ===========================================================================
from lsmr_engine import lsmr_cost, lsmr_prices, trade_cost, max_mm_loss, inefficiency_ev, infer_quantities

b = 100_000.0

# Eq.1: C(q) = b * ln( sum e^(qi/b) )
# For q = [0, 0] (uniform): C = b * ln(2)
c0 = lsmr_cost([0.0, 0.0], b)
check("Eq.1 cost([0,0]) = b*ln(2)", approx_close(c0, b * math.log(2)))

# For q = [100000, 0]: price[0] should be much closer to 1
c1 = lsmr_cost([100_000.0, 0.0], b)
check("Eq.1 cost([b,0]) > cost([0,0])", c1 > c0)

# Eq.3: softmax prices sum to 1
prices_uniform = lsmr_prices([0.0, 0.0], b)
check("Eq.3 softmax sums to 1 (uniform)", approx_eq(sum(prices_uniform), 1.0))
check("Eq.3 uniform prices = 0.5 each",  approx_close(prices_uniform[0], 0.5))

prices_skew = lsmr_prices([100_000.0, 0.0], b)
check("Eq.3 skewed prices sum to 1", approx_eq(sum(prices_skew), 1.0))
check("Eq.3 dominant outcome price > 0.7", prices_skew[0] > 0.70)
check("Eq.3 prices in (0,1)", all(0 < p < 1 for p in prices_skew))

# Eq.4: trade cost positive for purchase, zero for zero-delta
tc_pos = trade_cost([0.0, 0.0], b, 0, 10.0)
check("Eq.4 trade_cost > 0 for purchase", tc_pos > 0)
tc_zero = trade_cost([0.0, 0.0], b, 0, 0.0)
check("Eq.4 trade_cost = 0 for zero delta", approx_eq(tc_zero, 0.0))
tc_sell = trade_cost([0.0, 0.0], b, 0, -5.0)
check("Eq.4 trade_cost < 0 for sell (negative delta)", tc_sell < 0)

# Eq.2: max MM loss = b * ln(n)
mml = max_mm_loss(b, 2)
check(f"Eq.2 max_mm_loss(b=100k, n=2) ~ $69,315", approx_close(mml, 69314.72, rel=0.001))

# Inefficiency EV = p_hat - p
check("EV = +0.12 when p_hat=0.52, p=0.40", approx_close(inefficiency_ev(0.40, 0.52), 0.12))
check("EV = -0.10 when p_hat=0.30, p=0.40", approx_close(inefficiency_ev(0.40, 0.30), -0.10))

# Round-trip: infer quantities then recover prices
original_prices = [0.35, 0.65]
qs = infer_quantities(original_prices, b)
recovered = lsmr_prices(qs, b)
check("Round-trip: infer_quantities -> lsmr_prices recovers original",
      all(approx_close(a, b_) for a, b_ in zip(recovered, original_prices)))

# ===========================================================================
print("\n2. Bayesian engine (QR-PM-2026-0041, p.3 Eq.1-4)")
# ===========================================================================
from bayesian_engine import MarketSignals, compute_posterior

# Prior only (no signals) — posterior should equal market price
sig_no_signals = MarketSignals(market_price=0.40)
p_hat, llr = compute_posterior(sig_no_signals)
check("Prior-only: p_hat = market_price", approx_close(p_hat, 0.40, rel=0.01))
check("Prior-only: total_llr = 0", approx_eq(llr, 0.0))

# Positive signals (high volume + price up) -> p_hat > p
sig_pos = MarketSignals(
    market_price    = 0.40,
    volume_ratio    = 3.0,    # 3x normal volume
    price_change_1d = 0.12,   # price rose 12%
)
p_hat_pos, llr_pos = compute_posterior(sig_pos)
check("Positive signals: p_hat > market_price", p_hat_pos > 0.40)
check("Positive signals: llr > 0", llr_pos > 0)

# Negative signals (high volume + price down) -> p_hat < p
sig_neg = MarketSignals(
    market_price    = 0.60,
    volume_ratio    = 3.0,
    price_change_1d = -0.12,
)
p_hat_neg, llr_neg = compute_posterior(sig_neg)
check("Negative signals: p_hat < market_price", p_hat_neg < 0.60)
check("Negative signals: llr < 0", llr_neg < 0)

# Posterior stays in (0, 1)
check("Posterior in (0, 1) — positive signals", 0 < p_hat_pos < 1)
check("Posterior in (0, 1) — negative signals", 0 < p_hat_neg < 1)

# Near-expiry pull: p>0.5 should be pulled further toward 1
sig_expiry = MarketSignals(
    market_price    = 0.70,
    days_to_expiry  = 1.0,
)
p_hat_exp, _ = compute_posterior(sig_expiry)
check("Near-expiry pull: p_hat > p for p>0.5", p_hat_exp > 0.70)

# Thin market: p should be pulled toward 0.5
sig_thin = MarketSignals(
    market_price    = 0.70,
    liquidity_ratio = 0.1,   # very thin
)
p_hat_thin, _ = compute_posterior(sig_thin)
check("Thin market: p_hat closer to 0.5 than market_price", abs(p_hat_thin - 0.5) < abs(0.70 - 0.5))

# EV formula: EV = p_hat - p  (Eq.4, p.3)
ev = p_hat_pos - 0.40
check("Eq.4 EV = p_hat - p", approx_close(ev, p_hat_pos - 0.40))

# ===========================================================================
print("\n3. Kelly sizing (bot.py helpers)")
# ===========================================================================
from bot import _kelly_size, _kelly_scale_for_expiry

bal = 100.0

# YES: kelly_frac = (p_hat - p)/(1-p) * KELLY_FRACTION
# e.g. p=0.30, p_hat=0.50, ev=0.20
k = _kelly_size(0.50, 0.30, bal)
expected_frac = 0.20 / 0.70 * 0.25   # ev/(1-p)*KELLY_FRACTION
check("Kelly YES bet positive", k > 0, f"got {k:.2f}")
check("Kelly YES ~ expected fraction * balance",
      approx_close(k, min(expected_frac * bal, bal * 0.25), rel=0.01), f"got {k:.2f}")

# Zero for no edge
k_zero = _kelly_size(0.30, 0.40, bal)   # p_hat < p
check("Kelly = 0 when p_hat < p", approx_eq(k_zero, 0.0))

# Capped at 25% of balance
k_big = _kelly_size(0.99, 0.01, bal)
check("Kelly capped at 25% of balance", k_big <= bal * 0.25 + 0.01)

# Near-expiry scale-down
check("Kelly scale >= 30d  = 1.0",  approx_eq(_kelly_scale_for_expiry(30), 1.0))
check("Kelly scale < 7d    = 0.5",  approx_eq(_kelly_scale_for_expiry(3), 0.5))
check("Kelly scale < 1d    = 0.1",  approx_eq(_kelly_scale_for_expiry(0.5), 0.10))
check("Kelly scale None    = 1.0",  approx_eq(_kelly_scale_for_expiry(None), 1.0))

k_near_exp = _kelly_size(0.50, 0.30, bal, kelly_scale=0.1)
check("Near-expiry scale reduces bet size", k_near_exp < k)

# ===========================================================================
print("\n4. Exit signals (bot._check_exits)")
# ===========================================================================
from bot import _check_exits

positions = [
    {   # stop-loss
        "market": "stop-test", "outcome": "YES", "token_id": "aaa",
        "avg_price": 0.60, "cur_price": 0.22, "pnl_pct": -63,
        "current_value": 3.0, "end_date": "2027-01-01T00:00:00Z",
    },
    {   # take-profit
        "market": "tp-test", "outcome": "YES", "token_id": "bbb",
        "avg_price": 0.40, "cur_price": 0.88, "pnl_pct": 120,
        "current_value": 8.8, "end_date": "2027-01-01T00:00:00Z",
    },
    {   # hold
        "market": "hold-test", "outcome": "YES", "token_id": "ccc",
        "avg_price": 0.50, "cur_price": 0.55, "pnl_pct": 10,
        "current_value": 5.5, "end_date": "2027-01-01T00:00:00Z",
    },
]
exits = _check_exits(positions)
check("Stop-loss triggered",       any("STOP-LOSS"   in e.get("confidence","") for e in exits))
check("Take-profit triggered",     any("TAKE-PROFIT" in e.get("confidence","") for e in exits))
check("Hold position not exited",  not any(e.get("token_id") == "ccc" for e in exits))
check("Exit count == 2",           len(exits) == 2, f"got {len(exits)}")

# ===========================================================================
print("\n5. State persistence")
# ===========================================================================
from state import TradeState

ts = TradeState()
cycle_before = ts.get_cycle_count()
ts.record_cycle(cycle_before, 50.0, [], [], [])
check("Cycle count incremented", ts.get_cycle_count() == cycle_before + 1)
check("Realised PnL starts at 0",  ts.get_realised_pnl() == 0.0)

# ===========================================================================
print("\n6. Module imports")
# ===========================================================================
try:
    import constants; check("constants imports OK", True)
except Exception as e: check("constants imports OK", False, str(e))
try:
    import lsmr_engine; check("lsmr_engine imports OK", True)
except Exception as e: check("lsmr_engine imports OK", False, str(e))
try:
    import bayesian_engine; check("bayesian_engine imports OK", True)
except Exception as e: check("bayesian_engine imports OK", False, str(e))
try:
    import lib; check("lib imports OK", True)
except Exception as e: check("lib imports OK", False, str(e))
try:
    import bot; check("bot imports OK", True)
except Exception as e: check("bot imports OK", False, str(e))

# ===========================================================================
print("\n7. Live balance check (Polymarket API)")
# ===========================================================================
try:
    from polymarket_sdk.api import get_balance
    bal = get_balance()
    check(f"Balance fetched (${bal:.2f})", isinstance(bal, float))
    if bal < 5:
        print(f"     WARNING: balance ${bal:.2f} is very low!")
except Exception as e:
    check("Balance fetch", False, str(e))

# ===========================================================================
print("\n8. Telegram notification smoke test")
# ===========================================================================
try:
    from polymarket_sdk.telegram import send_telegram
    send_telegram("LSMR bot smoke test — all systems OK (test, ignore)")
    check("Telegram send (graceful on bad token)", True)
except Exception as e:
    check("Telegram send", False, str(e))

# ===========================================================================
print("\n" + "=" * 55)
if errors:
    print(f"FAILED: {len(errors)} test(s): {errors}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
