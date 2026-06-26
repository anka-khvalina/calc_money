"""
Обучение голевой модели на исторических closing-линиях и прогноз матча.

Pipeline (см. спецификацию):
  1. prepare + base weights (сезон/качество/дерби/нейтраль);
  2. de-vig (two-way для AH/тоталов, Shin для 1X2);
  3. восстановление S_m, D_m → λ_h, λ_a;
  4. рейтинг силы r_i, H — robust WLS по D_m;
  5. attack/defense μ, A_i, Df_i, H_g — robust WLS по log λ;
  6. калибровка a,b,c,d,γ по 1X2 (Shin) — Nelder–Mead;
  7. прогноз будущего матча → λ_h,λ_a → матрица → все рынки.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:
    from . import goal_model as gm
    from . import team_ranking as tr
except ImportError:  # pragma: no cover
    import goal_model as gm
    import team_ranking as tr


# --------------------------------------------------------------------------- #
# Конфиг и веса
# --------------------------------------------------------------------------- #

@dataclass
class SeasonWeight:
    label: str
    date_from: date
    date_to: date
    base_weight: float


@dataclass
class ModelConfig:
    max_goals: int = gm.MAX_GOALS_DEFAULT
    use_dixon_coles: bool = True
    target_margin: float = 0.03
    add_margin: bool = False

    # веса
    default_season_weight: float = 1.0
    season_weights: List[SeasonWeight] = field(default_factory=list)
    exclude_data_errors: bool = True

    # веса по размеру линии (фора)
    alpha_ah: float = 0.25
    p_ah: float = 2.0
    min_w_line_ah: float = 0.15
    max_w_line_ah: float = 1.0

    # веса по экстремальности тотала
    alpha_t: float = 0.50
    min_w_line_t: float = 0.30
    max_w_line_t: float = 1.0

    # robust
    delta_ah: float = 0.5
    delta_lambda: float = 0.25
    max_iter: int = 30
    tolerance: float = 1e-4

    # калибровка
    draw_loss_weight: float = 1.5

    # модель ничьей (отдельная) + коррекция диагонали матрицы
    use_draw_model: bool = True
    draw_diag_multiplier_min: float = 0.85
    draw_diag_multiplier_max: float = 1.15
    w_1x2_normal: float = 1.0
    w_1x2_suspicious: float = 0.5
    w_1x2_missing: float = 0.0

    # prior прошлого сезона и новички лиги (§15)
    prior_alpha: float = 0.70          # стягивание рейтинга прошлого сезона
    prior_weight: float = 0.0          # вес ridge-привязки к prior (0 = выкл.)
    promoted_reference_n: int = 3       # сколько слабейших усреднять для новичка
    allow_unknown_teams: bool = True    # подставлять fallback для новичков

    # прогноз / дерби
    lambda_epsilon: float = 0.05
    derby_shrink_tau: float = 30.0       # τ в w = n/(n+τ) для shrinkage δ_derby
    derby_h_default_ratio: float = 0.40  # H_derby ≈ ratio×H_league если мало дерби в выборке
    derby_min_matches: int = 3           # минимум дерби-матчей для оценки δ_derby


# --------------------------------------------------------------------------- #
# Историческая запись матча
# --------------------------------------------------------------------------- #

@dataclass
class RawMatch:
    date: Optional[date]
    league: str
    home_team: str
    away_team: str
    closing_ah_home: Optional[float] = None
    closing_total_line: Optional[float] = None
    ah_home_odds: Optional[float] = None
    ah_away_odds: Optional[float] = None
    over_odds: Optional[float] = None
    under_odds: Optional[float] = None
    home_odds: Optional[float] = None
    draw_odds: Optional[float] = None
    away_odds: Optional[float] = None
    neutral_flag: bool = False
    derby_flag: bool = False
    quality_flag: Optional[str] = None
    quality_match_weight: Optional[float] = None
    derby_match_weight: Optional[float] = None
    neutral_match_weight: Optional[float] = None


_CSV_ALIASES: Dict[str, str] = {
    "date": "date", "league": "league",
    "home_team": "home_team", "home": "home_team", "team_home": "home_team",
    "away_team": "away_team", "away": "away_team", "team_away": "away_team",
    "closing_ah_home": "closing_ah_home", "ah_home_line": "closing_ah_home",
    "ah_line": "closing_ah_home", "handicap": "closing_ah_home",
    "closing_total_line": "closing_total_line", "total_line": "closing_total_line",
    "total": "closing_total_line",
    "ah_home_odds": "ah_home_odds", "ah_away_odds": "ah_away_odds",
    "over_odds": "over_odds", "under_odds": "under_odds",
    "home_odds": "home_odds", "odds_1": "home_odds", "p1": "home_odds",
    "draw_odds": "draw_odds", "odds_x": "draw_odds", "x": "draw_odds",
    "away_odds": "away_odds", "odds_2": "away_odds", "p2": "away_odds",
    "neutral_flag": "neutral_flag", "neutral": "neutral_flag",
    "derby_flag": "derby_flag", "derby": "derby_flag",
    "quality_flag": "quality_flag", "quality": "quality_flag",
    "value": "value", "quality_value": "value", "quality_weight": "value",
    "derby_weight": "derby_weight", "derby_value": "derby_weight",
    "neutral_weight": "neutral_weight", "neutral_value": "neutral_weight",
}


def _norm_key(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum() or ch == "_")


def _to_float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    t = str(text).strip().replace(",", ".")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _to_bool(text: Optional[str]) -> bool:
    return str(text).strip().lower() in ("1", "true", "yes", "y", "да", "истина")


def _to_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    t = str(text).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def parse_raw_matches(text: str) -> List["RawMatch"]:
    """CSV (с заголовком) исторических closing-линий → список RawMatch."""
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [_CSV_ALIASES.get(_norm_key(h), "") for h in rows[0]]
    out: List[RawMatch] = []
    for row in rows[1:]:
        rec: Dict[str, str] = {}
        for col, val in zip(header, row):
            if col:
                rec[col] = val
        if not rec.get("home_team") or not rec.get("away_team"):
            continue
        out.append(RawMatch(
            date=_to_date(rec.get("date")),
            league=(rec.get("league") or "").strip(),
            home_team=rec["home_team"].strip(),
            away_team=rec["away_team"].strip(),
            closing_ah_home=_to_float(rec.get("closing_ah_home")),
            closing_total_line=_to_float(rec.get("closing_total_line")),
            ah_home_odds=_to_float(rec.get("ah_home_odds")),
            ah_away_odds=_to_float(rec.get("ah_away_odds")),
            over_odds=_to_float(rec.get("over_odds")),
            under_odds=_to_float(rec.get("under_odds")),
            home_odds=_to_float(rec.get("home_odds")),
            draw_odds=_to_float(rec.get("draw_odds")),
            away_odds=_to_float(rec.get("away_odds")),
            neutral_flag=_to_bool(rec.get("neutral_flag")),
            derby_flag=_to_bool(rec.get("derby_flag")),
            quality_flag=(rec.get("quality_flag") or "").strip() or None,
            quality_match_weight=_to_float(rec.get("value")),
            derby_match_weight=_to_float(rec.get("derby_weight")),
            neutral_match_weight=_to_float(rec.get("neutral_weight")),
        ))
    return out


def load_raw_matches(path: Path) -> List["RawMatch"]:
    return parse_raw_matches(Path(path).read_text(encoding="utf-8-sig"))


@dataclass
class PreparedMatch:
    raw: RawMatch
    home_team: str
    away_team: str
    i_home: int
    w_base: float = 1.0
    p_over_fair: Optional[float] = None
    p_ah_home_fair: Optional[float] = None
    p1_shin: Optional[float] = None
    px_shin: Optional[float] = None
    p2_shin: Optional[float] = None
    sum_goals: Optional[float] = None
    diff_goals: Optional[float] = None
    lambda_home: Optional[float] = None
    lambda_away: Optional[float] = None
    w_line_ah: float = 1.0
    w_line_t: float = 1.0
    w_robust: float = 1.0


# --------------------------------------------------------------------------- #
# Веса
# --------------------------------------------------------------------------- #

def season_weight(d: Optional[date], cfg: ModelConfig) -> float:
    if d is None:
        return cfg.default_season_weight
    # При пересечении диапазонов применяется добавленный последним (latest entry wins).
    for sw in reversed(cfg.season_weights):
        if sw.date_from <= d <= sw.date_to:
            return sw.base_weight
    return cfg.default_season_weight


def _match_weight(flag: bool, weight: Optional[float]) -> float:
    if not flag:
        return 1.0
    return 1.0 if weight is None else weight


DERBY_FLAG_YES: float = 1.0
DERBY_FLAG_NO: float = 0.0


def _derby_flag_from_weight(w: Optional[float]) -> bool:
    if w is None:
        return False
    return abs(float(w) - DERBY_FLAG_YES) < 1e-9


def _is_derby_match(m: RawMatch) -> bool:
    if m.derby_flag:
        return True
    return _derby_flag_from_weight(m.derby_match_weight)


def _neutral_weight_mult(m: RawMatch) -> float:
    if not m.neutral_flag:
        return 1.0
    return 1.0 if m.neutral_match_weight is None else m.neutral_match_weight


def _derby_home_indicator(m: "PreparedMatch") -> int:
    """1 если матч дерби на домашнем поле (не нейтраль)."""
    if m.i_home != 1:
        return 0
    return 1 if _is_derby_match(m.raw) else 0


def effective_home_advantage(
    strength: "StrengthModel",
    cfg: ModelConfig,
    *,
    neutral: bool,
    derby: bool,
) -> float:
    """H для прогноза: 0 на нейтрали; для дерби — H_league + shrinkage(δ_derby)."""
    if neutral:
        return 0.0
    h = strength.home_advantage
    if not derby:
        return h
    if strength.derby_n <= 0:
        return h * cfg.derby_h_default_ratio
    h_obs = h + strength.derby_home_delta
    w = strength.derby_shrink_w
    return w * h_obs + (1.0 - w) * h


def base_weight(m: RawMatch, cfg: ModelConfig) -> float:
    w_s = season_weight(m.date, cfg)
    w_m = 1.0 if m.quality_match_weight is None else m.quality_match_weight
    w_n = _neutral_weight_mult(m)
    return w_s * w_m * w_n


# --------------------------------------------------------------------------- #
# Этапы 1–3: подготовка, de-vig, восстановление S/D
# --------------------------------------------------------------------------- #

def prepare_matches(raw: Sequence[RawMatch], cfg: ModelConfig) -> List[PreparedMatch]:
    prepared: List[PreparedMatch] = []
    for r in raw:
        if r.quality_flag == "data_error" and cfg.exclude_data_errors:
            continue
        pm = PreparedMatch(
            raw=r,
            home_team=r.home_team.strip(),
            away_team=r.away_team.strip(),
            i_home=0 if r.neutral_flag else 1,
            w_base=base_weight(r, cfg),
        )
        prepared.append(pm)
    return prepared


def devig_and_infer(matches: Sequence[PreparedMatch], cfg: ModelConfig) -> None:
    """Заполняет p_*_fair, Shin, S/D, λ для каждого матча (in place)."""
    for m in matches:
        r = m.raw
        if r.over_odds and r.under_odds:
            m.p_over_fair, _ = gm.devig_two_way(r.over_odds, r.under_odds)
        if r.ah_home_odds and r.ah_away_odds:
            m.p_ah_home_fair, _ = gm.devig_two_way(r.ah_home_odds, r.ah_away_odds)
        if r.home_odds and r.draw_odds and r.away_odds:
            m.p1_shin, m.px_shin, m.p2_shin = gm.shin_devig_1x2(
                r.home_odds, r.draw_odds, r.away_odds
            )

        # S_m
        if r.closing_total_line is not None and m.p_over_fair is not None:
            m.sum_goals = gm.infer_total_sum(
                r.closing_total_line, m.p_over_fair, max_goals=cfg.max_goals
            )
        elif r.closing_total_line is not None:
            m.sum_goals = r.closing_total_line

        # D_m
        if m.sum_goals is not None:
            if r.closing_ah_home is not None and m.p_ah_home_fair is not None:
                m.diff_goals = gm.infer_goal_diff(
                    r.closing_ah_home, m.p_ah_home_fair, m.sum_goals,
                    max_goals=cfg.max_goals, eps=cfg.lambda_epsilon,
                )
            elif r.closing_ah_home is not None:
                m.diff_goals = gm.clamp_goal_diff(-r.closing_ah_home, m.sum_goals, cfg.lambda_epsilon)

        if m.sum_goals is not None and m.diff_goals is not None:
            m.diff_goals = gm.clamp_goal_diff(m.diff_goals, m.sum_goals, cfg.lambda_epsilon)
            m.lambda_home = (m.sum_goals + m.diff_goals) / 2.0
            m.lambda_away = (m.sum_goals - m.diff_goals) / 2.0

    # Вес по экстремальности тотала: w_line_T = 1/(1+alpha_T·(S−S̄)²), clamp.
    s_vals = [m.sum_goals for m in matches if m.sum_goals is not None]
    s_mean = sum(s_vals) / len(s_vals) if s_vals else 0.0
    for m in matches:
        if m.sum_goals is None:
            continue
        w = 1.0 / (1.0 + cfg.alpha_t * (m.sum_goals - s_mean) ** 2)
        m.w_line_t = min(cfg.max_w_line_t, max(cfg.min_w_line_t, w))


# --------------------------------------------------------------------------- #
# Этап 4: рейтинг силы (robust WLS)
# --------------------------------------------------------------------------- #

@dataclass
class StrengthModel:
    ratings: Dict[str, float]
    home_advantage: float
    derby_home_delta: float = 0.0
    derby_n: int = 0
    derby_shrink_w: float = 0.0
    mae: float = 0.0
    rmse: float = 0.0
    n: int = 0

    @property
    def home_advantage_derby(self) -> float:
        """H_derby после shrinkage (для отчётов)."""
        if self.derby_n <= 0:
            return self.home_advantage
        h_obs = self.home_advantage + self.derby_home_delta
        return self.derby_shrink_w * h_obs + (1.0 - self.derby_shrink_w) * self.home_advantage

    @property
    def derby_h_ratio(self) -> float:
        h = self.home_advantage
        if abs(h) < 1e-9:
            return 1.0
        return self.home_advantage_derby / h


def _huber_weight(e: float, delta: float) -> float:
    ae = abs(e)
    return 1.0 if ae <= delta else delta / ae


def fit_strength_ratings(
    matches: Sequence[PreparedMatch],
    cfg: ModelConfig,
    prior_ratings: Optional[Dict[str, float]] = None,
) -> StrengthModel:
    used = [m for m in matches if m.diff_goals is not None]
    if len(used) < 2:
        raise ValueError("Недостаточно матчей с восстановленной разницей D_m")
    teams = sorted({m.home_team for m in used} | {m.away_team for m in used})
    idx = {t: i for i, t in enumerate(teams)}
    n_derby = sum(_derby_home_indicator(m) for m in used)
    use_derby_coef = n_derby >= cfg.derby_min_matches
    p = len(teams) + (2 if use_derby_coef else 1)
    h_col = p - 2 if use_derby_coef else p - 1
    d_col = p - 1 if use_derby_coef else None

    # ridge-привязка к prior прошлого сезона: r_t ≈ α·prior_t
    prior_rows: List[Tuple[List[float], float, float]] = []
    if prior_ratings and cfg.prior_weight > 0:
        for t, pr in prior_ratings.items():
            if t in idx:
                row = [0.0] * p
                row[idx[t]] = 1.0
                prior_rows.append((row, cfg.prior_alpha * pr, cfg.prior_weight))

    coeffs: List[List[float]] = []
    targets: List[float] = []
    for m in used:
        row = [0.0] * p
        row[idx[m.home_team]] += 1.0
        row[idx[m.away_team]] -= 1.0
        row[h_col] = float(m.i_home)
        if d_col is not None:
            row[d_col] = float(_derby_home_indicator(m))
        coeffs.append(row)
        targets.append(m.diff_goals)
        m.w_line_ah = min(
            cfg.max_w_line_ah,
            max(cfg.min_w_line_ah, 1.0 / (1.0 + cfg.alpha_ah * abs(m.diff_goals) ** cfg.p_ah)),
        )
        m.w_robust = 1.0

    gauge = [1.0] * len(teams) + [0.0] * (p - len(teams))

    def solve(weights: Sequence[float]) -> Tuple[List[float], List[float]]:
        rows = [(coeffs[i], targets[i], weights[i]) for i in range(len(used))]
        rows.extend(prior_rows)
        rows.append((gauge, 0.0, tr.GAUGE_WEIGHT))
        sol = tr._solve_weighted(rows, p)
        resid = [
            (sum(coeffs[i][k] * sol[k] for k in range(p)) - targets[i])
            for i in range(len(used))
        ]
        return sol, resid

    weights = [m.w_base * m.w_line_ah for m in used]
    sol, resid = solve(weights)
    for _ in range(cfg.max_iter):
        new_w = []
        for i, m in enumerate(used):
            m.w_robust = _huber_weight(resid[i], cfg.delta_ah)
            new_w.append(m.w_base * m.w_line_ah * m.w_robust)
        delta = max(abs(new_w[i] - weights[i]) for i in range(len(used)))
        weights = new_w
        sol, resid = solve(weights)
        if delta < cfg.tolerance:
            break

    ratings = {t: sol[idx[t]] for t in teams}
    mean_r = sum(ratings.values()) / len(ratings)
    ratings = {t: r - mean_r for t, r in ratings.items()}
    mae = sum(abs(e) for e in resid) / len(resid)
    rmse = math.sqrt(sum(e * e for e in resid) / len(resid))
    h_league = sol[h_col]
    delta_raw = sol[d_col] if d_col is not None else 0.0
    if n_derby > 0 and use_derby_coef:
        shrink_w = n_derby / (n_derby + cfg.derby_shrink_tau)
        delta_shrunk = shrink_w * delta_raw
    else:
        shrink_w = 0.0
        delta_shrunk = 0.0
    return StrengthModel(
        ratings=ratings,
        home_advantage=h_league,
        derby_home_delta=delta_shrunk,
        derby_n=n_derby,
        derby_shrink_w=shrink_w,
        mae=mae,
        rmse=rmse,
        n=len(used),
    )


# --------------------------------------------------------------------------- #
# Этап 5: attack/defense (robust WLS по log λ)
# --------------------------------------------------------------------------- #

@dataclass
class GoalModel:
    mu: float
    attack: Dict[str, float]
    defense: Dict[str, float]
    home_goal_adv: float
    mae: float = 0.0
    rmse: float = 0.0
    n: int = 0


def fit_attack_defense(
    matches: Sequence[PreparedMatch],
    cfg: ModelConfig,
    prior_attack: Optional[Dict[str, float]] = None,
    prior_defense: Optional[Dict[str, float]] = None,
) -> GoalModel:
    used = [m for m in matches if m.lambda_home and m.lambda_away
            and m.lambda_home > 0 and m.lambda_away > 0]
    if len(used) < 2:
        raise ValueError("Недостаточно матчей с восстановленными λ")
    teams = sorted({m.home_team for m in used} | {m.away_team for m in used})
    nt = len(teams)
    a_idx = {t: i for i, t in enumerate(teams)}            # attack
    d_idx = {t: nt + i for i, t in enumerate(teams)}       # defense
    mu_col = 2 * nt
    hg_col = 2 * nt + 1
    p = 2 * nt + 2

    rows: List[List[float]] = []
    targets: List[float] = []
    base_w: List[float] = []
    for m in used:
        # строка хозяев: log λ_h = μ + A_home − Df_away + H_g·I_home
        rh = [0.0] * p
        rh[a_idx[m.home_team]] += 1.0
        rh[d_idx[m.away_team]] -= 1.0
        rh[mu_col] = 1.0
        rh[hg_col] = float(m.i_home)
        rows.append(rh)
        targets.append(math.log(m.lambda_home))
        base_w.append(m.w_base * m.w_line_t)
        # строка гостей: log λ_a = μ + A_away − Df_home
        ra = [0.0] * p
        ra[a_idx[m.away_team]] += 1.0
        ra[d_idx[m.home_team]] -= 1.0
        ra[mu_col] = 1.0
        rows.append(ra)
        targets.append(math.log(m.lambda_away))
        base_w.append(m.w_base * m.w_line_t)

    # gauge: Σ A = 0, Σ Df = 0
    gauge_a = [0.0] * p
    gauge_d = [0.0] * p
    for t in teams:
        gauge_a[a_idx[t]] = 1.0
        gauge_d[d_idx[t]] = 1.0

    prior_rows: List[Tuple[List[float], float, float]] = []
    if cfg.prior_weight > 0:
        for t in teams:
            if prior_attack and t in prior_attack:
                row = [0.0] * p
                row[a_idx[t]] = 1.0
                prior_rows.append((row, cfg.prior_alpha * prior_attack[t], cfg.prior_weight))
            if prior_defense and t in prior_defense:
                row = [0.0] * p
                row[d_idx[t]] = 1.0
                prior_rows.append((row, cfg.prior_alpha * prior_defense[t], cfg.prior_weight))

    def solve(weights: Sequence[float]) -> Tuple[List[float], List[float]]:
        wrows = [(rows[i], targets[i], weights[i]) for i in range(len(rows))]
        wrows.extend(prior_rows)
        wrows.append((gauge_a, 0.0, tr.GAUGE_WEIGHT))
        wrows.append((gauge_d, 0.0, tr.GAUGE_WEIGHT))
        sol = tr._solve_weighted(wrows, p)
        resid = [
            (sum(rows[i][k] * sol[k] for k in range(p)) - targets[i])
            for i in range(len(rows))
        ]
        return sol, resid

    w_robust = [1.0] * len(rows)
    weights = list(base_w)
    sol, resid = solve(weights)
    for _ in range(cfg.max_iter):
        new_w = []
        for i in range(len(rows)):
            w_robust[i] = _huber_weight(resid[i], cfg.delta_lambda)
            new_w.append(base_w[i] * w_robust[i])
        delta = max(abs(new_w[i] - weights[i]) for i in range(len(rows)))
        weights = new_w
        sol, resid = solve(weights)
        if delta < cfg.tolerance:
            break

    attack = {t: sol[a_idx[t]] for t in teams}
    defense = {t: sol[d_idx[t]] for t in teams}
    ma = sum(attack.values()) / nt
    md = sum(defense.values()) / nt
    attack = {t: v - ma for t, v in attack.items()}
    defense = {t: v - md for t, v in defense.items()}
    mae = sum(abs(e) for e in resid) / len(resid)
    rmse = math.sqrt(sum(e * e for e in resid) / len(resid))
    return GoalModel(
        mu=sol[mu_col] + ma - md,  # μ впитывает сдвиги нормировки атаки/обороны
        attack=attack,
        defense=defense,
        home_goal_adv=sol[hg_col],
        mae=mae, rmse=rmse, n=len(used),
    )


# --------------------------------------------------------------------------- #
# Этап 6: калибровка a,b,c,d,γ (Nelder–Mead по 1X2 Shin)
# --------------------------------------------------------------------------- #

@dataclass
class Calibration:
    a: float = 0.0
    b: float = 1.0
    c: float = 0.0
    d: float = 1.0
    gamma: float = 0.0
    loss: float = 0.0


def _model_d_s(
    m: PreparedMatch,
    strength: StrengthModel,
    goals: GoalModel,
    cfg: ModelConfig,
) -> Tuple[float, float]:
    derby = _is_derby_match(m.raw) and m.i_home == 1
    h_eff = effective_home_advantage(
        strength, cfg, neutral=m.i_home == 0, derby=derby,
    )
    d_model = (
        strength.ratings.get(m.home_team, 0.0)
        - strength.ratings.get(m.away_team, 0.0)
        + h_eff
    )
    k_hg = (h_eff / strength.home_advantage) if strength.home_advantage > 1e-9 else 1.0
    lh = math.exp(goals.mu + goals.attack.get(m.home_team, 0.0)
                  - goals.defense.get(m.away_team, 0.0) + goals.home_goal_adv * m.i_home * k_hg)
    la = math.exp(goals.mu + goals.attack.get(m.away_team, 0.0)
                  - goals.defense.get(m.home_team, 0.0))
    return d_model, lh + la


def nelder_mead(
    f: Callable[[List[float]], float],
    x0: List[float],
    *,
    step: float = 0.2,
    max_iter: int = 400,
    tol: float = 1e-7,
) -> List[float]:
    n = len(x0)
    simplex = [list(x0)]
    for i in range(n):
        pt = list(x0)
        pt[i] += step if pt[i] == 0 else step * abs(pt[i])
        simplex.append(pt)
    fvals = [f(p) for p in simplex]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda k: fvals[k])
        simplex = [simplex[k] for k in order]
        fvals = [fvals[k] for k in order]
        if abs(fvals[-1] - fvals[0]) < tol:
            break
        centroid = [sum(simplex[k][i] for k in range(n)) / n for i in range(n)]
        # reflection
        xr = [centroid[i] + (centroid[i] - simplex[-1][i]) for i in range(n)]
        fr = f(xr)
        if fvals[0] <= fr < fvals[-2]:
            simplex[-1], fvals[-1] = xr, fr
        elif fr < fvals[0]:
            xe = [centroid[i] + 2.0 * (centroid[i] - simplex[-1][i]) for i in range(n)]
            fe = f(xe)
            if fe < fr:
                simplex[-1], fvals[-1] = xe, fe
            else:
                simplex[-1], fvals[-1] = xr, fr
        else:
            xc = [centroid[i] + 0.5 * (simplex[-1][i] - centroid[i]) for i in range(n)]
            fc = f(xc)
            if fc < fvals[-1]:
                simplex[-1], fvals[-1] = xc, fc
            else:
                for k in range(1, n + 1):
                    simplex[k] = [simplex[0][i] + 0.5 * (simplex[k][i] - simplex[0][i]) for i in range(n)]
                    fvals[k] = f(simplex[k])
    best = min(range(n + 1), key=lambda k: fvals[k])
    return simplex[best]


def calibrate(
    matches: Sequence[PreparedMatch],
    strength: StrengthModel,
    goals: GoalModel,
    cfg: ModelConfig,
) -> Calibration:
    used = [m for m in matches if m.p1_shin is not None]
    if not used:
        return Calibration()
    pre = [(m, *_model_d_s(m, strength, goals, cfg), m.w_base) for m in used]

    def loss(params: List[float]) -> float:
        a, b, c, d, gamma = params
        if d <= 0:
            return 1e9
        total = 0.0
        for m, d_model, s_model, w in pre:
            s_final = c + d * s_model
            if s_final <= 0.2:
                return 1e9
            d_final = gm.clamp_goal_diff(a + b * d_model, s_final, cfg.lambda_epsilon)
            lh = (s_final + d_final) / 2.0
            la = (s_final - d_final) / 2.0
            if lh <= 0 or la <= 0:
                return 1e9
            matrix = gm.build_score_matrix(lh, la, cfg.max_goals)
            if cfg.use_dixon_coles and gamma:
                matrix = gm.apply_dixon_coles(matrix, lh, la, gamma)
            p1, px, p2 = gm.compute_1x2(matrix)
            total += w * (
                (p1 - m.p1_shin) ** 2
                + cfg.draw_loss_weight * (px - m.px_shin) ** 2
                + (p2 - m.p2_shin) ** 2
            )
        return total

    x = nelder_mead(loss, [0.0, 1.0, 0.0, 1.0, 0.0])
    return Calibration(a=x[0], b=x[1], c=x[2], d=x[3], gamma=x[4], loss=loss(x))


# --------------------------------------------------------------------------- #
# Обучение и прогноз
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Модель ничьей: logit(P_X) = α + β_D·|D| + β_S·S + β_S2·S² + β_DxS·|D|·S
# --------------------------------------------------------------------------- #

@dataclass
class DrawModel:
    alpha: float = 0.0
    beta_d: float = 0.0
    beta_s: float = 0.0
    beta_s2: float = 0.0
    beta_dxs: float = 0.0
    n: int = 0
    source: str = "fitted"  # fitted | default

    def target_px(self, d_final: float, s_final: float) -> float:
        z = (self.alpha
             + self.beta_d * abs(d_final)
             + self.beta_s * s_final
             + self.beta_s2 * s_final * s_final
             + self.beta_dxs * abs(d_final) * s_final)
        # σ(z) с защитой от переполнения
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)


_DRAW_DEFAULT = DrawModel(alpha=-0.95, beta_d=-0.55, beta_s=0.0, beta_s2=0.0,
                          beta_dxs=0.0, n=0, source="default")


def w_1x2_weight(m: PreparedMatch, cfg: ModelConfig) -> float:
    """Вес надёжности рынка 1X2 для обучения ничьей."""
    r = m.raw
    if not (r.home_odds and r.draw_odds and r.away_odds):
        return cfg.w_1x2_missing
    if m.raw.quality_flag == "suspicious_line":
        return cfg.w_1x2_suspicious
    return cfg.w_1x2_normal


def fit_draw_model(matches: Sequence[PreparedMatch], cfg: ModelConfig) -> DrawModel:
    """Взвешенный МНК logit(px_Shin) ~ 1 + |D| + S + S² + |D|·S."""
    rows: List[Tuple[List[float], float, float]] = []
    for m in matches:
        if m.px_shin is None or m.diff_goals is None or m.sum_goals is None:
            continue
        px = m.px_shin
        if px <= 0.0 or px >= 1.0:
            continue
        w = (m.w_base * w_1x2_weight(m, cfg))
        if w <= 0:
            continue
        d_abs = abs(m.diff_goals)
        s = m.sum_goals
        feats = [1.0, d_abs, s, s * s, d_abs * s]
        y = math.log(px / (1.0 - px))
        rows.append((feats, y, w))
    if len(rows) < 6:
        return _DRAW_DEFAULT
    p = 5
    try:
        beta = tr._solve_weighted(rows, p)
    except ValueError:
        return _DRAW_DEFAULT
    dm = DrawModel(alpha=beta[0], beta_d=beta[1], beta_s=beta[2],
                   beta_s2=beta[3], beta_dxs=beta[4], n=len(rows), source="fitted")
    # Санити: базовая ничья (D=0, S=2.6) в разумных пределах, иначе дефолт.
    base = dm.target_px(0.0, 2.6)
    if not (0.10 <= base <= 0.45):
        return _DRAW_DEFAULT
    return dm


@dataclass
class DrawDiagnosticRow:
    date: Optional[date]
    home_team: str
    away_team: str
    s_m: float
    d_m: float
    p_draw_shin: float
    p_draw_model: float
    error: float
    w_draw_final: float


def draw_diagnostics(
    model: "TrainedModel", prepared: Sequence[PreparedMatch]
) -> List[DrawDiagnosticRow]:
    dm = model.draw
    cfg = model.config
    out: List[DrawDiagnosticRow] = []
    for m in prepared:
        if m.px_shin is None or m.diff_goals is None or m.sum_goals is None:
            continue
        p_model = dm.target_px(m.diff_goals, m.sum_goals)
        out.append(DrawDiagnosticRow(
            date=m.raw.date, home_team=m.home_team, away_team=m.away_team,
            s_m=m.sum_goals, d_m=m.diff_goals,
            p_draw_shin=m.px_shin, p_draw_model=p_model,
            error=p_model - m.px_shin,
            w_draw_final=m.w_base * w_1x2_weight(m, cfg),
        ))
    out.sort(key=lambda r: abs(r.error), reverse=True)
    return out


@dataclass
class TrainedModel:
    strength: StrengthModel
    goals: GoalModel
    calibration: Calibration
    draw: DrawModel
    config: ModelConfig


def train_full_model(
    raw: Sequence[RawMatch],
    cfg: Optional[ModelConfig] = None,
    prior: Optional["TrainedModel"] = None,
) -> Tuple[TrainedModel, List[PreparedMatch]]:
    """Обучить модель. prior — модель прошлого сезона для ridge-стягивания (§15)."""
    cfg = cfg or ModelConfig()
    matches = prepare_matches(raw, cfg)
    devig_and_infer(matches, cfg)
    prior_r = prior.strength.ratings if prior else None
    prior_a = prior.goals.attack if prior else None
    prior_d = prior.goals.defense if prior else None
    strength = fit_strength_ratings(matches, cfg, prior_ratings=prior_r)
    goals = fit_attack_defense(matches, cfg, prior_attack=prior_a, prior_defense=prior_d)
    calibration = calibrate(matches, strength, goals, cfg)
    draw = fit_draw_model(matches, cfg) if cfg.use_draw_model else _DRAW_DEFAULT
    return TrainedModel(strength, goals, calibration, draw, cfg), matches


@dataclass
class Prediction:
    home_team: str
    away_team: str
    lambda_home: float
    lambda_away: float
    d_model: float
    s_model: float
    d_final: float
    s_final: float
    markets: gm.MatchMarkets
    draw_target: Optional[float] = None
    draw_diagnostics: Optional[Dict[str, float]] = None


def _promoted_rating(values: Dict[str, float], n: int) -> float:
    """Стартовый рейтинг новичка лиги = среднее n слабейших команд (§15)."""
    if not values:
        return 0.0
    weakest = sorted(values.values())[: max(1, n)]
    return sum(weakest) / len(weakest)


def predict_match(
    model: TrainedModel,
    home_team: str,
    away_team: str,
    *,
    neutral: bool = False,
    derby: bool = False,
) -> Prediction:
    cfg = model.config
    s, g, cal = model.strength, model.goals, model.calibration
    i_home = 0 if neutral else 1
    h_eff = effective_home_advantage(s, cfg, neutral=neutral, derby=derby)

    unknown = [t for t in (home_team, away_team) if t not in s.ratings]
    if unknown and not cfg.allow_unknown_teams:
        raise ValueError(f"Команда не найдена в модели: {', '.join(unknown)}")

    def rating(t: str) -> float:
        return s.ratings[t] if t in s.ratings else _promoted_rating(s.ratings, cfg.promoted_reference_n)

    def attack(t: str) -> float:
        return g.attack[t] if t in g.attack else _promoted_rating(g.attack, cfg.promoted_reference_n)

    def defense(t: str) -> float:
        return g.defense[t] if t in g.defense else _promoted_rating(g.defense, cfg.promoted_reference_n)

    k_hg = (h_eff / s.home_advantage) if s.home_advantage > 1e-9 else 1.0
    d_model = rating(home_team) - rating(away_team) + h_eff
    lh_ad = math.exp(g.mu + attack(home_team) - defense(away_team) + g.home_goal_adv * i_home * k_hg)
    la_ad = math.exp(g.mu + attack(away_team) - defense(home_team))
    s_model = lh_ad + la_ad

    d_final = cal.a + cal.b * d_model
    s_final = cal.c + cal.d * s_model
    d_final = gm.clamp_goal_diff(d_final, s_final, cfg.lambda_epsilon)
    lambda_home = (s_final + d_final) / 2.0
    lambda_away = (s_final - d_final) / 2.0

    matrix = gm.build_score_matrix(lambda_home, lambda_away, cfg.max_goals)
    if cfg.use_dixon_coles and cal.gamma:
        matrix = gm.apply_dixon_coles(matrix, lambda_home, lambda_away, cal.gamma)

    draw_target = None
    draw_diag = None
    if cfg.use_draw_model:
        draw_target = model.draw.target_px(d_final, s_final)
        matrix, draw_diag = gm.adjust_matrix_to_draw_target(
            matrix, draw_target,
            q_min=cfg.draw_diag_multiplier_min,
            q_max=cfg.draw_diag_multiplier_max,
        )

    markets = gm.markets_from_matrix(matrix)
    return Prediction(
        home_team=home_team, away_team=away_team,
        lambda_home=lambda_home, lambda_away=lambda_away,
        d_model=d_model, s_model=s_model, d_final=d_final, s_final=s_final,
        markets=markets,
        draw_target=draw_target,
        draw_diagnostics=draw_diag,
    )


# --------------------------------------------------------------------------- #
# Диагностика рейтинга силы (§17): D_market vs D_model по матчам
# --------------------------------------------------------------------------- #

@dataclass
class StrengthDiagnosticRow:
    date: Optional[date]
    home_team: str
    away_team: str
    d_market: float
    d_model: float
    error: float
    w_base: float
    w_line_ah: float
    w_line_t: float


def strength_diagnostics(
    model: TrainedModel, prepared: Sequence[PreparedMatch]
) -> List[StrengthDiagnosticRow]:
    s = model.strength
    out: List[StrengthDiagnosticRow] = []
    for m in prepared:
        if m.diff_goals is None:
            continue
        if m.home_team not in s.ratings or m.away_team not in s.ratings:
            continue
        d_model = (
            s.ratings[m.home_team] - s.ratings[m.away_team]
            + effective_home_advantage(
                s, model.config,
                neutral=m.i_home == 0,
                derby=_is_derby_match(m.raw) and m.i_home == 1,
            )
        )
        out.append(StrengthDiagnosticRow(
            date=m.raw.date, home_team=m.home_team, away_team=m.away_team,
            d_market=m.diff_goals, d_model=d_model, error=m.diff_goals - d_model,
            w_base=m.w_base, w_line_ah=m.w_line_ah, w_line_t=m.w_line_t,
        ))
    out.sort(key=lambda r: abs(r.error), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Walk-forward валидация (§16)
# --------------------------------------------------------------------------- #

@dataclass
class WalkForwardMetrics:
    n_eval: int
    mae_ah: float       # |центральная фора модели − closing_AH_home|
    mae_total: float    # |центральный тотал модели − closing_total_line|
    mae_p1: float       # |P1_model − P1_Shin|
    mae_px: float       # ничья
    mae_p2: float
    bias_px: float      # средн. (px_model − px_shin): систематический сдвиг ничьей


def walk_forward_validate(
    raw: Sequence[RawMatch],
    cfg: Optional[ModelConfig] = None,
    *,
    min_train: int = 12,
) -> WalkForwardMetrics:
    """Для каждого матча: обучаем на более ранних, прогнозируем, сравниваем с closing."""
    cfg = cfg or ModelConfig()
    dated = [r for r in raw if r.date is not None]
    dated.sort(key=lambda r: r.date)
    undated = [r for r in raw if r.date is None]
    ordered = dated + undated

    ah_err: List[float] = []
    t_err: List[float] = []
    p1_err: List[float] = []
    px_err: List[float] = []
    p2_err: List[float] = []
    px_signed: List[float] = []

    for k in range(len(ordered)):
        if k < min_train:
            continue
        target = ordered[k]
        train = ordered[:k]
        try:
            model, _ = train_full_model(train, cfg)
            pred = predict_match(
                model, target.home_team, target.away_team,
                neutral=target.neutral_flag, derby=target.derby_flag,
            )
        except (ValueError, ZeroDivisionError):
            continue
        mk = pred.markets
        if target.closing_ah_home is not None:
            ah_err.append(abs(mk.main_ah.line - target.closing_ah_home))
        if target.closing_total_line is not None:
            t_err.append(abs(mk.main_total.line - target.closing_total_line))
        if target.home_odds and target.draw_odds and target.away_odds:
            ps1, psx, ps2 = gm.shin_devig_1x2(target.home_odds, target.draw_odds, target.away_odds)
            p1_err.append(abs(mk.p1 - ps1))
            px_err.append(abs(mk.px - psx))
            p2_err.append(abs(mk.p2 - ps2))
            px_signed.append(mk.px - psx)

    def _mean(xs: List[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    n_eval = max(len(ah_err), len(t_err), len(p1_err))
    return WalkForwardMetrics(
        n_eval=n_eval,
        mae_ah=_mean(ah_err), mae_total=_mean(t_err),
        mae_p1=_mean(p1_err), mae_px=_mean(px_err), mae_p2=_mean(p2_err),
        bias_px=_mean(px_signed),
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _fmt(v: float, d: int = 2) -> str:
    return f"{v:.{d}f}"


def _cmd_train(args: argparse.Namespace) -> None:
    raw = load_raw_matches(Path(args.input))
    model, _ = train_full_model(raw)
    print(f"Матчей: {model.strength.n}  команд: {len(model.strength.ratings)}")
    print(f"H (сила): {_fmt(model.strength.home_advantage,3)}  "
          f"H_derby: {_fmt(model.strength.home_advantage_derby,3)}  "
          f"k={_fmt(model.strength.derby_h_ratio,3)}  "
          f"n_derby={model.strength.derby_n}  "
          f"RMSE_D: {_fmt(model.strength.rmse,4)}")
    print(f"μ: {_fmt(model.goals.mu,3)}  H_g: {_fmt(model.goals.home_goal_adv,3)}  "
          f"RMSE_logλ: {_fmt(model.goals.rmse,4)}")
    c = model.calibration
    print(f"Калибровка: a={_fmt(c.a,3)} b={_fmt(c.b,3)} c={_fmt(c.c,3)} "
          f"d={_fmt(c.d,3)} γ={_fmt(c.gamma,4)}  loss={_fmt(c.loss,5)}")
    print("\nРейтинги (сила, нейтраль):")
    for t, r in sorted(model.strength.ratings.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {t:<20} r={_fmt(r,3):>7}  A={_fmt(model.goals.attack[t],3):>7}  "
              f"Df={_fmt(model.goals.defense[t],3):>7}")
    if args.home and args.away:
        pred = predict_match(model, args.home, args.away,
                             neutral=args.neutral, derby=args.derby)
        _print_prediction(pred)


def _print_prediction(pred: Prediction) -> None:
    mk = pred.markets
    print(f"\n=== Прогноз: {pred.home_team} — {pred.away_team} ===")
    print(f"λ_h={_fmt(pred.lambda_home,3)}  λ_a={_fmt(pred.lambda_away,3)}  "
          f"(D_final={_fmt(pred.d_final,3)}, S_final={_fmt(pred.s_final,3)})")
    print(f"1X2: П1={_fmt(mk.p1*100,1)}% X={_fmt(mk.px*100,1)}% П2={_fmt(mk.p2*100,1)}%  "
          f"→ {_fmt(mk.k1())} / {_fmt(mk.kx())} / {_fmt(mk.k2())}")
    t = mk.main_total
    print(f"Тотал {t.line}: Over {_fmt(t.home_or_over_odds)} / Under {_fmt(t.away_or_under_odds)}")
    a = mk.main_ah
    print(f"Фора хозяев {a.line:+}: {_fmt(a.home_or_over_odds)} / гости {_fmt(a.away_or_under_odds)}")
    print("Топ счетов:", ", ".join(f"{i}:{j} {_fmt(p*100,1)}%" for i, j, p in mk.top_scores[:5]))


def _cmd_validate(args: argparse.Namespace) -> None:
    raw = load_raw_matches(Path(args.input))
    m = walk_forward_validate(raw, min_train=args.min_train)
    print(f"Walk-forward: оценено матчей = {m.n_eval}")
    print(f"  MAE форы:   {_fmt(m.mae_ah,3)}")
    print(f"  MAE тотала: {_fmt(m.mae_total,3)}")
    print(f"  MAE П1/X/П2 vs Shin: {_fmt(m.mae_p1,4)} / {_fmt(m.mae_px,4)} / {_fmt(m.mae_p2,4)}")
    print(f"  Смещение ничьи (px_model − px_Shin): {_fmt(m.bias_px,4)}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Голевая модель футбольной линии (closing data).")
    sub = p.add_subparsers(dest="command", required=True)
    pt = sub.add_parser("train", help="Обучить модель по CSV и (опц.) спрогнозировать матч.")
    pt.add_argument("--input", required=True, help="CSV исторических closing-линий.")
    pt.add_argument("--home", help="Хозяева прогнозируемого матча.")
    pt.add_argument("--away", help="Гости прогнозируемого матча.")
    pt.add_argument("--neutral", action="store_true", help="Нейтральное поле.")
    pt.add_argument("--derby", action="store_true", help="Дерби.")
    pt.set_defaults(func=_cmd_train)

    pv = sub.add_parser("validate", help="Walk-forward валидация модели по CSV.")
    pv.add_argument("--input", required=True, help="CSV исторических closing-линий.")
    pv.add_argument("--min-train", type=int, default=12, dest="min_train",
                    help="Минимум матчей для обучения перед первым прогнозом.")
    pv.set_defaults(func=_cmd_validate)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
