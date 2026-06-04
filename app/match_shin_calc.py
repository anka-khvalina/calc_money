"""
Расчёт вероятностей матча по методу Shin.

P1/P2 — Shin (de-vig + цепочка сил / рейтинг).
Draw — отдельная модель px(d) из history_store.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from . import devig_shin as ds
    from . import history_store as hs
    from . import team_ranking as tr
except ImportError:  # pragma: no cover
    import devig_shin as ds
    import history_store as hs
    import team_ranking as tr


class ShinCalculationError(Exception):
    """Ошибка валидации или недостатка данных."""


class CalculationSource(str, Enum):
    COMMON_OPPONENT = "Common opponent"
    LEAGUE_MATCHES = "League matches"
    PREVIOUS_SEASON = "Previous season"


SOURCE_LABELS_RU = {
    CalculationSource.COMMON_OPPONENT: "Общий соперник",
    CalculationSource.LEAGUE_MATCHES: "Матчи лиги",
    CalculationSource.PREVIOUS_SEASON: "Предыдущий сезон",
}

ERR_LEAGUE = "Лига не найдена. Проверьте название лиги и повторите расчет."
ERR_SEASON = "Сезон не найден. Проверьте выбранный сезон или загрузите данные по сезону."
ERR_TEAM = "Команда не найдена. Проверьте название команды и повторите расчет."
ERR_DATA = (
    "Недостаточно данных для расчета. В текущем сезоне найдено менее 3 матчей, "
    "предыдущий загруженный сезон отсутствует."
)

MIN_LEAGUE_MATCHES = 3


@dataclass(frozen=True)
class ShinMatchResult:
    p1: float
    px: float
    p2: float
    source: CalculationSource
    season_used: str
    matches_used: int
    method: str = "Shin"
    common_opponent: Optional[str] = None
    team1: str = ""
    team2: str = ""
    d_market: float = 0.0
    h_used: Optional[float] = None

    @property
    def source_label_ru(self) -> str:
        return SOURCE_LABELS_RU.get(self.source, self.source.value)

    @property
    def k1(self) -> float:
        return 1.0 / self.p1 if self.p1 > 1e-12 else float("inf")

    @property
    def kx(self) -> float:
        return 1.0 / self.px if self.px > 1e-12 else float("inf")

    @property
    def k2(self) -> float:
        return 1.0 / self.p2 if self.p2 > 1e-12 else float("inf")

    def format_odds(self, digits: int = 2) -> str:
        """Строка k1 / kx / k2 с десятичной запятой."""
        return " / ".join(
            _fmt_odds(k, digits) for k in (self.k1, self.kx, self.k2)
        )


def _fmt_odds(value: float, digits: int = 2) -> str:
    if not math.isfinite(value):
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


def _fmt_d(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def previous_season(league: str, season: str) -> Optional[str]:
    """Предыдущий загруженный сезон той же лиги (старее текущего)."""
    seasons = hs.list_seasons(league)
    if season not in seasons:
        return None
    idx = seasons.index(season)
    if idx <= 0:
        return None
    return seasons[idx - 1]


def resolve_team_name(name: str, matches: Sequence[hs.HistoricalMatch]) -> str:
    """Найти каноническое имя команды в сезоне (без учёта регистра)."""
    needle = name.strip()
    if not needle:
        raise ShinCalculationError(ERR_TEAM)
    norm = _norm_team(needle)
    teams: Dict[str, str] = {}
    for m in matches:
        for t in (m.home_team, m.away_team):
            if _norm_team(t) == norm:
                return t
            teams[_norm_team(t)] = t
    # частичное вхождение
    for key, canonical in teams.items():
        if norm in key or key in norm:
            return canonical
    raise ShinCalculationError(ERR_TEAM)


def _norm_team(text: str) -> str:
    return "".join(ch for ch in text.strip().lower() if ch.isalnum())


def _team_matches(
    team: str, matches: Sequence[hs.HistoricalMatch]
) -> List[hs.HistoricalMatch]:
    return [m for m in matches if team in (m.home_team, m.away_team)]


def count_team_matches(
    team1: str, team2: str, matches: Sequence[hs.HistoricalMatch]
) -> int:
    """Уникальные матчи сезона с участием Команды 1 или Команды 2."""
    keys = set()
    for m in matches:
        if team1 in (m.home_team, m.away_team) or team2 in (m.home_team, m.away_team):
            keys.add((m.date, m.home_team, m.away_team))
    return len(keys)


def find_common_opponents(
    team1: str,
    team2: str,
    matches: Sequence[hs.HistoricalMatch],
) -> List[str]:
    """Соперники, против которых играли обе команды в этом наборе матчей."""
    opps1 = {
        (m.away_team if m.home_team == team1 else m.home_team)
        for m in matches
        if team1 in (m.home_team, m.away_team)
    }
    opps2 = {
        (m.away_team if m.home_team == team2 else m.home_team)
        for m in matches
        if team2 in (m.home_team, m.away_team)
    }
    common = sorted(opps1 & opps2 - {team1, team2})
    return common


def _pick_best_opponent(
    team1: str,
    team2: str,
    opponents: Sequence[str],
    matches: Sequence[hs.HistoricalMatch],
) -> str:
    best = opponents[0]
    best_score = -1
    for opp in opponents:
        n1 = len(
            [m for m in matches if opp in (m.home_team, m.away_team) and team1 in (m.home_team, m.away_team)]
        )
        n2 = len(
            [m for m in matches if opp in (m.home_team, m.away_team) and team2 in (m.home_team, m.away_team)]
        )
        score = min(n1, n2)
        if score > best_score:
            best_score = score
            best = opp
    return best


def _geom_mean_ratios(ratios: Sequence[float]) -> float:
    vals = [r for r in ratios if r > 0 and math.isfinite(r)]
    if not vals:
        raise ShinCalculationError(ERR_DATA)
    if len(vals) == 1:
        return vals[0]
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def _strength_ratio_shin(match: hs.HistoricalMatch, focal: str) -> float:
    """Относительная сила focal в матче (Shin fair probs)."""
    p1, _px, p2 = ds.shin_devig(match.odds_1, match.odds_x, match.odds_2)
    if focal == match.home_team:
        return p1 / p2 if p2 > 1e-12 else 1e6
    if focal == match.away_team:
        return p2 / p1 if p1 > 1e-12 else 1e6
    raise ValueError(f"Команда {focal} не участвует в матче")


def _probs_from_strengths(s1: float, s2: float, px: float) -> Tuple[float, float, float]:
    px = max(0.0, min(0.95, px))
    rem = max(1e-9, 1.0 - px)
    denom = s1 + s2
    if denom <= 0:
        raise ShinCalculationError(ERR_DATA)
    p1 = (s1 / denom) * rem
    p2 = (s2 / denom) * rem
    total = p1 + px + p2
    return p1 / total, px / total, p2 / total


def _shin_market_diff(match: tr.MatchOdds) -> Tuple[float, float, float]:
    p1, px, p2 = ds.shin_devig(match.odds_1, match.odds_x, match.odds_2)
    e_home = p1 + 0.5 * px
    e_away = p2 + 0.5 * px
    if e_home <= 0 or e_away <= 0:
        raise ValueError("Ожидаемые очки должны быть > 0")
    return 400.0 * math.log10(e_home / e_away), e_home, e_away


def build_ranking_shin(matches: Sequence[tr.MatchOdds]) -> tr.RankingResult:
    """Рейтинг команд с Shin de-vig (копия build_ranking с другим de-vig)."""
    teams = sorted({m.home_team for m in matches} | {m.away_team for m in matches})
    n_teams = len(teams)
    if n_teams < 2:
        raise ShinCalculationError(ERR_DATA)

    idx = {team: i for i, team in enumerate(teams)}
    p = n_teams + 1
    coeffs: List[List[float]] = []
    targets: List[float] = []
    for m in matches:
        d_market, _eh, _ea = _shin_market_diff(m)
        coeff = [0.0] * p
        coeff[idx[m.home_team]] = 1.0
        coeff[idx[m.away_team]] = -1.0
        coeff[-1] = 1.0
        coeffs.append(coeff)
        targets.append(d_market)

    gauge = [1.0] * n_teams + [0.0]
    rows = [(coeffs[i], targets[i], 1.0) for i in range(len(matches))]
    rows.append((gauge, 0.0, tr.GAUGE_WEIGHT))
    sol = tr._solve_weighted(rows, p)
    ratings = {t: sol[idx[t]] for t in teams}
    h = sol[-1]
    resid = [
        (ratings[m.home_team] - ratings[m.away_team] + h) - targets[i]
        for i, m in enumerate(matches)
    ]
    rmse = math.sqrt(sum(r * r for r in resid) / len(resid))
    ranking = [
        tr.TeamRating(team=t, rating=r, strength_coef=(10.0 ** (r / 400.0)))
        for t, r in ratings.items()
    ]
    ranking.sort(key=lambda x: x.rating, reverse=True)
    return tr.RankingResult(
        teams=ranking,
        home_advantage=h,
        home_advantage_coef=(10.0 ** (h / 400.0)),
        rmse=rmse,
        matches_count=len(matches),
        method="shin_ols",
    )


def _calc_common_opponent(
    team1: str,
    team2: str,
    opponent: str,
    matches: Sequence[hs.HistoricalMatch],
    league_key: str,
    season_used: str,
) -> ShinMatchResult:
    m1 = [
        m
        for m in matches
        if team1 in (m.home_team, m.away_team) and opponent in (m.home_team, m.away_team)
    ]
    m2 = [
        m
        for m in matches
        if team2 in (m.home_team, m.away_team) and opponent in (m.home_team, m.away_team)
    ]
    if not m1 or not m2:
        raise ShinCalculationError(ERR_DATA)

    r1 = _geom_mean_ratios([_strength_ratio_shin(m, team1) for m in m1])
    r2 = _geom_mean_ratios([_strength_ratio_shin(m, team2) for m in m2])
    r_ab = r1 / r2 if r2 > 0 else 1.0
    s1 = math.sqrt(r_ab)
    s2 = 1.0 / math.sqrt(r_ab) if r_ab > 0 else 1.0

    draw = hs.calibrate_draw_model(league_key)
    d = 400.0 * math.log10(s1 / s2) if s2 > 0 else 0.0
    px = draw.px(d)
    p1, px, p2 = _probs_from_strengths(s1, s2, px)
    used = len(m1) + len(m2)
    try:
        h_est = hs.home_advantage_prior(league_key).h_final
    except ValueError:
        h_est = hs.LEAGUE_DEFAULT_H.get(league_key, 58.0)
    return ShinMatchResult(
        p1=p1,
        px=px,
        p2=p2,
        source=CalculationSource.COMMON_OPPONENT,
        season_used=season_used,
        matches_used=used,
        common_opponent=opponent,
        team1=team1,
        team2=team2,
        d_market=d,
        h_used=h_est,
    )


def _calc_league_ranking(
    team1: str,
    team2: str,
    matches: Sequence[hs.HistoricalMatch],
    league_key: str,
    season_used: str,
    source: CalculationSource,
) -> ShinMatchResult:
    odds_matches = [m.to_match_odds() for m in matches]
    ranking = build_ranking_shin(odds_matches)
    ratings = {t.team: t.rating for t in ranking.teams}
    if team1 not in ratings or team2 not in ratings:
        raise ShinCalculationError(ERR_TEAM)

    h_coef = ranking.home_advantage_coef
    s1 = (10.0 ** (ratings[team1] / 400.0)) * h_coef
    s2 = 10.0 ** (ratings[team2] / 400.0)
    d_rating = ratings[team1] - ratings[team2]
    d_target = d_rating + ranking.home_advantage
    draw = hs.calibrate_draw_model(league_key)
    px = draw.px(d_rating)
    p1, px, p2 = _probs_from_strengths(s1, s2, px)
    used = len(matches)
    return ShinMatchResult(
        p1=p1,
        px=px,
        p2=p2,
        source=source,
        season_used=season_used,
        matches_used=used,
        team1=team1,
        team2=team2,
        d_market=d_target,
        h_used=ranking.home_advantage,
    )


def calculate_shin_match(
    team1: str,
    team2: str,
    league: str,
    season: str,
) -> ShinMatchResult:
    """
    Расчёт P1 / Draw / P2 по методу Shin с приоритетом источников данных.
    Команда 1 считается хозяином целевого матча.
    """
    try:
        league_key = hs.normalize_league(league)
    except ValueError as exc:
        raise ShinCalculationError(ERR_LEAGUE) from exc

    if league_key not in hs.list_leagues():
        raise ShinCalculationError(ERR_LEAGUE)

    seasons = hs.list_seasons(league_key)
    if season.strip() not in seasons:
        raise ShinCalculationError(ERR_SEASON)

    current_matches = hs.load_season(league_key, season.strip())
    t1 = resolve_team_name(team1, current_matches)
    t2 = resolve_team_name(team2, current_matches)

    # 1) Общий соперник в выбранном сезоне
    common = find_common_opponents(t1, t2, current_matches)
    if common:
        opp = _pick_best_opponent(t1, t2, common, current_matches)
        return _calc_common_opponent(t1, t2, opp, current_matches, league_key, season.strip())

    # 2) Матчи лиги: >= 3 матчей с участием команд
    n_current = count_team_matches(t1, t2, current_matches)
    if n_current >= MIN_LEAGUE_MATCHES:
        return _calc_league_ranking(
            t1,
            t2,
            current_matches,
            league_key,
            season.strip(),
            CalculationSource.LEAGUE_MATCHES,
        )

    # 3) Предыдущий сезон
    prev = previous_season(league_key, season.strip())
    if prev is None:
        raise ShinCalculationError(ERR_DATA)

    prev_matches = hs.load_season(league_key, prev)
    t1p = resolve_team_name(team1, prev_matches)
    t2p = resolve_team_name(team2, prev_matches)

    common_prev = find_common_opponents(t1p, t2p, prev_matches)
    if common_prev:
        opp = _pick_best_opponent(t1p, t2p, common_prev, prev_matches)
        return _calc_common_opponent(
            t1p, t2p, opp, prev_matches, league_key, prev
        )

    n_prev = count_team_matches(t1p, t2p, prev_matches)
    if n_prev < MIN_LEAGUE_MATCHES:
        raise ShinCalculationError(ERR_DATA)

    return _calc_league_ranking(
        t1p,
        t2p,
        prev_matches,
        league_key,
        prev,
        CalculationSource.PREVIOUS_SEASON,
    )
