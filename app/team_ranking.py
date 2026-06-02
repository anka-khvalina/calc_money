"""
Рейтинг команд по рыночным коэффициентам 1X2 (одна лига, один сезон).

Идея:
  1) По каждому матчу снимаем маржу (de-vig) с коэффициентов 1/X/2.
  2) Считаем expected score:
       E_home = p1 + 0.5 * px
       E_away = p2 + 0.5 * px
  3) Переводим в рыночную рейтинговую разницу:
       D_market = 400 * log10(E_home / E_away)
  4) Для каждого матча формируем уравнение:
       R_home - R_away + H = D_market
     где R_* — рейтинги команд, H — домашнее преимущество.
  5) Решаем систему в МНК с нормировкой sum(R_team)=0.

Выход:
  - рейтинг команд;
  - коэффициент силы team_strength = 10^(R/400);
  - оценка домашнего преимущества H и его мультипликатор.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class MatchOdds:
    home_team: str
    away_team: str
    odds_1: float
    odds_x: float
    odds_2: float


@dataclass(frozen=True)
class TeamRating:
    team: str
    rating: float
    strength_coef: float


@dataclass(frozen=True)
class RankingResult:
    teams: List[TeamRating]
    home_advantage: float
    home_advantage_coef: float
    rmse: float
    matches_count: int


def _norm_header(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum())


def _parse_float(text: str) -> float:
    if text is None:
        raise ValueError("Пустое числовое поле")
    value = text.strip().replace(",", ".")
    if not value:
        raise ValueError("Пустое числовое поле")
    return float(value)


def _detect_columns(fieldnames: Sequence[str]) -> Dict[str, str]:
    if not fieldnames:
        raise ValueError("CSV без заголовков")

    raw_by_norm = {_norm_header(h): h for h in fieldnames}

    def choose(aliases: Iterable[str], label: str) -> str:
        for a in aliases:
            key = _norm_header(a)
            if key in raw_by_norm:
                return raw_by_norm[key]
        raise ValueError(f"Не найдена колонка '{label}' в CSV: {list(fieldnames)}")

    home_col = choose(
        [
            "home_team",
            "home",
            "team1",
            "homeTeam",
            "команда_дома",
            "хозяева",
            "дома",
        ],
        "home_team",
    )
    away_col = choose(
        [
            "away_team",
            "away",
            "team2",
            "awayTeam",
            "команда_гостей",
            "гости",
        ],
        "away_team",
    )
    odds1_col = choose(
        [
            "odds_1",
            "odds1",
            "1_odds",
            "1odds",
            "p1",
            "коэф_дома",
            "коэффициент_дома",
        ],
        "odds_1",
    )
    oddsx_col = choose(
        [
            "odds_x",
            "oddsx",
            "x_odds",
            "xodds",
            "x",
            "draw",
            "draw_odds",
            "ничья",
        ],
        "odds_x",
    )
    odds2_col = choose(
        [
            "odds_2",
            "odds2",
            "2_odds",
            "2odds",
            "p2",
            "коэф_гости",
            "коэффициент_гости",
        ],
        "odds_2",
    )
    return {
        "home": home_col,
        "away": away_col,
        "odds1": odds1_col,
        "oddsx": oddsx_col,
        "odds2": odds2_col,
    }


def load_matches_csv(path: Path, encoding: str = "utf-8-sig") -> List[MatchOdds]:
    with path.open("r", encoding=encoding, newline="") as f:
        # Универсальный разбор CSV/semicolon CSV.
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        raw_rows = [row for row in csv.reader(f, dialect=dialect) if any(c.strip() for c in row)]

    if not raw_rows:
        raise ValueError("Файл не содержит матчей")

    def parse_positional(row: Sequence[str], row_idx: int) -> MatchOdds:
        if len(row) < 5:
            raise ValueError(
                f"Строка {row_idx}: нужно минимум 5 колонок (home, away, odds1, oddsX, odds2)"
            )
        home = row[0].strip()
        away = row[1].strip()
        odds_1 = _parse_float(row[2])
        odds_x = _parse_float(row[3])
        odds_2 = _parse_float(row[4])
        if not home or not away:
            raise ValueError(f"Пустое имя команды в строке {row_idx}")
        if home == away:
            raise ValueError(f"Одинаковые команды в строке {row_idx}: {home}")
        if odds_1 <= 1 or odds_x <= 1 or odds_2 <= 1:
            raise ValueError(
                f"Коэффициенты должны быть > 1 (строка {row_idx}: "
                f"{odds_1}, {odds_x}, {odds_2})"
            )
        return MatchOdds(home_team=home, away_team=away, odds_1=odds_1, odds_x=odds_x, odds_2=odds_2)

    # Режим 1: CSV без заголовка (первая строка уже данные home,away,odds1,oddsX,odds2).
    try:
        _parse_float(raw_rows[0][2])
        _parse_float(raw_rows[0][3])
        _parse_float(raw_rows[0][4])
        first_is_data = True
    except Exception:
        first_is_data = False

    if first_is_data:
        matches = []
        for i, row in enumerate(raw_rows, start=1):
            try:
                matches.append(parse_positional(row, i))
            except Exception as exc:
                raise ValueError(f"Ошибка в строке {i}: {exc}") from exc
        return matches

    # Режим 2: CSV с заголовком (канонические или алиасы колонок).
    header = raw_rows[0]
    cols = _detect_columns(header)
    index = {name: idx for idx, name in enumerate(header)}

    def get_cell(row: Sequence[str], col_name: str) -> str:
        idx = index[col_name]
        return row[idx] if idx < len(row) else ""

    matches: List[MatchOdds] = []
    for row_idx, row in enumerate(raw_rows[1:], start=2):
        try:
            home = get_cell(row, cols["home"]).strip()
            away = get_cell(row, cols["away"]).strip()
            odds_1 = _parse_float(get_cell(row, cols["odds1"]))
            odds_x = _parse_float(get_cell(row, cols["oddsx"]))
            odds_2 = _parse_float(get_cell(row, cols["odds2"]))
            matches.append(parse_positional([home, away, str(odds_1), str(odds_x), str(odds_2)], row_idx))
        except Exception as exc:
            raise ValueError(f"Ошибка в строке {row_idx}: {exc}") from exc

    if not matches:
        raise ValueError("Файл не содержит матчей")
    return matches


def _market_rating_diff(match: MatchOdds) -> float:
    p1_raw = 1.0 / match.odds_1
    px_raw = 1.0 / match.odds_x
    p2_raw = 1.0 / match.odds_2
    overround = p1_raw + px_raw + p2_raw
    if overround <= 0:
        raise ValueError("Некорректный overround <= 0")

    p1 = p1_raw / overround
    px = px_raw / overround
    p2 = p2_raw / overround
    e_home = p1 + 0.5 * px
    e_away = p2 + 0.5 * px
    if e_home <= 0 or e_away <= 0:
        raise ValueError("Ожидаемые очки должны быть > 0")
    return 400.0 * math.log10(e_home / e_away)


def _gaussian_solve(a: List[List[float]], b: List[float]) -> List[float]:
    n = len(a)
    # Прямой ход с выбором главного элемента.
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError(
                "Система вырожденная: недостаточно связности матчей для рейтинга"
            )
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            b[col], b[pivot] = b[pivot], b[col]

        pivot_val = a[col][col]
        for j in range(col, n):
            a[col][j] /= pivot_val
        b[col] /= pivot_val

        for row in range(col + 1, n):
            factor = a[row][col]
            if factor == 0:
                continue
            for j in range(col, n):
                a[row][j] -= factor * a[col][j]
            b[row] -= factor * b[col]

    # Обратный ход.
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = b[i] - sum(a[i][j] * x[j] for j in range(i + 1, n))
    return x


def _solve_least_squares(rows: Sequence[Tuple[List[float], float]], p: int) -> List[float]:
    ata = [[0.0] * p for _ in range(p)]
    atb = [0.0] * p
    for coeff, target in rows:
        for i in range(p):
            ci = coeff[i]
            if ci == 0:
                continue
            atb[i] += ci * target
            for j in range(p):
                cj = coeff[j]
                if cj == 0:
                    continue
                ata[i][j] += ci * cj
    return _gaussian_solve(ata, atb)


def build_ranking(matches: Sequence[MatchOdds]) -> RankingResult:
    teams = sorted({m.home_team for m in matches} | {m.away_team for m in matches})
    n_teams = len(teams)
    if n_teams < 2:
        raise ValueError("Нужно минимум 2 команды")

    idx = {team: i for i, team in enumerate(teams)}
    p = n_teams + 1  # +1 для H (home advantage)
    rows: List[Tuple[List[float], float]] = []
    diffs: List[Tuple[str, str, float]] = []

    for m in matches:
        d_market = _market_rating_diff(m)
        diffs.append((m.home_team, m.away_team, d_market))

        coeff = [0.0] * p
        coeff[idx[m.home_team]] = 1.0
        coeff[idx[m.away_team]] = -1.0
        coeff[-1] = 1.0
        rows.append((coeff, d_market))

    # Нормировка шкалы: средний рейтинг = 0.
    gauge = [1.0] * n_teams + [0.0]
    rows.append((gauge, 0.0))

    solution = _solve_least_squares(rows, p)
    h = solution[-1]
    ratings = {team: solution[idx[team]] for team in teams}

    rmse_acc = 0.0
    for home, away, target in diffs:
        pred = ratings[home] - ratings[away] + h
        err = pred - target
        rmse_acc += err * err
    rmse = math.sqrt(rmse_acc / len(diffs))

    ranking = [
        TeamRating(team=t, rating=r, strength_coef=(10.0 ** (r / 400.0)))
        for t, r in ratings.items()
    ]
    ranking.sort(key=lambda x: x.rating, reverse=True)

    return RankingResult(
        teams=ranking,
        home_advantage=h,
        home_advantage_coef=(10.0 ** (h / 400.0)),
        rmse=rmse,
        matches_count=len(matches),
    )


def _print_result(result: RankingResult, top: int = 0) -> None:
    rows = result.teams if top <= 0 else result.teams[:top]
    print(f"Матчей: {result.matches_count}")
    print(f"Команд: {len(result.teams)}")
    print(
        "Домашнее преимущество H: "
        f"{result.home_advantage:.3f}  (мультипликатор {result.home_advantage_coef:.4f})"
    )
    print(f"RMSE системы: {result.rmse:.3f}\n")

    w_rank = 4
    w_team = max(12, max(len(r.team) for r in rows))
    print(
        f"{'№':>{w_rank}}  {'Team':<{w_team}}  {'Rating':>10}  "
        f"{'StrengthCoef':>12}"
    )
    print("-" * (w_rank + w_team + 28))
    for i, r in enumerate(rows, start=1):
        print(f"{i:>{w_rank}}  {r.team:<{w_team}}  {r.rating:>10.3f}  {r.strength_coef:>12.4f}")


def save_ranking_csv(path: Path, result: RankingResult) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "team", "rating", "strength_coef"])
        for i, r in enumerate(result.teams, start=1):
            writer.writerow([i, r.team, f"{r.rating:.6f}", f"{r.strength_coef:.8f}"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Рейтинг команд по коэффициентам 1/X/2 (одна лига, один сезон)"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Путь к CSV с матчами. Нужны колонки home_team, away_team, odds_1, odds_x, odds_2 "
        "(или совместимые алиасы, напр. home_team/away_team/p1/x/p2).",
    )
    parser.add_argument("--output", help="Куда сохранить рейтинг CSV (опционально).")
    parser.add_argument("--top", type=int, default=0, help="Показать только TOP-N команд.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    matches = load_matches_csv(Path(args.input))
    result = build_ranking(matches)
    _print_result(result, top=args.top)
    if args.output:
        save_ranking_csv(Path(args.output), result)
        print(f"\nCSV сохранён: {args.output}")


if __name__ == "__main__":
    main()
