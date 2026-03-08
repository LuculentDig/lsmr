import os

# --- Telegram ---
TELEGRAM_CHAT_ID = 1728093986

# --- Polymarket wallet / chain ---
FUNDER = "0xba957b3b751977730b673fb7a38e0a7eb2bb2154"
CHAIN_ID = 137
CLOB_HOST = "https://clob.polymarket.com"

# --- Market filters ---
MIN_VOLUME_24H        = 50_000    # Minimum 24-hr volume
MIN_LIQUIDITY         = 10_000    # Minimum on-book liquidity
MIN_YES_PRICE         = 0.05
MAX_YES_PRICE         = 0.95
MAX_CANDIDATE_MARKETS = 20

# ---------------------------------------------------------------------------
# LSMR parameter  (QR-PM-2026-0041, Eq.1-3)
# ---------------------------------------------------------------------------
# Polymarket binary markets use b ~= 100,000 USDC.
# L_max = b*ln(2) ~= $69,315  --  maximum market-maker loss.
LSMR_B = 100_000.0

# ---------------------------------------------------------------------------
# Bayesian / entry threshold  (p.3 Eq.4: EV = p_hat - p)
# ---------------------------------------------------------------------------
MIN_EV = 0.08      # minimum |p_hat - p| to open a trade (8pp edge)

# ---------------------------------------------------------------------------
# Kelly / position sizing
# ---------------------------------------------------------------------------
# Document annotation: "NEVER full Kelly on 5min markets!"
# Base multiplier is quarter-Kelly; bot.py applies additional scale-down
# for markets with days_to_expiry < 1 (near-resolution / very short-dated).
KELLY_FRACTION       = 0.25
MAX_TRADE_FRACTION   = 0.25
MIN_TRADE_AMOUNT     = 2.00
MIN_BALANCE_TO_TRADE   = 10.00
EMERGENCY_STOP_BALANCE =  5.00
MAX_OPEN_POSITIONS   = 5

# ---------------------------------------------------------------------------
# Exit thresholds
# ---------------------------------------------------------------------------
STOP_LOSS_FRAC   = 0.45    # exit if cur_price < avg_price * STOP_LOSS_FRAC
TAKE_PROFIT_FRAC = 0.85    # exit if cur_price >= TAKE_PROFIT_FRAC and PnL>=30%

# ---------------------------------------------------------------------------
# Loop schedule
# ---------------------------------------------------------------------------
CYCLE_INTERVAL_HOURS = float(os.getenv("CYCLE_INTERVAL_HOURS", "1"))

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
STATE_FILE = "trade_history.json"
