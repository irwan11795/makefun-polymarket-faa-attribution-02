# FAA outage attribution forecaster v1

Forecasts, minute by minute and 4 hours ahead, the Yes price of every
market in a Polymarket family, as five quantiles (5%, 25%, 50%, 75%, 95%)
per minute. Entry for make.fun `polymarket-faa-attribution-02`.

## Method

Persistence + sum-reversion median with a realised-volatility fan. The
algorithm is the one behind our submitted nvidia-ceo-forecast-01 v4
(practice loss 0.248323), re-implemented fresh for this competition; the
practice cases here are the NVIDIA event, so the method transfers
directly.

1. **Median.** The forecast centre glides from the market's checkpoint
   price toward its normalised share of the family's price sum:
   `centre(h) = x + (1 - exp(-h/210)) * (x/S - x)`, where `S` is the sum
   of all markets' checkpoint prices. Sibling markets in a mutually
   exclusive family sum toward ~1, so a market above its share is
   expected to drift down and one below it to drift up. The glide moves
   the whole fan, not just the median.
2. **Spread.** Realised volatility from the market's own 1-minute price
   moves: diffs winsorised at ±0.05, RMS blended over trailing windows
   of 120 / 360 / 1440 minutes (weights 0.55 / 0.30 / 0.15), floored at
   0.0002 and capped at 0.012 before a global scale of 0.50. The fan
   half-width is `z_tau * sigma * sqrt(h)` (random-walk widening),
   times a horizon bucket: 1.00 for h 1–30, 0.85 for h 31–120, 0.85
   for h 121–240.
3. **Staleness.** A market whose price has not moved for ≥ 120 minutes
   gets its sigma halved — a flat price means the market is asleep.
4. **Activity.** The third of markets with the most price changes get
   sigma widened ×1.15 — busy markets move more.
5. **Quantile offsets.** The 75% quantile is pulled down 0.04 and the
   95% pushed up 0.005. Realised prices are steppy with a large point
   mass at the checkpoint price, so the 50–75% percentiles nearly
   coincide; −0.04 is the measured optimum on the practice.

Parameters sit at the centre of a flat plateau (neighbours tau 200–220,
scale 0.49–0.51, weights .55/.3/.15–.6/.3/.1 all similar), not at the
argmin — chosen for robustness on unseen prices, which is what the
final scores.

## Results (practice, local grader)

- Mean pinball loss **0.248323** (p00 0.382963, p01 0.297720,
  p02 0.326009, p03 0.246519, p04 0.186512, p05 0.104664).
- House baseline (persistence, sigma 0.0025): **0.363589**.
- So v1 beats the house baseline by 0.115 on the practice.

## Honest risks

- **Practice ≠ final.** The practice is the NVIDIA CEO event (19–24
  candidate markets, mostly quiet). The final is 12 cases on the FAA
  attribution markets (4 markets: Iran, foreign actor, hack, AI sandbox
  escape), recorded after the deadline. Attribution news moves in sharp
  jumps when the US government speaks; the practice's quiet regime does
  not cover that. The sum-reversion glide assumes sibling prices sum
  toward ~1 — true for a mutually exclusive family, but the four FAA
  options are not strictly exclusive (an attribution could name more
  than one cause, or none).
- **The house model forecast is a second gate.** A prize needs a loss
  strictly below the lower of the house baseline and the house model
  forecast. The model forecast's loss on the final is unknown.
- **Practice gain is concentrated.** In the nvidia grind, the v4 gain
  over v3 lived mostly in one practice case (p03); the method is a
  robust small improvement, not a leap.

## Run

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Pure Python 3.13 standard library, deterministic, offline. Reads every
`<id>.in` in the input directory and writes `<id>.out` to the output
directory. Runtime ~0.1 s per case (cap 8 s).

## License

MIT — see LICENSE.
