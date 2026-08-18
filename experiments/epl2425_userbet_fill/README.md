# EPL History fill from userbet archive

Works for **2024-25** (season_id=3) and **2023-24** (season_id=15).

## ID trap

| Where | Field | Example | «Получить данные» |
|-------|-------|---------|-------------------|
| URL `/lineups_fixture/.../21106972/` | `fid` | 21106972 | **No** (`{}`) |
| Archive card / site JS | `ps_id` | 1593036427 | **Yes** |

`cpid` only highlights a chip; HTML lists all leagues — we parse `England : Premier League`.

Older seasons: current odds API often returns `1X2=0` or missing OU. Script then takes **closing ticks** from `load_lineups_odds_histoty`.

## Run

```bash
# 2024-25 (default)
python3 experiments/epl2425_userbet_fill/fill_epl2425.py --write --skip-filled

# 2023-24
python3 experiments/epl2425_userbet_fill/fill_epl2425.py --season-id 15 --write --skip-filled
```

Artifacts: `/opt/cursor/artifacts/epl{YYYYMM}_userbet_fill/` and copies in this folder.
