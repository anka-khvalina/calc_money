"""
Supabase REST-клиент для вкладки «История» (сезоны и матчи).

Источники: v_season_summary, v_matches_full.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any, List, Optional, Union

from supabase_teams import SupabaseError, _request


@dataclass(frozen=True)
class SeasonSummary:
    league_id: str
    league_name: str
    season_id: int
    season_label: str
    matches_count: int
    imported_at: str

    @property
    def row_id(self) -> str:
        return f"{self.league_id}|{self.season_id}"


@dataclass(frozen=True)
class MatchFull:
    match_id: int
    match_date: str
    league_id: str
    league_name: str
    season_id: int
    season_label: str
    home_team_id: Optional[int]
    home_team: str
    away_team_id: Optional[int]
    away_team: str
    closing_ah_home: Optional[float]
    closing_total_line: Optional[float]
    ah_home_odds: Optional[float]
    ah_away_odds: Optional[float]
    over_odds: Optional[float]
    under_odds: Optional[float]
    home_odds: Optional[float]
    draw_odds: Optional[float]
    away_odds: Optional[float]
    is_neutral: bool
    match_weight: Optional[float]
    derby_weight: Optional[float]
    neutral_weight: Optional[float]
    note: Optional[str]


def format_imported_at(iso: Optional[str]) -> str:
    if not iso:
        return ""
    return iso.replace("T", " ")[:16]


def format_cell(value: Any, *, kind: str = "text") -> str:
    if value is None:
        return "—"
    if kind == "bool":
        return "да" if value else "нет"
    if kind == "num":
        if isinstance(value, bool):
            return "да" if value else "нет"
        if isinstance(value, (int, float)):
            return f"{value:g}"
        return str(value)
    return str(value)


def _parse_season_row(row: dict) -> Optional[SeasonSummary]:
    if not isinstance(row, dict):
        return None
    try:
        league_id = str(row.get("league_id", "")).strip()
        league_name = str(row.get("league_name", "")).strip()
        season_id = int(row["season_id"])
        season_label = str(row.get("season_label", "")).strip()
        matches_count = int(row.get("matches_count") or 0)
        imported_at = str(row.get("imported_at") or "")
    except (KeyError, TypeError, ValueError):
        return None
    if not league_id or not league_name or not season_label:
        return None
    return SeasonSummary(
        league_id=league_id,
        league_name=league_name,
        season_id=season_id,
        season_label=season_label,
        matches_count=matches_count,
        imported_at=imported_at,
    )


def _opt_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _opt_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_match_row(row: dict) -> Optional[MatchFull]:
    if not isinstance(row, dict):
        return None
    try:
        match_id = int(row["match_id"])
        home_team = str(row.get("home_team", "")).strip()
        away_team = str(row.get("away_team", "")).strip()
        if not home_team or not away_team:
            return None
        return MatchFull(
            match_id=match_id,
            match_date=str(row.get("match_date") or ""),
            league_id=str(row.get("league_id") or ""),
            league_name=str(row.get("league_name") or ""),
            season_id=int(row.get("season_id") or 0),
            season_label=str(row.get("season_label") or ""),
            home_team_id=_opt_int(row.get("home_team_id")),
            home_team=home_team,
            away_team_id=_opt_int(row.get("away_team_id")),
            away_team=away_team,
            closing_ah_home=_opt_float(row.get("closing_ah_home")),
            closing_total_line=_opt_float(row.get("closing_total_line")),
            ah_home_odds=_opt_float(row.get("ah_home_odds")),
            ah_away_odds=_opt_float(row.get("ah_away_odds")),
            over_odds=_opt_float(row.get("over_odds")),
            under_odds=_opt_float(row.get("under_odds")),
            home_odds=_opt_float(row.get("home_odds")),
            draw_odds=_opt_float(row.get("draw_odds")),
            away_odds=_opt_float(row.get("away_odds")),
            is_neutral=bool(row.get("is_neutral")),
            match_weight=_opt_float(row.get("match_weight")),
            derby_weight=_opt_float(row.get("derby_weight")),
            neutral_weight=_opt_float(row.get("neutral_weight")),
            note=str(row["note"]).strip() if row.get("note") not in (None, "") else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def fetch_season_summary() -> List[SeasonSummary]:
    q = urllib.parse.urlencode(
        {
            "select": "league_id,league_name,season_id,season_label,matches_count,imported_at",
            "matches_count": "gt.0",
            "order": "league_name.asc,season_label.desc",
        }
    )
    rows = _request("GET", f"/v_season_summary?{q}")
    if not isinstance(rows, list):
        raise SupabaseError("Некорректный ответ v_season_summary")
    out: List[SeasonSummary] = []
    for row in rows:
        ent = _parse_season_row(row)
        if ent is not None:
            out.append(ent)
    return out


_MATCH_SELECT = (
    "match_id,match_date,league_id,league_name,season_id,season_label,"
    "home_team_id,home_team,away_team_id,away_team,"
    "closing_ah_home,closing_total_line,ah_home_odds,ah_away_odds,"
    "over_odds,under_odds,home_odds,draw_odds,away_odds,"
    "is_neutral,match_weight,derby_weight,neutral_weight,note"
)


def fetch_matches(league_id: str, season_id: Union[int, str]) -> List[MatchFull]:
    lid = str(league_id).strip()
    try:
        sid = int(season_id)
    except (TypeError, ValueError):
        return []
    if not lid:
        return []
    q = urllib.parse.urlencode(
        {
            "select": _MATCH_SELECT,
            "league_id": f"eq.{lid}",
            "season_id": f"eq.{sid}",
            "order": "match_date.asc",
        }
    )
    rows = _request("GET", f"/v_matches_full?{q}")
    if not isinstance(rows, list):
        raise SupabaseError("Некорректный ответ v_matches_full")
    out: List[MatchFull] = []
    for row in rows:
        ent = _parse_match_row(row)
        if ent is not None:
            out.append(ent)
    return out


def matches_to_goal_csv(matches: List[MatchFull], *, league_name: str = "") -> str:
    """Конвертация матчей в CSV closing-линий для вкладки «Линия»."""
    header = (
        "date,league,home_team,away_team,closing_ah_home,closing_total_line,"
        "ah_home_odds,ah_away_odds,over_odds,under_odds,home_odds,draw_odds,away_odds,"
        "neutral_flag,derby_flag,quality_flag,value,derby_weight,neutral_weight"
    )

    def cell(val: Any) -> str:
        if val is None:
            return ""
        return str(val)

    lines = [header]
    lg = league_name
    for m in matches:
        lg = lg or m.league_name
        derby_flag = (m.derby_weight or 1) != 1
        lines.append(
            ",".join(
                [
                    cell(m.match_date),
                    cell(lg),
                    cell(m.home_team),
                    cell(m.away_team),
                    cell(m.closing_ah_home),
                    cell(m.closing_total_line),
                    cell(m.ah_home_odds),
                    cell(m.ah_away_odds),
                    cell(m.over_odds),
                    cell(m.under_odds),
                    cell(m.home_odds),
                    cell(m.draw_odds),
                    cell(m.away_odds),
                    "true" if m.is_neutral else "false",
                    "true" if derby_flag else "false",
                    cell(m.note or ""),
                    cell(m.match_weight if m.match_weight is not None else 1),
                    cell(m.derby_weight if m.derby_weight is not None else 1),
                    cell(m.neutral_weight if m.neutral_weight is not None else 1),
                ]
            )
        )
    return "\n".join(lines)
