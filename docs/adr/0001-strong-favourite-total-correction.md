# ADR-0001: Local expected-total correction for Strong Favourites

## Status

Accepted

## Context

Matches in the Strong Favourite segment systematically understate expected total goals (`S`) relative to the AH/OU market structure, while goal difference (`D`) remains adequately aligned. Adjusting `dBigFav` or SFA did not improve OU fidelity on this segment without harming other markets. Closing 1X2 remains a separate market basis and must not drive core `S`/`D` tuning.

## Decision

Introduce a **feature-flagged local lift** applied only to `S` after model `S` (and S-calibration) and after SFA has used the uncorrected `S` for its D-path:

```text
if feature enabled AND match ∈ Strong Favourite segment:
    S_final = S_model + totalCorrection
else:
    S_final = S_model
```

Defaults:

| Parameter | Default |
|-----------|--------:|
| `strongFavouriteTotalCorrection.enabled` | `true` |
| `strongFavouriteThreshold` | `1.30` |
| `totalCorrection` | `+0.10` |

Segment membership is defined by market favorite odds ≤ `strongFavouriteThreshold` (configurable Strong Favourite boundary). Setting `enabled: false` restores the previous behaviour with no code rollback.

## Consequences

- Improves OU / `λ_fav` on the Strong Favourite segment when the flag is on.
- Does not change ratings, `D`, `dBigFav`, SFA algorithms, Poisson, or Dynamic Dixon–Coles implementations.
- Is **not** a Closing 1X2 calibration layer; residual AH/OU↔1X2 basis remains out of scope for this change.
- Threshold changes do not require rewriting the business rule — only config.
