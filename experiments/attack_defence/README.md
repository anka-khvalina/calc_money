# Attack/Defence Poisson experiment (isolated)

Read-only console experiment. Does **not** change production model, UI, or API.

## Run

```bash
PYTHONPATH=app:. python -m experiments.attack_defence --dry-run \
  --date-from 2025-03-01 --date-to 2025-05-25
```

Default window is spring 2025 because FT scores in `note` currently cover ~2023-08 … 2025-05 (later seasons often lack FT text).

Optional: `--league "Serie A"` (repeatable), `--regularization 0.5`, `--min-team-matches 5`.

## Data

- Source: Supabase `v_matches_full` via GET only.
- Actual FT scores: parsed from existing `note` (`FT h:a …`). No new tables / no writes.
- Matches without FT in `note` are excluded with a logged reason.
- Premier League rows in DB currently lack FT notes → skipped until notes exist.

## Compare vs production (EXP-038/039/040)

```bash
mkdir -p /opt/cursor/artifacts/ad_compare
PYTHONPATH=app:. python3 -m experiments.attack_defence.compare_prod
```

Writes `COMPARE_REPORT.md` here and artifacts under `/opt/cursor/artifacts/ad_compare/`.

## Safety

- Fail-closed GET-only HTTP client.
- Mutating methods and SQL keywords rejected.
- Secrets never printed.
- Production entrypoints / UI / API untouched.

