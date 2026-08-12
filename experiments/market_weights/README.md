# Market information weights (EXP-041 … 044)

Offline experiments on a **side branch**. Production UI/API/configs are untouched.

## Baseline (kept ON)

- DB `match_weight` as-is (incl. Soft 0.6/0.8/1.0 where set)
- Dynamic D, S-EMA, SFA, SFTC from `web/model_config.json`
- Expanding monthly refit
- Each new weight tested **alone** as multiplier on top of baseline

## Run

```bash
mkdir -p /opt/cursor/artifacts/exp041_044
PYTHONPATH=app:. python3 -m experiments.market_weights
```

Artifacts: `/opt/cursor/artifacts/exp041_044/` and `experiments/market_weights/REPORT.md`.

## Data limits

- No opening lines in DB → `open_close_*` features skipped
- PL/BL 2023-24 & 2024-25 fixtures without odds → excluded from train/eval
- Verdict metrics: closing AH/Total MAE (not FT)
