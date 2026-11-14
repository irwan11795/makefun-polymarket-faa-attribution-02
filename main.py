# Polymarket FAA outage attribution forecaster v1 (make.fun polymarket-faa-attribution-02).
#
# Method: persistence + sum-reversion median with a realised-volatility fan.
# This is the algorithm of our submitted nvidia-ceo-forecast-01 v4 (practice
# loss 0.248323, graded #356), re-implemented fresh for this competition.
# The practice cases here ARE the NVIDIA event (same checkpoints, same
# prices), so the method transfers directly: it reads whatever markets a
# case holds and forecasts each one the same way.
#
# How it works, per market:
#   1. MEDIAN = persistence gliding toward the market's normalised share.
#      centre(h) = x + (1 - exp(-h/TAU)) * (x/S - x), TAU = 210 minutes,
#      S = sum of all markets' checkpoint prices. Sibling markets' prices
#      sum toward ~1 in a mutually-exclusive family, so a market priced
#      above its share of the sum is expected to drift down, and vice versa.
#      The glide applies to the whole fan, not just the median.
#   2. SPREAD = realised volatility of the market's own 1-minute moves.
#      Diffs are winsorised at +-0.05, then the RMS is blended over three
#      trailing windows (120 / 360 / 1440 minutes, weights 0.55 / 0.30 /
#      0.15), floored at 0.0002 and capped at 0.012 BEFORE a global scale
#      of 0.50 is applied. Fan half-width at horizon h is
#      width(h) = z_tau * sigma * sqrt(h), so the spread widens like a
#      random walk. A horizon bucket then multiplies the width: 1.00 for
#      h 1-30, 0.85 for h 31-120, 0.85 for h 121-240.
#   3. STALENESS: a market whose last price has not moved for >= 120
#      minutes gets sigma halved (x0.5) -- a flat price is evidence the
#      market is asleep, not that it is about to jump.
#   4. ACTIVITY: the third of markets with the most price changes get
#      sigma widened x1.15 -- busy markets move more.
#   5. QUANTILE OFFSETS (additive, in price units): the 75% quantile is
#      pulled down 0.04 and the 95% pushed up 0.005. The steppy realised
#      prices have a large point mass at the checkpoint price, so the
#      50-75% percentiles nearly coincide; the -0.04 offset is the measured
#      optimum on the practice, not a guess.
#
# Parameters below are the centre of a flat plateau (neighbours tau
# 200-220, scale 0.49-0.51, weights .55/.3/.15-.6/.3/.1 all similar),
# NOT the argmin -- chosen for robustness on unseen prices, which is
# what the final scores (12 cases on the FAA attribution markets, 4
# markets, recorded after the deadline).
#
# The v4 experimental knobs (RMS/meanabs blend, stale-blocks-active,
# active window, gap-scaled tau, glide skew) were all tested and REJECTED
# in the nvidia grind and are inert at their defaults, so they are
# omitted here; this file implements exactly the shipped v4 behaviour.
#
# Pure Python 3.13 standard library, deterministic, offline.
# Reads every <id>.in in the input directory (MAKEFUN_INPUT_DIR or /input)
# and writes <id>.out to the output directory (MAKEFUN_OUTPUT_DIR or /output).
# Output: {"forecasts": [{"id": <market id>, "q": [[q05,q25,q50,q75,q95] x 240]}]}.

import json
import math
import os
from pathlib import Path

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
# Standard-normal quantiles for QUANTILES (rounded to 9 dp; symmetric).
Z = [-1.644853627, -0.67448975, 0.0, 0.67448975, 1.644853627]

# --- Shipped parameters (centre of the plateau) ---
PARAMS = {
    "tau": 210.0,            # sum-reversion glide time constant (minutes)
    "win_weights": ((120, 0.55), (360, 0.3), (1440, 0.15)),  # vol windows
    "winsor": 0.05,          # per-diff winsor cap
    "sigma_floor": 0.0002,
    "sigma_cap": 0.012,
    "buckets": ((30, 1.0), (120, 0.85), (240, 0.85)),  # (h_max, mult)
    "stale_minutes": 120,    # unchanged-price run that counts as stale
    "stale_mult": 0.5,
    "active_mult": 1.15,     # top-tertile activity widening
    "offsets": (0.0, 0.0, 0.0, -0.04, 0.005),
    "sigma_scale": 0.50,     # global calibration on RMS sigma
}


def price6(x):
    """Clamp to [0, 1] and round to 6 decimals (0, never -0)."""
    return round(min(1.0, max(0.0, x)) * 1e6) / 1e6 + 0.0


def _diffs(prices):
    """1-minute diffs of a price series."""
    return [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]


def _sigma(prices, p):
    """Blended realised vol: RMS of winsorised diffs over trailing windows.
    Floor/cap are applied BEFORE the scale (the effective floor is
    floor*scale, the effective cap cap*scale)."""
    diffs = _diffs(prices)
    w = p["winsor"]
    win = [max(-w, min(w, d)) for d in diffs]
    if not win:
        return p["sigma_floor"]
    sig = 0.0
    for minutes, weight in p["win_weights"]:
        seg = win[-minutes:] if len(win) > minutes else win
        n = len(seg)
        var = sum(d * d for d in seg) / n
        sig += weight * math.sqrt(var)
    return min(max(sig, p["sigma_floor"]), p["sigma_cap"]) * p["sigma_scale"]


def _stale_run(prices):
    """Length (in minutes) the last price has been unchanged."""
    x = prices[-1]
    run = 0
    for v in reversed(prices):
        if v != x:
            break
        run += 1
    return run - 1  # k equal prices span k-1 minute gaps


def _bucket_mult(h, p):
    """Horizon-bucket width multiplier for horizon h (1-based)."""
    for h_max, mult in p["buckets"]:
        if h <= h_max:
            return mult
    return p["buckets"][-1][1]


def forecast_case(case, p=None):
    """Forecast one case: list of {"id": ..., "q": [[5 quantiles] x 240]}."""
    p = PARAMS if p is None else p
    horizon = case["horizon_minutes"]
    markets = case["markets"]
    n = len(markets)
    last = [m["prices"][-1] for m in markets]
    total = sum(last)  # S: sum of the checkpoint prices

    # Per-market vol, staleness and activity counts (computed once).
    sig = []
    changes = []
    for m in markets:
        prices = m["prices"]
        s = _sigma(prices, p)
        if _stale_run(prices) >= p["stale_minutes"]:
            s *= p["stale_mult"]
        changes.append(sum(1 for v in _diffs(prices) if v != 0))
        sig.append(s)
    # Activity tier: top tertile by change count gets the widening.
    order = sorted(range(n), key=lambda i: changes[i])
    n_top = (n + 2) // 3  # ceil(n/3)
    active = set(order[n - n_top:])
    for i in active:
        sig[i] *= p["active_mult"]

    out = []
    for i, m in enumerate(markets):
        x = last[i]
        target = x / total if total > 0 else x
        rows = []
        for h in range(1, horizon + 1):
            glide = 1.0 - math.exp(-h / p["tau"])
            centre = x + glide * (target - x)
            mult = _bucket_mult(h, p) * sig[i] * math.sqrt(h)
            row = []
            for j in range(5):
                row.append(price6(centre + mult * Z[j] + p["offsets"][j]))
            for j in range(1, 5):  # enforce non-decreasing quantiles
                if row[j] < row[j - 1]:
                    row[j] = row[j - 1]
            rows.append(row)
        out.append({"id": m["id"], "q": rows})
    return out


def main():
    in_dir = Path(INPUT_DIR)
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs = sorted(p.name for p in in_dir.iterdir() if p.name.endswith(".in"))
    for name in inputs:
        case_id = name[: -len(".in")]
        case = json.loads((in_dir / name).read_text(encoding="utf-8"))
        if list(case["quantiles"]) != QUANTILES:
            raise ValueError(f"{name} asks for quantiles {case['quantiles']}")
        forecasts = forecast_case(case)
        (out_dir / f"{case_id}.out").write_text(
            json.dumps({"forecasts": forecasts}) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
