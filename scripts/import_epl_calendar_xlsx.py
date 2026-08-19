#!/usr/bin/env python3
"""Import EPL fixture calendar from top-5 leagues Excel into Supabase."""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import openpyxl  # noqa: E402
from supabase_teams import SupabaseError, _request, fetch_teams  # noqa: E402

EPL_LEAGUE_ID = "2613ee27-7e8d-4d18-bd5e-e3f525121848"
SHEET_EPL = "АПЛ"
DEFAULT_SEASON = "2026-27"


def read_epl_fixtures(xlsx_path: Path) -> list[tuple[str, str, str]]:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    if SHEET_EPL not in wb.sheetnames:
        raise ValueError(f"Лист {SHEET_EPL!r} не найден. Доступны: {wb.sheetnames}")
    ws = wb[SHEET_EPL]
    out: list[tuple[str, str, str]] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) < 3:
            continue
        raw_date, home, away = row[0], row[1], row[2]
        if not raw_date or not home or not away:
            continue
        if isinstance(raw_date, datetime):
            date_str = raw_date.strftime("%Y-%m-%d")
        else:
            date_str = str(raw_date).strip()[:10]
        out.append((date_str, str(home).strip(), str(away).strip()))
    wb.close()
    if not out:
        raise ValueError(f"На листе {SHEET_EPL!r} нет матчей")
    return out


def team_name_map(league_id: str) -> dict[str, int]:
    return {t.name_team: t.id for t in fetch_teams(league_id)}


def fetch_season_id(league_id: str, label: str) -> int | None:
    q = urllib.parse.urlencode(
        {
            "select": "id,label",
            "leagues_id": f"eq.{league_id}",
            "label": f"eq.{label}",
        }
    )
    rows = _request("GET", f"/season?{q}")
    if not rows:
        return None
    return int(rows[0]["id"])


def create_season(league_id: str, label: str) -> int:
    rows = _request(
        "POST",
        "/season?select=id,label",
        body={"leagues_id": league_id, "label": label},
    )
    return int(rows[0]["id"])


def count_season_matches(season_id: int) -> int:
    q = urllib.parse.urlencode({"select": "id", "season_id": f"eq.{season_id}"})
    rows = _request("GET", f"/matches?{q}")
    return len(rows) if isinstance(rows, list) else 0


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
            payloads.append(
                _match_payload(season_id, match_date, team_map[home], team_map[away])
            )
    if missing:
        raise ValueError(
            "Команды отсутствуют в Supabase (таблица team): "
            + ", ".join(sorted(missing))
        )

    inserted = 0
    for i in range(0, len(payloads), batch_size):
        batch = payloads[i : i + batch_size]
        _request("POST", "/matches?select=id", body=batch)
        inserted += len(batch)
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "xlsx",
        type=Path,
        help="Excel с календарём (лист АПЛ)",
    )
    parser.add_argument(
        "--season-label",
        default=DEFAULT_SEASON,
        help=f"Метка сезона (default: {DEFAULT_SEASON})",
    )
    parser.add_argument(
        "--league-id",
        default=EPL_LEAGUE_ID,
        help="UUID лиги в Supabase",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Удалить существующие матчи сезона перед импортом",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только проверка, без записи в Supabase",
    )
    args = parser.parse_args()

    if not args.xlsx.is_file():
        raise SystemExit(f"Файл не найден: {args.xlsx}")

    fixtures = read_epl_fixtures(args.xlsx)
    team_map = team_name_map(args.league_id)
    teams_used = sorted({n for _, h, a in fixtures for n in (h, a)})
    unknown = [t for t in teams_used if t not in team_map]

    print(f"Файл: {args.xlsx.name}")
    print(f"Сезон: {args.season_label}")
    print(f"Матчей в Excel: {len(fixtures)}")
    print(f"Команд: {len(teams_used)}")
    if unknown:
        raise SystemExit(
            "Не найдены в Supabase: " + ", ".join(unknown)
        )

    existing_id = fetch_season_id(args.league_id, args.season_label)
    existing_count = count_season_matches(existing_id) if existing_id else 0
    if existing_id:
        print(f"Сезон уже есть: season_id={existing_id}, матчей={existing_count}")
    else:
        print("Сезон в Supabase ещё не создан")

    if args.dry_run:
        print("Dry-run: запись не выполнялась")
        return

    if existing_id and existing_count:
        if not args.replace:
            raise SystemExit(
                f"Сезон {args.season_label} уже содержит {existing_count} матчей. "
                "Используйте --replace для перезаписи."
            )
        _request("DELETE", f"/matches?season_id=eq.{existing_id}")
        print(f"Удалено матчей: {existing_count}")

    season_id = existing_id or create_season(args.league_id, args.season_label)
    if not existing_id:
        print(f"Создан season_id={season_id}")

    try:
        n = insert_matches(season_id, fixtures, team_map)
    except SupabaseError as exc:
        raise SystemExit(f"Ошибка Supabase: {exc} ({exc.body[:300]})") from exc

    final_count = count_season_matches(season_id)
    print(f"Импортировано: {n} матчей (всего в сезоне: {final_count})")


if __name__ == "__main__":
    main()
