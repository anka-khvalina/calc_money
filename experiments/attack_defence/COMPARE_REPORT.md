# Production vs Attack/Defence Poisson — comparison

- Holdout: `2025-03-01` … `2025-05-25` (train date < `2025-03-01`)
- Joined rows (FT in note + full closing line): `2741`
- Read-only: PASS; writes 0
- Runtime: 150s

## Data notes

- Premier League / late 2025-26 often lack FT in `note` → holdout uses spring 2025 Big-4 with scores.
- Ligue 1 seasons 2023-24/2024-25 have nearly all `derby_weight=1` → PROD train disables derby H coef to avoid singular WLS.

## Modes

| Mode | Meaning |
|---|---|
| MARKET_LINE | closing Total / −AH as S/D |
| AD_POISSON | new experiment (FT Poisson A/D) |
| AD_POISSON_H0 | same ratings, H=0 at predict (EXP-039) |
| PROD | current full model |
| PROD_STATIC | prod without dynamics/SFA/SFTC |

## Pooled results (n-weighted)

| Mode | n | MAE D(FT) | MAE S(FT) | MAE AH(mkt) | MAE Tot(mkt) |
|---|---:|---:|---:|---:|---:|
| MARKET_LINE | 444 | 1.1548 | 1.2810 | 0.0000 | 0.0000 |
| AD_POISSON | 444 | 1.2429 | 1.2710 | 0.3598 | 0.3964 |
| AD_POISSON_H0 | 444 | 1.2051 | 1.2760 | 0.4493 | 0.3699 |
| PROD | 444 | 1.2093 | 1.2833 | 0.2370 | 0.2410 |
| PROD_STATIC | 444 | 1.2337 | 1.2802 | 0.2877 | 0.2821 |

## Per league

| League | Mode | n | MAE D(FT) | MAE S(FT) | MAE AH | MAE Tot |
|---|---|---:|---:|---:|---:|---:|
| Bundesliga | AD_POISSON | 98 | 1.4171 | 1.4000 | 0.3903 | 0.7704 |
| Bundesliga | AD_POISSON_H0 | 98 | 1.3100 | 1.4127 | 0.4923 | 0.5306 |
| Bundesliga | MARKET_LINE | 98 | 1.3342 | 1.5408 | 0.0000 | 0.0000 |
| Bundesliga | PROD | 98 | 1.4096 | 1.4155 | 0.2934 | 0.4209 |
| Bundesliga | PROD_STATIC | 98 | 1.4086 | 1.4175 | 0.2755 | 0.4847 |
| La Liga | AD_POISSON | 129 | 1.1336 | 1.2716 | 0.3314 | 0.3178 |
| La Liga | AD_POISSON_H0 | 129 | 1.1027 | 1.2982 | 0.4457 | 0.3721 |
| La Liga | MARKET_LINE | 129 | 1.0097 | 1.2248 | 0.0000 | 0.0000 |
| La Liga | PROD | 129 | 1.0670 | 1.2730 | 0.2190 | 0.1996 |
| La Liga | PROD_STATIC | 129 | 1.1118 | 1.2609 | 0.2597 | 0.2364 |
| Ligue 1 | AD_POISSON | 98 | 1.4636 | 1.3508 | 0.3980 | 0.2372 |
| Ligue 1 | AD_POISSON_H0 | 98 | 1.4766 | 1.3510 | 0.4923 | 0.3342 |
| Ligue 1 | MARKET_LINE | 98 | 1.4107 | 1.3214 | 0.0000 | 0.0000 |
| Ligue 1 | PROD | 98 | 1.4410 | 1.3683 | 0.2245 | 0.2066 |
| Ligue 1 | PROD_STATIC | 98 | 1.4757 | 1.3748 | 0.3520 | 0.2653 |
| Serie A | AD_POISSON | 119 | 1.0363 | 1.0984 | 0.3340 | 0.3046 |
| Serie A | AD_POISSON_H0 | 119 | 1.0061 | 1.0775 | 0.3824 | 0.2647 |
| Serie A | MARKET_LINE | 119 | 0.9538 | 1.0945 | 0.0000 | 0.0000 |
| Serie A | PROD | 119 | 1.0079 | 1.1157 | 0.2206 | 0.1660 |
| Serie A | PROD_STATIC | 119 | 1.0225 | 1.1100 | 0.2752 | 0.1786 |

## Verdicts

### EXP-038_AD_vs_PROD: **FAIL_FOR_PRICING**
Poisson AD worse than PROD on closing AH and Total

### EXP-039_HOME_on_AD: **FAIL**
H=0 better or equal on FT D — league H not needed / harmful; AH_mkt H_better=True

### EXP-040_AD_vs_MARKET_LINE: **FAIL**
closing lines beat Poisson AD on FT D (market already good goals proxy)

### OVERALL: **KEEP_PRODUCTION**
Production tracks closing AH/OU much better — required for fair odds.

## Conclusions

1. **Fair-odds product** needs forecasts close to **closing AH/OU**. Production is trained on market-implied S/D; Poisson AD is trained on FT goals — different targets.
2. If AD wins on FT but loses on closing lines, it is a **goals model**, not a drop-in replacement for Line pricing.
3. EXP-039 (home): compare AD vs AD_H0; keep league H only if it helps FT D and does not break AH.
4. EXP-040: if MARKET_LINE already beats AD on FT, closing lines are a strong goals proxy — AD has limited edge.
5. Recommended next step if AD shows FT value: **shadow / hybrid** (e.g. AD for S diagnostics) without replacing PROD pricing.
