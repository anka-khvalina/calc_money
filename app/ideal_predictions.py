"""Ideal predictions: store/list/detail with match validations against Supabase."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from supabase_teams import SupabaseError, _request

CLOSING_1X2 = ("home_odds", "draw_odds", "away_odds")
CLOSING_AH = ("closing_ah_home", "ah_home_odds", "ah_away_odds")
CLOSING_OU = ("closing_total_line", "over_odds", "under_odds")


class IdealPredictionError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        n = float(v)
        return n if n == n else None  # NaN check
    except (TypeError, ValueError):
        return None


def has_closing_odds(m: Mapping[str, Any]) -> bool:
    """Played marker for this feature: any market closing odds present."""
    if all(_num(m.get(k)) and _num(m.get(k)) > 1 for k in CLOSING_1X2):
        return True
    if (
        m.get("closing_ah_home") is not None
        and _num(m.get("ah_home_odds"))
        and _num(m.get("ah_home_odds")) > 1
        and _num(m.get("ah_away_odds"))
        and _num(m.get("ah_away_odds")) > 1
    ):
        return True
    if (
        m.get("closing_total_line") is not None
        and _num(m.get("over_odds"))
        and _num(m.get("over_odds")) > 1
        and _num(m.get("under_odds"))
        and _num(m.get("under_odds")) > 1
    ):
        return True
    return False


def fetch_match(match_id: int) -> Dict[str, Any]:
    q = urllib.parse.urlencode(
        {
            "select": "*",
            "match_id": f"eq.{int(match_id)}",
            "limit": "1",
        }
    )
    rows = _request("GET", f"/v_matches_full?{q}")
    if not isinstance(rows, list) or not rows:
        raise IdealPredictionError(
            "MATCH_NOT_FOUND",
            "Будущая встреча этих команд не найдена в календаре.",
            404,
        )
    return dict(rows[0])


def current_season_for_league(league_id: str) -> Tuple[Optional[int], Optional[str]]:
    """Newest non-empty season for league by season_label desc."""
    lid = str(league_id).strip()
    q = urllib.parse.urlencode(
        {
            "select": "season_id,season_label,matches_count",
            "league_id": f"eq.{lid}",
            "order": "season_label.desc",
        }
    )
    rows = _request("GET", f"/v_season_summary?{q}")
    if not isinstance(rows, list):
        return None, None
    for row in rows:
        if not isinstance(row, dict):
            continue
        lab = str(row.get("season_label") or "")
        if "TEST" in lab.upper():
            continue
        cnt = int(row.get("matches_count") or 0)
        if cnt <= 0:
            continue
        try:
            return int(row["season_id"]), lab
        except (KeyError, TypeError, ValueError):
            continue
    # fallback: first row even if empty/test
    for row in rows:
        if isinstance(row, dict) and row.get("season_id") is not None:
            try:
                return int(row["season_id"]), str(row.get("season_label") or "")
            except (TypeError, ValueError):
                continue
    return None, None


def assert_saveable(match: Mapping[str, Any]) -> None:
    if has_closing_odds(match):
        season = match.get("season_label") or match.get("season_id") or ""
        raise IdealPredictionError(
            "MATCH_ALREADY_PLAYED",
            f"Упс, кажется, этот матч уже был сыгран в сезоне {season}.",
            409,
        )
    lid = str(match.get("league_id") or "")
    cur_id, cur_lab = current_season_for_league(lid)
    mid_season = match.get("season_id")
    try:
        mid_season_i = int(mid_season) if mid_season is not None else None
    except (TypeError, ValueError):
        mid_season_i = None
    if cur_id is not None and mid_season_i is not None and mid_season_i != cur_id:
        raise IdealPredictionError(
            "INVALID_SEASON",
            "Сохранение доступно только для матчей текущего сезона.",
            422,
        )


def get_ideal_by_match(match_id: int) -> Optional[Dict[str, Any]]:
    q = urllib.parse.urlencode(
        {
            "select": "*",
            "match_id": f"eq.{int(match_id)}",
            "limit": "1",
        }
    )
    try:
        rows = _request("GET", f"/ideal_predictions?{q}")
    except SupabaseError as e:
        if e.status == 404:
            return None
        raise
    if isinstance(rows, list) and rows:
        return dict(rows[0])
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_ideal(
    match_id: int,
    prediction_data: Mapping[str, Any],
    calculation_settings: Mapping[str, Any],
) -> Dict[str, Any]:
    match = fetch_match(match_id)
    assert_saveable(match)
    existing = get_ideal_by_match(match_id)
    if existing:
        raise IdealPredictionError(
            "IDEAL_PREDICTION_ALREADY_EXISTS",
            "Для этого матча уже сохранен идеальный прогноз.",
            409,
        )
    now = _now_iso()
    payload = {
        "match_id": int(match_id),
        "league_id": str(match.get("league_id")),
        "league_name": str(match.get("league_name") or ""),
        "season_id": int(match["season_id"]),
        "season_label": str(match.get("season_label") or ""),
        "home_team_id": int(match["home_team_id"]),
        "away_team_id": int(match["away_team_id"]),
        "home_team": str(match.get("home_team") or ""),
        "away_team": str(match.get("away_team") or ""),
        "match_date": str(match.get("match_date") or "")[:10],
        "prediction_data": dict(prediction_data),
        "calculation_settings": dict(calculation_settings),
        "saved_at": now,
        "updated_at": now,
    }
    try:
        rows = _request("POST", "/ideal_predictions", body=payload)
    except SupabaseError as e:
        if e.status == 409 or (e.body and "uq_ideal_predictions_match" in str(e.body)):
            raise IdealPredictionError(
                "IDEAL_PREDICTION_ALREADY_EXISTS",
                "Для этого матча уже сохранен идеальный прогноз.",
                409,
            ) from e
        raise
    row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else payload)
    return {
        "id": row.get("id"),
        "matchId": int(match_id),
        "status": "CREATED",
        "savedAt": row.get("saved_at") or now,
    }


def update_ideal(
    match_id: int,
    prediction_data: Mapping[str, Any],
    calculation_settings: Mapping[str, Any],
) -> Dict[str, Any]:
    match = fetch_match(match_id)
    assert_saveable(match)
    existing = get_ideal_by_match(match_id)
    if not existing:
        raise IdealPredictionError(
            "IDEAL_PREDICTION_NOT_FOUND",
            "Сохраненный идеальный прогноз не найден.",
            404,
        )
    now = _now_iso()
    payload = {
        "prediction_data": dict(prediction_data),
        "calculation_settings": dict(calculation_settings),
        "saved_at": now,
        "updated_at": now,
        # refresh snapshot labels from live match
        "league_name": str(match.get("league_name") or existing.get("league_name") or ""),
        "home_team": str(match.get("home_team") or existing.get("home_team") or ""),
        "away_team": str(match.get("away_team") or existing.get("away_team") or ""),
        "match_date": str(match.get("match_date") or existing.get("match_date") or "")[:10],
        "season_label": str(match.get("season_label") or existing.get("season_label") or ""),
    }
    q = urllib.parse.urlencode({"match_id": f"eq.{int(match_id)}"})
    rows = _request("PATCH", f"/ideal_predictions?{q}", body=payload)
    row = rows[0] if isinstance(rows, list) and rows else {**existing, **payload}
    return {
        "id": row.get("id") or existing.get("id"),
        "matchId": int(match_id),
        "status": "UPDATED",
        "savedAt": row.get("saved_at") or now,
    }


def closing_markets_from_match(m: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not has_closing_odds(m):
        return None
    markets: List[Dict[str, Any]] = []
    if all(_num(m.get(k)) and _num(m.get(k)) > 1 for k in CLOSING_1X2):
        markets.append(
            {
                "market": "1X2",
                "line": None,
                "outcomes": [
                    {"outcome": "HOME", "odds": float(m["home_odds"])},
                    {"outcome": "DRAW", "odds": float(m["draw_odds"])},
                    {"outcome": "AWAY", "odds": float(m["away_odds"])},
                ],
            }
        )
    if (
        m.get("closing_ah_home") is not None
        and _num(m.get("ah_home_odds"))
        and _num(m.get("ah_home_odds")) > 1
        and _num(m.get("ah_away_odds"))
        and _num(m.get("ah_away_odds")) > 1
    ):
        markets.append(
            {
                "market": "ASIAN_HANDICAP",
                "line": float(m["closing_ah_home"]),
                "outcomes": [
                    {"outcome": "HOME", "odds": float(m["ah_home_odds"])},
                    {"outcome": "AWAY", "odds": float(m["ah_away_odds"])},
                ],
            }
        )
    if (
        m.get("closing_total_line") is not None
        and _num(m.get("over_odds"))
        and _num(m.get("over_odds")) > 1
        and _num(m.get("under_odds"))
        and _num(m.get("under_odds")) > 1
    ):
        markets.append(
            {
                "market": "TOTAL",
                "line": float(m["closing_total_line"]),
                "outcomes": [
                    {"outcome": "OVER", "odds": float(m["over_odds"])},
                    {"outcome": "UNDER", "odds": float(m["under_odds"])},
                ],
            }
        )
    return {"markets": markets} if markets else None


def list_ideals(
    *,
    league: Optional[str] = None,
    season: Optional[str] = None,
    team: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    filters: List[str] = ["select=*", "order=match_date.desc"]
    if league:
        # accept league name or uuid
        if len(str(league)) == 36 and "-" in str(league):
            filters.append(f"league_id=eq.{urllib.parse.quote(str(league))}")
        else:
            filters.append(f"league_name=eq.{urllib.parse.quote(str(league))}")
    if season:
        filters.append(f"season_label=eq.{urllib.parse.quote(str(season))}")
    if date_from:
        filters.append(f"match_date=gte.{urllib.parse.quote(str(date_from)[:10])}")
    if date_to:
        filters.append(f"match_date=lte.{urllib.parse.quote(str(date_to)[:10])}")
    if team:
        t = urllib.parse.quote(f"%{team}%")
        filters.append(f"or=(home_team.ilike.{t},away_team.ilike.{t})")
    # fetch a bit more then filter status client-side (status from match closing)
    filters.append(f"limit={page_size * 3}")
    filters.append(f"offset={(page - 1) * page_size}")
    path = "/ideal_predictions?" + "&".join(filters)
    rows = _request("GET", path)
    if not isinstance(rows, list):
        rows = []
    items: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mid = int(row["match_id"])
        try:
            match = fetch_match(mid)
            played = has_closing_odds(match)
        except IdealPredictionError:
            played = False
            match = {}
        st = "PLAYED" if played else "UPCOMING"
        if status and status.upper() not in ("", "ALL") and st != status.upper():
            continue
        items.append(
            {
                "id": row.get("id"),
                "matchId": mid,
                "league": row.get("league_name"),
                "season": row.get("season_label"),
                "homeTeam": row.get("home_team"),
                "awayTeam": row.get("away_team"),
                "matchDate": row.get("match_date"),
                "savedAt": row.get("saved_at"),
                "status": st,
                "hasClosingOdds": played,
            }
        )
        if len(items) >= page_size:
            break
    return {
        "items": items,
        "page": page,
        "pageSize": page_size,
        "total": len(items),
    }


def get_ideal_detail(match_id: int) -> Dict[str, Any]:
    row = get_ideal_by_match(match_id)
    if not row:
        raise IdealPredictionError(
            "IDEAL_PREDICTION_NOT_FOUND",
            "Сохраненный идеальный прогноз не найден.",
            404,
        )
    match = fetch_match(match_id)
    played = has_closing_odds(match)
    return {
        "id": row.get("id"),
        "match": {
            "matchId": int(match_id),
            "league": row.get("league_name") or match.get("league_name"),
            "season": row.get("season_label") or match.get("season_label"),
            "homeTeam": row.get("home_team") or match.get("home_team"),
            "awayTeam": row.get("away_team") or match.get("away_team"),
            "matchDate": row.get("match_date") or match.get("match_date"),
        },
        "savedAt": row.get("saved_at"),
        "status": "PLAYED" if played else "UPCOMING",
        "predictionData": row.get("prediction_data") or {},
        "calculationSettings": row.get("calculation_settings") or {},
        "closingOdds": closing_markets_from_match(match),
    }


def find_upcoming_match(
    *,
    league_id: str,
    season_id: int,
    home_team_id: int,
    away_team_id: int,
) -> Dict[str, Any]:
    """Resolve unique upcoming match for Line save flow."""
    q = urllib.parse.urlencode(
        {
            "select": "*",
            "league_id": f"eq.{league_id}",
            "season_id": f"eq.{int(season_id)}",
            "home_team_id": f"eq.{int(home_team_id)}",
            "away_team_id": f"eq.{int(away_team_id)}",
            "order": "match_date.asc",
        }
    )
    rows = _request("GET", f"/v_matches_full?{q}")
    if not isinstance(rows, list) or not rows:
        raise IdealPredictionError(
            "MATCH_NOT_FOUND",
            "Будущая встреча этих команд не найдена в календаре. Необходима ручная проверка раздела \"История\".",
            404,
        )
    if len(rows) > 1:
        # Prefer first without closing; if still multiple — ambiguous
        upcoming = [r for r in rows if isinstance(r, dict) and not has_closing_odds(r)]
        if len(upcoming) == 1:
            return dict(upcoming[0])
        raise IdealPredictionError(
            "AMBIGUOUS_MATCH",
            "Найдено несколько подходящих матчей. Сохранение запрещено до ручной проверки.",
            409,
        )
    return dict(rows[0])
