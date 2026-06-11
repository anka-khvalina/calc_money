"""
Хранилище истории матчей прошлых сезонов по основным лигам.

Назначение:
  * импортировать историю сезона (CSV с коэффициентами 1/X/2, голами, результатами)
    в единое каноническое хранилище;
  * хранить данные по лигам/сезонам в предсказуемой структуре каталогов;
  * использовать накопленную историю в дальнейших расчётах:
      - приор домашнего преимущества H (с дисконтом старых сезонов и усадкой
        к приору по мере накопления матчей текущего сезона);
      - калибровка логит-модели ничьей px = σ(α + β·X + γ·X²), X = |D|.

Поддерживаемые лиги (ключи хранилища):
    epl, la_liga, bundesliga, serie_a, ligue_1

Структура хранилища (по умолчанию <repo>/data/history):
    data/history/
        index.json                # реестр лиг и сезонов
        epl/2025-26.csv           # канонический CSV сезона
        la_liga/2024-25.csv
        ...

Канонический CSV сезона (заголовок):
    date,home_team,away_team,odds_1,odds_x,odds_2,
    home_goals,away_goals,result,derby,venue_type,comment

Импорт принимает «человеческий» формат таблицы (как в Excel-выгрузке):
    Match Date, Team Home, 1 Odds, X Odds, 2 Odds, Away Team,
    Derby, Venue Type, Home Goals, Away Goals, Result, Comment
а также любые совместимые алиасы колонок (см. _COLUMN_ALIASES).

Зависит только от стандартной библиотеки и модуля team_ranking (тот же пакет).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # запуск как модуль пакета или как отдельный файл
    from . import team_ranking as tr
except ImportError:  # pragma: no cover - прямой запуск python3 app/history_store.py
    import team_ranking as tr


# --------------------------------------------------------------------------- #
# Лиги
# --------------------------------------------------------------------------- #

LEAGUES: Dict[str, str] = {
    "epl": "English Premier League",
    "la_liga": "La Liga",
    "bundesliga": "Bundesliga",
    "serie_a": "Serie A",
    "ligue_1": "Ligue 1",
}

# Приор H (Elo-пункты) на случай отсутствия истории. Ориентиры для топ-лиг
# с поправкой на вековой спад домашнего преимущества.
LEAGUE_DEFAULT_H: Dict[str, float] = {
    "epl": 55.0,
    "la_liga": 60.0,
    "bundesliga": 58.0,
    "serie_a": 62.0,
    "ligue_1": 65.0,
}

# Алиасы названий лиг -> канонический ключ.
_LEAGUE_ALIASES: Dict[str, str] = {}


def _norm(text: str) -> str:
    return "".join(ch for ch in str(text).strip().lower() if ch.isalnum())


def _register_league_aliases() -> None:
    table = {
        "epl": [
            "epl", "eng", "england", "premierleague", "englishpremierleague",
            "apl", "англия", "апл", "premier",
        ],
        "la_liga": [
            "laliga", "la_liga", "spain", "espana", "испания", "лалига",
            "примера", "primera",
        ],
        "bundesliga": [
            "bundesliga", "germany", "германия", "бундеслига", "bl", "buli",
        ],
        "serie_a": [
            "seriea", "serie_a", "italy", "италия", "серияa", "серияа", "calcio",
        ],
        "ligue_1": [
            "ligue1", "ligue_1", "france", "франция", "лига1", "лигуе1", "l1",
        ],
    }
    for key, aliases in table.items():
        _LEAGUE_ALIASES[_norm(key)] = key
        for a in aliases:
            _LEAGUE_ALIASES[_norm(a)] = key


_register_league_aliases()


def normalize_league(name: str) -> str:
    """Привести произвольное имя лиги к каноническому ключу хранилища."""
    key = _LEAGUE_ALIASES.get(_norm(name))
    if key is None:
        raise ValueError(
            f"Неизвестная лига: '{name}'. Доступны: {', '.join(LEAGUES)}"
        )
    return key


def league_title(league_key: str) -> str:
    return LEAGUES.get(league_key, league_key)


# --------------------------------------------------------------------------- #
# Модель матча
# --------------------------------------------------------------------------- #

CANONICAL_HEADER = [
    "date",
    "home_team",
    "away_team",
    "odds_1",
    "odds_x",
    "odds_2",
    "home_goals",
    "away_goals",
    "result",
    "derby",
    "venue_type",
    "comment",
]

# Заголовок для просмотра/экспорта (как в Excel-таблице пользователя)
DISPLAY_HEADER = [
    "Match Date",
    "Team Home",
    "1 Odds",
    "X Odds",
    "2 Odds",
    "Away Team",
    "Derby",
    "Venue Type",
    "Home Goals",
    "Away Goals",
    "Result",
    "Comment",
]


@dataclass(frozen=True)
class HistoricalMatch:
    home_team: str
    away_team: str
    odds_1: float
    odds_x: float
    odds_2: float
    date: str = ""
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None
    result: str = ""  # H / D / A
    derby: str = ""
    venue_type: str = ""
    comment: str = ""

    def to_match_odds(self) -> tr.MatchOdds:
        return tr.MatchOdds(
            home_team=self.home_team,
            away_team=self.away_team,
            odds_1=self.odds_1,
            odds_x=self.odds_x,
            odds_2=self.odds_2,
        )

    def derived_result(self) -> str:
        """Результат H/D/A: берётся из поля result, иначе из голов."""
        if self.result:
            r = self.result.strip().upper()
            if r in ("H", "D", "A"):
                return r
            # частые варианты
            mapping = {"1": "H", "X": "D", "2": "A", "Д": "D", "Х": "D"}
            if r in mapping:
                return mapping[r]
        if self.home_goals is not None and self.away_goals is not None:
            if self.home_goals > self.away_goals:
                return "H"
            if self.home_goals < self.away_goals:
                return "A"
            return "D"
        return ""


# --------------------------------------------------------------------------- #
# Разбор «человеческого» CSV
# --------------------------------------------------------------------------- #

_COLUMN_ALIASES: Dict[str, List[str]] = {
    "date": ["matchdate", "date", "дата", "датаматча"],
    "home_team": [
        "teamhome", "hometeam", "home_team", "home", "team1",
        "хозяева", "командадома", "дома",
    ],
    "away_team": [
        "awayteam", "team_away", "away_team", "away", "team2",
        "гости", "командагостей",
    ],
    "odds_1": ["1odds", "odds1", "odds_1", "p1", "1", "коэфдома", "кэфдома"],
    "odds_x": ["xodds", "oddsx", "odds_x", "px", "x", "коэфничья", "ничья"],
    "odds_2": ["2odds", "odds2", "odds_2", "p2", "2", "коэфгости", "кэфгости"],
    "home_goals": ["homegoals", "home_goals", "hg", "голыдома", "забилидома"],
    "away_goals": ["awaygoals", "away_goals", "ag", "голыгости", "забилигости"],
    "result": ["result", "результат", "ftr", "исход"],
    "derby": ["derby", "дерби"],
    "venue_type": ["venuetype", "venue_type", "venue", "площадка", "типплощадки"],
    "comment": ["comment", "комментарий", "note", "примечание"],
}


def _build_alias_lookup() -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for field_name, aliases in _COLUMN_ALIASES.items():
        for a in aliases:
            lookup[_norm(a)] = field_name
    return lookup


_ALIAS_LOOKUP = _build_alias_lookup()


def _parse_int(text: str) -> Optional[int]:
    if text is None:
        return None
    s = str(text).strip()
    if not s or s in ("-", "—", "?"):
        return None
    try:
        return int(float(s.replace(",", ".")))
    except ValueError:
        return None


def _map_header(header: Sequence[str]) -> Dict[str, int]:
    """field_name -> индекс колонки по заголовку (через нормализованные алиасы)."""
    mapping: Dict[str, int] = {}
    for idx, raw in enumerate(header):
        field_name = _ALIAS_LOOKUP.get(_norm(raw))
        if field_name and field_name not in mapping:
            mapping[field_name] = idx
    return mapping


def _is_blank_row(row: Sequence[str]) -> bool:
    """Строка CSV без данных (только пустые ячейки, кавычки, запятые)."""
    for c in row:
        s = str(c).strip()
        if not s:
            continue
        if s in ('-', '—', '–', '""', "''"):
            continue
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
            s = s[1:-1].strip()
        if s:
            return False
    return True


def _filter_blank_rows(rows: Sequence[Sequence[str]]) -> Tuple[List[List[str]], int]:
    """Убрать пустые строки данных (заголовок сохраняется)."""
    if not rows:
        return [], 0
    header = list(rows[0])
    data = [list(r) for r in rows[1:] if not _is_blank_row(r)]
    skipped = len(rows) - 1 - len(data)
    return [header] + data, skipped


def clean_history_text(text: str) -> Tuple[str, int]:
    """Удалить пустые строки из текста CSV; вернуть очищенный текст и число удалённых."""
    sample = text.strip()
    if not sample:
        return "", 0
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(sample), delimiter=delim))
    cleaned_rows, skipped = _filter_blank_rows(rows)
    if not cleaned_rows:
        return "", skipped
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delim, lineterminator="\n")
    for row in cleaned_rows:
        writer.writerow(row)
    return buf.getvalue(), skipped


def parse_history_csv(path: Path) -> List[HistoricalMatch]:
    """Разобрать CSV истории сезона в «человеческом» или каноническом формате.

    Требуется строка заголовка. Обязательные поля: home_team, away_team,
    odds_1, odds_x, odds_2. Остальные — опциональны.
    """
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = [r for r in csv.reader(f, dialect=dialect)]

    rows, _skipped = _filter_blank_rows(rows)
    return _parse_history_rows(rows, source=str(path))


def parse_history_text(text: str) -> List[HistoricalMatch]:
    """Разобрать историю из текста (вставка из Excel / буфера)."""
    cleaned, _skipped = clean_history_text(text)
    if not cleaned.strip():
        raise ValueError("Текст пуст")
    sample = cleaned.strip()
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(sample), delimiter=delim))
    return _parse_history_rows(rows, source="текст")


def parse_history_text_with_stats(text: str) -> Tuple[List[HistoricalMatch], int]:
    """Как parse_history_text, плюс число удалённых пустых строк."""
    cleaned, skipped = clean_history_text(text)
    if not cleaned.strip():
        raise ValueError("Текст пуст")
    sample = cleaned.strip()
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(sample), delimiter=delim))
    return _parse_history_rows(rows, source="текст"), skipped


def _parse_history_rows(rows: Sequence[Sequence[str]], source: str = "") -> List[HistoricalMatch]:
    if not rows:
        raise ValueError("Нет данных для разбора")

    header = rows[0]
    mapping = _map_header(header)
    required = ["home_team", "away_team", "odds_1", "odds_x", "odds_2"]
    missing = [r for r in required if r not in mapping]
    if missing:
        raise ValueError(
            "В заголовке не найдены обязательные колонки: "
            f"{missing}. Заголовок: {list(header)}. "
            "Нужны команды и коэффициенты 1/X/2 "
            "(алиасы: 'Team Home', '1 Odds', 'X Odds', '2 Odds', …)."
        )

    def cell(row: Sequence[str], field_name: str) -> str:
        idx = mapping.get(field_name)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    matches: List[HistoricalMatch] = []
    for line_no, row in enumerate(rows[1:], start=2):
        home = cell(row, "home_team")
        away = cell(row, "away_team")
        if not home or not away:
            continue
        try:
            odds_1 = tr._parse_float(cell(row, "odds_1"))
            odds_x = tr._parse_float(cell(row, "odds_x"))
            odds_2 = tr._parse_float(cell(row, "odds_2"))
        except ValueError as exc:
            raise ValueError(f"Строка {line_no}: {exc}") from exc
        if odds_1 <= 1 or odds_x <= 1 or odds_2 <= 1:
            raise ValueError(
                f"Строка {line_no}: коэффициенты должны быть > 1 "
                f"({odds_1}, {odds_x}, {odds_2})"
            )
        matches.append(
            HistoricalMatch(
                home_team=home,
                away_team=away,
                odds_1=odds_1,
                odds_x=odds_x,
                odds_2=odds_2,
                date=cell(row, "date"),
                home_goals=_parse_int(cell(row, "home_goals")),
                away_goals=_parse_int(cell(row, "away_goals")),
                result=cell(row, "result"),
                derby=cell(row, "derby"),
                venue_type=cell(row, "venue_type"),
                comment=cell(row, "comment"),
            )
        )

    if not matches:
        raise ValueError(f"Не найдено ни одного валидного матча ({source})")
    return matches


# --------------------------------------------------------------------------- #
# Каталоги хранилища
# --------------------------------------------------------------------------- #


def data_root() -> Path:
    """Корень хранилища истории.

    Приоритет: FAIR_ODDS_DATA → рядом с exe → <repo>/data/history.
    """
    env = os.environ.get("FAIR_ODDS_DATA")
    if env:
        return Path(env).expanduser()
    try:
        from .runtime_paths import history_data_root
    except ImportError:  # pragma: no cover
        from runtime_paths import history_data_root
    if getattr(sys, "frozen", False):
        return history_data_root()
    return Path(__file__).resolve().parent.parent / "data" / "history"


def league_dir(league_key: str) -> Path:
    return data_root() / league_key


def season_path(league_key: str, season: str) -> Path:
    return league_dir(league_key) / f"{_safe_season(season)}.csv"


def _safe_season(season: str) -> str:
    s = season.strip().replace("/", "-").replace(" ", "")
    if not s:
        raise ValueError("Пустое имя сезона")
    return s


def index_path() -> Path:
    return data_root() / "index.json"


# --------------------------------------------------------------------------- #
# Запись / чтение канонического CSV
# --------------------------------------------------------------------------- #


def write_canonical_csv(path: Path, matches: Sequence[HistoricalMatch]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CANONICAL_HEADER)
        for m in matches:
            writer.writerow(
                [
                    m.date,
                    m.home_team,
                    m.away_team,
                    f"{m.odds_1:g}",
                    f"{m.odds_x:g}",
                    f"{m.odds_2:g}",
                    "" if m.home_goals is None else m.home_goals,
                    "" if m.away_goals is None else m.away_goals,
                    m.derived_result(),
                    m.derby,
                    m.venue_type,
                    m.comment,
                ]
            )


def load_season(league: str, season: str) -> List[HistoricalMatch]:
    key = normalize_league(league)
    path = season_path(key, season)
    if not path.exists():
        raise FileNotFoundError(
            f"Сезон не найден в хранилище: {league_title(key)} / {season} ({path})"
        )
    return parse_history_csv(path)


def matches_to_csv_text(
    matches: Sequence[HistoricalMatch], display_format: bool = True
) -> str:
    """Сериализовать матчи в CSV-текст для просмотра/редактирования."""
    import io as _io

    buf = _io.StringIO()
    if display_format:
        writer = csv.writer(buf)
        writer.writerow(DISPLAY_HEADER)
        for m in matches:
            writer.writerow(
                [
                    m.date,
                    m.home_team,
                    f"{m.odds_1:g}",
                    f"{m.odds_x:g}",
                    f"{m.odds_2:g}",
                    m.away_team,
                    m.derby,
                    m.venue_type,
                    "" if m.home_goals is None else m.home_goals,
                    "" if m.away_goals is None else m.away_goals,
                    m.derived_result(),
                    m.comment,
                ]
            )
    else:
        writer = csv.writer(buf)
        writer.writerow(CANONICAL_HEADER)
        for m in matches:
            writer.writerow(
                [
                    m.date,
                    m.home_team,
                    m.away_team,
                    f"{m.odds_1:g}",
                    f"{m.odds_x:g}",
                    f"{m.odds_2:g}",
                    "" if m.home_goals is None else m.home_goals,
                    "" if m.away_goals is None else m.away_goals,
                    m.derived_result(),
                    m.derby,
                    m.venue_type,
                    m.comment,
                ]
            )
    return buf.getvalue().rstrip("\n") + "\n"


def view_season_text(league: str, season: str) -> str:
    """Текст CSV сохранённого сезона для просмотра."""
    matches = load_season(league, season)
    return matches_to_csv_text(matches, display_format=True)


def list_leagues() -> List[str]:
    root = data_root()
    if not root.exists():
        return []
    found = []
    for key in LEAGUES:
        if (root / key).is_dir() and any((root / key).glob("*.csv")):
            found.append(key)
    return found


def list_seasons(league: str) -> List[str]:
    """Сезоны лиги, отсортированные по возрастанию имени (старые -> новые)."""
    key = normalize_league(league)
    d = league_dir(key)
    if not d.is_dir():
        return []
    seasons = sorted(p.stem for p in d.glob("*.csv"))
    return seasons


@dataclass(frozen=True)
class SeasonInfo:
    league: str
    league_title: str
    season: str
    matches: int
    imported_at: str
    path: Path


def list_all_seasons() -> List[SeasonInfo]:
    """Все сохранённые сезоны всех лиг (для GUI/отчётов)."""
    index = _load_index()
    out: List[SeasonInfo] = []
    leagues_meta = index.get("leagues", {})
    for key in LEAGUES:
        for season in list_seasons(key):
            meta = leagues_meta.get(key, {}).get("seasons", {}).get(season, {})
            try:
                cnt = len(load_season(key, season))
            except (ValueError, FileNotFoundError):
                cnt = int(meta.get("matches", 0))
            out.append(
                SeasonInfo(
                    league=key,
                    league_title=league_title(key),
                    season=season,
                    matches=cnt,
                    imported_at=str(meta.get("imported_at", "")),
                    path=season_path(key, season),
                )
            )
    out.sort(key=lambda s: (s.league, s.season))
    return out


def format_league_options() -> List[Tuple[str, str]]:
    """Пары (отображаемое имя, ключ) для combobox."""
    return [(LEAGUES[k], k) for k in LEAGUES]


# --------------------------------------------------------------------------- #
# Реестр (index.json)
# --------------------------------------------------------------------------- #


def _load_index() -> dict:
    p = index_path()
    if not p.exists():
        return {"leagues": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"leagues": {}}


def _save_index(index: dict) -> None:
    p = index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_index(league_key: str, season: str, count: int) -> None:
    index = _load_index()
    leagues = index.setdefault("leagues", {})
    entry = leagues.setdefault(
        league_key, {"name": league_title(league_key), "seasons": {}}
    )
    entry["name"] = league_title(league_key)
    entry.setdefault("seasons", {})[_safe_season(season)] = {
        "matches": count,
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_index(index)


# --------------------------------------------------------------------------- #
# Импорт
# --------------------------------------------------------------------------- #


@dataclass
class ImportResult:
    league: str
    season: str
    matches: int
    path: Path
    replaced: bool = False
    previous_matches: int = 0
    blank_rows_removed: int = 0


def season_exists(league: str, season: str) -> bool:
    """Есть ли уже сохранённый сезон в хранилище."""
    key = normalize_league(league)
    safe = _safe_season(season)
    return season_path(key, safe).exists()


def import_season(league: str, season: str, csv_path: Path) -> ImportResult:
    """Импортировать CSV сезона в хранилище (перезаписывает существующий)."""
    raw = Path(csv_path).read_text(encoding="utf-8-sig")
    cleaned, blank_removed = clean_history_text(raw)
    if not cleaned.strip():
        raise ValueError("Файл пуст или содержит только пустые строки")
    sample = cleaned.strip()
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(sample), delimiter=delim))
    matches = _parse_history_rows(rows, source=str(csv_path))
    res = import_season_matches(league, season, matches)
    return ImportResult(
        league=res.league,
        season=res.season,
        matches=res.matches,
        path=res.path,
        replaced=res.replaced,
        previous_matches=res.previous_matches,
        blank_rows_removed=blank_removed,
    )


def import_season_matches(
    league: str, season: str, matches: Sequence[HistoricalMatch]
) -> ImportResult:
    """Сохранить уже разобранные матчи сезона в хранилище."""
    if not matches:
        raise ValueError("Нет матчей для импорта")
    key = normalize_league(league)
    try:
        from . import team_registry as _tr
    except ImportError:  # pragma: no cover
        import team_registry as _tr
    _tr.validate_matches_teams(key, matches)
    safe = _safe_season(season)
    dest = season_path(key, safe)
    replaced = dest.exists()
    previous_matches = 0
    if replaced:
        try:
            previous_matches = len(load_season(key, safe))
        except (ValueError, FileNotFoundError):
            previous_matches = 0
    write_canonical_csv(dest, matches)
    _update_index(key, safe, len(matches))
    return ImportResult(
        league=key,
        season=safe,
        matches=len(matches),
        path=dest,
        replaced=replaced,
        previous_matches=previous_matches,
    )


# --------------------------------------------------------------------------- #
# Расчёты на истории
# --------------------------------------------------------------------------- #


@dataclass
class HomeAdvantageEstimate:
    league: str
    h_prior: float
    h_final: float
    confidence: str
    from_default: bool
    seasons_used: List[Tuple[str, float, float]] = field(default_factory=list)  # (season, H, weight)
    current_matches: int = 0
    current_h: Optional[float] = None


def home_advantage_prior(
    league: str,
    current_matches: Optional[Sequence[tr.MatchOdds]] = None,
    kappa: float = 30.0,
    xi: float = 0.5,
    robust: bool = True,
) -> HomeAdvantageEstimate:
    """Оценка H по истории лиги с усадкой к приору.

    1. По каждому хранимому сезону считаем H (робастный МНК).
    2. Приор = экспоненциально-взвешенное среднее по сезонам
       (свежие сезоны весомее: вес exp(-xi*k), k=0 для самого свежего).
    3. Если есть матчи текущего сезона (current_matches), усаживаем:
         H = w * H_current + (1-w) * H_prior,  w = m / (m + kappa).
       При m <= 2 берём только приор.
    """
    key = normalize_league(league)
    seasons = list_seasons(key)  # старые -> новые

    season_estimates: List[Tuple[str, float]] = []
    for season in seasons:
        try:
            matches = load_season(key, season)
            res = tr.build_ranking([m.to_match_odds() for m in matches], robust=robust)
            season_estimates.append((season, res.home_advantage))
        except (ValueError, FileNotFoundError):
            continue

    from_default = False
    used: List[Tuple[str, float, float]] = []
    if season_estimates:
        # самый свежий сезон в конце списка -> k=0
        ordered = list(reversed(season_estimates))  # новые -> старые
        num = 0.0
        den = 0.0
        for k, (season, h) in enumerate(ordered):
            w = math.exp(-xi * k)
            num += w * h
            den += w
            used.append((season, h, w))
        h_prior = num / den
    else:
        h_prior = LEAGUE_DEFAULT_H.get(key, 60.0)
        from_default = True

    current_h: Optional[float] = None
    m = len(current_matches) if current_matches else 0
    if current_matches and m > 2:
        try:
            current_h = tr.build_ranking(list(current_matches), robust=robust).home_advantage
            w = m / (m + kappa)
            h_final = w * current_h + (1 - w) * h_prior
            confidence = "low" if m < 10 else "medium" if m < 30 else "high"
        except ValueError:
            h_final = h_prior
            confidence = "low"
    else:
        h_final = h_prior
        confidence = "default" if from_default else "low"

    return HomeAdvantageEstimate(
        league=key,
        h_prior=h_prior,
        h_final=h_final,
        confidence=confidence,
        from_default=from_default,
        seasons_used=used,
        current_matches=m,
        current_h=current_h,
    )


# --------------------------------------------------------------------------- #
# Draw-модель: логит-регрессия px по X = |D|
#
#   X_i = |R_home − R_away + H|   (R, H — Shin-МНК сезона, D-шкала 400·log10)
#   Y_i = ln(px_i / (1 − px_i)),  px_i — Shin de-vig ничьей матча
#   МНК:      Y = α + β·X + γ·X²
#   Прогноз:  px = 1 / (1 + exp(−(α + β·X + γ·X²)))
# --------------------------------------------------------------------------- #

_DRAW_PX_LO = 0.04
_DRAW_PX_HI = 0.45
_DRAW_DEFAULT_ALPHA = -0.8954  # logit(0.29): px ≈ 29 % при X = 0
_DRAW_DEFAULT_BETA = -0.0028   # на пункт D-шкалы
_DRAW_DEFAULT_GAMMA = 0.0
_DRAW_MIN_POINTS = 5


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ez = math.exp(x)
    return ez / (1.0 + ez)


@dataclass
class DrawModel:
    """Логит-модель ничьей: px = σ(α + β·X + γ·X²), X = |D| (D-шкала)."""

    alpha: float
    beta: float
    gamma: float = 0.0
    x_max: float = 400.0  # кламп X: не экстраполируем квадратику за данные
    lo: float = _DRAW_PX_LO
    hi: float = _DRAW_PX_HI
    n: int = 0
    source: str = "market"  # market | default

    def logit(self, d: float) -> float:
        x = min(abs(d), self.x_max)
        return self.alpha + self.beta * x + self.gamma * x * x

    def px(self, d: float) -> float:
        return max(self.lo, min(self.hi, _sigmoid(self.logit(d))))

    def forecast_px(
        self, d_target: float, s1: float = 1.0, s2: float = 1.0
    ) -> Tuple[float, List[str]]:
        """px для прогноза + строки расчёта (s1/s2 не используются)."""
        x_raw = abs(d_target)
        x = min(x_raw, self.x_max)
        logit = self.alpha + self.beta * x + self.gamma * x * x
        raw = _sigmoid(logit)
        px = max(self.lo, min(self.hi, raw))

        def _f(value: float, digits: int = 4) -> str:
            return f"{value:.{digits}f}".replace(".", ",")

        lines = [
            f"  Логит-модель: α={_f(self.alpha)}, β={_f(self.beta, 6)}, "
            f"γ={_f(self.gamma, 8)} (n={self.n}, источник={self.source})",
            f"  X = |EffectiveD| = {_f(x_raw, 1)}"
            + (f" → кламп до X_max={_f(self.x_max, 0)}" if x_raw > self.x_max else ""),
            f"  logit = α + β·X + γ·X² = {_f(logit)}",
            f"  px = 1/(1+e^(−logit)) = {_f(raw * 100, 2)} %",
        ]
        if px != raw:
            lines.append(
                f"  Кламп [{_f(self.lo * 100, 0)} %; {_f(self.hi * 100, 0)} %] → px = {_f(px * 100, 2)} %"
            )
        else:
            lines.append(f"  Итого px (ничья) = {_f(px * 100, 2)} %")
        return px, lines


def _default_draw_model(n: int = 0) -> DrawModel:
    return DrawModel(
        alpha=_DRAW_DEFAULT_ALPHA,
        beta=_DRAW_DEFAULT_BETA,
        gamma=_DRAW_DEFAULT_GAMMA,
        x_max=400.0,
        n=n,
        source="default",
    )


def _season_draw_points(matches: Sequence["HistoricalMatch"]) -> List[Tuple[float, float]]:
    """Точки калибровки сезона: (X = |R_h − R_a + H|, Y = logit(px Shin))."""
    try:
        from . import devig_shin as ds
    except ImportError:  # pragma: no cover
        import devig_shin as ds

    teams = sorted({m.home_team for m in matches} | {m.away_team for m in matches})
    if len(teams) < 2:
        return []
    idx = {t: i for i, t in enumerate(teams)}
    p = len(teams) + 1  # + H
    rows: List[Tuple[List[float], float, float]] = []
    devigs: List[Tuple["HistoricalMatch", float]] = []
    for m in matches:
        try:
            p1, px, p2 = ds.shin_devig(m.odds_1, m.odds_x, m.odds_2)
        except ValueError:
            continue
        if p1 <= 0 or p2 <= 0 or not (0.0 < px < 1.0):
            continue
        d = 400.0 * math.log10(p1 / p2)
        coeff = [0.0] * p
        coeff[idx[m.home_team]] = 1.0
        coeff[idx[m.away_team]] = -1.0
        coeff[-1] = 1.0
        rows.append((coeff, d, 1.0))
        devigs.append((m, px))
    if len(rows) < 3:
        return []
    gauge = [1.0] * len(teams) + [0.0]
    rows.append((gauge, 0.0, tr.GAUGE_WEIGHT))
    try:
        sol = tr._solve_weighted(rows, p)
    except ValueError:
        return []
    h = sol[-1]
    points: List[Tuple[float, float]] = []
    for m, px in devigs:
        d_fit = sol[idx[m.home_team]] - sol[idx[m.away_team]] + h
        points.append((abs(d_fit), math.log(px / (1.0 - px))))
    return points


def calibrate_draw_model(league: str) -> DrawModel:
    """Логит-калибровка ничьей по истории лиги.

    По каждому сезону: Shin-МНК → X_i = |R_home − R_away + H|;
    Y_i = ln(px_i/(1−px_i)) из Shin de-vig. МНК: Y = α + β·X + γ·X².
    """
    key = normalize_league(league)
    pts: List[Tuple[float, float]] = []
    for season in list_seasons(key):
        try:
            matches = load_season(key, season)
        except (ValueError, FileNotFoundError):
            continue
        pts.extend(_season_draw_points(matches))

    n = len(pts)
    if n < _DRAW_MIN_POINTS:
        return _default_draw_model(n)

    # МНК Y = α + β·X + γ·X²: нормальные уравнения 3×3.
    s = [0.0] * 5  # суммы X^0..X^4
    t = [0.0] * 3  # суммы Y·X^0..X^2
    for x, y in pts:
        xp = 1.0
        for k in range(5):
            s[k] += xp
            if k < 3:
                t[k] += y * xp
            xp *= x
    a_mat = [
        [s[0], s[1], s[2]],
        [s[1], s[2], s[3]],
        [s[2], s[3], s[4]],
    ]
    try:
        alpha, beta, gamma = tr._gaussian_solve(a_mat, list(t))
    except ValueError:
        return _default_draw_model(n)

    if not (0.10 <= _sigmoid(alpha) <= 0.45):
        return _default_draw_model(n)
    x_max = max(100.0, max(x for x, _ in pts))
    return DrawModel(alpha=alpha, beta=beta, gamma=gamma, x_max=x_max, n=n, source="market")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _cmd_import(args: argparse.Namespace) -> None:
    res = import_season(args.league, args.season, Path(args.input))
    note = f" (перезаписано, было {res.previous_matches})" if res.replaced else " (новый)"
    print(
        f"Импортировано: {league_title(res.league)} / {res.season} — "
        f"{res.matches} матчей{note} -> {res.path}"
    )


def _cmd_list(args: argparse.Namespace) -> None:
    if args.league:
        key = normalize_league(args.league)
        seasons = list_seasons(key)
        print(f"{league_title(key)} ({key}):")
        if not seasons:
            print("  (нет сохранённых сезонов)")
        for s in seasons:
            try:
                cnt = len(load_season(key, s))
            except (ValueError, FileNotFoundError):
                cnt = 0
            print(f"  {s}: {cnt} матчей")
        return

    leagues = list_leagues()
    print(f"Хранилище: {data_root()}")
    if not leagues:
        print("  (пусто — импортируйте историю командой import)")
        return
    for key in leagues:
        seasons = list_seasons(key)
        print(f"  {league_title(key)} ({key}): {', '.join(seasons)}")


def _cmd_prior(args: argparse.Namespace) -> None:
    current = None
    if args.current:
        current = [m.to_match_odds() for m in parse_history_csv(Path(args.current))]
    est = home_advantage_prior(
        args.league, current_matches=current, kappa=args.kappa, robust=not args.no_robust
    )
    print(f"Лига: {league_title(est.league)} ({est.league})")
    if est.from_default:
        print(f"  Истории нет — приор по умолчанию: H = {est.h_prior:.1f}")
    else:
        print("  Сезоны (свежие весомее):")
        for season, h, w in est.seasons_used:
            print(f"    {season}: H={h:.1f}  вес={w:.3f}")
        print(f"  H_prior (взвеш.) = {est.h_prior:.2f}")
    if est.current_matches > 2 and est.current_h is not None:
        w = est.current_matches / (est.current_matches + args.kappa)
        print(
            f"  Текущий сезон: {est.current_matches} матчей, H_current={est.current_h:.2f}, "
            f"вес w={w:.3f}"
        )
    print(f"  ИТОГ H = {est.h_final:.2f}  (мультипликатор {10 ** (est.h_final / 400):.4f}); "
          f"уверенность: {est.confidence}")


def _cmd_draw_model(args: argparse.Namespace) -> None:
    dm = calibrate_draw_model(args.league)
    key = normalize_league(args.league)
    print(f"Лига: {league_title(key)} ({key})")
    print(
        f"  draw-модель px = σ({dm.alpha:.4f} + ({dm.beta:.6f})·X + ({dm.gamma:.8f})·X²), "
        f"X = |D| ≤ {dm.x_max:.0f}, кламп [{dm.lo}; {dm.hi}]"
    )
    print(f"  источник: {dm.source}, наблюдений: {dm.n}")
    print("  Примеры:")
    for d in (0, 50, 100, 200, 300):
        print(f"    |d|={d:>3}: px={dm.px(d):.3f}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Хранилище истории матчей прошлых сезонов (EPL, La Liga, Bundesliga, Serie A, Ligue 1)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_imp = sub.add_parser("import", help="Импортировать CSV сезона в хранилище.")
    p_imp.add_argument("--league", required=True, help="Лига (EPL, La Liga, Bundesliga, Serie A, Ligue 1).")
    p_imp.add_argument("--season", required=True, help="Сезон, напр. 2024-25.")
    p_imp.add_argument("--input", required=True, help="Путь к CSV истории.")
    p_imp.set_defaults(func=_cmd_import)

    p_list = sub.add_parser("list", help="Показать лиги/сезоны в хранилище.")
    p_list.add_argument("--league", help="Показать сезоны конкретной лиги.")
    p_list.set_defaults(func=_cmd_list)

    p_prior = sub.add_parser("prior", help="Оценка приора H по истории лиги.")
    p_prior.add_argument("--league", required=True)
    p_prior.add_argument("--current", help="CSV матчей текущего сезона (для усадки).")
    p_prior.add_argument("--kappa", type=float, default=30.0, help="Константа усадки (по умолч. 30).")
    p_prior.add_argument("--no-robust", action="store_true", help="Отключить робастный МНК.")
    p_prior.set_defaults(func=_cmd_prior)

    p_draw = sub.add_parser("draw-model", help="Калибровать draw-модель px(d) по истории.")
    p_draw.add_argument("--league", required=True)
    p_draw.set_defaults(func=_cmd_draw_model)

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
