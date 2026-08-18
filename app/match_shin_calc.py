"""
Расчёт вероятностей матча по методу Shin.

P1/P2 — Shin (de-vig + цепочка сил / рейтинг).
Draw — отдельная модель px(d) из history_store.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from . import devig_shin as ds
    from . import history_store as hs
    from . import team_ranking as tr
    from . import team_registry as tg
except ImportError:  # pragma: no cover
    import devig_shin as ds
    import history_store as hs
    import team_ranking as tr
    import team_registry as tg


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
NEW_TEAM_RATING = 0.0  # R для команд без матчей в сезоне (новички лиги)


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
    team1_id: str = ""
    team2_id: str = ""
    team1_new: bool = False
    team2_new: bool = False
    d_market: float = 0.0
    h_used: Optional[float] = None
    d_chain: Optional[float] = None
    details: str = ""

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


def _fmt_p(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _fmt_pct(value: float, digits: int = 2) -> str:
    return _fmt_odds(value * 100, digits) + " %"


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


def resolve_team_ref(
    league: str,
    team_ref: str,
    matches: Sequence[hs.HistoricalMatch],
) -> Tuple[str, str, bool]:
    """
    team_ref — id из справочника (epl:3) или legacy-имя.
    Возвращает (id, имя для расчёта, is_new_in_season).
    """
    league_key = hs.normalize_league(league)
    try:
        tid = tg.resolve_team_id(league_key, team_ref, required=True)
    except ValueError as exc:
        raise ShinCalculationError(str(exc)) from exc
    reg_name = tg.team_name_by_id(tid)
    try:
        canon = resolve_team_name(reg_name, matches)
        return tid, canon, False
    except ShinCalculationError:
        return tid, reg_name, True


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


def _match_draw_px(
    draw: hs.DrawModel,
    d_target: float,
    s1: float,
    s2: float,
) -> Tuple[float, List[str]]:
    """px(d) с учётом явного фаворита (цепочка s1/s2)."""
    return draw.forecast_px(d_target, s1, s2)


def _resolve_home_advantage(
    team1: str,
    team2: str,
    matches: Sequence[hs.HistoricalMatch],
    league_key: str,
) -> Tuple[float, float]:
    """H (D-шкала) и h_coef = 10^(H/400) для цепочки через соперника."""
    _d_draw, h_rank = _target_match_d(team1, team2, matches)
    if h_rank is not None:
        odds_matches = [m.to_match_odds() for m in matches]
        coef = build_ranking_shin(odds_matches).home_advantage_coef
        return h_rank, coef
    try:
        h_est = hs.home_advantage_prior(league_key).h_final
    except ValueError:
        h_est = hs.LEAGUE_DEFAULT_H.get(league_key, 58.0)
    return h_est, 10.0 ** (h_est / 400.0)


def _chain_match_lines(
    team: str,
    opponent: str,
    chain_matches: Sequence[hs.HistoricalMatch],
    h_coef: float,
) -> Tuple[List[str], List[float]]:
    """Строки расчёта и список ρ (нейтральное поле) по матчам цепочки."""
    lines: List[str] = []
    rhos: List[float] = []
    for m in chain_matches:
        p1, px, p2 = ds.shin_devig(m.odds_1, m.odds_x, m.odds_2)
        if team == m.home_team:
            r_raw = p1 / p2 if p2 > 1e-12 else 0.0
            side = "дома"
            rho = r_raw / h_coef if h_coef > 0 else r_raw
            adj = f"ρ = r/h = {_fmt_p(rho)}"
        else:
            r_raw = p2 / p1 if p1 > 1e-12 else 0.0
            side = "в гостях"
            rho = r_raw * h_coef if h_coef > 0 else r_raw
            adj = f"ρ = r·h = {_fmt_p(rho)}"
        rhos.append(rho)
        lines.append(
            f"    {m.date}  {m.home_team}  {_fmt_odds(m.odds_1)}/{_fmt_odds(m.odds_x)}/"
            f"{_fmt_odds(m.odds_2)}  {m.away_team}"
        )
        lines.append(
            f"      Shin: p1={_fmt_p(p1)} px={_fmt_p(px)} p2={_fmt_p(p2)}  |  "
            f"{team} {side}: r={_fmt_p(r_raw)}  →  {adj}"
        )
    return lines, rhos


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


def _target_match_d(
    team1: str,
    team2: str,
    matches: Sequence[hs.HistoricalMatch],
) -> Tuple[Optional[float], Optional[float]]:
    """D целевого матча team1 (дома) vs team2: R1 − R2 + H по Shin-рейтингу сезона."""
    try:
        odds_matches = [m.to_match_odds() for m in matches]
        ranking = build_ranking_shin(odds_matches)
        ratings = {t.team: t.rating for t in ranking.teams}
        if team1 not in ratings or team2 not in ratings:
            return None, None
        return ratings[team1] - ratings[team2] + ranking.home_advantage, ranking.home_advantage
    except (ValueError, ZeroDivisionError):
        return None, None


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

    h_est, h_coef = _resolve_home_advantage(team1, team2, matches, league_key)

    det: List[str] = []
    det.append(f"Источник: общий соперник — {opponent}")
    det.append(f"Матчей в цепочке: {len(m1)} ({team1}) + {len(m2)} ({team2}) = {len(m1)+len(m2)}")
    det.append(
        f"H = {_fmt_d(h_est)},  h = 10^(H/400) = {_fmt_p(h_coef)}  "
        f"(дома: ρ=r/h,  в гостях: ρ=r·h)"
    )
    det.append("")
    det.append(f"Шаг A. Матчи {team1} против {opponent} → нейтральное поле")
    lines1, rhos1 = _chain_match_lines(team1, opponent, m1, h_coef)
    det.extend(lines1)
    rho1 = _geom_mean_ratios(rhos1)
    if len(rhos1) > 1:
        ln_sum = " + ".join(_fmt_p(math.log(x)) for x in rhos1)
        det.append(
            f"  ρ̄_{team1[:3]} = exp(mean(ln ρ)) = exp(({ln_sum})/{len(rhos1)}) = {_fmt_p(rho1)}"
        )
    else:
        det.append(f"  ρ̄_{team1[:3]} = {_fmt_p(rho1)}")
    det.append("")
    det.append(f"Шаг B. Матчи {team2} против {opponent} → нейтральное поле")
    lines2, rhos2 = _chain_match_lines(team2, opponent, m2, h_coef)
    det.extend(lines2)
    rho2 = _geom_mean_ratios(rhos2)
    if len(rhos2) > 1:
        ln_sum = " + ".join(_fmt_p(math.log(x)) for x in rhos2)
        det.append(
            f"  ρ̄_{team2[:3]} = exp(mean(ln ρ)) = exp(({ln_sum})/{len(rhos2)}) = {_fmt_p(rho2)}"
        )
    else:
        det.append(f"  ρ̄_{team2[:3]} = {_fmt_p(rho2)}")
    det.append("")
    det.append("Шаг C. Прогноз team1 дома (ρ̄ → × h на отношение сил)")
    r_ab_neutral = rho1 / rho2 if rho2 > 0 else 1.0
    r_ab = r_ab_neutral * h_coef
    s1 = math.sqrt(r_ab)
    s2 = 1.0 / math.sqrt(r_ab) if r_ab > 0 else 1.0
    det.append(
        f"  ρ_AB (нейтр.) = ρ̄₁/ρ̄₂ = {_fmt_p(rho1)}/{_fmt_p(rho2)} = {_fmt_p(r_ab_neutral)}"
    )
    det.append(
        f"  r_AB (прогноз) = ρ_AB × h = {_fmt_p(r_ab_neutral)} × {_fmt_p(h_coef)} = {_fmt_p(r_ab)}"
    )
    det.append(f"  s₁ = √r_AB = {_fmt_p(s1)},  s₂ = 1/√r_AB = {_fmt_p(s2)}")

    chain_d = 400.0 * math.log10(s1 / s2) if s2 > 0 else 0.0
    det.append(f"  D_цепь = 400·log₁₀(s₁/s₂) = {_fmt_d(chain_d)}")
    det.append("")
    det.append("Шаг D. D для ничьи (целевой матч, team1 дома)")
    d_draw, _h_rank = _target_match_d(team1, team2, matches)
    if d_draw is None:
        d_draw = chain_d
        det.append("  Рейтинг сезона недоступен → D_цель = D_цепь")
        det.append(f"  H = {_fmt_d(h_est)}")
    else:
        odds_matches = [m.to_match_odds() for m in matches]
        ranking = build_ranking_shin(odds_matches)
        ratings = {t.team: t.rating for t in ranking.teams}
        det.append(
            f"  R({team1})={_fmt_d(ratings[team1])}, R({team2})={_fmt_d(ratings[team2])}, "
            f"H={_fmt_d(ranking.home_advantage)}"
        )
        det.append(
            f"  D_цель = R₁ − R₂ + H = {_fmt_d(d_draw)}  (для px(d), не для s₁/s₂)"
        )

    det.append("")
    det.append("Шаг E. Ничья px(d), отдельно от Shin P1/P2")
    draw = hs.calibrate_draw_model(league_key)
    px, px_lines = _match_draw_px(draw, d_draw, s1, s2)
    det.extend(px_lines)
    det.append("")
    det.append("Шаг F. Итоговые вероятности и коэффициенты")
    p1_raw = (s1 / (s1 + s2)) * (1.0 - px)
    p2_raw = (s2 / (s1 + s2)) * (1.0 - px)
    det.append(
        f"  p1' = s₁/(s₁+s₂)·(1−px) = {_fmt_pct(p1_raw)},  "
        f"p2' = s₂/(s₁+s₂)·(1−px) = {_fmt_pct(p2_raw)},  px = {_fmt_pct(px)}"
    )
    p1, px, p2 = _probs_from_strengths(s1, s2, px)
    det.append(f"  После нормализации: p1={_fmt_pct(p1)}, px={_fmt_pct(px)}, p2={_fmt_pct(p2)}")
    det.append(
        f"  k = 1/p: {_fmt_odds(1/p1)} / {_fmt_odds(1/px)} / {_fmt_odds(1/p2)}"
    )
    used = len(m1) + len(m2)
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
        d_market=d_draw,
        h_used=h_est,
        d_chain=chain_d,
        details="\n".join(det),
    )


def _rating_for_team(
    team: str,
    ratings: Dict[str, float],
    *,
    is_new: bool,
) -> float:
    if team in ratings:
        return ratings[team]
    if is_new:
        return NEW_TEAM_RATING
    raise ShinCalculationError(ERR_TEAM)


def _calc_league_ranking(
    team1: str,
    team2: str,
    matches: Sequence[hs.HistoricalMatch],
    league_key: str,
    season_used: str,
    source: CalculationSource,
    *,
    team1_new: bool = False,
    team2_new: bool = False,
    team1_id: str = "",
    team2_id: str = "",
) -> ShinMatchResult:
    odds_matches = [m.to_match_odds() for m in matches]
    ranking = build_ranking_shin(odds_matches)
    ratings = {t.team: t.rating for t in ranking.teams}
    r1 = _rating_for_team(team1, ratings, is_new=team1_new)
    r2 = _rating_for_team(team2, ratings, is_new=team2_new)

    h_coef = ranking.home_advantage_coef
    s1 = (10.0 ** (r1 / 400.0)) * h_coef
    s2 = 10.0 ** (r2 / 400.0)
    d_rating = r1 - r2
    d_target = d_rating + ranking.home_advantage
    draw = hs.calibrate_draw_model(league_key)
    px, px_lines = _match_draw_px(draw, d_target, s1, s2)
    p1, px, p2 = _probs_from_strengths(s1, s2, px)
    used = len(matches)
    det = [
        f"Источник: {SOURCE_LABELS_RU[source]}",
        f"Матчей в сезоне (вся лига): {used}",
        "",
        "Шаг A. Shin-рейтинг по всем матчам сезона (МНК)",
    ]
    if team1_new:
        det.append(
            f"  R({team1}) = {_fmt_d(r1)}  ← новая команда (нет матчей в сезоне, R={NEW_TEAM_RATING:.0f})"
        )
    else:
        det.append(f"  R({team1}) = {_fmt_d(r1)}")
    if team2_new:
        det.append(
            f"  R({team2}) = {_fmt_d(r2)}  ← новая команда (нет матчей в сезоне, R={NEW_TEAM_RATING:.0f})"
        )
    else:
        det.append(f"  R({team2}) = {_fmt_d(r2)}")
    det.extend(
        [
            f"  H = {_fmt_d(ranking.home_advantage)},  коэф. дома = {_fmt_p(h_coef)}",
            "",
            "Шаг B. Силы для целевого матча (team1 дома)",
            f"  s₁ = 10^(R₁/400)·H^coef = {_fmt_p(s1)}",
            f"  s₂ = 10^(R₂/400) = {_fmt_p(s2)}",
            f"  D_цель = R₁ − R₂ + H = {_fmt_d(d_target)}",
            "",
            "Шаг C. Ничья px(d)",
        ]
    )
    det.extend(px_lines)
    det.extend(
        [
            "",
            "Шаг D. Итог",
            f"  p1={_fmt_pct(p1)}, px={_fmt_pct(px)}, p2={_fmt_pct(p2)}",
            f"  k = {_fmt_odds(1/p1)} / {_fmt_odds(1/px)} / {_fmt_odds(1/p2)}",
        ]
    )
    return ShinMatchResult(
        p1=p1,
        px=px,
        p2=p2,
        source=source,
        season_used=season_used,
        matches_used=used,
        team1=team1,
        team2=team2,
        team1_id=team1_id,
        team2_id=team2_id,
        team1_new=team1_new,
        team2_new=team2_new,
        d_market=d_target,
        h_used=ranking.home_advantage,
        details="\n".join(det),
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
    t1_id, t1, t1_new = resolve_team_ref(league_key, team1, current_matches)
    t2_id, t2, t2_new = resolve_team_ref(league_key, team2, current_matches)

    header = [
        "═══ Подробный расчёт Shin ═══",
        f"Матч: {t1} (дома) — {t2}",
        f"ID: {t1_id} / {t2_id}",
        f"Лига: {hs.league_title(league_key)}, сезон: {season.strip()}",
        "",
        "Выбор источника данных:",
    ]
    if t1_new or t2_new:
        new_note = []
        if t1_new:
            new_note.append(f"{t1} ({t1_id})")
        if t2_new:
            new_note.append(f"{t2} ({t2_id})")
        header.insert(
            5,
            f"Новые команды (нет матчей в сезоне): {', '.join(new_note)} → R={NEW_TEAM_RATING:.0f}",
        )
        header.insert(6, "")

    # 1) Общий соперник в выбранном сезоне (только если обе команды играли в сезоне)
    common = []
    if not t1_new and not t2_new:
        common = find_common_opponents(t1, t2, current_matches)
    if common:
        opp = _pick_best_opponent(t1, t2, common, current_matches)
        cand_lines = []
        for c in common:
            n1 = len(
                [
                    m
                    for m in current_matches
                    if c in (m.home_team, m.away_team)
                    and t1 in (m.home_team, m.away_team)
                ]
            )
            n2 = len(
                [
                    m
                    for m in current_matches
                    if c in (m.home_team, m.away_team)
                    and t2 in (m.home_team, m.away_team)
                ]
            )
            cand_lines.append(f"  • {c}: min({n1},{n2})={min(n1,n2)} матчей")
        header.append("  1) Общий соперник — ДА")
        header.extend(cand_lines)
        header.append(f"  → выбран: {opp}")
        header.append("")
        res = _calc_common_opponent(t1, t2, opp, current_matches, league_key, season.strip())
        return replace(
            res,
            team1_id=t1_id,
            team2_id=t2_id,
            team1_new=t1_new,
            team2_new=t2_new,
            details="\n".join(header) + "\n" + res.details,
        )

    header.append("  1) Общий соперник — нет" + (" (новая команда)" if (t1_new or t2_new) else ""))

    # 2) Матчи лиги: >= 3 матчей с участием команд (новые команды: R=0)
    n_current = count_team_matches(t1, t2, current_matches) if not (t1_new and t2_new) else 0
    league_ok = n_current >= MIN_LEAGUE_MATCHES or (t1_new or t2_new) and len(current_matches) >= MIN_LEAGUE_MATCHES
    if league_ok:
        header.append(
            f"  2) Матчи в сезоне — ДА ({n_current} матчей с участием команд"
            + (", новая команда → R=0" if (t1_new or t2_new) else "")
            + ")"
        )
        header.append("")
        res = _calc_league_ranking(
            t1,
            t2,
            current_matches,
            league_key,
            season.strip(),
            CalculationSource.LEAGUE_MATCHES,
            team1_new=t1_new,
            team2_new=t2_new,
            team1_id=t1_id,
            team2_id=t2_id,
        )
        return replace(res, details="\n".join(header) + "\n" + res.details)

    header.append(f"  2) Матчи в сезоне — мало ({n_current} < {MIN_LEAGUE_MATCHES})")

    # 3) Предыдущий сезон
    prev = previous_season(league_key, season.strip())
    if prev is None:
        raise ShinCalculationError(ERR_DATA)

    prev_matches = hs.load_season(league_key, prev)
    t1p_id, t1p, t1p_new = resolve_team_ref(league_key, team1, prev_matches)
    t2p_id, t2p, t2p_new = resolve_team_ref(league_key, team2, prev_matches)

    header.append(f"  3) Предыдущий сезон — {prev}")
    header.append("")

    common_prev = []
    if not t1p_new and not t2p_new:
        common_prev = find_common_opponents(t1p, t2p, prev_matches)
    if common_prev:
        opp = _pick_best_opponent(t1p, t2p, common_prev, prev_matches)
        res = _calc_common_opponent(
            t1p, t2p, opp, prev_matches, league_key, prev
        )
        return replace(
            res,
            team1_id=t1p_id,
            team2_id=t2p_id,
            team1_new=t1p_new,
            team2_new=t2p_new,
            details="\n".join(header) + "\n" + res.details,
        )

    n_prev = count_team_matches(t1p, t2p, prev_matches) if not (t1p_new and t2p_new) else 0
    prev_league_ok = n_prev >= MIN_LEAGUE_MATCHES or (t1p_new or t2p_new) and len(prev_matches) >= MIN_LEAGUE_MATCHES
    if not prev_league_ok:
        raise ShinCalculationError(ERR_DATA)

    res = _calc_league_ranking(
        t1p,
        t2p,
        prev_matches,
        league_key,
        prev,
        CalculationSource.PREVIOUS_SEASON,
        team1_new=t1p_new,
        team2_new=t2p_new,
        team1_id=t1p_id,
        team2_id=t2p_id,
    )
    return replace(res, details="\n".join(header) + "\n" + res.details)
