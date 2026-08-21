# Bundesliga History fill from userbet archive

Works for **2023-24** (`season_id=14`) and **2024-25** (`season_id=1`).

> Note: on userbet.info **`cpid=2` = Bundesliga**, `cpid=3` = Ligue 1.

## Prerequisites

1. Match rows already in Supabase (date + teams). For 2023-24:
   ```bash
   python3 scripts/import_bundesliga_fd_csv.py experiments/bundesliga2324_userbet_fill/D1_2324.csv
   ```
2. Then fill closing odds from archive.

## ID trap

| Where | Field | Works with odds API? |
|-------|-------|----------------------|
| URL `/lineups_fixture/.../fid/` | `fid` | **No** |
| Archive card | `ps_id` | **Yes** |

## Run

```bash
# 2023-24 (default)
python3 experiments/bundesliga2324_userbet_fill/fill_bundesliga.py --write --skip-filled

# 2024-25
python3 experiments/bundesliga2324_userbet_fill/fill_bundesliga.py --season-id 1 --write --skip-filled

# dry-run first 5
python3 experiments/bundesliga2324_userbet_fill/fill_bundesliga.py --limit 5
```

Artifacts: `/opt/cursor/artifacts/bl{YYYYMM}_userbet_fill/` and copies in this folder.
