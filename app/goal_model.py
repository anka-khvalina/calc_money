"""
Голевая модель футбольной линии: λ_h, λ_a → матрица счетов → все рынки.

Ядро без обучения и GUI (импортируемо для тестов):

  * Пуассон-матрица счетов P(i,j) + поправка Dixon-Coles;
  * расчёт исходов азиатских фор и тоталов (включая .0/.25/.5/.75);
  * честные коэффициенты по распределению исходов (через EV);
  * восстановление скрытых S_m = λ_h+λ_a и D_m = λ_h−λ_a из
    исторических closing-линий (тотал + Over/Under, фора + AH odds);
  * рынки 1X2, тоталы, форы, индивидуальные тоталы, точный счёт;
  * поиск центральной форы/тотала и наложение маржи.

Схема: closing lines → скрытые S,D → λ_h,λ_a → матрица → вся линия.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:
    from . import devig_shin as ds
except ImportError:  # pragma: no cover - прямой запуск
    import devig_shin as ds


MAX_GOALS_DEFAULT = 10
_EPS = 1e-12


# --------------------------------------------------------------------------- #
# Пуассон и матрица счетов
# --------------------------------------------------------------------------- #

def poisson_pmf(k: int, lam: float) -> float:
    if lam < 0:
        raise ValueError("lambda должна быть >= 0")
    if k < 0:
        return 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def build_score_matrix(
    lambda_home: float,
    lambda_away: float,
    max_goals: int = MAX_GOALS_DEFAULT,
) -> List[List[float]]:
    """Матрица P(i,j) = Pois(i;λ_h)·Pois(j;λ_a), нормированная к сумме 1."""
    if lambda_home <= 0 or lambda_away <= 0:
        raise ValueError("λ_h и λ_a должны быть > 0")
    ph = [poisson_pmf(i, lambda_home) for i in range(max_goals + 1)]
    pa = [poisson_pmf(j, lambda_away) for j in range(max_goals + 1)]
    matrix = [[ph[i] * pa[j] for j in range(max_goals + 1)] for i in range(max_goals + 1)]
    return _normalize_matrix(matrix)


def apply_dixon_coles(
    matrix: List[List[float]],
    lambda_home: float,
    lambda_away: float,
    gamma: float,
) -> List[List[float]]:
    """Поправка Dixon-Coles для низких счетов (0:0, 1:0, 0:1, 1:1)."""
    if gamma == 0.0:
        return _normalize_matrix([row[:] for row in matrix])
    m = [row[:] for row in matrix]
    n = len(m)
    if n >= 1:
        m[0][0] *= 1.0 + lambda_home * lambda_away * gamma
    if n >= 2:
        m[1][0] *= 1.0 - lambda_away * gamma
        m[0][1] *= 1.0 - lambda_home * gamma
        m[1][1] *= 1.0 + gamma
    # Поправки могут уйти в минус при большой |gamma| — обрезаем.
    for i in range(min(2, n)):
        for j in range(min(2, n)):
            if m[i][j] < 0:
                m[i][j] = 0.0
    return _normalize_matrix(m)


def _normalize_matrix(matrix: List[List[float]]) -> List[List[float]]:
    total = sum(sum(row) for row in matrix)
    if total <= 0:
        raise ValueError("Матрица счетов вырождена (нулевая сумма)")
    return [[v / total for v in row] for row in matrix]


# --------------------------------------------------------------------------- #
# Исходы ставок: (win_part, loss_part); push_part = 1 − win − loss
#   EV = win·(K−1) − loss = 0  →  K = 1 + loss/win
# Половинчатые исходы (четвертные линии): win/loss = 0.5.
# --------------------------------------------------------------------------- #

def _simple_line_components(line: float) -> Tuple[float, ...]:
    """Разложить линию на «простые» половины (.0/.5).

    Четвертная линия (.25/.75) = среднее двух соседних половин.
    Остальные возвращаются как одна компонента.
    """
    frac = round(line - math.floor(line), 2)
    if frac in (0.25, 0.75):
        return (line - 0.25, line + 0.25)
    return (line,)


def _ah_home_units_simple(i: int, j: int, line: float) -> Tuple[float, float]:
    """win_part, loss_part для ставки на хозяев с простой форой (.0/.5)."""
    margin = (i - j) + line
    if margin > _EPS:
        return 1.0, 0.0
    if margin < -_EPS:
        return 0.0, 1.0
    return 0.0, 0.0  # push (только для целой линии)


def ah_home_units(i: int, j: int, line: float) -> Tuple[float, float]:
    """win_part, loss_part для ставки «хозяева (фора line)» при счёте i:j."""
    comps = _simple_line_components(line)
    win = sum(_ah_home_units_simple(i, j, c)[0] for c in comps) / len(comps)
    loss = sum(_ah_home_units_simple(i, j, c)[1] for c in comps) / len(comps)
    return win, loss


def ah_away_units(i: int, j: int, away_line: float) -> Tuple[float, float]:
    """Ставка на гостей с форой away_line (в их сторону)."""
    # Для гостей знак счёта инвертирован: эквивалентно «дому» с (j,i).
    return ah_home_units(j, i, away_line)


def _total_units_simple(goals: int, line: float, side: str) -> Tuple[float, float]:
    margin = goals - line
    over_win = margin > _EPS
    if abs(margin) <= _EPS:  # push (целая линия)
        return 0.0, 0.0
    if side == "over":
        return (1.0, 0.0) if over_win else (0.0, 1.0)
    return (0.0, 1.0) if over_win else (1.0, 0.0)


def total_units(goals: int, line: float, side: str) -> Tuple[float, float]:
    """win_part, loss_part для Over/Under по тоталу line при сумме goals."""
    if side not in ("over", "under"):
        raise ValueError("side должно быть 'over' или 'under'")
    comps = _simple_line_components(line)
    win = sum(_total_units_simple(goals, c, side)[0] for c in comps) / len(comps)
    loss = sum(_total_units_simple(goals, c, side)[1] for c in comps) / len(comps)
    return win, loss


def _accumulate_units(
    matrix: List[List[float]],
    unit_fn: Callable[[int, int], Tuple[float, float]],
) -> Tuple[float, float]:
    """Σ p·win_part, Σ p·loss_part по всей матрице."""
    win = 0.0
    loss = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            w, l = unit_fn(i, j)
            win += p * w
            loss += p * l
    return win, loss


def fair_odds_from_units(win_part: float, loss_part: float) -> float:
    """Честный коэффициент: K = 1 + loss/win = (win+loss)/win."""
    if win_part <= _EPS:
        return float("inf")
    return 1.0 + loss_part / win_part


def conditional_win_prob(win_part: float, loss_part: float) -> float:
    """P(выигрыш | не возврат) — как при two-way de-vig (push исключён)."""
    denom = win_part + loss_part
    if denom <= _EPS:
        return 0.5
    return win_part / denom


# --------------------------------------------------------------------------- #
# Снятие маржи
# --------------------------------------------------------------------------- #

def devig_two_way(odds_a: float, odds_b: float) -> Tuple[float, float]:
    """Снятие маржи для двухисходного рынка по модели Shin."""
    if odds_a <= 1.0 or odds_b <= 1.0:
        raise ValueError("Коэффициенты двухисходного рынка должны быть > 1")
    return ds.shin_devig_two_way(odds_a, odds_b)


def shin_devig_1x2(home_odds: float, draw_odds: float, away_odds: float) -> Tuple[float, float, float]:
    """Честные p1/px/p2 по Shin (сумма = 1)."""
    return ds.shin_devig(home_odds, draw_odds, away_odds)


# --------------------------------------------------------------------------- #
# 1-D минимизация (golden-section): для унимодальных гладких целей
# --------------------------------------------------------------------------- #

_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0  # ≈ 0.618


def minimize_1d(
    f: Callable[[float], float],
    lo: float,
    hi: float,
    tol: float = 1e-6,
    max_iter: int = 200,
) -> float:
    a, b = lo, hi
    c = b - _GOLDEN * (b - a)
    d = a + _GOLDEN * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(max_iter):
        if abs(b - a) < tol:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - _GOLDEN * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + _GOLDEN * (b - a)
            fd = f(d)
    return 0.5 * (a + b)


# --------------------------------------------------------------------------- #
# Восстановление скрытых S_m (сумма) и D_m (разница) из closing-линий
# --------------------------------------------------------------------------- #

def infer_total_sum(
    total_line: float,
    p_over_fair: float,
    *,
    max_goals: int = MAX_GOALS_DEFAULT,
    s_min: float = 0.3,
    s_max: float = 7.0,
) -> float:
    """Найти S = λ_h+λ_a так, чтобы P(Over | не возврат) совпала с рынком.

    Сумма голов ~ Pois(S); конкретное деление на λ_h/λ_a здесь не важно
    (сумма независимых Пуассонов снова Пуассон со средним S).
    """
    p_over_fair = min(max(p_over_fair, 1e-4), 1.0 - 1e-4)

    def model_p_over(s: float) -> float:
        win = loss = 0.0
        for g in range(0, 2 * max_goals + 1):
            pg = poisson_pmf(g, s)
            w, l = total_units(g, total_line, "over")
            win += pg * w
            loss += pg * l
        return conditional_win_prob(win, loss)

    return minimize_1d(lambda s: (model_p_over(s) - p_over_fair) ** 2, s_min, s_max)


def infer_goal_diff(
    ah_home_line: float,
    p_ah_home_fair: float,
    sum_goals: float,
    *,
    max_goals: int = MAX_GOALS_DEFAULT,
    eps: float = 0.05,
) -> float:
    """Найти D = λ_h−λ_a так, чтобы P(хозяева покрыли | не возврат) = рынок.

    Использует полную матрицу счетов (учитывает возвраты и половины фор).
    """
    p_ah_home_fair = min(max(p_ah_home_fair, 1e-4), 1.0 - 1e-4)
    d_min = -sum_goals + eps
    d_max = sum_goals - eps

    def model_p_home(d: float) -> float:
        lh = (sum_goals + d) / 2.0
        la = (sum_goals - d) / 2.0
        if lh <= 0 or la <= 0:
            return 1.0 if d > 0 else 0.0
        matrix = build_score_matrix(lh, la, max_goals)
        win, loss = _accumulate_units(matrix, lambda i, j: ah_home_units(i, j, ah_home_line))
        return conditional_win_prob(win, loss)

    return minimize_1d(lambda d: (model_p_home(d) - p_ah_home_fair) ** 2, d_min, d_max)


def clamp_goal_diff(d: float, sum_goals: float, eps: float = 0.05) -> float:
    return max(-sum_goals + eps, min(d, sum_goals - eps))


# --------------------------------------------------------------------------- #
# Рынки из матрицы
# --------------------------------------------------------------------------- #

@dataclass
class MarketLine:
    line: float
    home_or_over_odds: float
    away_or_under_odds: float
    p_home_or_over: float  # условная (без возврата)
    p_away_or_under: float


@dataclass
class MatchMarkets:
    lambda_home: float
    lambda_away: float
    p1: float
    px: float
    p2: float
    main_total: MarketLine
    main_ah: MarketLine
    totals: List[MarketLine] = field(default_factory=list)
    handicaps: List[MarketLine] = field(default_factory=list)
    team_totals_home: List[Tuple[float, float, float]] = field(default_factory=list)  # (line, over_odds, under_odds)
    team_totals_away: List[Tuple[float, float, float]] = field(default_factory=list)
    top_scores: List[Tuple[int, int, float]] = field(default_factory=list)  # (i, j, prob)

    def k1(self) -> float:
        return 1.0 / self.p1 if self.p1 > _EPS else float("inf")

    def kx(self) -> float:
        return 1.0 / self.px if self.px > _EPS else float("inf")

    def k2(self) -> float:
        return 1.0 / self.p2 if self.p2 > _EPS else float("inf")


def compute_1x2(matrix: List[List[float]]) -> Tuple[float, float, float]:
    p1 = px = p2 = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                p1 += p
            elif i == j:
                px += p
            else:
                p2 += p
    return p1, px, p2


def total_market(matrix: List[List[float]], line: float) -> MarketLine:
    win_o, loss_o = _accumulate_units(matrix, lambda i, j: total_units(i + j, line, "over"))
    win_u, loss_u = _accumulate_units(matrix, lambda i, j: total_units(i + j, line, "under"))
    return MarketLine(
        line=line,
        home_or_over_odds=fair_odds_from_units(win_o, loss_o),
        away_or_under_odds=fair_odds_from_units(win_u, loss_u),
        p_home_or_over=conditional_win_prob(win_o, loss_o),
        p_away_or_under=conditional_win_prob(win_u, loss_u),
    )


def ah_market(matrix: List[List[float]], home_line: float) -> MarketLine:
    win_h, loss_h = _accumulate_units(matrix, lambda i, j: ah_home_units(i, j, home_line))
    win_a, loss_a = _accumulate_units(matrix, lambda i, j: ah_away_units(i, j, -home_line))
    return MarketLine(
        line=home_line,
        home_or_over_odds=fair_odds_from_units(win_h, loss_h),
        away_or_under_odds=fair_odds_from_units(win_a, loss_a),
        p_home_or_over=conditional_win_prob(win_h, loss_h),
        p_away_or_under=conditional_win_prob(win_a, loss_a),
    )


def team_total_odds(matrix: List[List[float]], line: float, *, home: bool) -> Tuple[float, float]:
    """Честные (over, under) на индивидуальный тотал команды (.5-линии)."""
    p_over = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            goals = i if home else j
            if goals > line:
                p_over += p
    p_over = min(max(p_over, _EPS), 1.0 - _EPS)
    return 1.0 / p_over, 1.0 / (1.0 - p_over)


def top_scorelines(matrix: List[List[float]], top_n: int = 8) -> List[Tuple[int, int, float]]:
    flat = [(i, j, p) for i, row in enumerate(matrix) for j, p in enumerate(row)]
    flat.sort(key=lambda t: t[2], reverse=True)
    return flat[:top_n]


def _candidate_lines(lo: float, hi: float, step: float = 0.25) -> List[float]:
    n = int(round((hi - lo) / step))
    return [round(lo + k * step, 2) for k in range(n + 1)]


def find_main_total(matrix: List[List[float]], lines: Optional[Sequence[float]] = None) -> MarketLine:
    lines = lines or _candidate_lines(0.5, 6.0)
    best = None
    best_score = float("inf")
    for ln in lines:
        m = total_market(matrix, ln)
        score = abs(m.home_or_over_odds - m.away_or_under_odds)
        if score < best_score:
            best_score, best = score, m
    return best


def find_main_ah(matrix: List[List[float]], lines: Optional[Sequence[float]] = None) -> MarketLine:
    lines = lines or _candidate_lines(-5.0, 5.0)
    best = None
    best_score = float("inf")
    for ln in lines:
        m = ah_market(matrix, ln)
        score = abs(m.home_or_over_odds - m.away_or_under_odds)
        if score < best_score:
            best_score, best = score, m
    return best


def draw_probability(matrix: List[List[float]]) -> float:
    return sum(matrix[i][i] for i in range(len(matrix)))


def adjust_matrix_to_draw_target(
    matrix: List[List[float]],
    p_draw_target: float,
    *,
    q_min: float = 0.90,
    q_max: float = 1.10,
) -> Tuple[List[List[float]], Dict[str, float]]:
    """Скорректировать диагональ матрицы под целевую вероятность ничьей.

    Диагональ × q (q клампится в [q_min, q_max]); недиагональ × c так, чтобы
    сумма осталась 1. Возвращает (новая матрица, диагностика).
    """
    p_draw_matrix = draw_probability(matrix)
    diag: Dict[str, float] = {
        "draw_from_matrix": p_draw_matrix,
        "draw_target_model": p_draw_target,
    }
    if p_draw_matrix <= _EPS or p_draw_matrix >= 1.0 - _EPS:
        diag.update(diag_multiplier_raw=1.0, diag_multiplier_used=1.0,
                    draw_after_calibration=p_draw_matrix, non_diag_multiplier=1.0)
        return [row[:] for row in matrix], diag

    q_raw = p_draw_target / p_draw_matrix
    q = min(q_max, max(q_min, q_raw))
    p_draw_after = q * p_draw_matrix
    c = (1.0 - p_draw_after) / (1.0 - p_draw_matrix)

    out = [row[:] for row in matrix]
    n = len(out)
    for i in range(n):
        for j in range(n):
            out[i][j] *= q if i == j else c
    out = _normalize_matrix(out)

    diag.update(
        diag_multiplier_raw=q_raw,
        diag_multiplier_used=q,
        draw_after_calibration=draw_probability(out),
        non_diag_multiplier=c,
    )
    return out, diag


def markets_from_matrix(
    matrix: List[List[float]],
    *,
    total_lines: Optional[Sequence[float]] = None,
    ah_lines: Optional[Sequence[float]] = None,
    team_total_lines: Sequence[float] = (0.5, 1.5, 2.5),
) -> MatchMarkets:
    """Все рынки из готовой (уже скорректированной) матрицы счетов."""
    p1, px, p2 = compute_1x2(matrix)
    total_grid = list(total_lines) if total_lines else _candidate_lines(0.5, 6.0)
    ah_grid = list(ah_lines) if ah_lines else _candidate_lines(-5.0, 5.0)

    totals = [total_market(matrix, ln) for ln in total_grid]
    handicaps = [ah_market(matrix, ln) for ln in ah_grid]
    main_total = min(totals, key=lambda m: abs(m.home_or_over_odds - m.away_or_under_odds))
    main_ah = min(handicaps, key=lambda m: abs(m.home_or_over_odds - m.away_or_under_odds))

    tt_home = [(ln, *team_total_odds(matrix, ln, home=True)) for ln in team_total_lines]
    tt_away = [(ln, *team_total_odds(matrix, ln, home=False)) for ln in team_total_lines]

    # λ восстанавливаем как мат.ожидание по матрице (для отображения)
    n = len(matrix)
    lam_h = sum(i * sum(matrix[i]) for i in range(n))
    lam_a = sum(j * sum(matrix[i][j] for i in range(n)) for j in range(n))

    return MatchMarkets(
        lambda_home=lam_h,
        lambda_away=lam_a,
        p1=p1, px=px, p2=p2,
        main_total=main_total,
        main_ah=main_ah,
        totals=totals,
        handicaps=handicaps,
        team_totals_home=tt_home,
        team_totals_away=tt_away,
        top_scores=top_scorelines(matrix),
    )


def compute_all_markets(
    lambda_home: float,
    lambda_away: float,
    *,
    gamma: float = 0.0,
    max_goals: int = MAX_GOALS_DEFAULT,
    total_lines: Optional[Sequence[float]] = None,
    ah_lines: Optional[Sequence[float]] = None,
    team_total_lines: Sequence[float] = (0.5, 1.5, 2.5),
) -> MatchMarkets:
    matrix = build_score_matrix(lambda_home, lambda_away, max_goals)
    if gamma:
        matrix = apply_dixon_coles(matrix, lambda_home, lambda_away, gamma)
    p1, px, p2 = compute_1x2(matrix)

    total_grid = list(total_lines) if total_lines else _candidate_lines(0.5, 6.0)
    ah_grid = list(ah_lines) if ah_lines else _candidate_lines(-5.0, 5.0)

    totals = [total_market(matrix, ln) for ln in total_grid]
    handicaps = [ah_market(matrix, ln) for ln in ah_grid]
    main_total = min(totals, key=lambda m: abs(m.home_or_over_odds - m.away_or_under_odds))
    main_ah = min(handicaps, key=lambda m: abs(m.home_or_over_odds - m.away_or_under_odds))

    tt_home = [(ln, *team_total_odds(matrix, ln, home=True)) for ln in team_total_lines]
    tt_away = [(ln, *team_total_odds(matrix, ln, home=False)) for ln in team_total_lines]

    return MatchMarkets(
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        p1=p1, px=px, p2=p2,
        main_total=main_total,
        main_ah=main_ah,
        totals=totals,
        handicaps=handicaps,
        team_totals_home=tt_home,
        team_totals_away=tt_away,
        top_scores=top_scorelines(matrix),
    )


# --------------------------------------------------------------------------- #
# Маржа
# --------------------------------------------------------------------------- #

def apply_margin_1x2(p1: float, px: float, p2: float, margin: float) -> Tuple[float, float, float]:
    f = 1.0 + margin
    return 1.0 / (p1 * f), 1.0 / (px * f), 1.0 / (p2 * f)


def apply_margin_two_way(p_a: float, p_b: float, margin: float) -> Tuple[float, float]:
    f = 1.0 + margin
    return 1.0 / (p_a * f), 1.0 / (p_b * f)
