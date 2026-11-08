"""H2H closing 1X2 history block for Line predict (BT-01).

Pure view-model builder: no I/O. Frontend mirrors these rules.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MSG_PREV_ONE = (
    "Ошибка! Найдены коэффициенты только на 1 матч прошлого сезона. "
    "Необходима ручная проверка архива!"
)
MSG_PREV_NONE = (
    "Соперники не встречались в прошлом сезоне или коэффициенты их матчей "
    "отсутствуют в истории"
)
MSG_BOTH_NONE = (
    "Соперники не встречались в этом и прошлом сезонах или коэффициенты их "
    "матчей отсутствуют в истории"
)


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        n = float(v)
        return n if n == n else None
    except (TypeError, ValueError):
        return None


def classify_1x2(m: Mapping[str, Any]) -> str:
    """Return 'complete' | 'partial' | 'blank' for closing 1X2 fields."""
    vals = [_num(m.get(k)) for k in ("home_odds", "draw_odds", "away_odds")]
    present = [v for v in vals if v is not None]
    if len(present) == 0:
        return "blank"
    if len(present) == 3 and all(v > 1 for v in present):
        return "complete"
    return "partial"


def has_full_1x2(m: Mapping[str, Any]) -> bool:
    return classify_1x2(m) == "complete"


def format_match_date(date: str) -> str:
    d = str(date or "")[:10]
    if len(d) >= 10 and d[4] == "-" and d[7] == "-":
        return f"{d[8:10]}.{d[5:7]}.{d[0:4]}"
    return d or "?"


def incomplete_warning(m: Mapping[str, Any]) -> str:
    home = str(m.get("home_team") or m.get("homeTeam") or "?")
    away = str(m.get("away_team") or m.get("awayTeam") or "?")
    date_fmt = format_match_date(str(m.get("match_date") or m.get("matchDate") or ""))
    return (
        f"Отсутствует часть данных по матчу {home} — {away}, {date_fmt}. "
        "Необходима ручная проверка архива"
    )


def _row(m: Mapping[str, Any]) -> Dict[str, Any]:
    date = str(m.get("match_date") or "")[:10]
    return {
        "matchId": m.get("match_id") or m.get("matchId"),
        "matchDate": date,
        "homeTeam": str(m.get("home_team") or ""),
        "awayTeam": str(m.get("away_team") or ""),
        "homeOdds": float(m["home_odds"]),
        "drawOdds": float(m["draw_odds"]),
        "awayOdds": float(m["away_odds"]),
    }


def _sort_chrono(rows: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return sorted(rows, key=lambda m: str(m.get("match_date") or ""))


def previous_season_from_list(
    seasons_desc: Sequence[Mapping[str, Any]],
    current_season_id: Any,
) -> Optional[Mapping[str, Any]]:
    """seasons_desc: newest first. Return the season entry after current."""
    idx = None
    for i, s in enumerate(seasons_desc):
        sid = s.get("season_id")
        try:
            if int(sid) == int(current_season_id):
                idx = i
                break
        except (TypeError, ValueError):
            if str(sid) == str(current_season_id):
                idx = i
                break
    if idx is None or idx + 1 >= len(seasons_desc):
        return None
    return seasons_desc[idx + 1]


def _split_season_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    skip_blank: bool,
) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]], List[Mapping[str, Any]]]:
    """Return (archive_all, complete, incomplete).

    skip_blank=True (current season): all-null closing rows are ignored
    (upcoming fixtures). Previous season keeps blanks as incomplete.
    """
    archive: List[Mapping[str, Any]] = []
    complete: List[Mapping[str, Any]] = []
    incomplete: List[Mapping[str, Any]] = []
    for m in rows or []:
        kind = classify_1x2(m)
        if kind == "blank" and skip_blank:
            continue
        archive.append(m)
        if kind == "complete":
            complete.append(m)
        else:
            incomplete.append(m)
    return archive, complete, incomplete


def build_h2h_view(
    *,
    prev_season_label: Optional[str],
    curr_season_label: Optional[str],
    prev_matches: Sequence[Mapping[str, Any]],
    curr_matches: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build H2H block model from History rows of the same league."""
    prev_all, prev_complete, prev_incomplete = _split_season_rows(
        prev_matches, skip_blank=False
    )
    curr_all, curr_complete, curr_incomplete = _split_season_rows(
        curr_matches, skip_blank=True
    )

    warnings: List[str] = []

    # FT-04.5 / КП-11
    for m in _sort_chrono(prev_incomplete) + _sort_chrono(curr_incomplete):
        warnings.append(incomplete_warning(m))

    # FT-04.4
    if not prev_all and not curr_all:
        warnings.append(MSG_BOTH_NONE)
        return {
            "seasons": [],
            "warnings": warnings,
            "prevSeasonLabel": prev_season_label,
            "currSeasonLabel": curr_season_label,
        }

    # FT-04.1 (КП-10: not when 2+ archive rows)
    if len(prev_all) == 1:
        warnings.append(MSG_PREV_ONE)

    # FT-04.3
    if not prev_all and curr_all:
        warnings.append(MSG_PREV_NONE)

    seasons: List[Dict[str, Any]] = []
    if prev_complete:
        seasons.append(
            {
                "seasonLabel": prev_season_label or "",
                "matches": [_row(m) for m in _sort_chrono(prev_complete)],
            }
        )

    # FT-04.2: no empty current section
    if curr_complete:
        seasons.append(
            {
                "seasonLabel": curr_season_label or "",
                "matches": [_row(m) for m in _sort_chrono(curr_complete)],
            }
        )

    return {
        "seasons": seasons,
        "warnings": warnings,
        "prevSeasonLabel": prev_season_label,
        "currSeasonLabel": curr_season_label,
    }
