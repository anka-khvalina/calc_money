# Market Information Model (offline, side-branch)

Production UI/API/configs are untouched.

## Status

| EXP | Status |
|---|---|
| 041–044 bucket weights | **CLOSED** — crude multipliers NO EFFECT / FAIL on baseline |
| 045 Stage 1 revaluation prediction | **PARTIAL** (`r≈0.10`, no mono) |
| 045A/B/C diagnostics | **DONE** — best `L3` + `next_5` → `r≈0.147`, still below stop-rule |
| MIM / weighting mine | **CLOSE_MIM** |
| 046 learned weighting | **BLOCKED** |

## Run

```bash
# closed bucket study (041–044)
PYTHONPATH=app:. python3 -m experiments.market_weights run

# EXP-045 Stage 1 — predict future market revaluation (no weighting)
PYTHONPATH=app:. python3 -m experiments.market_weights 045

# EXP-045A/B/C last diagnostic round (label / horizon / features)
PYTHONPATH=app:. python3 -m experiments.market_weights 045diag
```

Artifacts: `/opt/cursor/artifacts/exp041_044/`, `/opt/cursor/artifacts/exp045/`, `/opt/cursor/artifacts/exp045_diag/`.

## Final narrow conclusion

> Crude multipliers fail on a saturated baseline.
> After one quality round on label/horizon/features, we still cannot reliably OOS-predict
> which observations the market will treat as informative (`|r|≲0.15` and stop-rule fail).
> Close the weighting mine; next research needs new observables (and opening lines if available).
