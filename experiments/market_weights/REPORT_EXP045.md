# EXP-045 Stage 1 — Market Revaluation Prediction

> Narrow prior conclusion (041–044): **crude manual multipliers on current baseline add no OOS signal.**
> This experiment asks a different question: can we **OOS-predict** which observations the market will treat as informative?

- Runtime: 6s
- Labeled team-match samples: `7341`
- OOS window: `2025-12` … `2026-02` (expanding monthly)
- Model: standardized ridge (λ=1), separate abs / signed targets
- Label: median latent strength over next 3 appearances − pre-match strength (opponent/H adjusted Elo-like path)
- Opening lines: **not in DB** → open/close drift features unavailable

## Odds coverage

| League | Season | n | closing D% | closing S% | opening D% |
|---|---|---:|---:|---:|---:|
| Bundesliga | 2024-25 | 307 | 0.0 | 0.0 | 0.0 |
| Bundesliga | 2025-26 | 306 | 100.0 | 100.0 | 0.0 |
| La Liga | 2023-24 | 376 | 100.0 | 100.0 | 0.0 |
| La Liga | 2024-25 | 379 | 100.0 | 100.0 | 0.0 |
| La Liga | 2025-26 | 380 | 100.0 | 100.0 | 0.0 |
| Ligue 1 | 2023-24 | 310 | 100.0 | 100.0 | 0.0 |
| Ligue 1 | 2024-25 | 307 | 100.0 | 100.0 | 0.0 |
| Ligue 1 | 2025-26 | 306 | 100.0 | 100.0 | 0.0 |
| Premier League | 2023-24 | 381 | 0.0 | 0.0 | 0.0 |
| Premier League | 2024-25 | 380 | 0.0 | 0.0 | 0.0 |
| Premier League | 2025-26 | 380 | 100.0 | 99.7 | 0.0 |
| Serie A | 2023-24 | 379 | 100.0 | 100.0 | 0.0 |
| Serie A | 2024-25 | 379 | 100.0 | 100.0 | 0.0 |
| Serie A | 2025-26 | 380 | 100.0 | 100.0 | 0.0 |

## OOS ridge results

- n_oos team-rows: **1170**
- r(pred, actual abs revaluation): **0.1019915034438083**
- r(pred, actual signed revaluation): **0.050374679696933246**
- Quantile monotonicity (abs): **False**

| Month | n_test | r_abs | r_signed |
|---|---:|---:|---:|
| 2025-12 | 340 | 0.15602700622933183 | 0.10196617721228611 |
| 2026-01 | 446 | 0.03980769366432347 | 0.022345926743132186 |
| 2026-02 | 384 | 0.1596377361097329 | 0.046655899525375084 |

## Holdout predicted-info quantiles vs actual |reval|

| Q | n | pred_mean | actual_abs_mean |
|---:|---:|---:|---:|
| 1 | 234 | 0.0385 | 0.0498 |
| 2 | 234 | 0.0518 | 0.0431 |
| 3 | 234 | 0.0582 | 0.0397 |
| 4 | 234 | 0.0659 | 0.0448 |
| 5 | 234 | 0.0838 | 0.0587 |

## Verdicts

### EXP-045_STAGE1_ABS: **PARTIAL**
|r|=0.102 in [0.10, 0.20) — weak; refine features/label before weighting

### EXP-045_STAGE1_SIGNED: **FAIL**
|r|=0.050 < 0.10 — cannot reliably predict informative matches; do not build weighting layer

### EXP-045_OVERALL: **PARTIAL**
Market Information Model Stage1 (predict revaluation only). |r|=0.102 in [0.10, 0.20) — weak; refine features/label before weighting | Do NOT start EXP-046.

### EXP-041: **CLOSED_NO_EFFECT**
bucket predicted-info weights; superseded by EXP-045 framing

### EXP-042: **CLOSED_NO_EFFECT**
crude change-point weights duplicate Dynamic D / expanding

### EXP-043: **CLOSED_NO_EFFECT**
team volatility decay buckets

### EXP-044: **CLOSED_FAIL**
D buckets FAIL; SMALL_D kept as feature candidate (is_small_d in EXP-045)

## Roadmap

| EXP | Status |
|---|---|
| 041 Predicted info buckets | CLOSED — NO EFFECT |
| 042 Change point weights | CLOSED — NO EFFECT |
| 043 Volatility decay | CLOSED — NO EFFECT |
| 044 D buckets | CLOSED — FAIL; SMALL_D feature candidate |
| 045 Rich market-revaluation prediction | THIS RUN |
| 046 Learned weighting policy | only if 045 PASS |
| 047 Interactions / league-specific | after 045–046 |

Layer name: **Market Information Model** (not just match weights).
