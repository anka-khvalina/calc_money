"""
Supabase REST-клиент для вкладки «История» (сезоны и матчи).

Источники: v_season_summary, v_matches_full.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, replace
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Set, Union

from supabase_teams import SupabaseError, _request

# Поле derby_weight в БД — булев флаг: 1 = дерби, 0 = не дерби (default).
# Вес дерби (поправка H) считается при обучении, в БД не хранится.
DERBY_FLAG_YES: float = 1.0
DERBY_FLAG_NO: float = 0.0

PATCH_WHITELIST: FrozenSet[str] = frozenset(
    {
        "ah_home_odds",
        "closing_ah_home",
        "ah_away_odds",
        "over_odds",
        "closing_total_line",
        "under_odds",
        "home_odds",
        "draw_odds",
        "away_odds",
        "is_neutral",
        "match_weight",
        "derby_weight",
        "neutral_weight",
    }
)

ODDS_GT_ONE_FIELDS: FrozenSet[str] = frozenset(
    {
        "ah_home_odds",
        "ah_away_odds",
        "over_odds",
        "under_odds",
        "home_odds",
        "draw_odds",
        "away_odds",
    }
)

UI_COL_TO_FIELD: Dict[str, str] = {
    "ah1": "ah_home_odds",
    "ah": "closing_ah_home",
    "ah2": "ah_away_odds",
    "over": "over_odds",
    "tot": "closing_total_line",
    "under": "under_odds",
    "o1": "home_odds",
    "ox": "draw_odds",
    "o2": "away_odds",
    "neutral": "is_neutral",
    "derby": "derby_weight",
    "match_w": "match_weight",
    "neutr_w": "neutral_weight",
}

# Порядок столбцов линии в UI (AH1 → AH → AH2 → O → Тот → U)
HIST_LINE_UI_COLS: tuple[str, ...] = ("ah1", "ah", "ah2", "over", "tot", "under")

# Поля весов и флагов в раскрываемом блоке «Веса»
HIST_WEIGHT_UI_COLS: tuple[str, ...] = ("neutral", "derby", "match_w", "neutr_w")

EDITABLE_UI_COLS: FrozenSet[str] = frozenset(UI_COL_TO_FIELD)


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


def is_derby_match(match: MatchFull) -> bool:
    w = match.derby_weight
    if w is None:
        return False
    return abs(float(w) - DERBY_FLAG_YES) < 1e-9


def derby_weight_from_bool(is_derby: bool) -> float:
    return DERBY_FLAG_YES if is_derby else DERBY_FLAG_NO


def normalized_derby_weight(raw: Optional[float]) -> float:
    """0 = не дерби; null и legacy-значения → 0."""
    if raw is None:
        return DERBY_FLAG_NO
    if abs(float(raw) - DERBY_FLAG_YES) < 1e-9:
        return DERBY_FLAG_YES
    return DERBY_FLAG_NO


def count_matches_for_derby_reset() -> int:
    """Сколько строк в matches ещё не derby_weight=0."""
    rows = _request("GET", "/matches?select=id,derby_weight")
    if not isinstance(rows, list):
        raise SupabaseError("Некорректный ответ matches")
    n = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        w = _opt_float(row.get("derby_weight"))
        if w is None or abs(w - DERBY_FLAG_NO) >= 1e-9:
            n += 1
    return n


def reset_all_derby_flags(*, dry_run: bool = False) -> int:
    """
    Проставить derby_weight=0 всем матчам в matches.
    Возвращает число строк, которые ещё не были 0 (оценка до PATCH).
    """
    pending = count_matches_for_derby_reset()
    if dry_run or pending == 0:
        return pending
    q = urllib.parse.urlencode({"id": "gte.1"})
    _request(
        "PATCH",
        f"/matches?{q}",
        body={"derby_weight": DERBY_FLAG_NO},
        prefer="return=minimal",
    )
    return pending


def format_imported_at(iso: Optional[str]) -> str:
    if not iso:
        return ""
    return iso.replace("T", " ")[:16]


def format_ui_date(iso: Optional[str]) -> str:
    """YYYY-MM-DD → DD.MM.YYYY для отображения в UI."""
    if not iso:
        return ""
    s = str(iso).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}.{s[5:7]}.{s[:4]}"
    return s


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


def edit_display_value(match: MatchFull, ui_col: str) -> str:
    field = UI_COL_TO_FIELD.get(ui_col)
    if not field:
        return ""
    if field == "is_neutral":
        return "да" if match.is_neutral else "нет"
    if ui_col == "derby":
        return "да" if is_derby_match(match) else "нет"
    val = getattr(match, _match_attr(field), None)
    if val is None:
        return ""
    if isinstance(val, float):
        return f"{val:g}".replace(".", ",")
    return str(val)


def _match_attr(db_field: str) -> str:
    mapping = {
        "closing_ah_home": "closing_ah_home",
        "closing_total_line": "closing_total_line",
        "ah_home_odds": "ah_home_odds",
        "ah_away_odds": "ah_away_odds",
        "over_odds": "over_odds",
        "under_odds": "under_odds",
        "home_odds": "home_odds",
        "draw_odds": "draw_odds",
        "away_odds": "away_odds",
        "is_neutral": "is_neutral",
        "derby_weight": "derby_weight",
        "match_weight": "match_weight",
        "neutral_weight": "neutral_weight",
    }
    return mapping[db_field]


def parse_numeric_input(text: str) -> Optional[float]:
    raw = str(text or "").strip().replace("\u00a0", " ")
    if not raw or raw == "—" or raw == "-":
        return None
    raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        raise ValueError("Проверьте значения коэффициентов") from None


def parse_bool_input(text: str) -> bool:
    raw = str(text or "").strip().lower()
    if raw in ("да", "true", "1", "yes", "y"):
        return True
    if raw in ("нет", "false", "0", "no", "n", ""):
        return False
    raise ValueError("Проверьте значения коэффициентов")


def parse_field_input(ui_col: str, text: str) -> Any:
    field = UI_COL_TO_FIELD[ui_col]
    if field == "is_neutral" or ui_col == "derby":
        return parse_bool_input(text)
    return parse_numeric_input(text)


def validate_match_patch(changes: Mapping[str, Any]) -> None:
    for key, val in changes.items():
        if key not in PATCH_WHITELIST:
            raise ValueError(f"Поле {key!r} нельзя изменять")
        if val is None:
            continue
        if key == "is_neutral":
            if not isinstance(val, bool):
                raise ValueError("Проверьте значения коэффициентов")
            continue
        if not isinstance(val, (int, float)):
            raise ValueError("Проверьте значения коэффициентов")
        if key in ODDS_GT_ONE_FIELDS and val <= 1:
            raise ValueError("Проверьте значения коэффициентов")
        if key == "derby_weight":
            fv = float(val)
            if fv < 0 or fv > 1:
                raise ValueError("Проверьте значения коэффициентов")
            continue
        if key in ("match_weight", "neutral_weight") and val < 0:
            raise ValueError("Проверьте значения коэффициентов")


def field_value_from_match(match: MatchFull, db_field: str) -> Any:
    if db_field == "is_neutral":
        return match.is_neutral
    if db_field == "derby_weight":
        return is_derby_match(match)
    return getattr(match, _match_attr(db_field))


def build_dirty_patch(original: MatchFull, edited: Mapping[str, str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for ui_col, text in edited.items():
        if ui_col not in EDITABLE_UI_COLS:
            continue
        field = UI_COL_TO_FIELD[ui_col]
        new_val = parse_field_input(ui_col, text)
        if ui_col == "derby":
            old_derby = is_derby_match(original)
            if bool(new_val) == old_derby:
                continue
            payload["derby_weight"] = derby_weight_from_bool(bool(new_val))
            continue
        old_val = field_value_from_match(original, field)
        if _values_equal(field, old_val, new_val):
            continue
        payload[field] = new_val
    validate_match_patch(payload)
    return payload


def _values_equal(field: str, old: Any, new: Any) -> bool:
    if old is None and new is None:
        return True
    if field == "is_neutral" or field == "derby_weight":
        return bool(old) == bool(new)
    try:
        return old is not None and new is not None and abs(float(old) - float(new)) < 1e-9
    except (TypeError, ValueError):
        return old == new


def patch_match(match_id: int, changes: Mapping[str, Any], *, original: MatchFull) -> MatchFull:
    if not changes:
        raise ValueError("Нет изменений для сохранения")
    payload = dict(changes)
    extra = set(payload) - PATCH_WHITELIST
    if extra:
        raise ValueError(f"Поле {next(iter(extra))!r} нельзя изменять")
    validate_match_patch(payload)
    q = urllib.parse.urlencode({"id": f"eq.{int(match_id)}"})
    rows = _request("PATCH", f"/matches?{q}", body=payload)
    if not (isinstance(rows, list) and rows) and not isinstance(rows, dict):
        raise SupabaseError("Не удалось сохранить изменения")
    return apply_patch_to_match(original, payload)


def apply_patch_to_match(original: MatchFull, changes: Mapping[str, Any]) -> MatchFull:
    kw: Dict[str, Any] = {}
    for field, val in changes.items():
        if field not in PATCH_WHITELIST:
            continue
        attr = _match_attr(field)
        if field == "is_neutral":
            kw[attr] = bool(val)
        elif val is None:
            kw[attr] = None
        else:
            kw[attr] = float(val)
    return replace(original, **kw)


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
            derby_weight=normalized_derby_weight(_opt_float(row.get("derby_weight"))),
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
        "date,league,league_id,home_team_id,home_team,away_team_id,away_team,"
        "closing_ah_home,closing_total_line,"
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
        derby_flag = is_derby_match(m)
        lines.append(
            ",".join(
                [
                    cell(m.match_date),
                    cell(lg),
                    cell(m.league_id),
                    cell(m.home_team_id),
                    cell(m.home_team),
                    cell(m.away_team_id),
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
                    cell(m.derby_weight if m.derby_weight is not None else 0),
                    cell(m.neutral_weight if m.neutral_weight is not None else 1),
                ]
            )
        )
    return "\n".join(lines)
