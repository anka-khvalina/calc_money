#!/usr/bin/env python3
"""Import Bundesliga calendar from football-data.co.uk CSV into Supabase matches.

Creates / fills season rows (date + teams only). Odds come from
experiments/bundesliga2324_userbet_fill/fill_bundesliga.py.
"""

from __future__ import annotations

import argparse
import csv
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from supabase_teams import SupabaseError, _request, fetch_teams  # noqa: E402

BUNDESLIGA_LEAGUE_ID = "0e928134-ae08-48fd-8d2c-3539a994b054"
DEFAULT_SEASON_LABEL = "2023-24"
DEFAULT_SEASON_ID = 14

# football-data HomeTeam → Supabase team.name_team (identity when omitted)
FD_NAME_ALIASES: dict[str, str] = {
    "M'gladbach": "M'gladbach",
    "Bayern Munich": "Bayern Munich",
    "Ein Frankfurt": "Ein Frankfurt",
    "FC Koln": "FC Koln",
    "RB Leipzig": "RB Leipzig",
    "Werder Bremen": "Werder Bremen",
    "Union Berlin": "Union Berlin",
}


def parse_fd_date(raw: str) -> str:
    """DD/MM/YYYY → YYYY-MM-DD."""
    return datetime.strptime(raw.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")


def read_fixtures(csv_path: Path) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            home = FD_NAME_ALIASES.get(row["HomeTeam"], row["HomeTeam"]).strip()
            away = FD_NAME_ALIASES.get(row["AwayTeam"], row["AwayTeam"]).strip()
            out.append((parse_fd_date(row["Date"]), home, away))
    if not out:
        raise ValueError(f"Нет матчей в {csv_path}")
    return out


def team_name_map(league_id: str) -> dict[str, int]:
    return {t.name_team: t.id for t in fetch_teams(league_id)}


def fetch_season_id(league_id: str, label: str) -> int | None:
    q = urllib.parse.urlencode(
        {"select": "id,label", "leagues_id": f"eq.{league_id}", "label": f"eq.{label}"}
    )
    rows = _request("GET", f"/season?{q}")
    if not rows:
        return None
    return int(rows[0]["id"])


def _match_payload(season_id: int, match_date: str, home_id: int, away_id: int) -> dict:
    return {
        "season_id": season_id,
        "match_date": match_date,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "is_neutral": False,
        "match_weight": 1.0,
        "derby_weight": 0.0,
        "neutral_weight": 1.0,
        "active": True,
        "home_rotation_code": "none",
        "away_rotation_code": "none",
        "motivation": True,
    }


def insert_matches(
    season_id: int,
    fixtures: list[tuple[str, str, str]],
    team_map: dict[str, int],
    *,
    batch_size: int = 50,
) -> int:
    missing: set[str] = set()
    payloads: list[dict] = []
    for match_date, home, away in fixtures:
        if home not in team_map:
            missing.add(home)
        if away not in team_map:
            missing.add(away)
        if home in team_map and away in team_map:
            payloads.append(_match_payload(season_id, match_date, team_map[home], team_map[away]))
    if missing:
        raise ValueError("Команды отсутствуют в Supabase: " + ", ".join(sorted(missing)))
    inserted = 0
    for i in range(0, len(payloads), batch_size):
        batch = payloads[i : i + batch_size]
        _request("POST", "/matches?select=id", body=batch)
        inserted += len(batch)
    return inserted


def count_season_matches(season_id: int) -> int:
    rows = _request("GET", f"/matches?select=id&season_id=eq.{season_id}&limit=1000")
    return len(rows) if isinstance(rows, list) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "csv",
        type=Path,
        nargs="?",
        default=ROOT / "experiments/bundesliga2324_userbet_fill/D1_2324.csv",
        help="football-data D1.csv",
    )
    ap.add_argument("--season-label", default=DEFAULT_SEASON_LABEL)
    ap.add_argument("--season-id", type=int, default=DEFAULT_SEASON_ID)
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.csv.is_file():
        raise SystemExit(f"Файл не найден: {args.csv}")

    fixtures = read_fixtures(args.csv)
    team_map = team_name_map(BUNDESLIGA_LEAGUE_ID)
    print(f"CSV: {args.csv.name} → {len(fixtures)} матчей")
    print(f"Первый: {fixtures[0][0]} {fixtures[0][1]}–{fixtures[0][2]}")
    print(f"Последний: {fixtures[-1][0]} {fixtures[-1][1]}–{fixtures[-1][2]}")

    season_id = fetch_season_id(BUNDESLIGA_LEAGUE_ID, args.season_label) or args.season_id
    existing = count_season_matches(season_id)
    print(f"Сезон {args.season_label}: season_id={season_id}, матчей сейчас={existing}")

    if args.dry_run:
        print("Dry-run: запись не выполнялась")
        return 0

    if existing:
        if not args.replace:
            raise SystemExit(
                f"Сезон уже содержит {existing} матчей. Используйте --replace или пропустите."
            )
        _request("DELETE", f"/matches?season_id=eq.{season_id}")
        print(f"Удалено матчей: {existing}")

    n = insert_matches(season_id, fixtures, team_map)
    print(f"Импортировано: {n} (проверка: {count_season_matches(season_id)})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SupabaseError as exc:
        print(f"Ошибка Supabase: {exc}", file=sys.stderr)
        if getattr(exc, "body", None):
            print(exc.body, file=sys.stderr)
        raise SystemExit(1)
