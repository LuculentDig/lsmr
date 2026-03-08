"""LSMR (Logarithmic Market Scoring Rule) pricing mechanics.

Implements all formulae from QR-PM-2026-0041, Section 1-4:

  Eq.1  Cost function:   C(q) = b · ln( Σᵢ e^(qᵢ/b) )
  Eq.2  Max MM loss:     L_max = b · ln(n)
  Eq.3  Price (softmax): pᵢ(q) = e^(qᵢ/b) / Σⱼ e^(qⱼ/b)
  Eq.4  Trade cost:      Cost = C(q₁,…,qᵢ+δ,…,qₙ) − C(q₁,…,qᵢ,…,qₙ)

  Inefficiency signal (Sec.4 + p.3 Eq.4):
        EV = p̂ − p
"""

import math
from typing import List


# ---------------------------------------------------------------------------
# Core LSMR functions
# ---------------------------------------------------------------------------

def lsmr_cost(quantities: List[float], b: float) -> float:
    """C(q) = b · ln( Σᵢ e^(qᵢ/b) )

    Numerically stable via the log-sum-exp trick:
        log Σ e^(xᵢ) = max(x) + log Σ e^(xᵢ − max(x))
    """
    if not quantities:
        raise ValueError("quantities must be non-empty")
    if b <= 0:
        raise ValueError("b must be positive")
    max_q = max(quantities)
    log_sum = math.log(sum(math.exp((q - max_q) / b) for q in quantities))
    return b * (log_sum + max_q / b)


def lsmr_prices(quantities: List[float], b: float) -> List[float]:
    """pᵢ = softmax(q/b) — instantaneous outcome prices.

    Critical properties from the document:
      Σ pᵢ = 1   and   pᵢ ∈ (0, 1) ∀i
    """
    if b <= 0:
        raise ValueError("b must be positive")
    max_q = max(quantities)
    exps = [math.exp((q - max_q) / b) for q in quantities]
    total = sum(exps)
    return [e / total for e in exps]


def lsmr_price(quantities: List[float], b: float, i: int) -> float:
    """Instantaneous price of outcome i.  Convenience wrapper."""
    return lsmr_prices(quantities, b)[i]


def trade_cost(quantities: List[float], b: float, outcome_idx: int, delta: float) -> float:
    """Cost of purchasing δ shares of outcome outcome_idx.

    Cost = C(q₁,…, qᵢ+δ ,…,qₙ) − C(q₁,…,qᵢ,…,qₙ)

    For a purchase (delta > 0) this is positive — money leaves the buyer.
    Uses the closed-form difference which is more precise than two separate
    log-sum-exp calls:

        Cost = b · ln( e^(δ/b) · e^(qᵢ/b) + Σⱼ≠ᵢ e^(qⱼ/b) )
                   − b · ln( Σⱼ e^(qⱼ/b) )
    """
    q_new = list(quantities)
    q_new[outcome_idx] += delta
    return lsmr_cost(q_new, b) - lsmr_cost(quantities, b)


def infer_quantities(prices: List[float], b: float) -> List[float]:
    """Recover implied quantity vector from observed LSMR prices.

    Since pᵢ = softmax(q/b), the inverse is:
        qᵢ = b · ln(pᵢ) + constant

    The additive constant is shift-invariant (cancels in all price
    calculations), so we return qᵢ = b · ln(pᵢ) shifted so min(q) = 0.
    """
    log_ps = [math.log(max(p, 1e-10)) * b for p in prices]
    min_q = min(log_ps)
    return [lp - min_q for lp in log_ps]


def max_mm_loss(b: float, n_outcomes: int = 2) -> float:
    """L_max = b · ln(n)  — maximum possible market-maker loss.

    For binary markets (n=2) with b=100,000: L_max ≈ $69,315.
    """
    if n_outcomes < 2:
        raise ValueError("n_outcomes must be ≥ 2")
    return b * math.log(n_outcomes)


# ---------------------------------------------------------------------------
# Inefficiency / entry signal
# ---------------------------------------------------------------------------

def inefficiency_ev(market_price: float, posterior: float) -> float:
    """EV = p̂ − p   (document p.3, Eq.4).

    Positive  → YES token is underpriced relative to our Bayesian belief.
    Negative  → NO token is underpriced.
    """
    return posterior - market_price
