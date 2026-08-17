# EPL 2024-25 — fill History odds from userbet archive

## ID trap

| Where | Field | Example | Works with «Получить данные»? |
|-------|-------|---------|-------------------------------|
| URL `/lineups_fixture/.../21106972/` | `fid` | 21106972 | **No** (`{}`) |
| Archive card / site JS | `ps_id` | 1593036427 | **Yes** (posted as `id_fixture`) |

`cpid` on archive only highlights a chip; HTML lists all leagues — we parse the
`England : Premier League` tournament block (`cpid=5` for Premier League highlight).

## Run

```bash
# dry-run map + fetch (no DB write)
python3 experiments/epl2425_userbet_fill/fill_epl2425.py --from-date 2024-08-16 --to-date 2024-08-19

# write to Supabase
python3 experiments/epl2425_userbet_fill/fill_epl2425.py --write

# resume (skip already-filled)
python3 experiments/epl2425_userbet_fill/fill_epl2425.py --write --skip-filled
```

Artifacts: `/opt/cursor/artifacts/epl2425_userbet_fill/`
