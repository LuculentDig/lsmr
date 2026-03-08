"""Real-Time Bayesian Signal Processing — posterior engine.

Implements the agent decision architecture from QR-PM-2026-0041, p.3:

  Eq.1  Bayes update:   P(H|D) = P(D|H) · P(H) / P(D)
  Eq.2  Sequential:     P(H | D₁…Dₜ) ∝ P(H) · ∏ₖ P(Dₖ | H)
                        [annotated: "NEVER full Kelly on 5min markets!"]
  Eq.3  Log-space:      log P(H|D) = log P(H) + Σₖ log P(Dₖ|H) − log Z
  Eq.4  EV:             EV = p̂ · (1−p) − (1−p̂) · p  =  p̂ − p

Observable signals Dₖ used as likelihood inputs:
  D₁  Volume × price-direction cross-signal  (informed-flow detector)
  D₂  Price momentum                         (autocorrelation signal)
  D₃  Near-expiry resolution pull            (convergence prior)
  D₄  Thin-market liquidity discount         (shrinks log-odds toward 0)
"""

import math
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Log-likelihood-ratio (LLR) strength coefficients
# ---------------------------------------------------------------------------
# Each coefficient controls how much a unit of signal shifts the posterior
# log-odds.  Calibrated conservatively so that a "strong" combination of
# signals (3× volume spike + 15pp price move) produces a ~6-8pp posterior
# update.  This avoids over-fitting prediction-market noise.

_STRENGTH = {
    "volume_momentum": 1.00,   # D₁: vol_ratio × price_direction cross-signal
    "price_momentum":  0.50,   # D₂: pure lagged price drift
    "expiry_pull":     0.80,   # D₃: near-expiry convergence
}

# Maximum log-odds shrink for thin markets (D₄).
# At liquidity_ratio=0 the prior log-odds is halved toward 0 (i.e., p→0.5).
_THIN_MARKET_SHRINK = 0.50


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class MarketSignals:
    """Observable data D₁…Dₜ for one binary market at one point in time.

    All optional fields default to None which means "no signal available"
    and the corresponding likelihood term is simply omitted from the update.
    """
    market_price: float              # current LSMR price p  — used as prior P(H)
    volume_ratio: Optional[float] = None      # volume_24h / MIN_VOLUME_24H
    price_change_1d: Optional[float] = None   # fractional 1-day YES price change
    days_to_expiry: Optional[float] = None    # calendar days until resolution
    liquidity_ratio: Optional[float] = None   # current_liquidity / MIN_LIQUIDITY


# ---------------------------------------------------------------------------
# Numerical helpers
# ---------------------------------------------------------------------------

def _logit(p: float) -> float:
    """log(p / (1−p))  — log-odds / logit transform."""
    p = max(min(p, 1.0 - 1e-9), 1e-9)
    return math.log(p / (1.0 - p))


def _sigmoid(lo: float) -> float:
    """Inverse logit: 1 / (1 + e^{−lo}).  Clamped to avoid overflow."""
    lo = max(min(lo, 30.0), -30.0)
    return 1.0 / (1.0 + math.exp(-lo))


def _llr(signal_name: str, delta: float) -> float:
    """Log-likelihood ratio  =  log P(D|H=YES) − log P(D|H=NO)."""
    return _STRENGTH.get(signal_name, 0.0) * delta


# ---------------------------------------------------------------------------
# Main posterior computation
# ---------------------------------------------------------------------------

def compute_posterior(signals: MarketSignals) -> tuple:
    """Return (p̂, total_llr) for a YES outcome using sequential Bayesian update.

    Implementation follows Eq.3 in log-odds (logit) space, which is
    numerically equivalent to the log-probability form after normalisation:

        logit(p̂) = logit(p)  +  Σₖ LLRₖ

    where  LLRₖ = log P(Dₖ | H=YES) − log P(Dₖ | H=NO).

    This eliminates the need to compute log Z explicitly — it cancels in the
    binary normalisation step performed by the logit/sigmoid pair.
    """
    p = signals.market_price

    # ── Prior: market price (encodes all publicly priced information) ─────
    log_odds = _logit(p)    # log-odds prior = log P(H) / P(¬H)
    total_llr = 0.0

    # ── D₄: Thin-market liquidity discount (applied to prior, not LLR sum) ─
    # Low-liquidity prices are noisier → shrink log-odds toward 0 (toward
    # p = 0.5) before adding signal LLRs.  Shrink factor ∈ [0.5, 1.0].
    if signals.liquidity_ratio is not None:
        deficit = max(0.0, 1.0 - signals.liquidity_ratio)  # 0 when liquid
        shrink = 1.0 - deficit * _THIN_MARKET_SHRINK
        shrink = max(shrink, 0.5)
        log_odds *= shrink

    # ── D₁: Volume × price-direction cross-signal ────────────────────────
    # High volume moving in the same direction as a price change is the
    # hallmark of informed-order flow.  Using log(1 + vol_ratio) dampens
    # extreme volume ratios (e.g. 10× becomes log(11) ≈ 2.4 rather than 10).
    if signals.volume_ratio is not None and signals.price_change_1d is not None:
        cross = signals.price_change_1d * math.log(1.0 + max(signals.volume_ratio, 0.0))
        total_llr += _llr("volume_momentum", cross)

    # ── D₂: Price momentum ───────────────────────────────────────────────
    # Prediction markets exhibit short-run autocorrelation driven by news
    # diffusion: a price drift toward YES is a mild positive likelihood
    # signal.  Clipped to ±30pp to prevent single large jumps dominating.
    if signals.price_change_1d is not None:
        delta = max(min(signals.price_change_1d, 0.30), -0.30)
        total_llr += _llr("price_momentum", delta)

    # ── D₃: Near-expiry resolution pull ──────────────────────────────────
    # Within 7 days of resolution, unresolved uncertainty shrinks rapidly;
    # the current leading outcome (p > 0.5) tends to keep gaining.
    # urgency = 0 at 7 days, 1 at 0 days (linear ramp).
    if signals.days_to_expiry is not None and 0 < signals.days_to_expiry < 7:
        urgency = (7.0 - signals.days_to_expiry) / 7.0
        direction = 1.0 if p > 0.5 else -1.0
        total_llr += _llr("expiry_pull", direction * urgency)

    # ── Sequential Bayesian update (Eq.3) ────────────────────────────────
    # logit(p̂) = logit(p) + Σ LLRₖ
    log_odds_posterior = log_odds + total_llr
    p_hat = _sigmoid(log_odds_posterior)

    return p_hat, total_llr
