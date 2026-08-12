# EXP-041 … 044 — Market information weights (offline)

- Baseline: DB `match_weight` × Dynamic D / S-EMA / SFA / SFTC / expanding monthly
- Holdout months: `2025-12` … `2026-02`
- Rows (full closing, motivated): `3837`
- Opening lines in DB: **absent** → open/close drift features skipped
- Runtime: 1918s
- Read-only DB; production codepaths untouched

## EXP-041 correlation study

| Feature | n | Pearson vs future_reval_max |
|---|---:|---:|
| abs_d | 2707 | 0.0034 |
| abs_delta1_max | 2659 | 0.0716 |
| abs_delta3_max | 2659 | 0.0044 |
| rest_min | 2707 | -0.0262 |
| s_market | 2707 | 0.0222 |
| season_stage | 2707 | 0.0736 |
| sign_consistency_max | 2659 | -0.0353 |
| vol_max | 2609 | 0.0303 |

Selected feature for weight buckets: `abs_delta1_max` (|r|=0.0716)

## Pooled results (n-weighted over leagues × months)

| Scheme | n | MAE AH | MAE Tot | n AH≥0.5 | n Tot≥0.5 |
|---|---:|---:|---:|---:|---:|
| BASELINE | 585 | 0.1509 | 0.1436 | 44 | 36 |
| EXP043_VOL_DECAY | 585 | 0.1513 | 0.1432 | 46 | 37 |
| EXP044_SMALL_D | 585 | 0.1483 | 0.1419 | 40 | 34 |
| EXP044_LARGE_D | 585 | 0.1526 | 0.1457 | 45 | 38 |
| EXP044_USHAPE | 585 | 0.1526 | 0.1444 | 44 | 38 |
| EXP042_CP_0.3 | 585 | 0.1513 | 0.1415 | 44 | 37 |
| EXP042_CP_0.6 | 585 | 0.1521 | 0.1406 | 43 | 35 |
| EXP041_PRED_INFO | 585 | 0.1517 | 0.1444 | 43 | 42 |

## Verdicts

### EXP-043_VOL_DECAY: **NO EFFECT**
within eps; dAH=0.0004 dTot=-0.0004

### EXP-044_SMALL_D: **NO EFFECT**
within eps; dAH=-0.0026 dTot=-0.0017

### EXP-044_LARGE_D: **NO EFFECT**
within eps; dAH=0.0017 dTot=0.0021

### EXP-044_USHAPE: **NO EFFECT**
within eps; dAH=0.0017 dTot=0.0009

### EXP-042_CP_0.3: **NO EFFECT**
within eps; dAH=0.0004 dTot=-0.0021

### EXP-042_CP_0.6: **NO EFFECT**
within eps; dAH=0.0013 dTot=-0.0030

### EXP-041_PRED_INFO: **NO EFFECT**
within eps; dAH=0.0009 dTot=0.0009

### EXP-044_OVERALL: **FAIL**
no |D|-bucket scheme beats baseline on incremental closing MAE

## Method notes

1. Each scheme = **BASELINE + one multiplier** on `quality_match_weight`.
2. Metrics = closing AH/Total only (FT not used for verdicts).
3. Change-point & volatility use only history before each monthly cut.
4. EXP-041 future revaluation is train label / correlation only; runtime weight uses causal feature buckets.
