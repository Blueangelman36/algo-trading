"""
Black-Scholes option pricing + greeks. Pure stdlib (math.erf), no scipy.

This is the pricing core of the options backtester. It is a MODEL: it assumes
lognormal prices, constant volatility, European exercise, and no vol smile.
Real option prices deviate from it — but it captures the dynamics that matter
for testing a directional options strategy: how an option gains/loses with the
underlying (delta), and how it bleeds as expiry nears (theta).
"""

import math

SQRT2 = math.sqrt(2.0)


def _N(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def _n(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _intrinsic(S, K, kind):
    return max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)


def price(S, K, T, sigma, r=0.04, q=0.0, kind="call") -> float:
    """Option price. S=spot, K=strike, T=years to expiry, sigma=annual vol,
    r=risk-free, q=dividend yield."""
    if T <= 0 or sigma <= 0:
        return _intrinsic(S, K, kind)
    vsqrt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vsqrt
    d2 = d1 - vsqrt
    if kind == "call":
        return S * math.exp(-q * T) * _N(d1) - K * math.exp(-r * T) * _N(d2)
    return K * math.exp(-r * T) * _N(-d2) - S * math.exp(-q * T) * _N(-d1)


def greeks(S, K, T, sigma, r=0.04, q=0.0, kind="call") -> dict:
    """delta, gamma, theta (per calendar day), vega (per 1 vol point)."""
    if T <= 0 or sigma <= 0:
        itm = (S > K) if kind == "call" else (S < K)
        return {"delta": (1.0 if kind == "call" else -1.0) if itm else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    vsqrt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vsqrt
    d2 = d1 - vsqrt
    disc_q = math.exp(-q * T)
    if kind == "call":
        delta = disc_q * _N(d1)
        theta = (-S * disc_q * _n(d1) * sigma / (2 * math.sqrt(T))
                 - r * K * math.exp(-r * T) * _N(d2) + q * S * disc_q * _N(d1))
    else:
        delta = -disc_q * _N(-d1)
        theta = (-S * disc_q * _n(d1) * sigma / (2 * math.sqrt(T))
                 + r * K * math.exp(-r * T) * _N(-d2) - q * S * disc_q * _N(-d1))
    gamma = disc_q * _n(d1) / (S * vsqrt)
    vega = S * disc_q * _n(d1) * math.sqrt(T)
    return {"delta": delta, "gamma": gamma,
            "theta": theta / 365.0, "vega": vega / 100.0}
