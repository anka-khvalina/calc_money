"""
Хранилище истории матчей прошлых сезонов по основным лигам.

Назначение:
  * импортировать историю сезона (CSV с коэффициентами 1/X/2, голами, результатами)
    в единое каноническое хранилище;
  * хранить данные по лигам/сезонам в предсказуемой структуре каталогов;
  * использовать накопленную историю в дальнейших расчётах:
      - приор домашнего преимущества H (с дисконтом старых сезонов и усадкой
        к приору по мере накопления матчей текущего сезона);
      - калибровка draw-модели px(d) по фактическим результатам.

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

    Приоритет: переменная окружения FAIR_ODDS_DATA, иначе <repo>/data/history.
    """
    env = os.environ.get("FAIR_ODDS_DATA")
    if env:
        return Path(env).expanduser()
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
    try:
        from . import team_registry as _tr
    except ImportError:  # pragma: no cover
        import team_registry as _tr
    _tr.sync_from_matches(matches)
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


# Минимальный наклон px(d): при большом |d| ничья должна снижаться.
_DRAW_SLOPE_FLOOR = -0.0010

# Плавный нижний пол px для фаворитов: линейная модель уходит в 6%, рынок — ~12–17%.
_DRAW_PX_FLOOR_D_START = 100.0
_DRAW_PX_FLOOR_D_MID = 250.0
_DRAW_PX_FLOOR_D_FULL = 350.0
_DRAW_PX_LO_BASE = 0.06
_DRAW_PX_LO_MID = 0.13
_DRAW_PX_LO_FULL = 0.15


def draw_px_lower_bound(abs_d: float) -> float:
    """Нижняя граница px при прогнозе: растёт с |D| для явных фаворитов."""
    ad = abs(abs_d)
    if ad <= _DRAW_PX_FLOOR_D_START:
        return _DRAW_PX_LO_BASE
    if ad <= _DRAW_PX_FLOOR_D_MID:
        t = (ad - _DRAW_PX_FLOOR_D_START) / (_DRAW_PX_FLOOR_D_MID - _DRAW_PX_FLOOR_D_START)
        return _DRAW_PX_LO_BASE + t * (_DRAW_PX_LO_MID - _DRAW_PX_LO_BASE)
    if ad <= _DRAW_PX_FLOOR_D_FULL:
        t = (ad - _DRAW_PX_FLOOR_D_MID) / (_DRAW_PX_FLOOR_D_FULL - _DRAW_PX_FLOOR_D_MID)
        return _DRAW_PX_LO_MID + t * (_DRAW_PX_LO_FULL - _DRAW_PX_LO_MID)
    return _DRAW_PX_LO_FULL


def forecast_draw_px(
    draw: DrawModel,
    d_target: float,
    s1: float = 1.0,
    s2: float = 1.0,
) -> Tuple[float, List[str]]:
    """px(d) для прогноза матча: линейная модель + пол по |D| + cap по s1/s2."""
    lines: List[str] = []
    dm = draw.for_match_forecast()
    px_lin = dm.px(d_target)
    lines.append(
        f"  px(d) по модели: a={dm.a:.4f}, b={dm.b:.6f}, n={dm.n}, источник={dm.source}"
    )
    lines.append(
        f"  px_mod = a + b·|d| = {dm.a:.4f} + ({dm.b:.6f})·|{abs(d_target):.1f}| "
        f"→ {px_lin * 100:.2f} %"
    )
    lo_eff = draw_px_lower_bound(d_target)
    lines.append(
        f"  px_floor(|d|): плавный пол { _DRAW_PX_LO_BASE * 100:.0f}–"
        f"{_DRAW_PX_LO_FULL * 100:.0f} % → {lo_eff * 100:.2f} %"
    )
    px = max(px_lin, lo_eff)
    if px > px_lin:
        lines.append(f"  Берём max(px_mod, px_floor) = {px * 100:.2f} %")
    px_fav: Optional[float] = None
    if s2 > 1e-12 and s1 > 0:
        log_ratio = math.log10(s1 / s2)
        px_fav = max(0.18, min(0.30, 0.265 - 0.06 * abs(log_ratio)))
        lines.append(
            f"  px_cap (фаворит по s1/s2) = 0,265 − 0,06·|log₁₀(s₁/s₂)| "
            f"→ {px_fav * 100:.2f} %"
        )
        if px > px_fav:
            px = px_fav
            lines.append(f"  Ограничение сверху: px = {px * 100:.2f} %")
    px = max(lo_eff, min(dm.hi, px))
    lines.append(f"  Итого px (ничья) = {px * 100:.2f} %")
    return px, lines


@dataclass
class DrawModel:
    """Линейная модель ничьей px(d) = clamp(a + b*|d|, lo, hi)."""

    a: float
    b: float
    lo: float = 0.06
    hi: float = 0.34
    n: int = 0
    source: str = "results"  # results | market | default

    def px(self, d: float) -> float:
        val = self.a + self.b * abs(d)
        return max(self.lo, min(self.hi, val))

    def for_match_forecast(self) -> "DrawModel":
        """Скорректировать калибровку для прогноза: ничья падает при большом |d|."""
        if self.source == "default":
            return self
        b = self.b if self.b <= _DRAW_SLOPE_FLOOR else _DRAW_SLOPE_FLOOR
        return DrawModel(
            a=self.a, b=b, lo=self.lo, hi=self.hi, n=self.n, source=self.source
        )

    def forecast_px(self, d_target: float, s1: float = 1.0, s2: float = 1.0) -> Tuple[float, List[str]]:
        """px(d) для прогноза с плавным полом по |D| (см. forecast_draw_px)."""
        return forecast_draw_px(self, d_target, s1, s2)


def calibrate_draw_model(league: str) -> DrawModel:
    """Калибровать draw-модель px(|d|) по всей истории лиги.

    Предпочтение — по фактическим результатам (доля ничьих в зависимости от
    |D_market|). Если результатов нет — по de-vig вероятности ничьей.
    Линейная регрессия y ~ a + b*|d| методом наименьших квадратов.
    """
    key = normalize_league(league)
    xs: List[float] = []
    ys: List[float] = []
    source = "results"
    have_results = False

    for season in list_seasons(key):
        try:
            matches = load_season(key, season)
        except (ValueError, FileNotFoundError):
            continue
        for m in matches:
            try:
                d, _eh, _ea = tr._market_diff_with_scores(m.to_match_odds())
            except ValueError:
                continue
            res = m.derived_result()
            if res:
                have_results = True
                xs.append(abs(d))
                ys.append(1.0 if res == "D" else 0.0)

    if not have_results:
        # fallback: de-vig px
        source = "market"
        xs, ys = [], []
        for season in list_seasons(key):
            try:
                matches = load_season(key, season)
            except (ValueError, FileNotFoundError):
                continue
            for m in matches:
                mo = m.to_match_odds()
                try:
                    d, _eh, _ea = tr._market_diff_with_scores(mo)
                except ValueError:
                    continue
                overround = 1 / mo.odds_1 + 1 / mo.odds_x + 1 / mo.odds_2
                px = (1 / mo.odds_x) / overround
                xs.append(abs(d))
                ys.append(px)

    n = len(xs)
    if n < 5:
        return DrawModel(a=0.26, b=-0.0006, n=n, source="default")

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    b = (sxy / sxx) if sxx > 1e-9 else 0.0
    a = mean_y - b * mean_x
    return DrawModel(a=a, b=b, n=n, source=source)


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
    print(f"  draw-модель px(d) = clamp({dm.a:.4f} + ({dm.b:.6f})*|d|, {dm.lo}, {dm.hi})")
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
