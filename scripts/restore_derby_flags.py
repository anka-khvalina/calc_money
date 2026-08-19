#!/usr/bin/env python3
"""Восстановить derby_weight=1 по списку пар config/derby_pairs.json.

После ошибочного массового сброса/инверсии флагов не нужно кликать каждый матч
в «Истории» — скрипт находит пары home/away и проставляет дерби автоматически.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from supabase_teams import SupabaseError, _request, fetch_teams  # noqa: E402

LEAGUE_IDS: dict[str, str] = {
    "epl": "2613ee27-7e8d-4d18-bd5e-e3f525121848",
    "la_liga": "8a33ea15-b5e2-4962-8196-a31c8b9fa8fe",
    "bundesliga": "0e928134-ae08-48fd-8d2c-3539a994b054",
    "serie_a": "c95e9c68-5679-41ca-ac92-b5b3975bfb02",
    "ligue_1": "f89e6854-2836-4540-9d82-b3ff4019dc6a",
}

DERBY_PAIRS_PATH = ROOT / "config" / "derby_pairs.json"


def load_derby_pairs() -> dict[str, list[tuple[str, str]]]:
    raw = json.loads(DERBY_PAIRS_PATH.read_text(encoding="utf-8"))
    out: dict[str, list[tuple[str, str]]] = {}
    for league, pairs in raw.items():
        if league.startswith("_") or not isinstance(pairs, list):
            continue
        norm: list[tuple[str, str]] = []
        for item in pairs:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            a, b = str(item[0]).strip(), str(item[1]).strip()
            if a and b:
                norm.append((a, b))
        out[league] = norm
    return out


def team_name_index(league_id: str) -> dict[str, int]:
    teams = fetch_teams(league_id)
    idx: dict[str, int] = {}
    for t in teams:
        name = str(t.name_team or "").strip()
        if name:
            idx[name] = int(t.id)
    return idx


def pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def build_derby_id_pairs(
    league_slug: str,
    league_id: str,
    pairs: list[tuple[str, str]],
) -> set[tuple[int, int]]:
    names = team_name_index(league_id)
    missing: set[str] = set()
    out: set[tuple[int, int]] = set()
    for a, b in pairs:
        ta = names.get(a)
        tb = names.get(b)
        if ta is None:
            missing.add(a)
        if tb is None:
            missing.add(b)
        if ta is not None and tb is not None:
            out.add(pair_key(ta, tb))
    if missing:
        print(f"  [!] {league_slug}: нет в справочнике: {', '.join(sorted(missing))}")
    return out


def fetch_match_ids(league_id: str) -> list[tuple[int, int, int]]:
    """(match_id, home_team_id, away_team_id) через v_matches_full."""
    out: list[tuple[int, int, int]] = []
    offset = 0
    page = 500
    while True:
        q = urllib.parse.urlencode({
            "select": "match_id,home_team_id,away_team_id",
            "league_id": f"eq.{league_id}",
            "order": "match_id.asc",
            "limit": str(page),
            "offset": str(offset),
        })
        rows = _request("GET", f"/v_matches_full?{q}")
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            mid = r.get("match_id")
            h = r.get("home_team_id")
            a = r.get("away_team_id")
            if mid is None or h is None or a is None:
                continue
            out.append((int(mid), int(h), int(a)))
        if len(rows) < page:
            break
        offset += page
    return out


def patch_derby_batch(ids: list[int]) -> int:
    if not ids:
        return 0
    id_list = ",".join(str(i) for i in ids)
    rows = _request(
        "PATCH",
        f"/matches?id=in.({id_list})",
        body={"derby_weight": 1.0},
        prefer="return=representation",
    )
    return len(rows) if isinstance(rows, list) else 0


def restore_league(
    league_slug: str,
    *,
    dry_run: bool,
    batch: int,
) -> tuple[int, int]:
    if league_slug not in LEAGUE_IDS:
        raise ValueError(f"Неизвестная лига {league_slug!r}")
    pairs_cfg = load_derby_pairs()
    pairs = pairs_cfg.get(league_slug, [])
    if not pairs:
        print(f"  {league_slug}: нет пар в {DERBY_PAIRS_PATH.name}")
        return 0, 0

    league_id = LEAGUE_IDS[league_slug]
    derby_ids = build_derby_id_pairs(league_slug, league_id, pairs)
    if not derby_ids:
        return 0, 0

    matches = fetch_match_ids(league_id)
    to_fix: list[int] = []
    for mid, h, a in matches:
        if pair_key(h, a) in derby_ids:
            to_fix.append(mid)

    print(f"  {league_slug}: пар {len(derby_ids)}, матчей-дерби {len(to_fix)}")
    if dry_run or not to_fix:
        return len(to_fix), 0

    fixed = 0
    for i in range(0, len(to_fix), batch):
        fixed += patch_derby_batch(to_fix[i : i + batch])
    return len(to_fix), fixed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--league",
        choices=list(LEAGUE_IDS.keys()) + ["all"],
        default="all",
        help="лига или all (default)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()

    leagues = list(LEAGUE_IDS.keys()) if args.league == "all" else [args.league]
    total = 0
    fixed = 0
    try:
        for slug in leagues:
            n, f = restore_league(slug, dry_run=args.dry_run, batch=args.batch)
            total += n
            fixed += f
    except SupabaseError as exc:
        print(f"Ошибка Supabase: {exc}", file=sys.stderr)
        if exc.body:
            print(exc.body, file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"DRY RUN: будет проставлено derby_weight=1 для {total} матчей")
    else:
        print(f"Готово: derby_weight=1 для {fixed} матчей (из {total} найденных пар)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
