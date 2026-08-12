"""Read-only load of active matches with full closing AH+OU."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from supabase_config import load_supabase_settings
from supabase_teams import _request

import goal_model_train as gmt


SELECT = (
    "match_id,match_date,league_id,league_name,season_id,season_label,"
    "home_team_id,home_team,away_team_id,away_team,"
    "closing_ah_home,closing_total_line,ah_home_odds,ah_away_odds,"
    "over_odds,under_odds,home_odds,draw_odds,away_odds,"
    "is_neutral,match_weight,derby_weight,neutral_weight,"
    "home_rotation_code,away_rotation_code,motivation,active"
)


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Row:
    match_id: str
    match_date: date
    league_id: str
    league_name: str
    season_id: Optional[str]
    season_label: Optional[str]
    home_team_id: str
    away_team_id: str
    home_team: str
    away_team: str
    closing_ah_home: float
    closing_total_line: float
    ah_home_odds: float
    ah_away_odds: float
    over_odds: float
    under_odds: float
    home_odds: Optional[float]
    draw_odds: Optional[float]
    away_odds: Optional[float]
    is_neutral: bool
    match_weight: float
    derby_weight: Optional[float]
    neutral_weight: float
    home_rotation_code: str
    away_rotation_code: str

    @property
    def d_market(self) -> float:
        """Market-implied goal diff (home − away), AH convention: D ≈ −AH."""
        return -float(self.closing_ah_home)

    @property
    def s_market(self) -> float:
        return float(self.closing_total_line)


def fetch_all_view_rows() -> List[dict]:
    settings = load_supabase_settings()
    out: List[dict] = []
    start = 0
    page = 400
    while True:
        part = None
        last_err: Optional[Exception] = None
        for _try in range(5):
            try:
                url = (
                    f"{settings.rest_url}/v_matches_full?select={SELECT}"
                    f"&active=eq.true&order=match_date.asc"
                )
                req = urllib.request.Request(
                    url,
                    headers={
                        "apikey": settings.anon_key,
                        "Authorization": f"Bearer {settings.anon_key}",
                        "Range": f"{start}-{start + page - 1}",
                        "Prefer": "count=exact",
                    },
                )
                with urllib.request.urlopen(req, timeout=90) as resp:
                    part = json.loads(resp.read().decode())
                break
            except Exception as exc:
                last_err = exc
                part = None
        if part is None:
            raise RuntimeError(f"fetch failed at {start}: {last_err}")
        out.extend(part)
        if len(part) < page:
            return out
        start += page


def parse_rows(raw: Sequence[dict]) -> List[Row]:
    rows: List[Row] = []
    for r in raw:
        if r.get("motivation") is False or str(r.get("motivation")).lower() == "false":
            continue
        needed = (
            "closing_ah_home",
            "closing_total_line",
            "ah_home_odds",
            "ah_away_odds",
            "over_odds",
            "under_odds",
            "home_team_id",
            "away_team_id",
            "match_date",
        )
        if any(_f(r.get(k)) is None and k not in ("home_team_id", "away_team_id", "match_date") for k in needed[:6]):
            continue
        if r.get("home_team_id") is None or r.get("away_team_id") is None:
            continue
        ds = (r.get("match_date") or "")[:10]
        try:
            dt = date.fromisoformat(ds)
        except ValueError:
            continue
        if any(
            _f(r.get(k)) is None
            for k in (
                "closing_ah_home",
                "closing_total_line",
                "ah_home_odds",
                "ah_away_odds",
                "over_odds",
                "under_odds",
            )
        ):
            continue
        rows.append(
            Row(
                match_id=str(r.get("match_id")),
                match_date=dt,
                league_id=str(r.get("league_id") or ""),
                league_name=str(r.get("league_name") or ""),
                season_id=str(r["season_id"]) if r.get("season_id") is not None else None,
                season_label=r.get("season_label"),
                home_team_id=str(r["home_team_id"]),
                away_team_id=str(r["away_team_id"]),
                home_team=str(r.get("home_team") or r["home_team_id"]),
                away_team=str(r.get("away_team") or r["away_team_id"]),
                closing_ah_home=float(r["closing_ah_home"]),
                closing_total_line=float(r["closing_total_line"]),
                ah_home_odds=float(r["ah_home_odds"]),
                ah_away_odds=float(r["ah_away_odds"]),
                over_odds=float(r["over_odds"]),
                under_odds=float(r["under_odds"]),
                home_odds=_f(r.get("home_odds")),
                draw_odds=_f(r.get("draw_odds")),
                away_odds=_f(r.get("away_odds")),
                is_neutral=bool(r.get("is_neutral")),
                match_weight=_f(r.get("match_weight")) or 1.0,
                derby_weight=_f(r.get("derby_weight")),
                neutral_weight=_f(r.get("neutral_weight")) or 1.0,
                home_rotation_code=(r.get("home_rotation_code") or "none"),
                away_rotation_code=(r.get("away_rotation_code") or "none"),
            )
        )
    return rows


def to_raw_match(r: Row, *, quality_mult: float = 1.0) -> gmt.RawMatch:
    derby_flag = gmt._derby_flag_from_weight(r.derby_weight)
    return gmt.RawMatch(
        date=r.match_date,
        league=r.league_name,
        home_team=r.home_team,
        away_team=r.away_team,
        home_team_id=r.home_team_id,
        away_team_id=r.away_team_id,
        league_id=r.league_id,
        closing_ah_home=r.closing_ah_home,
        closing_total_line=r.closing_total_line,
        ah_home_odds=r.ah_home_odds,
        ah_away_odds=r.ah_away_odds,
        over_odds=r.over_odds,
        under_odds=r.under_odds,
        home_odds=r.home_odds,
        draw_odds=r.draw_odds,
        away_odds=r.away_odds,
        neutral_flag=r.is_neutral,
        derby_flag=derby_flag,
        quality_match_weight=float(r.match_weight) * float(quality_mult),
        derby_match_weight=r.derby_weight,
        neutral_match_weight=r.neutral_weight,
        home_rotation_code=r.home_rotation_code,
        away_rotation_code=r.away_rotation_code,
        season_id=r.season_id,
        season_label=r.season_label,
    )


def probe_readonly() -> Dict[str, str]:
    """GET-only sanity: leagues list."""
    rows = _request("GET", "/leagues?select=id&limit=1")
    if not isinstance(rows, list):
        raise RuntimeError("readonly probe failed")
    settings = load_supabase_settings()
    u = urllib.parse.urlparse(settings.rest_url)
    return {"scheme": u.scheme, "host": u.hostname or "?", "path": u.path or "/"}
