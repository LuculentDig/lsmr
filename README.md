# LSMR + Bayesian Polymarket Trading Bot

A quantitative trading bot for Polymarket implementing the formulas from
**QR-PM-2026-0041** (Quantitative Research Division, February 2026).

---

## Mathematical Foundation

### Page 1 — LSMR Pricing Mechanics

Polymarket uses the **Logarithmic Market Scoring Rule (LSMR)** to set prices.

| Formula                       | Description                                                       |
| ----------------------------- | ----------------------------------------------------------------- |
| `C(q) = b · ln( Σ e^(qᵢ/b) )` | Cost function (Eq.1)                                              |
| `pᵢ = softmax(q/b)ᵢ`          | Instantaneous price (Eq.3) — identical to neural-net softmax      |
| `Cost = C(q + δ·eᵢ) - C(q)`   | Cost of a trade (Eq.4)                                            |
| `L_max = b · ln(n)`           | Max market-maker loss: ~$69,315 for binary markets with b=100,000 |

### Page 3 — Bayesian Signal Architecture

The bot estimates a **Bayesian posterior** p̂ and enters when the implied
edge EV = p̂ − p is large enough.

| Formula                          | Description              |
| -------------------------------- | ------------------------ | --------------- | ---------------------------- |
| `P(H                             | D) = P(D                 | H)·P(H) / P(D)` | Bayes theorem (Eq.1)         |
| `P(H                             | D₁…Dₜ) ∝ P(H) · ∏ P(Dₖ   | H)`             | Sequential update (Eq.2)     |
| `log P(H                         | D) = logP(H) + Σ logP(Dₖ | H) − logZ`      | Log-space stable form (Eq.3) |
| `EV = p̂·(1−p) − (1−p̂)·p = p̂ − p` | Entry signal (Eq.4)      |

> **Document annotation:** "NEVER full Kelly on 5min markets!"  
> The bot applies quarter-Kelly as its base multiplier, with additional
> scale-down (×0.1) for markets expiring within 24 hours.

---

## Strategy

```
for each market:
    1. Read LSMR price p from Polymarket Gamma API
    2. Infer implied quantity vector q = b·ln(pᵢ) (inverse softmax)
    3. Build signal vector:
           D₁ = volume_ratio × price_direction  (informed-flow)
           D₂ = 1-day price change              (momentum)
           D₃ = near-expiry pull                (convergence)
           D₄ = liquidity discount              (thin-market shrink)
    4. Sequential Bayesian update (log-odds space):
           logit(p̂) = logit(p) + Σ LLRₖ
    5. EV = p̂ − p
    6. If EV >= MIN_EV (8pp):  open BUY
           Kelly fraction = EV / (1−p) × KELLY_FRACTION × expiry_scale
    7. If EV_no >= MIN_EV:     open NO BUY (same logic on NO token)
```

Exit rules:

- **Stop-loss**: sell if `cur_price < avg_price × 0.45`
- **Take-profit**: sell if `cur_price ≥ 0.85` AND `PnL ≥ 30%`
- **Near-expiry cut**: sell if `days_left ≤ 3` AND `PnL < −20%`

---

## File Structure

```
lsmr/
├── polymarket_sdk/      ← reusable API/Telegram/infra helpers (new SDK)
│   ├── __init__.py
│   ├── api.py
│   ├── config.py
│   ├── telegram.py
│   └── docker/          ← example compose + scripts for VPN setup
├── lsmr_engine.py       ← LSMR cost / price / trade-cost formulas (pure math)
├── bayesian_engine.py   ← Sequential Bayesian posterior computation
├── bot.py               ← Main trading loop (imports from SDK)
├── lib.py               ← compatibility shim pointing at SDK
├── constants.py         ← All configuration parameters
├── state.py             ← Trade history persistence
├── test_bot.py          ← Full test suite (all formulas verified)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml   ← Bot + NordVPN (gluetun) two-container setup
├── docker-entrypoint.sh ← Waits for VPN before starting bot
└── run-docker.sh        ← Build & start helper
```

---

## Configuration

Copy `.env` from the polymarket project and ensure it contains:

```bash
PRIVATE_KEY=0x...                   # Polymarket signing key
TELEGRAM_TOKEN=77...                # Bot token from @BotFather
TELEGRAM_CHAT_ID=172...             # Your chat ID
NORDVPN_TOKEN=<nordvpn-token>       # From https://my.nordaccount.com

# Optional overrides
CYCLE_INTERVAL_HOURS=1              # Default: 1 hour (fractional ok, e.g. 0.5)
DRY_RUN=0                           # Set to 1 to simulate without orders
```

### Key parameters (`constants.py`)

| Parameter            | Default | Description                           |
| -------------------- | ------- | ------------------------------------- |
| `LSMR_B`             | 100,000 | LSMR liquidity parameter              |
| `MIN_EV`             | 0.08    | Minimum \|p̂ − p\| to enter (8pp edge) |
| `KELLY_FRACTION`     | 0.25    | Quarter-Kelly base multiplier         |
| `MAX_TRADE_FRACTION` | 0.25    | Hard cap: 25% of balance per trade    |
| `MIN_TRADE_AMOUNT`   | $2.00   | Minimum order size                    |
| `MAX_OPEN_POSITIONS` | 5       | Concurrent position limit             |
| `STOP_LOSS_FRAC`     | 0.45    | Exit if price < entry × 0.45          |
| `TAKE_PROFIT_FRAC`   | 0.85    | Exit if price ≥ 0.85 with ≥30% gain   |

---

## Running

### Tests first

```bash
cd ~/Documents/lsmr
python3 test_bot.py
```

### Local run

```bash
cd ~/Documents/lsmr
source ../.venv/bin/activate   # reuse polymarket venv
python3 bot.py

# Dry-run (no orders placed)
DRY_RUN=1 python3 bot.py
```

### Docker + NordVPN (recommended — VPN required for Polymarket)

```bash
cd ~/Documents/lsmr

# 1. One-time: disable macOS sleep
sudo cp com.polymarket.pmset.plist /Library/LaunchDaemons/
sudo launchctl load /Library/LaunchDaemons/com.polymarket.pmset.plist

# 2. Start containers (builds image + starts gluetun VPN sidecar)
./run-docker.sh

# 3. Follow logs
./run-docker.sh logs

# 4. Check status
./run-docker.sh status

# 5. Stop
./run-docker.sh stop
```

---

## How the Math Connects to Trading

### Why LSMR matters

The LSMR softmax formula means that a market price of p=0.35 implies a
quantity imbalance of `q_yes - q_no = b·ln(0.35/0.65) ≈ −62,000 USDC`.
The `trade_cost` function (`lsmr_engine.py`) computes the exact USDC cost to
move the market, giving a reference for market impact.

### Why Bayesian updating

A pure price-observer sees p = 0.35 and knows nothing more. The Bayesian
agent accumulates:

- **Volume spike** (D₁): 3× normal volume with price rising → informed
  buyers are loading up → posterior moves above market price
- **Momentum** (D₂): price was 0.30 yesterday, now 0.35 → short-term drift
  continues with some probability
- **Expiry pull** (D₃): 2 days to resolution, p=0.70 → convergence pressure

These are combined via `logit(p̂) = logit(p) + Σ LLRₖ` (Eq.3, log-Z
cancels in binary normalisation).

### Why fractional Kelly

Full Kelly maximises log-wealth asymptotically but is extremely sensitive to
mis-estimated probabilities. With EV ≈ 8pp and Kelly fraction = 0.25:

```
kelly_frac = EV / (1−p) × 0.25
           = 0.08 / 0.65 × 0.25 ≈ 3.1% of balance per trade
```

For a $3,000 account this is ~$93 per signal — substantial but not ruinous
even if the edge estimate is off by 50%.

---

## Disclaimer

This bot trades real money on prediction markets. The formulas faithfully
implement QR-PM-2026-0041 but signals are based on public market data only;
no alpha source is guaranteed. Always start with `DRY_RUN=1`, verify the
signal quality on historical cycles, then fund incrementally.
