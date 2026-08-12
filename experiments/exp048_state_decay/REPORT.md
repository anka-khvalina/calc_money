# EXP-048 — Cross-season Dynamic State Decay

Offline only. Ratings trained on **2025–26 only**. Validate on Aug-12 book cards (n=34).
D_base / S_base kept; only past-season dynamic correction is scaled.
1X2 uses **same match overround** as the book.

Arms: CURRENT=100%, DECAY75=75%, DECAY50=50%, DECAY25=25%, RESET=0%.

Series: **BOTH** (D+S same scale), **D_ONLY** (S full), **S_ONLY** (D full).

## Summary — BOTH (primary)

| Arm | MAE AH | N(≥0.5) | flip AH% | MAE Tot | MAE odds1 | benefit vs CURRENT |
|---|---:|---:|---:|---:|---:|---:|
| CURRENT | 0.243 | 6 | 17.6% | 0.154 | 0.641 | 0.0% |
| DECAY75 | 0.213 | 5 | 11.8% | 0.154 | 0.500 | 17.6% |
| DECAY50 | 0.140 | 0 | 5.9% | 0.125 | 0.369 | 44.1% |
| DECAY25 | 0.125 | 1 | 2.9% | 0.110 | 0.267 | 55.9% |
| RESET | 0.103 | 2 | 5.9% | 0.110 | 0.223 | 58.8% |

## Summary — D_ONLY (S-EMA full)

| Arm | MAE AH | N(≥0.5) | flip AH% | MAE Tot | benefit vs CURRENT |
|---|---:|---:|---:|---:|---:|
| CURRENT | 0.243 | 6 | 17.6% | 0.154 | 0.0% |
| DECAY75 | 0.213 | 5 | 11.8% | 0.154 | 17.6% |
| DECAY50 | 0.140 | 0 | 5.9% | 0.154 | 44.1% |
| DECAY25 | 0.118 | 1 | 2.9% | 0.154 | 58.8% |
| RESET | 0.110 | 2 | 5.9% | 0.154 | 55.9% |

## Summary — S_ONLY (Dynamic D full)

| Arm | MAE AH | N(≥0.5) | MAE Tot | benefit vs CURRENT |
|---|---:|---:|---:|---:|
| CURRENT | 0.243 | 6 | 0.154 | 0.0% |
| DECAY75 | 0.243 | 6 | 0.154 | 0.0% |
| DECAY50 | 0.243 | 6 | 0.125 | 0.0% |
| DECAY25 | 0.243 | 6 | 0.110 | 0.0% |
| RESET | 0.243 | 6 | 0.110 | 0.0% |

## Named openers — BOTH arms AH

| Match | mkt | CURRENT | DECAY75 | DECAY50 | DECAY25 | RESET | flip@CURRENT |
|---|---:|---:|---:|---:|---:|---:|---|
| FC Koln–Hoffenheim | +0.25 | +0.50 | +0.50 | +0.50 | +0.25 | +0.25 | False |
| Union Berlin–Ein Frankfurt | +0.00 | +0.25 | +0.25 | +0.25 | +0.00 | +0.00 | True |
| Everton–Crystal Palace | -0.25 | -0.75 | -0.50 | -0.50 | -0.25 | -0.25 | False |
| Nott'm Forest–Leeds | -0.25 | +0.25 | +0.25 | -0.25 | -0.25 | -0.25 | True |
| Genoa–Napoli | +0.50 | +1.00 | +1.00 | +0.75 | +0.75 | +0.50 | False |
| Udinese–Como | +0.50 | +1.00 | +1.00 | +0.75 | +0.75 | +0.50 | False |

### D-path at CURRENT (for context)

| Match | D_base | D_corr(full) | D_final | D_mkt | days gap (min) |
|---|---:|---:|---:|---:|---:|
| FC Koln–Hoffenheim | -0.228 | -0.383 | -0.666 | -0.250 | 97 |
| Union Berlin–Ein Frankfurt | +0.033 | -0.419 | -0.423 | -0.000 | 91 |
| Everton–Crystal Palace | +0.214 | +0.483 | +0.755 | +0.250 | 90 |
| Nott'm Forest–Leeds | +0.341 | -0.537 | -0.276 | +0.250 | 83 |
| Genoa–Napoli | -0.644 | -0.471 | -1.212 | -0.500 | 83 |
| Udinese–Como | -0.648 | -0.414 | -1.153 | -0.500 | 83 |

## Notes

- This run is the **season-boundary** case (2026–27 openers): no new-season matches yet,
  so decay = pure carryover shrink. Within-season re-accumulation after GW1 is a follow-up.
- Time-aware decay (by days since last match) is the natural next step; `days_since_*`
  columns are already emitted for that design.
- Ideal GW shape (RESET→DECAY→CURRENT) needs expanding-window historical eval; this card
  test asks whether any decay beats CURRENT on early openers.
