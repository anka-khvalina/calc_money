# EXP-052 — Strong favourite tail decomposition

Diagnostic only: no parameter is tuned, aging / S / SFA are frozen.

## Method

- OOS expanding by month, leagues trained separately, eval seasons 2024-25, 2025-26.
- Dynamic State Aging frozen ON, `H_D = H_S = 60`.
- `D_market` inferred from AH prices exactly like training (not `-AH`).
- All D errors signed so that **positive = model overstates the market favourite**.
- Probabilities in **pp** vs Shin-devigged market; decimal odds are not used.
- Arms: A = model D, B = market D (same matrix), B2 = market D and market S,
  auto C / D = Auto matrix (NB+copula, alpha=0.05, rho=0.05) at model / market D.

n = 2409 matches

## Main table

| Segment | n | ΔD_base | ΔD_preSFA | SFA effect | ΔD_final | Auto ΔPdog @modelD | Auto ΔPdog @marketD | Legacy ΔPdog @modelD | Legacy ΔPdog @marketD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| All | 2409 | -0.075 | +0.028 | +0.023 | +0.051 | +1.41 | +2.21 | +0.29 | +1.12 |
| Fav <=1.50 | 449 | -0.238 | -0.010 | +0.032 | +0.022 | +2.01 | +2.17 | +0.48 | +0.62 |
| Fav 1.50-1.75 | 422 | -0.144 | +0.021 | +0.032 | +0.053 | +1.54 | +2.25 | +0.09 | +0.80 |
| Fav 1.75-2.00 | 422 | -0.045 | +0.037 | +0.032 | +0.069 | +1.51 | +2.62 | +0.28 | +1.43 |
| Fav >2.00 | 1116 | +0.005 | +0.042 | +0.013 | +0.055 | +1.08 | +2.07 | +0.29 | +1.32 |
| Home fav 1.50-2.00 | 541 | -0.050 | +0.062 | +0.029 | +0.091 | +1.05 | +2.49 | -0.30 | +1.17 |
| Away fav 1.50-2.00 | 303 | -0.174 | -0.030 | +0.038 | +0.008 | +2.36 | +2.33 | +1.06 | +1.01 |
| Away fav 1.50-2.00 Bundesliga | 15 | -0.176 | -0.020 | +0.046 | +0.026 | +1.60 | +1.77 | +0.24 | +0.40 |
| Away fav 1.50-2.00 La Liga | 67 | -0.111 | +0.036 | +0.031 | +0.067 | +1.19 | +2.12 | -0.08 | +0.87 |
| Away fav 1.50-2.00 Ligue 1 | 88 | -0.235 | -0.080 | +0.044 | -0.037 | +2.92 | +2.14 | +1.81 | +0.96 |
| Away fav 1.50-2.00 Premier League | 25 | -0.052 | +0.029 | +0.028 | +0.058 | +1.27 | +2.11 | -0.01 | +0.84 |
| Away fav 1.50-2.00 Serie A | 108 | -0.192 | -0.046 | +0.039 | -0.007 | +2.99 | +2.75 | +1.52 | +1.26 |

## Draw and oracle-on-S control

| Segment | n | ΔP_x (A) | Legacy ΔPdog @marketD+marketS | share ΔPdog<0 (A) | share ΔPdog<0 (B) |
|---|---:|---:|---:|---:|---:|
| All | 2409 | -0.34 | +1.20 | 0.46 | 0.14 |
| Fav <=1.50 | 449 | +1.54 | +1.04 | 0.43 | 0.30 |
| Fav 1.50-1.75 | 422 | +0.42 | +1.14 | 0.46 | 0.20 |
| Fav 1.75-2.00 | 422 | -0.44 | +1.47 | 0.45 | 0.09 |
| Fav >2.00 | 1116 | -1.35 | +1.18 | 0.48 | 0.08 |
| Home fav 1.50-2.00 | 541 | -0.26 | +1.34 | 0.51 | 0.14 |
| Away fav 1.50-2.00 | 303 | +0.43 | +1.25 | 0.36 | 0.16 |
| Away fav 1.50-2.00 Bundesliga | 15 | +0.96 | +1.11 | 0.33 | 0.27 |
| Away fav 1.50-2.00 La Liga | 67 | +0.14 | +1.21 | 0.54 | 0.15 |
| Away fav 1.50-2.00 Ligue 1 | 88 | +0.52 | +1.31 | 0.30 | 0.16 |
| Away fav 1.50-2.00 Premier League | 25 | +0.51 | +0.94 | 0.56 | 0.24 |
| Away fav 1.50-2.00 Serie A | 108 | +0.44 | +1.32 | 0.26 | 0.12 |

## Stability by season

| Season | Segment | n | ΔPdog A | ΔPdog B | share<0 | ΔD_final |
|---|---|---:|---:|---:|---:|---:|
| 2024-25 | All | 1065 | +0.46 | +1.16 | 0.43 | +0.041 |
| 2024-25 | Away fav 1.50-2.00 | 133 | +1.73 | +1.08 | 0.32 | -0.024 |
| 2024-25 | Home fav 1.50-2.00 | 244 | -0.40 | +1.23 | 0.48 | +0.099 |
| 2025-26 | All | 1344 | +0.15 | +1.08 | 0.48 | +0.058 |
| 2025-26 | Away fav 1.50-2.00 | 170 | +0.54 | +0.96 | 0.39 | +0.033 |
| 2025-26 | Home fav 1.50-2.00 | 297 | -0.22 | +1.12 | 0.54 | +0.084 |
