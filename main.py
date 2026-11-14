# Polymarket FAA outage attribution forecaster v3 (make.fun polymarket-faa-attribution-02).
#
# v2's method PLUS the fix that matters most for the final: an
# EXHAUSTIVENESS GATE on the share glide.
#
# The problem v3 fixes (measured, not hypothesised). v1/v2 glide the
# centre toward the market's normalised share x/S, on the assumption
# that the sibling prices sum to ~1 -- that the family is exhaustive,
# so a market above its share must fall and one below it must rise.
# That holds on the practice (the NVIDIA CEO event: S = 0.81-1.09 at
# the six checkpoints). It does NOT hold on the final: the four FAA
# attribution markets are four different possible causes of one outage,
# and the crowd prices them at Iran 0.021, foreign actor 0.030, hack
# 0.037, AI sandbox escape 0.006 -- a sum S of ~0.094, because there is
# a ~91% chance of "none of these / unknown / no attribution". The
# family is NOT exhaustive, so S will not drift to 1, and gliding
# toward x/S (~10x the price) drags every median far up for no reason.
# On a synthetic FAA-like case (S = 0.094, realised prices ~ persistence)
# v1/v2 score ~4.53 -- twelve times worse than the house baseline
# (0.363589) -- while a persistence centre scores ~0.045. The share
# glide, left ungated, would throw the final away.
#
# The gate. The share glide is multiplied by an exhaustiveness factor
# g(S) that is 1 while the family is (near-)exhaustive and 0 when it
# clearly is not:
#     g(S) = 1                      if |S - 1| <= 0.30
#     g(S) = 1 - (|S - 1| - 0.30)/0.30   if 0.30 < |S - 1| < 0.60
#     g(S) = 0                      if |S - 1| >= 0.60
# The deadband is chosen so every practice checkpoint (S in 0.81-1.09)
# sits inside it: on the practice v3 is EXACTLY v2 (loss 0.245554, same
# per-case numbers), so the gate costs nothing where the glide is valid.
# On the final (S ~ 0.094) the gate is 0 and v3 behaves as persistence
# plus the slow trailing-mean glide -- the correct centre for a
# non-exhaustive family. The gate is per case (each checkpoint's own S),
# so if a later final case ever has S near 1 the glide comes back.
#
# Everything else is v2 unchanged: the slow trailing-mean glide
# (weight 0.20, tau 960, window 1440), the stronger share glide
# (gain 1.25, tau 210), the narrower upper tail (0.85x), the
# realised-vol fan (RMS winsorised diffs, windows 120/360/1440 at
# 0.55/0.30/0.15, floor 0.0002, cap 0.012, scale 0.50), the horizon
# buckets (1.00/0.85/0.85), staleness (x0.5 at >=120 min flat) and
# activity (x1.15 top tertile), and the quantile offsets (q75 -0.04,
# q95 +0.005). See FAA-V2-CHALLENGER.md for how each was measured.
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
CFG = {
    "share_tau": 210.0,          # share-glide time constant (minutes)
    "share_gain": 1.25,          # share-glide strength
    "gate_dead": 0.30,           # |S-1| inside which the glide is full
    "gate_width": 0.30,          # |S-1| span over which it fades to 0
    "mean_weight": 0.20,         # trailing-mean glide weight
    "mean_window": 1440,         # trailing window for the mean (minutes)
    "mean_tau": 960.0,           # trailing-mean glide time constant
    "vol_windows": ((120, 0.55), (360, 0.30), (1440, 0.15)),
    "winsor": 0.05,              # per-increment winsor cap
    "sigma_floor": 0.0002,
    "sigma_cap": 0.012,
    "sigma_scale": 0.50,         # global calibration on RMS sigma
    "horizon_mult": ((30, 1.00), (120, 0.85), (240, 0.85)),
    "stale_minutes": 120,        # unchanged-price run that counts as stale
    "stale_mult": 0.5,
    "active_mult": 1.15,         # top-tertile activity widening
    "offsets": (0.0, 0.0, 0.0, -0.04, 0.005),
    "upper_width": 0.85,         # upper-tail (q75, q95) width multiplier
}


def clamp01(x):
    """Clamp to [0, 1] and round to 6 decimals (0, never -0)."""
    return round(min(1.0, max(0.0, x)) * 1e6) / 1e6 + 0.0


def exhaustiveness_gate(total, cfg):
    """How much of the share glide to apply, from the family's total S.
    1 while the family looks exhaustive (|S-1| within the deadband),
    fading linearly to 0 across the gate width, 0 beyond it. The glide
    toward x/S is only meaningful when S is near 1; for a
    non-exhaustive family (S far below 1, as the FAA markets are) the
    share target is nonsense and the glide must not run."""
    gap = abs(total - 1.0)
    dead = cfg["gate_dead"]
    width = cfg["gate_width"]
    if gap <= dead:
        return 1.0
    if gap >= dead + width:
        return 0.0
    return 1.0 - (gap - dead) / width


def _increments(prices):
    """1-minute increments of a price series."""
    return [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]


def realised_sigma(prices, cfg):
    """Blended realised vol: RMS of winsorised increments over trailing
    windows. Floor/cap are applied BEFORE the scale."""
    inc = _increments(prices)
    w = cfg["winsor"]
    clipped = [max(-w, min(w, d)) for d in inc]
    if not clipped:
        return cfg["sigma_floor"]
    sigma = 0.0
    for minutes, weight in cfg["vol_windows"]:
        seg = clipped[-minutes:] if len(clipped) > minutes else clipped
        var = sum(d * d for d in seg) / len(seg)
        sigma += weight * math.sqrt(var)
    return min(max(sigma, cfg["sigma_floor"]), cfg["sigma_cap"]) * cfg["sigma_scale"]


def flat_run(prices):
    """Length (in minutes) the last price has been unchanged."""
    x = prices[-1]
    run = 0
    for v in reversed(prices):
        if v != x:
            break
        run += 1
    return run - 1  # k equal prices span k-1 minute gaps


def horizon_factor(h, cfg):
    """Horizon-bucket width multiplier for horizon h (1-based)."""
    for h_max, mult in cfg["horizon_mult"]:
        if h <= h_max:
            return mult
    return cfg["horizon_mult"][-1][1]


def trailing_mean(prices, window):
    """Mean of the last `window` minutes (or the whole series)."""
    seg = prices[-window:] if len(prices) > window else prices
    return sum(seg) / len(seg)


def forecast_case(case, cfg=None):
    """Forecast one case: list of {"id": ..., "q": [[5 quantiles] x 240]}."""
    cfg = CFG if cfg is None else cfg
    horizon = case["horizon_minutes"]
    markets = case["markets"]
    n = len(markets)
    last = [m["prices"][-1] if m["prices"] else 0.5 for m in markets]
    total = sum(last)  # S: sum of the checkpoint prices
    gate = exhaustiveness_gate(total, cfg)

    # Per-market vol, staleness and activity counts (computed once).
    sigma = []
    moves = []
    for m in markets:
        prices = m["prices"]
        if len(prices) < 2:
            sigma.append(cfg["sigma_floor"] * cfg["sigma_scale"])
            moves.append(0)
            continue
        s = realised_sigma(prices, cfg)
        if flat_run(prices) >= cfg["stale_minutes"]:
            s *= cfg["stale_mult"]
        moves.append(sum(1 for v in _increments(prices) if v != 0))
        sigma.append(s)
    # Activity tier: top tertile by move count gets the widening.
    order = sorted(range(n), key=lambda i: moves[i])
    n_top = (n + 2) // 3  # ceil(n/3)
    busy = set(order[n - n_top:])
    for i in busy:
        sigma[i] *= cfg["active_mult"]

    out = []
    for i, m in enumerate(markets):
        x = last[i]
        prices = m["prices"]
        if len(prices) < 2:
            # Degenerate market: no history to glide from; centre flat.
            share_target = x
            mean_target = x
        else:
            share_target = x / total if total > 0 else x
            mean_target = trailing_mean(prices, cfg["mean_window"])
        rows = []
        for h in range(1, horizon + 1):
            # Two glides, both starting at the checkpoint price and
            # saturating exponentially. The share glide is gated by the
            # family's exhaustiveness; the trailing-mean glide is not
            # (a market's own history is meaningful whatever S is).
            g_share = 1.0 - math.exp(-h / cfg["share_tau"])
            g_mean = 1.0 - math.exp(-h / cfg["mean_tau"])
            centre = x
            centre += gate * cfg["share_gain"] * g_share * (share_target - x)
            centre += cfg["mean_weight"] * g_mean * (mean_target - x)
            width = horizon_factor(h, cfg) * sigma[i] * math.sqrt(h)
            row = []
            for j in range(5):
                tail = 1.0 if j <= 2 else cfg["upper_width"]
                row.append(clamp01(centre + width * Z[j] * tail + cfg["offsets"][j]))
            for j in range(1, 5):  # enforce non-decreasing quantiles
                if row[j] < row[j - 1]:
                    row[j] = row[j - 1]
            rows.append(row)
        out.append({"id": m["id"], "q": rows})
    return out


def check_rows(forecasts, case):
    """Validate the forecast shape before writing it: one row per market,
    240 rows per market, 5 non-decreasing quantiles in [0, 1] each.
    A malformed output scores as all-0.5, so fail loudly here instead
    of shipping a bad file to the grader."""
    horizon = case["horizon_minutes"]
    want = {m["id"] for m in case["markets"]}
    got = {f["id"] for f in forecasts}
    if want != got:
        raise ValueError(f"market ids mismatch: missing {want - got}, extra {got - want}")
    for f in forecasts:
        if len(f["q"]) != horizon:
            raise ValueError(f"{f['id']}: {len(f['q'])} rows, want {horizon}")
        for row in f["q"]:
            if len(row) != 5:
                raise ValueError(f"{f['id']}: row of {len(row)} quantiles, want 5")
            for j in range(5):
                if not (0.0 <= row[j] <= 1.0):
                    raise ValueError(f"{f['id']}: quantile {row[j]} out of [0, 1]")
            for j in range(1, 5):
                if row[j] < row[j - 1]:
                    raise ValueError(f"{f['id']}: quantiles not non-decreasing: {row}")


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
        check_rows(forecasts, case)
        (out_dir / f"{case_id}.out").write_text(
            json.dumps({"forecasts": forecasts}) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
