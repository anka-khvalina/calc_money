# EXP-053 — Season Boundary Residual

Diagnostic only. Aging H60 frozen ON. No product edits.

## Question

After aging shrinks stale short-term state, what remains at the season boundary — especially in **D_base** (rating + HA) vs market?

## Method

- OOS expanding by month; eval seasons 2024-25, 2025-26.
- Dynamic State Aging ON, `H_D = H_S = 60`.
- Long break ≥ 60 days between consecutive team matches.
- Match regime = min(mslb_home, mslb_away).
- `D_market` / `S_market` inferred from AH/OU prices (same as training).
- ΔD signed so **positive = model overstates the market favourite**.
- ΔP in percentage points vs Shin-devigged 1X2.
- Control: teams with both Long-break and Stable observations in the same season.

n = 2409 matches

## Regime table

| Regime | n | ΔD_base | ΔD_after_aging | ΔD_final | |ΔD_base| | ΔS | ΔP_fav | ΔP_X | ΔP_dog | AH MAE | Tot MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| All | 2409 | -0.075 | -0.020 | +0.051 | 0.235 | -0.020 | +0.05 | -0.34 | +0.29 | 0.163 | 0.153 |
| Long break / first (0 new) | 57 | -0.027 | -0.025 | +0.047 | 0.223 | +0.091 | +0.08 | -0.69 | +0.60 | 0.189 | 0.197 |
| Warm-up 2 (1 new) | 58 | -0.035 | -0.018 | +0.054 | 0.216 | +0.094 | +0.11 | -0.60 | +0.49 | 0.216 | 0.159 |
| Warm-up 3 (2 new) | 60 | -0.012 | +0.029 | +0.089 | 0.211 | +0.039 | +1.27 | -0.72 | -0.55 | 0.221 | 0.154 |
| Warm-up 4–5 (3–4 new) | 117 | -0.085 | -0.035 | +0.028 | 0.188 | +0.026 | -0.44 | -0.61 | +1.04 | 0.162 | 0.152 |
| Stable (≥5 new) | 2116 | -0.079 | -0.021 | +0.051 | 0.239 | -0.030 | +0.04 | -0.30 | +0.26 | 0.160 | 0.152 |
| Unknown (no prior) | 1 | +0.093 | +0.093 | +0.115 | 0.093 | +0.417 | +3.31 | -4.36 | +1.05 | 0.000 | 0.250 |

## Control — same teams Long-break → Stable

Teams with ≥1 Long-break and ≥1 Stable obs in the same league-season: **101**.

| Slice | n | ΔD_base | ΔD_after_aging | ΔD_final | |ΔD_base| | ΔP_fav | ΔP_dog | AH MAE | Tot MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Control teams @ Long break | 101 | -0.041 | -0.043 | +0.029 | 0.215 | -0.24 | +0.86 | 0.181 | 0.193 |
| Same teams @ Stable (≥5 new) | 3163 | -0.083 | -0.019 | +0.051 | 0.242 | +0.06 | +0.33 | 0.158 | 0.151 |
| Control @ Long break Jul–Sep | 101 | -0.041 | -0.043 | +0.029 | 0.215 | -0.24 | +0.86 | 0.181 | 0.193 |
| Same teams @ Stable Oct+ | 1056 | -0.065 | -0.012 | +0.058 | 0.217 | +0.29 | +0.38 | 0.152 | 0.145 |

If Long-break ΔD_base / AH error is large and Stable on the **same teams** is near overall mid-season levels without any model change, that supports missing-information / regime-shift rather than a permanent tail bug.

## By season

| Slice | n | ΔD_base | ΔD_final | |ΔD_base| | ΔP_dog | AH MAE |
|---|---:|---:|---:|---:|---:|---:|
| 2024-25 / All | 1065 | -0.098 | +0.041 | 0.240 | +0.46 | 0.164 |
| 2024-25 / Long break / first (0 new) | 28 | +0.027 | +0.094 | 0.186 | -0.37 | 0.170 |
| 2024-25 / Stable (≥5 new) | 918 | -0.109 | +0.040 | 0.250 | +0.49 | 0.165 |
| 2025-26 / All | 1344 | -0.057 | +0.058 | 0.232 | +0.15 | 0.163 |
| 2025-26 / Long break / first (0 new) | 29 | -0.079 | +0.001 | 0.259 | +1.54 | 0.207 |
| 2025-26 / Stable (≥5 new) | 1198 | -0.056 | +0.059 | 0.231 | +0.08 | 0.156 |

## Long-break vs Stable by league

| Slice | n | ΔD_base | ΔD_final | |ΔD_base| | ΔP_dog | AH MAE | Tot MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bundesliga / Stable (≥5 new) | 119 | -0.083 | +0.043 | 0.232 | -0.06 | 0.168 | 0.166 |
| La Liga / Long break / first (0 new) | 19 | -0.011 | +0.049 | 0.228 | +0.56 | 0.171 | 0.250 |
| La Liga / Stable (≥5 new) | 647 | -0.047 | +0.060 | 0.226 | +0.01 | 0.153 | 0.148 |
| Ligue 1 / Long break / first (0 new) | 18 | -0.115 | -0.044 | 0.259 | +1.77 | 0.194 | 0.153 |
| Ligue 1 / Stable (≥5 new) | 520 | -0.134 | +0.028 | 0.279 | +0.64 | 0.179 | 0.164 |
| Premier League / Stable (≥5 new) | 181 | -0.056 | +0.053 | 0.227 | +0.08 | 0.162 | 0.156 |
| Serie A / Long break / first (0 new) | 20 | +0.038 | +0.126 | 0.186 | -0.41 | 0.200 | 0.188 |
| Serie A / Stable (≥5 new) | 649 | -0.072 | +0.061 | 0.226 | +0.31 | 0.149 | 0.143 |

## Verdict (read from the tables)

- On Long-break, `ΔD_after_aging` (-0.025) stays close to `ΔD_base` (-0.027): aging correctly kills stale dynamic state, so the remaining gap is not short-term EMA carry.
- Long-break AH MAE 0.189 / Tot MAE 0.197 vs Stable 0.160 / 0.152 (elevated early, then settles).
- Signed `ΔD_base` on Long-break is -0.027 (abs 0.223); Stable -0.079 (abs 0.239). A permanent fav-tail bug would not preferentially show as warm-up AH/Tot error.
- **Same-team control** (101 teams): Long-break AH MAE 0.181 → Stable 0.158; Tot 0.193 → 0.151; |ΔD_base| 0.215 → 0.242. Line error shrinks without model changes → supports missing-info / regime-shift at the boundary more than a fixed matrix bug.
- Next research target: how to move **base rating** (and HA) across seasons when dynamic state is already aged out — promoted teams, roster/coach shocks, and August information the market has and we do not.

## Frozen decisions (not this EXP)

- Aging H60 remains a production candidate (separate from this diagnostic).
- EXP-052 closed: no fav-tail / away / league correction.
- Legacy / Auto α–ρ calibration not touched here.

## Research question for follow-ups

How to carry a team from season N → N+1 when old dynamic state is stale and new matches are not yet enough — without confusing short-term aging with base-rating / roster-regime information the market already has?
