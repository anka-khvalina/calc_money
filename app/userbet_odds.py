"""
Получение и разбор коэффициентов с userbet.info для вкладки «История».
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence

USERBET_ODDS_URL = "https://userbet.info/user/get_current_lineups_odds/"
PREFERRED_BOOKMAKER = 70

USERBET_REQUEST_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://userbet.info/",
    "Accept": "application/json, text/html, */*",
}

ODDS_PATCH_FIELDS = frozenset(
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
    }
)

ODDS_FIELD_TO_UI_COL: Dict[str, str] = {
    "ah_home_odds": "ah1",
    "closing_ah_home": "ah",
    "ah_away_odds": "ah2",
    "over_odds": "over",
    "closing_total_line": "tot",
    "under_odds": "under",
    "home_odds": "o1",
    "draw_odds": "ox",
    "away_odds": "o2",
}

_HANDICAP_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_TOTAL_RE = re.compile(r"^\d+(?:\.\d+)?$")


class UserbetError(RuntimeError):
    pass


def round_odds(value: float) -> float:
    return round(float(value), 2)


def normalize_handicap(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip().replace("\u00a0", " ")
    if not text:
        return None
    if not _HANDICAP_RE.match(text):
        return None
    try:
        return round(float(text.replace("+", "")), 4)
    except ValueError:
        return None


def normalize_total_line(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip().replace("\u00a0", " ")
    if not text:
        return None
    if not _TOTAL_RE.match(text):
        return None
    try:
        val = float(text)
        return round(val, 4)
    except ValueError:
        return None


def _line_key(value: float) -> str:
    return f"{value:g}"


def filter_by_bookmaker(rows: Sequence[Mapping[str, Any]], *, preferred: int = PREFERRED_BOOKMAKER) -> List[dict]:
    out = [dict(r) for r in rows]
    bookmakers = {r.get("b") for r in out}
    if preferred in bookmakers:
        return [r for r in out if r.get("b") == preferred]
    return out


def _group_markets(rows: Sequence[Mapping[str, Any]]) -> Dict[str, List[dict]]:
    if not rows:
        raise UserbetError("Внешний сайт не вернул коэффициенты по матчу")
    filtered = filter_by_bookmaker(rows)
    by_m: Dict[int, List[dict]] = {}
    for row in filtered:
        try:
            m_val = int(row["m"])
        except (KeyError, TypeError, ValueError):
            continue
        by_m.setdefault(m_val, []).append(dict(row))
    unique = sorted(by_m)
    if len(unique) != 3:
        raise UserbetError("Не удалось определить группы рынков из ответа внешнего сайта")
    return {
        "1x2": by_m[unique[0]],
        "total": by_m[unique[1]],
        "ah": by_m[unique[2]],
    }


def _parse_1x2(rows: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    field_by_l = {"1": "home_odds", "x": "draw_odds", "2": "away_odds"}
    for row in rows:
        label = str(row.get("l", "")).strip().lower()
        field = field_by_l.get(label)
        if not field:
            continue
        try:
            out[field] = round_odds(float(row["v"]))
        except (KeyError, TypeError, ValueError):
            continue
    missing = [f for f in ("home_odds", "draw_odds", "away_odds") if f not in out]
    if missing:
        raise UserbetError("Не удалось определить коэффициенты 1X2")
    return out


def _parse_total(rows: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    pairs: Dict[str, Dict[str, float]] = {}
    for row in rows:
        line = normalize_total_line(row.get("t"))
        if line is None:
            continue
        label = str(row.get("l", "")).strip().lower()
        if label not in ("o", "u"):
            continue
        try:
            odds = round_odds(float(row["v"]))
        except (KeyError, TypeError, ValueError):
            continue
        key = _line_key(line)
        pairs.setdefault(key, {})[label] = odds
    best_key: Optional[str] = None
    best_diff = float("inf")
    for key, sides in pairs.items():
        if "o" not in sides or "u" not in sides:
            continue
        diff = abs(sides["o"] - sides["u"])
        if diff < best_diff:
            best_diff = diff
            best_key = key
    if best_key is None:
        raise UserbetError("Не удалось определить основной тотал")
    chosen = pairs[best_key]
    return {
        "closing_total_line": float(best_key),
        "over_odds": chosen["o"],
        "under_odds": chosen["u"],
    }


def _parse_ah(rows: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    pairs: Dict[str, Dict[str, float]] = {}
    for row in rows:
        line = normalize_handicap(row.get("h"))
        if line is None:
            continue
        label = str(row.get("l", "")).strip().lower()
        if label not in ("1", "2"):
            continue
        try:
            odds = round_odds(float(row["v"]))
        except (KeyError, TypeError, ValueError):
            continue
        key = _line_key(line)
        pairs.setdefault(key, {})[label] = odds
    best_key: Optional[str] = None
    best_diff = float("inf")
    for key, sides in pairs.items():
        if "1" not in sides or "2" not in sides:
            continue
        diff = abs(sides["1"] - sides["2"])
        if diff < best_diff:
            best_diff = diff
            best_key = key
    if best_key is None:
        raise UserbetError("Не удалось определить основную фору")
    chosen = pairs[best_key]
    return {
        "closing_ah_home": float(best_key),
        "ah_home_odds": chosen["1"],
        "ah_away_odds": chosen["2"],
    }


def parse_odds_response(rows: Any) -> Dict[str, float]:
    if not isinstance(rows, list):
        raise UserbetError("Внешний сайт не вернул коэффициенты по матчу")
    groups = _group_markets(rows)
    payload: Dict[str, float] = {}
    payload.update(_parse_1x2(groups["1x2"]))
    payload.update(_parse_total(groups["total"]))
    payload.update(_parse_ah(groups["ah"]))
    extra = set(payload) - ODDS_PATCH_FIELDS
    if extra:
        raise UserbetError("Не удалось определить коэффициенты 1X2")
    return payload


def odds_to_ui_edits(odds: Mapping[str, float]) -> Dict[str, str]:
    edits: Dict[str, str] = {}
    for field, val in odds.items():
        ui_col = ODDS_FIELD_TO_UI_COL.get(field)
        if ui_col is None:
            continue
        text = f"{float(val):g}".replace(".", ",")
        edits[ui_col] = text
    return edits


def _decode_response_payload(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise UserbetError("Внешний сайт не вернул коэффициенты по матчу")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise UserbetError("Не удалось получить данные с внешнего сайта") from None


def fetch_odds(external_match_id: str, *, timeout: float = 30.0) -> Dict[str, float]:
    ext_id = str(external_match_id or "").strip()
    if not ext_id:
        raise UserbetError("Введите id матча с сайта неизвестного мужика")
    body = urllib.parse.urlencode({"id_fixture": ext_id}).encode("utf-8")
    req = urllib.request.Request(
        USERBET_ODDS_URL,
        data=body,
        method="POST",
        headers=dict(USERBET_REQUEST_HEADERS),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError):
        raise UserbetError("Не удалось получить данные с внешнего сайта") from None
    return parse_odds_response(_decode_response_payload(raw))
