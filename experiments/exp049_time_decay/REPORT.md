# EXP-049 — Time-aware Dynamic State Aging

Offline. `f(t)=2^(-t/H)`, `t=mean(days_home, days_away)`.
OOS expanding GW buckets on LL/SA/L1 × 2024–25 & 2025–26.
1X2 uses same-match overround. Does **not** pick a production H.

## BOTH — by GW bucket

| Bucket | mean t | CURRENT MAE | H45 MAE | H60 MAE | RESET MAE | CURRENT worse than RESET |
|---|---:|---:|---:|---:|---:|---:|
| GW1-2 | 52 | 0.287 | 0.228 | 0.226 | 0.207 | 43% |
| GW3-4 | 9 | 0.180 | 0.167 | 0.167 | 0.182 | 24% |
| GW5-6 | 7 | 0.133 | 0.133 | 0.133 | 0.172 | 20% |
| GW7-8 | 9 | 0.172 | 0.155 | 0.153 | 0.166 | 26% |

## BOTH — by days since previous match

| Days | n | mean t | CURRENT | H30 | H45 | H60 | H90 | RESET | % CUR worse |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0-14 | 373 | 8 | 0.177 | 0.164 | 0.168 | 0.168 | 0.170 | 0.179 | 26% |
| 15-29 | 30 | 15 | 0.208 | 0.150 | 0.167 | 0.175 | 0.192 | 0.158 | 30% |
| 60-89 | 30 | 84 | 0.300 | 0.200 | 0.192 | 0.175 | 0.192 | 0.208 | 43% |
| 90+ | 27 | 111 | 0.287 | 0.204 | 0.194 | 0.194 | 0.250 | 0.222 | 41% |
| unknown | 1 | nan | nan | nan | nan | nan | nan | nan | 0% |

## BOTH — by matches since long break (≥60d)

| MSLB | n | CURRENT | H45 | H60 | RESET | % CUR worse |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 57 | 0.294 | 0.193 | 0.184 | 0.215 | 42% |
| 2 | 58 | 0.284 | 0.267 | 0.272 | 0.203 | 45% |
| 3-4 | 119 | 0.179 | 0.166 | 0.166 | 0.185 | 23% |
| 5+ | 226 | 0.153 | 0.144 | 0.143 | 0.167 | 23% |
| unknown | 1 | nan | nan | nan | nan | 0% |

## Aug-12 cards (train 2025–26) — BOTH

| Arm | MAE AH | N(≥0.5) | flip | MAE Tot |
|---|---:|---:|---:|---:|
| CURRENT | 0.243 | 6 | 17.6% | 0.154 |
| H30 | 0.110 | 1 | 2.9% | 0.110 |
| H45 | 0.125 | 1 | 2.9% | 0.110 |
| H60 | 0.103 | 0 | 2.9% | 0.118 |
| H90 | 0.132 | 0 | 5.9% | 0.118 |
| RESET | 0.103 | 2 | 5.9% | 0.110 |

## Interpretation checklist

- If days 0–14: CURRENT ≤ decay arms → short gaps should keep state.
- If days 80+: RESET / short H beat CURRENT → summer aging confirmed OOS.
- If MSLB 1–2: decay helps; MSLB 5+: CURRENT catches up → warm-up curve.
- Do **not** crown a single H yet; look for a stable shape across seasons.
