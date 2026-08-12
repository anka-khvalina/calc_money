# EXP-047A — Early-season ablation (Dynamic D × S-EMA)

Offline only. Ratings / A-D / calib / SFA / SFTC / weights unchanged across arms.
1X2 uses **same match overround** as the book.

## Arms

| Arm | Dynamic D | S-EMA |
|---|---|---|
| A FULL | ON | ON |
| B | OFF | ON |
| C | ON | OFF |
| D BASE | OFF | OFF |

harm_D = |AH_A − AH_mkt| − |AH_B − AH_mkt|  (isolates Dynamic D)

harm_S = |Tot_A − Tot_mkt| − |Tot_C − Tot_mkt|  (isolates S-EMA)

## Pooled by GW bucket

| Bucket | Arm | n | MAE AH | N(≥0.5) | MAE Tot | flip AH% | MAE odds1 |
|---|---|---:|---:|---:|---:|---:|---:|
| GW1-2 | A_FULL | 116 | 0.287 | 38 | 0.250 | 17.2% | 0.664 |
| GW1-2 | B | 116 | 0.200 | 20 | 0.250 | 13.8% | 0.412 |
| GW1-2 | C | 116 | 0.287 | 37 | 0.183 | 17.2% | 0.682 |
| GW1-2 | D_BASE | 116 | 0.207 | 22 | 0.183 | 13.8% | 0.402 |
| GW3-4 | A_FULL | 114 | 0.180 | 18 | 0.143 | 14.0% | 0.280 |
| GW3-4 | B | 114 | 0.184 | 11 | 0.143 | 19.3% | 0.359 |
| GW3-4 | C | 114 | 0.182 | 18 | 0.158 | 14.0% | 0.317 |
| GW3-4 | D_BASE | 114 | 0.182 | 10 | 0.158 | 19.3% | 0.349 |
| GW5-6 | A_FULL | 115 | 0.133 | 11 | 0.148 | 9.6% | 0.319 |
| GW5-6 | B | 115 | 0.163 | 10 | 0.148 | 12.2% | 0.348 |
| GW5-6 | C | 115 | 0.133 | 11 | 0.163 | 9.6% | 0.336 |
| GW5-6 | D_BASE | 115 | 0.172 | 12 | 0.163 | 13.0% | 0.346 |
| GW7-8 | A_FULL | 116 | 0.172 | 14 | 0.121 | 12.9% | 0.315 |
| GW7-8 | B | 116 | 0.166 | 10 | 0.121 | 11.2% | 0.326 |
| GW7-8 | C | 116 | 0.172 | 14 | 0.155 | 12.9% | 0.326 |
| GW7-8 | D_BASE | 116 | 0.166 | 10 | 0.155 | 11.2% | 0.328 |

## Dynamic harm by bucket

| Bucket | n | harm_D mean | harm_D %pos | harm_S mean | harm_S %pos | flip FULL | flip BASE |
|---|---:|---:|---:|---:|---:|---:|---:|
| GW1-2 | 116 | 0.0862 | 43.1% | 0.0668 | 44.0% | 17.2% | 13.8% |
| GW3-4 | 114 | -0.0044 | 23.7% | -0.0154 | 13.2% | 14.0% | 19.3% |
| GW5-6 | 114 | -0.0307 | 21.1% | -0.0154 | 12.3% | 9.6% | 13.2% |
| GW7-8 | 116 | 0.0065 | 25.9% | -0.0345 | 7.8% | 12.9% | 11.2% |

## D-trace — Aug-12 named openers (train 2025–26)

| Match | D_base | slow_D | fast_D | D_corr | D_dyn | D_cal/SFA | D_mkt | AH BASE/FULL/mkt | harm_D | flip |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| Nott'm Forest–Leeds | +0.341 | +0.190 | -0.386 | -0.537 | -0.196 | -0.276 | +0.250 | -0.25/+0.25/-0.25 | +0.50 | True |
| Everton–Crystal Palace | +0.214 | +0.411 | +0.286 | +0.483 | +0.697 | +0.755 | +0.250 | -0.25/-0.75/-0.25 | +0.50 | False |
| FC Koln–Hoffenheim | -0.228 | -0.369 | -0.242 | -0.383 | -0.612 | -0.666 | -0.250 | +0.25/+0.50/+0.25 | +0.25 | False |
| Union Berlin–Ein Frankfurt | +0.033 | +0.007 | -0.393 | -0.419 | -0.386 | -0.423 | -0.000 | +0.00/+0.25/+0.00 | +0.25 | True |
| Udinese–Como | -0.648 | -0.819 | -0.243 | -0.414 | -1.062 | -1.153 | -0.500 | +0.50/+1.00/+0.50 | +0.50 | False |
| Genoa–Napoli | -0.644 | -0.749 | -0.367 | -0.471 | -1.116 | -1.212 | -0.500 | +0.50/+1.00/+0.50 | +0.50 | False |

### Margined 1X2 on traces

| Match | Market | FULL+margin | BASE+margin | flip 1X2 |
|---|---|---|---|---|
| Nott'm Forest–Leeds | 2.26/3.36/3.23 | 3.209/3.427/2.241 | 2.144/3.330/3.534 | True |
| Everton–Crystal Palace | 2.15/3.41/3.42 | 1.754/3.675/4.793 | 2.189/3.304/3.432 | False |
| FC Koln–Hoffenheim | 2.98/3.67/2.27 | 3.853/4.109/1.833 | 3.003/3.767/2.222 | False |
| Union Berlin–Ein Frankfurt | 2.47/3.51/2.78 | 3.344/3.815/2.048 | 2.460/3.465/2.822 | True |
| Udinese–Como | 4.50/3.52/1.83 | 7.495/4.000/1.491 | 4.869/3.439/1.793 | False |
| Genoa–Napoli | 4.98/3.24/1.84 | 9.724/3.899/1.442 | 5.469/3.170/1.803 | False |
