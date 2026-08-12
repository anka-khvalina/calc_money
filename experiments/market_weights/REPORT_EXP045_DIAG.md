# EXP-045A/B/C — Last diagnostic round (Market Information Model)

> No weighting. Question: can we reliably OOS-predict market-informative observations?

- Runtime: 10s
- Events: `7674`
- OOS window: `2025-12` … `2026-02`
- Opening lines: still **absent** (0% coverage)

## Stop-rule

Continue only if ALL hold: `|r|≳0.15`, soft quantile mono, ≥2 temporal folds with `|r|≥0.10`, ≥2 leagues with `r>0.05`.

## EXP-045A — Labels

| Label | OOS r | stop | detail |
|---|---:|---|---|
| L1 | 0.0707 | False | |r|=0.071<0.15; mono_soft=True; folds|r|≥0.10: 1/3; leagues_r>0.05: 2/5 |
| L2 | 0.1055 | False | |r|=0.106<0.15; mono_soft=False; folds|r|≥0.10: 2/3; leagues_r>0.05: 4/5 |
| L3 | 0.1237 | False | |r|=0.124<0.15; mono_soft=True; folds|r|≥0.10: 3/3; leagues_r>0.05: 3/5 |
| L4 | 0.0919 | False | |r|=0.092<0.15; mono_soft=True; folds|r|≥0.10: 1/3; leagues_r>0.05: 2/5 |

Best label: **L3**

## EXP-045B — Horizons

| Horizon | OOS r | stop | detail |
|---|---:|---|---|
| next_1 | 0.0707 | False | |r|=0.071<0.15; mono_soft=True; folds|r|≥0.10: 1/3; leagues_r>0.05: 2/5 |
| next_2 | 0.0907 | False | |r|=0.091<0.15; mono_soft=True; folds|r|≥0.10: 2/3; leagues_r>0.05: 3/5 |
| next_3 | 0.1237 | False | |r|=0.124<0.15; mono_soft=True; folds|r|≥0.10: 3/3; leagues_r>0.05: 3/5 |
| next_5 | 0.1468 | False | |r|=0.147<0.15; mono_soft=True; folds|r|≥0.10: 3/3; leagues_r>0.05: 3/5 |
| days_14 | 0.1356 | False | |r|=0.136<0.15; mono_soft=False; folds|r|≥0.10: 2/3; leagues_r>0.05: 4/5 |
| days_30 | 0.0774 | False | |r|=0.077<0.15; mono_soft=False; folds|r|≥0.10: 1/3; leagues_r>0.05: 2/5 |
| days_45 | 0.0628 | False | |r|=0.063<0.15; mono_soft=True; folds|r|≥0.10: 0/3; leagues_r>0.05: 2/5 |

Best horizon: **next_5**

## EXP-045C — Features

| Model | OOS r | mono_soft | stop | detail |
|---|---:|---|---|---|
| ridge_raw | 0.1468 | True | False | |r|=0.147<0.15; mono_soft=True; folds|r|≥0.10: 3/3; leagues_r>0.05: 3/5 |
| ridge_interactions | 0.1381 | True | False | |r|=0.138<0.15; mono_soft=True; folds|r|≥0.10: 2/3; leagues_r>0.05: 3/5 |
| shallow_tree | 0.0379 | True | False | |r|=0.038<0.15; mono_soft=True; folds|r|≥0.10: 1/3; leagues_r>0.05: 2/5 |

## Mine verdict

### **CLOSE_MIM**
After label/horizon/interactions/tree diagnostics, no config jointly satisfies OOS |r|≳0.15 + soft quantile mono + multi-fold + multi-league. Best seen: 045B/next_5 r=0.14679404634815507 (|r|=0.147<0.15; mono_soft=True; folds|r|≥0.10: 3/3; leagues_r>0.05: 3/5). Do not build weighting. Next research = new observable team state (lineups/xG/injuries/coach/style), not match weights. Opening coverage still 0% — noted as missing feature, not a free pass to keep mining.

## What this means

1. Market likely *does* revalue teams after some sequences — we still cannot **predict that OOS** from pre-match fields we have.
2. Closing the weighting / MIM mine is methodological honesty, not a claim that information weights are philosophically wrong.
3. Next useful research is **new observables** (lineups, xG, injuries, coach/tactical state), plus opening lines if/when available.
