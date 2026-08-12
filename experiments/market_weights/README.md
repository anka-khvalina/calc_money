# Market Information Model (offline, side-branch)

Production UI/API/configs are untouched.

## Status

| EXP | Status |
|---|---|
| 041–044 bucket weights | **CLOSED** — crude multipliers NO EFFECT / FAIL on baseline |
| 045 Stage 1 revaluation prediction | **NEXT / this package** |
| 046 learned weighting | only if 045 PASS |

## Run

```bash
# closed bucket study (041–044)
PYTHONPATH=app:. python3 -m experiments.market_weights run

# EXP-045 Stage 1 — predict future market revaluation (no weighting)
PYTHONPATH=app:. python3 -m experiments.market_weights 045
```

Artifacts: `/opt/cursor/artifacts/exp041_044/`, `/opt/cursor/artifacts/exp045/`.

## Narrow conclusion from 041–044

> Crude manual multipliers on top of the current saturated baseline do not add OOS closing signal.
> Direction remains: learn whether informative matches are **predictable**, then maybe a weighting policy.
