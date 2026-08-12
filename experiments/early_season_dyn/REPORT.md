# Early-season dynamics dampening (offline)

Production baseline **unchanged**. Offline comparison of Dynamic-D / S-EMA scales on season opens.

## Arms (policy)

| Arm | GW1–4 | After GW4 |
|---|---|---|
| **BASE** | Dynamic D + S-EMA full | current |
| **A** | both OFF | current |
| **B** | both ×0.25 | current |
| **C** | both ×0.50 | current |

Ratings, `d_base`/`s_base`, SFA, pricing untouched. Only the carried previous-season dynamic correction is scaled.

## Universe

Closing history allows prior-season train for:

- La Liga 2024-25, 2025-26
- Serie A 2024-25, 2025-26
- Ligue 1 2024-25, 2025-26

Premier League / Bundesliga skipped (only 2025-26 closing in DB → no prior season).

**GW** = max(home, away) team-match index within the season.  
Train = all earlier seasons in league + current-season matches with GW &lt; hold GW.  
Hold = that GW’s matches. Causal; model retrained each GW cut.

## How to run

```bash
PYTHONPATH=app:. python3 -m experiments.early_season_dyn
```

Artifacts: `/opt/cursor/artifacts/early_season_dyn/`.

## Results — policy (main)

### GW1–4 pooled (n=230 per arm)

| Arm | MAE AH | N(≥0.5) | N(≥0.75) | MAE Tot | N(Tot≥0.5) | MAE P1 (pp, monitor) |
|---|---:|---:|---:|---:|---:|---:|
| BASE | 0.209 | 46 | 11 | 0.172 | 23 | 4.86 |
| A (off) | 0.202 | 35 | 3 | 0.160 | 16 | 4.63 |
| **B (×0.25)** | **0.175** | **30** | **2** | 0.150 | 15 | 4.26 |
| **C (×0.50)** | **0.174** | 33 | 4 | **0.148** | **12** | **4.15** |

Lift vs BASE: **C** −0.035 AH MAE, −13 big AH misses; **B** similar AH, slightly fewer ≥0.5.

### By gameweek (policy)

| Bucket | BASE AH | A | B | C | Winner (AH) |
|---|---:|---:|---:|---:|---|
| GW1 (n=58) | 0.289 | 0.211 | **0.194** | 0.216 | B |
| GW2 | 0.216 | 0.228 | 0.203 | **0.194** | C |
| GW3 | 0.156 | 0.192 | 0.161 | **0.147** | C |
| GW4 | 0.172 | 0.177 | 0.142 | **0.138** | C |
| GW5–8 | 0.149 | 0.149 | 0.149 | 0.149 | (all = BASE by policy) |

GW1: full dynamic is clearly worst (20× ≥0.5 AH misses vs 8 for B).  
From GW2, pure OFF (A) is no longer best — some dynamic helps, but **half-scale (C)** wins through GW4.

### By league (GW1–4)

Same ranking in all three leagues: C/B beat BASE; A only a small AH win vs BASE.

## Diagnostic — fixed scale every GW (when does full dynamic help?)

Best scale by **MAE AH** / **MAE Tot**:

| GW | Best AH scale | Best Tot scale | FULL still OK? |
|---|---|---|---|
| 1 | 0.25 | 0.25 | no — FULL worst |
| 2 | 0.50 | 0.50 | no |
| 3 | 0.50 | 0.50 | close |
| 4 | 0.50 | 0.25 | no |
| 5 | 0.50 | **1.00** | Tot yes; AH still 0.50 |
| 6 | **1.00** | **1.00** | **yes (tied AH with 0.50)** |
| 7 | 0.50 | **1.00** | Tot yes |
| 8 | 0.50 | **1.00** | Tot yes |

**Crossover signal:** around **GW6** full dynamic becomes competitive on AH and preferred on Total. Through GW5, ×0.50 still better on AH. Total prefers full dynamic from GW5 onward.

## Margin rule (1X2)

Market 1/X/2 quotes include ~5% overround. Comparisons never mix fair model odds with margined market quotes.

| What | How |
|---|---|
| **AH / Total (primary)** | closing **lines** vs model lines — margin N/A |
| **1X2 probs (monitor)** | Shin-devig market → fair probs vs model fair probs |
| **1X2 odds (monitor)** | model fair probs × **same match overround**, then MAE vs market quotes; also `mae_odds_fav` on the shorter side |

### GW1–4 1X2 with margin accounted for (n=230)

| Arm | MAE P1/PX/P2 (pp, Shin) | MAE odds 1/X/2 (margined) | MAE odds fav |
|---|---:|---:|---:|
| BASE | 4.86 / 1.99 / 4.22 | 0.435 / 0.358 / 1.066 | 0.229 |
| A | 4.63 / 1.79 / 3.88 | 0.410 / 0.323 / 0.750 | 0.212 |
| B | 4.26 / 1.73 / 3.61 | 0.371 / 0.311 / 0.696 | 0.194 |
| **C** | **4.15 / 1.74 / 3.60** | **0.367 / 0.310 / 0.728** | **0.191** |

Same ranking as AH: C/B beat BASE once margin is matched.


1. Production mid-season path is fine; **season-open carry of Dynamic D / S-EMA hurts GW1 especially**.
2. Among policy arms on GW1–4, **C (×0.50)** or **B (×0.25)** beat BASE on AH and Tot; OFF (A) fixes GW1 but loses from GW2–4.
3. Practical offline recommendation to test later in product: **dampen dynamic to ~0.25–0.50 for GW1–4, restore full after ~GW5–6** (Tot wants full earlier than AH).
4. Do **not** ship without a product flag / config — this report does not change production.

## Files

- `predict_scaled.py` — scale correction then same calib/SFA/pricing path
- `gw.py` — GW labels + eval seasons
- `run.py` — expanding GW evaluation
- `metrics.py` — AH/Tot + 1X2 Shin monitor
