# Bias audit vs closing lines (existing artifacts, no DB / no prod changes)

- Source: `/opt/cursor/artifacts/exp041_044/pred_rows.csv` scheme `BASELINE`
- Rows: `585`; months: `2025-12, 2026-01, 2026-02`; leagues: `5`

Sign convention: `bias_D = D_model − D_market` (>0 ⇒ we make home stronger than market). `bias_Tot = Total_model − Total_market` (>0 ⇒ we price higher totals).
`±2se = YES` means bias is larger than 2 standard errors (unlikely pure noise). `stable sign = YES` means mean bias has the same sign in every month.

## Overall

- `bias_D` = **+0.0235** (2se YES, stable YES), MAE_AH 0.1509
- `bias_Tot` = **+0.0265** (2se YES, stable no), MAE_Tot 0.1436
- monthly `bias_D`: {'2025-12': 0.0059, '2026-01': 0.0258, '2026-02': 0.0365}
- monthly `bias_Tot`: {'2025-12': 0.0441, '2026-01': 0.0493, '2026-02': -0.0156}

## Tables

### By league

| Group | n | bias_D | ±2se | stable sign | bias_Tot | ±2se | stable sign |
|---|---:|---:|:--:|:--:|---:|:--:|:--:|
| Bundesliga | 104 | +0.0120 | no | no | +0.0721 | YES | no |
| La Liga | 115 | +0.0391 | no | YES | +0.0326 | no | no |
| Ligue 1 | 85 | +0.0206 | no | YES | +0.0618 | YES | YES |
| Premier League | 146 | -0.0017 | no | no | -0.0274 | no | no |
| Serie A | 135 | +0.0481 | YES | YES | +0.0222 | no | YES |

### By |D| bucket (market)

| Group | n | bias_D | ±2se | stable sign | bias_Tot | ±2se | stable sign |
|---|---:|---:|:--:|:--:|---:|:--:|:--:|
| 0.25-0.75 | 270 | +0.0472 | YES | YES | +0.0491 | YES | YES |
| 0.75-1.25 | 155 | -0.0226 | no | YES | -0.0290 | no | no |
| |D|<0.25 | 69 | +0.0471 | YES | YES | +0.1268 | YES | YES |
| |D|>=1.25 | 91 | +0.0137 | no | no | -0.0220 | no | no |

### By S bucket (market)

| Group | n | bias_D | ±2se | stable sign | bias_Tot | ±2se | stable sign |
|---|---:|---:|:--:|:--:|---:|:--:|:--:|
| 2.25-2.75 | 282 | +0.0222 | no | YES | +0.0594 | YES | YES |
| 2.75-3.25 | 172 | +0.0320 | no | no | -0.0334 | YES | no |
| S<2.25 | 65 | +0.0885 | YES | YES | +0.1962 | YES | YES |
| S>=3.25 | 66 | -0.0568 | no | YES | -0.1250 | YES | YES |

### By month

| Group | n | bias_D | ±2se | stable sign | bias_Tot | ±2se | stable sign |
|---|---:|---:|:--:|:--:|---:|:--:|:--:|
| 2025-12 | 170 | +0.0059 | no | YES | +0.0441 | YES | YES |
| 2026-01 | 223 | +0.0258 | no | YES | +0.0493 | YES | YES |
| 2026-02 | 192 | +0.0365 | YES | YES | -0.0156 | no | YES |

### League × |D| bucket

| Group | n | bias_D | ±2se | stable sign | bias_Tot | ±2se | stable sign |
|---|---:|---:|:--:|:--:|---:|:--:|:--:|
| Bundesliga / 0.25-0.75 | 51 | +0.0441 | no | YES | +0.1078 | YES | YES |
| Bundesliga / 0.75-1.25 | 29 | +0.0000 | no | no | -0.0431 | no | YES |
| Bundesliga / |D|<0.25 | 11 | +0.0909 | no | YES | +0.2045 | YES | YES |
| Bundesliga / |D|>=1.25 | 13 | -0.1538 | no | no | +0.0769 | no | no |
| La Liga / 0.25-0.75 | 49 | +0.0459 | no | YES | +0.0561 | no | no |
| La Liga / 0.75-1.25 | 27 | -0.0185 | no | no | +0.0000 | no | no |
| La Liga / |D|<0.25 | 22 | +0.0341 | no | YES | +0.1364 | YES | YES |
| La Liga / |D|>=1.25 | 17 | +0.1176 | no | YES | -0.1176 | no | YES |
| Ligue 1 / 0.25-0.75 | 40 | +0.0688 | YES | YES | +0.0813 | YES | YES |
| Ligue 1 / 0.75-1.25 | 21 | +0.0119 | no | no | -0.0119 | no | YES |
| Ligue 1 / |D|<0.25 | 10 | +0.0000 | no | no | +0.2250 | YES | YES |
| Ligue 1 / |D|>=1.25 | 14 | -0.0893 | no | no | +0.0000 | no | no |
| Premier League / 0.25-0.75 | 66 | +0.0114 | no | no | -0.0038 | no | no |
| Premier League / 0.75-1.25 | 42 | -0.0595 | no | YES | -0.0655 | no | no |
| Premier League / |D|<0.25 | 13 | +0.0577 | no | YES | -0.0385 | no | YES |
| Premier League / |D|>=1.25 | 25 | +0.0300 | no | YES | -0.0200 | no | no |
| Serie A / 0.25-0.75 | 64 | +0.0742 | YES | YES | +0.0312 | no | YES |
| Serie A / 0.75-1.25 | 36 | -0.0208 | no | no | -0.0069 | no | no |
| Serie A / |D|<0.25 | 13 | +0.0577 | no | YES | +0.1346 | YES | YES |
| Serie A / |D|>=1.25 | 22 | +0.0795 | no | YES | -0.0227 | no | no |

### League × S bucket

| Group | n | bias_D | ±2se | stable sign | bias_Tot | ±2se | stable sign |
|---|---:|---:|:--:|:--:|---:|:--:|:--:|
| Bundesliga / 2.25-2.75 | 34 | +0.0515 | no | YES | +0.1765 | YES | YES |
| Bundesliga / 2.75-3.25 | 45 | +0.0500 | no | no | +0.0611 | YES | no |
| Bundesliga / S<2.25 | 1 | +0.2500 | no | YES | +0.5000 | no | YES |
| Bundesliga / S>=3.25 | 24 | -0.1250 | YES | YES | -0.0729 | no | no |
| La Liga / 2.25-2.75 | 53 | +0.0142 | no | no | +0.0377 | no | no |
| La Liga / 2.75-3.25 | 17 | +0.0294 | no | no | -0.1029 | YES | YES |
| La Liga / S<2.25 | 29 | +0.0862 | YES | YES | +0.1897 | YES | YES |
| La Liga / S>=3.25 | 16 | +0.0469 | no | no | -0.1250 | no | YES |
| Ligue 1 / 2.25-2.75 | 44 | +0.0511 | no | YES | +0.1420 | YES | YES |
| Ligue 1 / 2.75-3.25 | 29 | +0.0000 | no | no | +0.0000 | no | no |
| Ligue 1 / S<2.25 | 1 | +0.0000 | no | no | +0.5000 | no | YES |
| Ligue 1 / S>=3.25 | 11 | -0.0455 | no | no | -0.1364 | no | YES |
| Premier League / 2.25-2.75 | 65 | +0.0000 | no | no | +0.0500 | YES | YES |
| Premier League / 2.75-3.25 | 64 | +0.0156 | no | no | -0.0742 | YES | no |
| Premier League / S<2.25 | 2 | -0.1250 | no | no | +0.2500 | no | YES |
| Premier League / S>=3.25 | 15 | -0.0667 | no | YES | -0.2000 | YES | YES |
| Serie A / 2.25-2.75 | 86 | +0.0174 | no | YES | -0.0087 | no | no |
| Serie A / 2.75-3.25 | 17 | +0.1029 | no | YES | -0.1176 | YES | YES |
| Serie A / S<2.25 | 32 | +0.1016 | YES | YES | +0.1797 | YES | YES |

## Shift vs slope (OLS of bias on centred market value)

`bias = a + b·(market − mean)`. `b < 0` ⇒ our value is compressed toward the mean, so a **gain** correction is needed rather than a flat shift.

| Target | n | intercept a | 2se | slope b | 2se | our spread / market (1+b) | corrective gain |
|---|---:|---:|:--:|---:|:--:|---:|---:|
| D | 585 | +0.0235 | YES | +0.0261 | YES | 1.026 | 0.975 |
| Total | 585 | +0.0265 | YES | -0.1817 | YES | 0.818 | 1.222 |

Per-month slope (stability check):

| Month | slope D | sig | slope Tot | sig |
|---|---:|:--:|---:|:--:|
| 2025-12 | +0.0598 | YES | -0.2245 | YES |
| 2026-01 | +0.0314 | no | -0.1474 | YES |
| 2026-02 | -0.0057 | no | -0.1740 | YES |

## Direct calibration fit (market = α + β·model)

This is the correction that would actually be applied. `β > 1` ⇒ expand model deviations. `MAE after` is **in-sample** and only an upper bound on the achievable gain.

| Target | n | α | β | β≠1 | MAE before | MAE after (in-sample) |
|---|---:|---:|---:|:--:|---:|---:|
| D | 585 | +0.0015 | 0.9103 | YES | 0.1509 | 0.1581 |
| Total | 585 | +0.0749 | 0.9612 | no | 0.1436 | 0.1524 |

Total calibration by league:

| League | n | α | β | β≠1 | MAE before | MAE after (in-sample) |
|---|---:|---:|---:|:--:|---:|---:|
| Bundesliga | 104 | +0.0032 | 0.9744 | no | 0.1635 | 0.1798 |
| La Liga | 115 | -0.1554 | 1.0491 | no | 0.1457 | 0.1599 |
| Ligue 1 | 85 | +0.0818 | 0.9473 | no | 0.1500 | 0.1630 |
| Premier League | 146 | +0.4882 | 0.8274 | YES | 0.1404 | 0.1535 |
| Serie A | 135 | +0.1904 | 0.9085 | no | 0.1259 | 0.1363 |

Total calibration by month (stability of β):

| Month | n | α | β | β≠1 | MAE before | MAE after (in-sample) |
|---|---:|---:|---:|:--:|---:|---:|
| 2025-12 | 170 | +0.0227 | 0.9746 | no | 0.1529 | 0.1677 |
| 2026-01 | 223 | +0.1369 | 0.9285 | YES | 0.1457 | 0.1544 |
| 2026-02 | 192 | +0.0390 | 0.9910 | no | 0.1328 | 0.1403 |

## Decisive held-out test: (gain, shift) grid with 0.25 rounding

Fitted on `2025-12, 2026-01`, tested on `2026-02`. Corrections are applied as `centre + gain·(model − centre) + shift`, then rounded to the 0.25 grid.

| Target | best gain | best shift | MAE train | MAE test | baseline MAE test | test Δ |
|---|---:|---:|---:|---:|---:|---:|
| Tot | 0.90 | +0.00 | 0.1476 | 0.1341 | 0.1328 | +0.0013 |
| D | 0.90 | +0.00 | 0.1425 | 0.1523 | 0.1510 | +0.0013 |

Single-knob variants (held-out):

| Target | gain-only | MAE test | shift-only | MAE test | baseline |
|---|---:|---:|---:|---:|---:|
| Tot | g=0.90 | 0.1341 | s=-0.10 | 0.1328 | 0.1328 |
| D | g=0.90 | 0.1523 | s=-0.10 | 0.1510 | 0.1510 |


## Recommended single knob

### **NO_KNOB (corrections do not transfer out-of-sample)**

- Held-out grid test: no (gain, shift) fitted on earlier months improves the unseen month after 0.25 rounding — Tot: best(train g=0.9, s=0.0) test MAE 0.1341 vs baseline 0.1328; D: best(train g=0.9, s=0.0) test MAE 0.1523 vs baseline 0.1510.
- The apparent S 'compression' (regressing bias on the market value) is a regression-attenuation artifact: the direct fit gives β≈0.96 (not different from 1) and least-squares calibration raises MAE because lines sit on a 0.25 grid.
- Best in-sample corrections are gain 0.90 / shift −0.10, i.e. shrinking toward the mean — the opposite of what the naive slope suggested. Neither transfers out of sample.
- Superseded diagnostic: Total: slope -0.1817 vs market S is significant → our S spread is 0.818 of the market's (compressed); corrective gain ≈ 1.222.
- Superseded diagnostic: D: slope +0.0261 vs market D is significant → spread 1.026 of market; corrective gain ≈ 0.975.

## Protocol if any knob is ever tried

1. Change **one** parameter; keep Dynamic D / S-EMA / SFA / SFTC as-is.
2. Fit the correction on months **before** the test month; never on the test month.
3. Judge MAE **after 0.25 rounding** — off-grid gains are illusory.
4. Accept only if closing MAE improves on ≥2 unseen months and no league degrades badly.
5. Record decision + window in an ADR before touching `web/model_config.json`.

## Caveats

- Only `585` rows over `3` months; one held-out month is a weak test.
- PL/BL history without odds is excluded, so league mix is uneven.
- Bias magnitudes (~0.02–0.03 goals) are far below the 0.25 line grid, so most of them cannot change a printed line at all.
