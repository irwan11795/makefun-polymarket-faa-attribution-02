# FAA v3 — Polymarket FAA attribution forecaster (final-robust challenger)

Competition: `polymarket-faa-attribution-02` ($250, top 100).
Deadline **2026-11-14 21:00 WIB**.

## What it is

v2's method PLUS the fix that matters most for the final: an
**exhaustiveness gate** on the share glide.

The share glide assumes the market family is exhaustive (sibling prices
sum to ~1). That holds on the practice (NVIDIA CEO event: S = 0.81-1.09)
but NOT on the final: the four FAA attribution markets are four
different possible causes of one outage, priced live at Iran 0.021,
foreign actor 0.030, hack 0.037, AI sandbox escape 0.006 -- a sum S of
~0.094, because there is a ~91% chance of "none of these / unknown / no
attribution". Gliding toward x/S (~10x the price) drags every median far
up for no reason.

The gate multiplies the share glide by g(S): 1 while |S-1| <= 0.30,
fading linearly to 0 by |S-1| = 0.60, 0 beyond. Every practice
checkpoint sits inside the deadband, so on the practice v3 is EXACTLY v2
(byte-identical outputs). On the final (S ~ 0.094) the gate is 0 and v3
behaves as persistence plus the slow trailing-mean glide -- the correct
centre for a non-exhaustive family.

## Practice results (local grader, grade.py, CLI output)

Identical to v2 (the gate is open on all six practice cases):

| case | v1 | v3 | diff |
|------|------|------|------|
| p00 | 0.382963 | 0.367403 | -0.015560 |
| p01 | 0.297720 | 0.299512 | +0.001792 |
| p02 | 0.326009 | 0.322583 | -0.003426 |
| p03 | 0.246519 | 0.249927 | +0.003408 |
| p04 | 0.186512 | 0.183986 | -0.002526 |
| p05 | 0.104664 | 0.102767 | -0.001897 |
| **MEAN** | **0.248323** | **0.245554** | **-0.002769** |

- Wins 4/6 cases; LOO wins all 6 subsets. House baseline 0.363589.
- Runtime 0.45 s for 6 cases (~0.08 s/case, cap 8 s/case).
  Deterministic: 2 runs byte-identical. Output sizes 225-297 KB.
  Format valid.

## Synthetic FAA-like case (S = 0.094, realised ~ persistence)

| version | loss |
|---------|------|
| v1 (ungated glide) | 4.525664 |
| v2 (ungated, stronger glide) | 5.681231 |
| **v3 (gated)** | **0.046288** |
| pure persistence (glide 0) | 0.045173 |

v3 is ~100x better than v1/v2 on a final-like case, at zero cost on
the practice. This is the version to submit.

## Honest risks

1. **Practice rank is ~#42, not top 20.** v3's practice loss 0.245554
   is better than v1's 0.248323 but does not reach the top-20 practice
   cutoff (0.241) and does not clear the >=0.01 bar. The case for
   submitting v3 rests on the FINAL, not the practice leaderboard:
   v1/v2's glide is catastrophically wrong for the final's S ~ 0.094.
2. **The synthetic final test is a model, not the final.** It assumes
   realised prices stay near their checkpoints (persistence). If the
   FAA markets jump hard on news, the realised prices will move and the
   loss will be higher for every entry -- but the glide error (a ~10x
   upward drift of every median) is a systematic bias that hurts
   regardless of the jumps, while v3's persistence centre does not
   carry it.
3. **Practice != final.** The final is 12 cases on the 4 FAA markets,
   recorded after the deadline. The practice's quiet regime does not
   cover attribution-news jumps.
