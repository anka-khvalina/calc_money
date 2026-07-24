"""
Обучение голевой модели на исторических closing-линиях и прогноз матча.

Pipeline (см. спецификацию):
  1. prepare + base weights (сезон/качество/дерби/нейтраль);
  2. de-vig (two-way для AH/тоталов, Shin для 1X2);
  3. восстановление S_m, D_m → λ_h, λ_a;
  4. рейтинг силы r_i, H — robust WLS по D_m;
  5. attack/defense μ, A_i, Df_i, H_g — robust WLS по log λ;
  6. калибровка dA,dB,sA,sB,γ по 1X2 (Shin) — Nelder–Mead;
  7. прогноз будущего матча → λ_h,λ_a → матрица → все рынки.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    from . import d_correction as dcorr
    from . import dynamic_dc_gamma as ddc
    from . import goal_model as gm
    from . import hierarchical_wls as hwls
    from . import line_weights as lw
    from . import momentum as mom
    from . import s_momentum as smom
    from . import strong_favorite_adjustment as sfa
    from . import team_ranking as tr
    from .rotation_training import (
        DEFAULT_ROTATION_TRAINING_WEIGHTS,
        annotate_rotation_training,
        normalize_rotation_code,
        training_weight_for_rotation,
    )
except ImportError:  # pragma: no cover
    import d_correction as dcorr
    import dynamic_dc_gamma as ddc
    import goal_model as gm
    import hierarchical_wls as hwls
    import line_weights as lw
    import momentum as mom
    import s_momentum as smom
    import strong_favorite_adjustment as sfa
    import team_ranking as tr
    from rotation_training import (
        DEFAULT_ROTATION_TRAINING_WEIGHTS,
        annotate_rotation_training,
        normalize_rotation_code,
        training_weight_for_rotation,
    )


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

    # веса по размеру линии (фора) — режим lineWeight.mode
    alpha_ah: float = 0.25
    p_ah: float = 2.0
    min_w_line_ah: float = 0.15
    max_w_line_ah: float = 1.0
    line_weight_mode: str = lw.MODE_SOFT  # current | soft | disabled | named preset
    # optional soft/custom breakpoints [(abs_D, weight), ...]; None → built-in soft preset
    line_weight_table: Optional[List[Tuple[float, float]]] = None
    # extra named tables from model_config lineWeight.presets
    line_weight_presets: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)

    # Strong Favorite Adjustment (amplify |D| for rare favorites)
    sfa_mode: str = sfa.MODE_PERCENTILE  # off | threshold | percentile
    sfa_shape: str = sfa.SHAPE_STEPWISE  # stepwise | linear | logistic
    sfa_min_matches: int = 40
    sfa_odds_threshold: float = 1.30
    sfa_beta: float = 0.15
    sfa_thresholds: List[Tuple[float, float]] = field(
        default_factory=lambda: list(sfa.DEFAULT_THRESHOLDS)
    )
    sfa_logistic_k: float = 0.8
    sfa_logistic_mid: float = 5.0
    sfa_logistic_max: float = 0.20

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
    # S_cal = sA + sB·S_model: off=фикс sA=0,sB=1; soft=штраф+лимит ΔS; free=как раньше
    s_calibration_mode: str = "off"  # off | soft | free
    s_cal_penalty_c: float = 2.0  # штраф на sA (legacy key name)
    s_cal_penalty_d: float = 2.0  # штраф на (sB−1)
    s_cal_max_delta: float = 0.15  # |S_cal−S_model| на матч (soft); free — без лимита

    # Dixon–Coles γ
    dc_gamma_min: float = -0.20
    dc_gamma_max: float = 0.20

    # модель ничьей: legacy = абсолютная P_X; residual_dc = q после DC (эксп.)
    use_draw_model: bool = False
    draw_model_mode: str = "residual_dc"  # legacy | residual_dc
    draw_diag_multiplier_min: float = 0.95   # legacy draw
    draw_diag_multiplier_max: float = 1.05
    draw_residual_multiplier_min: float = 0.98  # residual: узкая поправка после DC
    draw_residual_multiplier_max: float = 1.03
    w_1x2_normal: float = 1.0
    w_1x2_suspicious: float = 0.5
    w_1x2_missing: float = 0.0

    # prior прошлого сезона и новички лиги (§15)
    prior_alpha: float = 0.70          # стягивание рейтинга прошлого сезона
    prior_weight: float = 0.0          # вес ridge-привязки к prior (0 = выкл.)
    promoted_reference_n: int = 3       # сколько слабейших усреднять для новичка

    # D clamp: «чувствительные» контексты (дерби, нейтраль, мало матчей, начало сезона)
    d_clamp_low_team_matches: int = 3   # ≤N матчей команды в выборке → новичок / мало данных
    d_clamp_early_fraction: float = 0.25  # первые 25% матчей по дате → начало сезона
    allow_unknown_teams: bool = True    # подставлять fallback для новичков

    # прогноз / дерби
    lambda_epsilon: float = 0.05
    derby_shrink_tau: float = 30.0       # τ в w = n/(n+τ) для shrinkage δ_derby
    derby_h_default_ratio: float = 0.70  # H_eff ≈ ratio×H_league если мало дерби (0.4 — агрессивно)
    derby_min_matches: int = 3           # минимум дерби-матчей для оценки δ_derby

    # L2-регуляризация (ridge к нулю)
    reg_lambda: float = 0.10           # λ на рейтинги силы r
    reg_lambda_attack: float = 0.10    # λ_A на attack
    reg_lambda_defense: float = 0.10     # λ_Df на defense

    # ротация составов → training_weight (none/middle/high)
    rotation_training_weights: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_ROTATION_TRAINING_WEIGHTS)
    )

    # momentum / market-drift EMA поверх D_model (не меняет WLS)
    momentum_enabled: bool = True
    momentum_alpha: float = 0.30
    momentum_k: float = 0.80
    momentum_max_ema: float = 0.50
    momentum_min_matches: int = 1
    # опционально: league_id / имя → overrides {enabled,alpha,k,max_ema,min_matches}
    momentum_by_league: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # S-EMA / dynamic_s_ema поверх S_model (независимо от D-EMA)
    s_momentum_enabled: bool = False
    s_momentum_alpha: float = 0.30
    s_momentum_k: float = 1.00
    s_momentum_min_matches: int = 1
    s_momentum_max_abs_team_ema: Optional[float] = None
    s_momentum_max_abs_correction: Optional[float] = None
    s_momentum_lambda_min: float = 0.05
    s_momentum_reset_on_new_season: bool = True
    s_momentum_by_league: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Dixon–Coles γ(|D_model_final|) — сезонное обучение gamma не меняет
    # Default False for Python backward-compat; web model_config.json sets enabled:true
    dynamic_dc_gamma_enabled: bool = False
    dynamic_dc_gamma_source: str = "D_model_final"
    dynamic_dc_default_gamma: float = 0.09
    # None → встроенная таблица abs_D ≤0.5/1.0/1.5/>1.5
    dynamic_dc_segments: Optional[List[Dict[str, Any]]] = None

    # D correction architecture: legacy_ema | slow_fast | disabled
    d_correction_mode: str = dcorr.MODE_SLOW_FAST
    d_correction_slow_enabled: bool = True
    d_correction_slow_alpha: float = 0.12
    d_correction_slow_shrink_k: float = 12.0
    d_correction_slow_min_observations: int = 5
    d_correction_slow_max_abs: float = 0.35
    d_correction_fast_enabled: bool = True
    d_correction_fast_alpha: float = 0.40
    d_correction_fast_shrink_k: float = 6.0
    d_correction_fast_min_observations: int = 2
    d_correction_fast_max_abs: float = 0.40
    d_correction_total_max_abs: float = 0.60
    d_correction_cache_fallback: str = dcorr.FALLBACK_LAST_LOCAL
    d_correction_cache_versions_to_keep: int = 2
    d_correction_publish_cache: bool = False  # opt-in filesystem publish after train

    # Regularized hierarchical WLS (rating.mode); default keeps current WLS
    rating_mode: str = hwls.MODE_STANDARD
    rating_confidence_k: float = 8.0
    rating_lambda_mode: str = hwls.LAMBDA_MODE_CONFIDENCE
    rating_lambda_min: float = 0.02
    rating_lambda_max: float = 0.50
    rating_lambda_base: float = 0.10
    rating_n_floor: float = 1.0
    rating_effective_n_iters: int = 2
    rating_prior_mode: str = hwls.PRIOR_LEAGUE_MEAN
    rating_prior_reliability: float = 0.70
    rating_promoted_team_prior: Optional[float] = None
    rating_time_decay_enabled: bool = False
    rating_half_life_days: float = 120.0
    rating_suppress_season_weight: bool = True
    rating_volatility_enabled: bool = False
    rating_volatility_scale: float = 1.0
    rating_publish_cache: bool = False  # opt-in; model_config.json can enable for publish
    rating_cache_versions_to_keep: int = 2
    rating_cache_fallback: str = hwls.FALLBACK_LAST_LOCAL
    # league_id / league_name → partial rating overrides (e.g. mode)
    rating_by_league: Dict[str, Dict[str, Any]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Историческая запись матча
# --------------------------------------------------------------------------- #

def team_key(team_id: Optional[object], name: str) -> str:
    """Стабильный ключ команды: id из БД, иначе имя (legacy CSV)."""
    if team_id is not None:
        s = str(team_id).strip()
        if s:
            return s
    return (name or "").strip()


@dataclass
class RawMatch:
    date: Optional[date]
    league: str
    home_team: str
    away_team: str
    home_team_id: Optional[str] = None
    away_team_id: Optional[str] = None
    league_id: Optional[str] = None
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
    home_rotation_code: str = "none"
    away_rotation_code: str = "none"
    season_id: Optional[str] = None
    season_label: Optional[str] = None


_CSV_ALIASES: Dict[str, str] = {
    "date": "date", "league": "league",
    "home_team": "home_team", "home": "home_team", "team_home": "home_team",
    "home_team_id": "home_team_id", "homeid": "home_team_id",
    "away_team": "away_team", "away": "away_team", "team_away": "away_team",
    "away_team_id": "away_team_id", "awayid": "away_team_id",
    "league_id": "league_id",
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
    "home_rotation_code": "home_rotation_code", "home_rot": "home_rotation_code",
    "away_rotation_code": "away_rotation_code", "away_rot": "away_rotation_code",
    "season_id": "season_id", "seasonid": "season_id",
    "season_label": "season_label", "season": "season_label", "seasonlabel": "season_label",
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
        home_name = rec["home_team"].strip()
        away_name = rec["away_team"].strip()
        home_tid = (rec.get("home_team_id") or "").strip() or None
        away_tid = (rec.get("away_team_id") or "").strip() or None
        out.append(RawMatch(
            date=_to_date(rec.get("date")),
            league=(rec.get("league") or "").strip(),
            home_team=home_name,
            away_team=away_name,
            home_team_id=home_tid,
            away_team_id=away_tid,
            league_id=(rec.get("league_id") or "").strip() or None,
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
            home_rotation_code=normalize_rotation_code(rec.get("home_rotation_code"), log_unknown=True),
            away_rotation_code=normalize_rotation_code(rec.get("away_rotation_code"), log_unknown=True),
            season_id=(rec.get("season_id") or "").strip() or None,
            season_label=(rec.get("season_label") or "").strip() or None,
        ))
    return out


def load_raw_matches(path: Path) -> List["RawMatch"]:
    return parse_raw_matches(Path(path).read_text(encoding="utf-8-sig"))


@dataclass
class PreparedMatch:
    raw: RawMatch
    home_id: str
    away_id: str
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
    diff_goals_raw: Optional[float] = None
    d_clamp_hit: bool = False
    d_clamp_trim: float = 0.0
    d_clamp_at_lo: bool = False
    d_clamp_at_hi: bool = False
    d_infer_source: Optional[str] = None  # "infer" | "fallback_ah"
    lambda_home: Optional[float] = None
    lambda_away: Optional[float] = None
    w_line_ah: float = 1.0
    w_line_t: float = 1.0
    w_robust: float = 1.0
    w_time: float = 1.0


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
    w_r = training_weight_for_rotation(
        m.home_rotation_code,
        m.away_rotation_code,
        getattr(cfg, "rotation_training_weights", None),
    )
    return w_s * w_m * w_n * w_r


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
            home_id=team_key(r.home_team_id, r.home_team),
            away_id=team_key(r.away_team_id, r.away_team),
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
            d_raw: Optional[float] = None
            if r.closing_ah_home is not None and m.p_ah_home_fair is not None:
                d_raw = gm.infer_goal_diff(
                    r.closing_ah_home, m.p_ah_home_fair, m.sum_goals,
                    max_goals=cfg.max_goals, eps=cfg.lambda_epsilon,
                )
                m.d_infer_source = "infer"
            elif r.closing_ah_home is not None:
                d_raw = -r.closing_ah_home
                m.d_infer_source = "fallback_ah"
            if d_raw is not None:
                info = gm.apply_goal_diff_clamp(d_raw, m.sum_goals, cfg.lambda_epsilon)
                m.diff_goals_raw = d_raw
                m.diff_goals = info.value
                m.d_clamp_hit = info.hit
                m.d_clamp_trim = info.trim
                m.d_clamp_at_lo = info.at_lo
                m.d_clamp_at_hi = info.at_hi

        if m.sum_goals is not None and m.diff_goals is not None:
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


@dataclass
class DClampDiagnosticRow:
    date: Optional[date]
    home_team: str
    away_team: str
    sum_goals: float
    d_raw: float
    d_used: float
    trim: float
    at_lo: bool
    at_hi: bool
    ah_line: Optional[float]
    source: Optional[str]
    tags: List[str] = field(default_factory=list)

    @property
    def sensitive(self) -> bool:
        return bool(self.tags)


@dataclass
class DClampDiagnostics:
    n_total: int
    n_hit: int
    pct: float
    n_sensitive: int
    sensitive_pct: float
    max_abs_trim: float
    rows: List[DClampDiagnosticRow] = field(default_factory=list)


def _team_match_counts(matches: Sequence[PreparedMatch]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for m in matches:
        if m.diff_goals is None:
            continue
        counts[m.home_id] = counts.get(m.home_id, 0) + 1
        counts[m.away_id] = counts.get(m.away_id, 0) + 1
    return counts


def _early_season_cutoff_date(
    matches: Sequence[PreparedMatch],
    fraction: float,
) -> Optional[date]:
    dates = sorted({m.raw.date for m in matches if m.raw.date is not None})
    if not dates:
        return None
    fraction = min(max(fraction, 0.0), 1.0)
    idx = max(0, int(math.ceil(len(dates) * fraction)) - 1)
    return dates[idx]


def _d_clamp_sensitive_tags(
    m: PreparedMatch,
    team_counts: Dict[str, int],
    early_cutoff: Optional[date],
    cfg: ModelConfig,
) -> List[str]:
    tags: List[str] = []
    if m.i_home == 0:
        tags.append("нейтраль")
    if _derby_home_indicator(m):
        tags.append("дерби")
    thr = cfg.d_clamp_low_team_matches
    hc = team_counts.get(m.home_id, 0)
    ac = team_counts.get(m.away_id, 0)
    tmin = min(hc, ac)
    if tmin <= thr:
        tags.append(f"новичок/мало матчей ({tmin})")
    if early_cutoff is not None and m.raw.date is not None and m.raw.date <= early_cutoff:
        tags.append("начало сезона")
    return tags


def d_clamp_diagnostics(
    matches: Sequence[PreparedMatch],
    cfg: Optional[ModelConfig] = None,
) -> DClampDiagnostics:
    """Матчи, где D упёрся в clamp или границу (−S+ε, S−ε)."""
    cfg = cfg or ModelConfig()
    team_counts = _team_match_counts(matches)
    early_cutoff = _early_season_cutoff_date(matches, cfg.d_clamp_early_fraction)
    eligible = [m for m in matches if m.diff_goals is not None and m.sum_goals is not None]
    hits: List[DClampDiagnosticRow] = []
    for m in eligible:
        if not m.d_clamp_hit:
            continue
        tags = _d_clamp_sensitive_tags(m, team_counts, early_cutoff, cfg)
        hits.append(DClampDiagnosticRow(
            date=m.raw.date,
            home_team=m.home_team,
            away_team=m.away_team,
            sum_goals=m.sum_goals,
            d_raw=m.diff_goals_raw if m.diff_goals_raw is not None else m.diff_goals,
            d_used=m.diff_goals,
            trim=m.d_clamp_trim,
            at_lo=m.d_clamp_at_lo,
            at_hi=m.d_clamp_at_hi,
            ah_line=m.raw.closing_ah_home,
            source=m.d_infer_source,
            tags=tags,
        ))
    hits.sort(key=lambda r: (0 if r.sensitive else 1, -abs(r.trim)))
    n_total = len(eligible)
    n_hit = len(hits)
    n_sensitive = sum(1 for r in hits if r.sensitive)
    pct = 100.0 * n_hit / n_total if n_total else 0.0
    sens_pct = 100.0 * n_sensitive / n_total if n_total else 0.0
    max_trim = max((abs(r.trim) for r in hits), default=0.0)
    return DClampDiagnostics(
        n_total=n_total, n_hit=n_hit, pct=pct,
        n_sensitive=n_sensitive, sensitive_pct=sens_pct,
        max_abs_trim=max_trim, rows=hits,
    )


def log_d_clamp_diagnostics(diag: DClampDiagnostics, *, warn_pct: float = 2.0) -> None:
    log = logging.getLogger(__name__)
    if diag.n_hit == 0:
        log.info("D clamp: 0/%d matches (0%%)", diag.n_total)
        return
    level = logging.WARNING if diag.pct > warn_pct else logging.INFO
    if diag.n_sensitive > 0:
        level = logging.WARNING
    log.log(
        level,
        "D clamp/saturate: %d/%d matches (%.1f%%), sensitive %d (%.1f%%), max |trim|=%.3f",
        diag.n_hit, diag.n_total, diag.pct, diag.n_sensitive, diag.sensitive_pct, diag.max_abs_trim,
    )
    for row in diag.rows[:15]:
        bound = "lo" if row.at_lo else ("hi" if row.at_hi else "trim")
        tag_s = f" {{{', '.join(row.tags)}}}" if row.tags else ""
        prefix = "!" if row.sensitive else " "
        log.log(
            level,
            "%s %s — %s: S=%.2f AH=%s D_raw=%.3f → D=%.3f trim=%+.3f [%s, %s]%s",
            prefix, row.home_team, row.away_team, row.sum_goals,
            f"{row.ah_line:+.2f}" if row.ah_line is not None else "?",
            row.d_raw, row.d_used, row.trim, bound, row.source or "?", tag_s,
        )
    if len(diag.rows) > 15:
        log.log(level, "  … и ещё %d матчей", len(diag.rows) - 15)


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
    rating_mode: str = hwls.MODE_STANDARD
    rating_algorithm_version: str = "standard_wls_v1"
    team_meta: Dict[str, hwls.TeamRatingMeta] = field(default_factory=dict)
    priors: Dict[str, float] = field(default_factory=dict)
    regularization_parameters: Dict[str, Any] = field(default_factory=dict)
    time_decay_parameters: Dict[str, Any] = field(default_factory=dict)

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


def resolve_rating_config(
    cfg: ModelConfig,
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> hwls.HierarchicalWlsConfig:
    base = hwls.HierarchicalWlsConfig(
        mode=cfg.rating_mode,
        confidence_k=cfg.rating_confidence_k,
        lambda_mode=cfg.rating_lambda_mode,
        lambda_min=cfg.rating_lambda_min,
        lambda_max=cfg.rating_lambda_max,
        lambda_base=cfg.rating_lambda_base,
        n_floor=cfg.rating_n_floor,
        effective_n_iters=cfg.rating_effective_n_iters,
        prior=hwls.PriorConfig(
            mode=cfg.rating_prior_mode,
            prior_reliability=cfg.rating_prior_reliability,
            promoted_team_prior=cfg.rating_promoted_team_prior,
            promoted_reference_n=cfg.promoted_reference_n,
        ),
        time_decay=hwls.TimeDecayConfig(
            enabled=cfg.rating_time_decay_enabled,
            half_life_days=cfg.rating_half_life_days,
            suppress_season_weight=cfg.rating_suppress_season_weight,
        ),
        volatility=hwls.VolatilityConfig(
            enabled=cfg.rating_volatility_enabled,
            volatility_scale=cfg.rating_volatility_scale,
        ),
        cache=hwls.RatingCacheConfig(
            fallback=cfg.rating_cache_fallback,
            versions_to_keep=cfg.rating_cache_versions_to_keep,
        ),
        publish_cache=cfg.rating_publish_cache,
        by_league=dict(cfg.rating_by_league or {}),
    ).validated()
    return hwls.apply_league_overrides(
        base, league_id=league_id, league_name=league_name
    )


def apply_rating_config_from_mapping(
    cfg: ModelConfig,
    raw: Optional[Dict[str, Any]],
) -> ModelConfig:
    """Наложить блок rating из model_config.json на ModelConfig."""
    if not raw:
        return cfg
    parsed = hwls.rating_config_from_mapping(raw)
    return replace(
        cfg,
        rating_mode=parsed.mode,
        rating_confidence_k=parsed.confidence_k,
        rating_lambda_mode=parsed.lambda_mode,
        rating_lambda_min=parsed.lambda_min,
        rating_lambda_max=parsed.lambda_max,
        rating_lambda_base=parsed.lambda_base,
        rating_n_floor=parsed.n_floor,
        rating_effective_n_iters=parsed.effective_n_iters,
        rating_prior_mode=parsed.prior.mode,
        rating_prior_reliability=parsed.prior.prior_reliability,
        rating_promoted_team_prior=parsed.prior.promoted_team_prior,
        promoted_reference_n=parsed.prior.promoted_reference_n,
        rating_time_decay_enabled=parsed.time_decay.enabled,
        rating_half_life_days=parsed.time_decay.half_life_days,
        rating_suppress_season_weight=parsed.time_decay.suppress_season_weight,
        rating_volatility_enabled=parsed.volatility.enabled,
        rating_volatility_scale=parsed.volatility.volatility_scale,
        rating_publish_cache=parsed.publish_cache,
        rating_cache_versions_to_keep=parsed.cache.versions_to_keep,
        rating_cache_fallback=parsed.cache.fallback,
        rating_by_league=dict(parsed.by_league or {}),
    )


def resolve_line_weight_config(cfg: ModelConfig) -> lw.LineWeightConfig:
    """Build LineWeightConfig from ModelConfig fields."""
    return lw.LineWeightConfig(
        mode=cfg.line_weight_mode,
        alpha_ah=float(cfg.alpha_ah),
        p_ah=float(cfg.p_ah),
        min_w=float(cfg.min_w_line_ah),
        max_w=float(cfg.max_w_line_ah),
        table=cfg.line_weight_table,
        presets=dict(cfg.line_weight_presets or {}),
    ).validated()


def apply_line_weight_config_from_mapping(
    cfg: ModelConfig,
    raw: Optional[Mapping[str, Any]],
) -> ModelConfig:
    """Наложить блок lineWeight / lineWeightMode из model_config.json на ModelConfig."""
    if not raw:
        return cfg
    parsed = lw.line_weight_config_from_mapping(raw)
    return replace(
        cfg,
        line_weight_mode=parsed.mode,
        alpha_ah=parsed.alpha_ah,
        p_ah=parsed.p_ah,
        min_w_line_ah=parsed.min_w,
        max_w_line_ah=parsed.max_w,
        line_weight_table=parsed.table,
        line_weight_presets=dict(parsed.presets or {}),
    )


def resolve_sfa_config(cfg: ModelConfig) -> sfa.SfaConfig:
    return sfa.SfaConfig(
        mode=cfg.sfa_mode,
        shape=cfg.sfa_shape,
        min_matches=cfg.sfa_min_matches,
        odds_threshold=cfg.sfa_odds_threshold,
        beta=cfg.sfa_beta,
        thresholds=list(cfg.sfa_thresholds or sfa.DEFAULT_THRESHOLDS),
        logistic_k=cfg.sfa_logistic_k,
        logistic_mid=cfg.sfa_logistic_mid,
        logistic_max=cfg.sfa_logistic_max,
    ).validated()


def apply_sfa_config_from_mapping(
    cfg: ModelConfig,
    raw: Optional[Mapping[str, Any]],
) -> ModelConfig:
    """Наложить strongFavoriteAdjustment из model_config.json."""
    if not raw:
        return cfg
    parsed = sfa.sfa_config_from_mapping(raw)
    return replace(
        cfg,
        sfa_mode=parsed.mode,
        sfa_shape=parsed.shape,
        sfa_min_matches=parsed.min_matches,
        sfa_odds_threshold=parsed.odds_threshold,
        sfa_beta=parsed.beta,
        sfa_thresholds=list(parsed.thresholds),
        sfa_logistic_k=parsed.logistic_k,
        sfa_logistic_mid=parsed.logistic_mid,
        sfa_logistic_max=parsed.logistic_max,
    )


def build_sfa_book_for_matches(
    matches: Sequence[PreparedMatch],
    cfg: ModelConfig,
) -> sfa.FavoriteOddsBook:
    """Build league×season favorite-odds CDF from market Shin 1X2 (AC1)."""
    sfa_cfg = resolve_sfa_config(cfg)
    rows: List[Dict[str, Any]] = []
    default_league = "ALL"
    for m in matches:
        fo = sfa.market_favorite_odds(m.p1_shin, m.p2_shin)
        if fo is None:
            continue
        lg = (
            (m.raw.league_id and str(m.raw.league_id))
            or (m.raw.league or "").strip()
            or default_league
        )
        season = (
            (m.raw.season_label and str(m.raw.season_label).strip())
            or (m.raw.season_id and str(m.raw.season_id).strip())
            or "ALL"
        )
        rows.append({"league": lg, "season": season, "fav_odds": fo})
        if default_league == "ALL" and lg != "ALL":
            default_league = lg
    book = sfa.build_favorite_odds_book(
        rows, min_matches=sfa_cfg.min_matches, default_league=default_league
    )
    logging.getLogger(__name__).info(
        "SFA book built mode=%s shape=%s leagues=%d dists=%d minMatches=%d",
        sfa_cfg.mode,
        sfa_cfg.shape,
        len(book.seasons_by_league),
        len(book.distributions),
        sfa_cfg.min_matches,
    )
    return book


def _apply_line_ah_weights(used: Sequence[PreparedMatch], cfg: ModelConfig) -> None:
    lcfg = resolve_line_weight_config(cfg)
    for m in used:
        m.w_line_ah = lw.w_line_ah(abs(m.diff_goals), lcfg)
        m.w_robust = 1.0


def log_line_weight_diagnostics(
    matches: Sequence[PreparedMatch],
    cfg: ModelConfig,
) -> lw.LineWeightDiagnostics:
    """Log AC5 training diagnostics for w_line_AH."""
    lcfg = resolve_line_weight_config(cfg)
    used = [m for m in matches if m.diff_goals is not None]
    # Prefer already-applied weights; otherwise compute from config.
    weights: List[float] = []
    for m in used:
        w = getattr(m, "w_line_ah", None)
        if w is None:
            w = lw.w_line_ah(abs(m.diff_goals), lcfg)
        weights.append(float(w))
    diag = lw.diagnose_weights(weights, lcfg.mode)
    lw.log_diagnostics(diag)
    return diag


def _apply_time_weights(
    used: Sequence[PreparedMatch],
    cfg: ModelConfig,
    rcfg: hwls.HierarchicalWlsConfig,
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> None:
    if not rcfg.time_decay.enabled:
        for m in used:
            m.w_time = 1.0
        return
    half = hwls.resolve_half_life(rcfg, league_id=league_id, league_name=league_name)
    dates = [m.raw.date for m in used if m.raw.date is not None]
    as_of = max(dates) if dates else None
    for m in used:
        m.w_time = hwls.time_weight(m.raw.date, as_of=as_of, half_life_days=half)


def _strength_observation_weight(
    m: PreparedMatch,
    cfg: ModelConfig,
    rcfg: hwls.HierarchicalWlsConfig,
    *,
    include_robust: bool = True,
) -> float:
    """Match weight for strength WLS / effective_n (no Elo update)."""
    w = m.w_base
    if rcfg.time_decay.enabled and rcfg.time_decay.suppress_season_weight:
        sw = season_weight(m.raw.date, cfg)
        if sw > 1e-12:
            w = w / sw
        w *= m.w_time
    elif rcfg.time_decay.enabled:
        w *= m.w_time
    w *= m.w_line_ah
    if include_robust:
        w *= m.w_robust
    return w


def _strength_model_from_hierarchical(fit: hwls.HierarchicalFitResult) -> StrengthModel:
    return StrengthModel(
        ratings=dict(fit.ratings),
        home_advantage=fit.home_advantage,
        derby_home_delta=fit.derby_home_delta,
        derby_n=fit.derby_n,
        derby_shrink_w=fit.derby_shrink_w,
        mae=fit.mae,
        rmse=fit.rmse,
        n=fit.n,
        rating_mode=fit.rating_mode,
        rating_algorithm_version=fit.rating_algorithm_version,
        team_meta=dict(fit.team_meta),
        priors=dict(fit.priors),
        regularization_parameters=dict(fit.regularization_parameters),
        time_decay_parameters=dict(fit.time_decay_parameters),
    )


def fit_hierarchical_strength_ratings(
    matches: Sequence[PreparedMatch],
    cfg: ModelConfig,
    prior_ratings: Optional[Dict[str, float]] = None,
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
    rcfg: Optional[hwls.HierarchicalWlsConfig] = None,
) -> StrengthModel:
    """
    Single joint hierarchical WLS/IRLS over D_market with team-specific shrinkage.
    No sequential Elo R ← R + K·error.
    """
    rcfg = (rcfg or resolve_rating_config(cfg)).validated()
    used = [m for m in matches if m.diff_goals is not None]
    if len(used) < 2:
        raise ValueError("Недостаточно матчей с восстановленной разницей D_m")
    teams = sorted({m.home_id for m in used} | {m.away_id for m in used})
    idx = {t: i for i, t in enumerate(teams)}
    n_derby = sum(_derby_home_indicator(m) for m in used)
    use_derby_coef = n_derby >= cfg.derby_min_matches
    p = len(teams) + (2 if use_derby_coef else 1)
    h_col = p - 2 if use_derby_coef else p - 1
    d_col = p - 1 if use_derby_coef else None

    _apply_line_ah_weights(used, cfg)
    _apply_time_weights(
        used, cfg, rcfg, league_id=league_id, league_name=league_name
    )

    priors = hwls.resolve_team_priors(teams, rcfg, previous_season=prior_ratings)

    coeffs: List[List[float]] = []
    targets: List[float] = []
    for m in used:
        row = [0.0] * p
        row[idx[m.home_id]] += 1.0
        row[idx[m.away_id]] -= 1.0
        row[h_col] = float(m.i_home)
        if d_col is not None:
            row[d_col] = float(_derby_home_indicator(m))
        coeffs.append(row)
        targets.append(m.diff_goals)

    gauge = [1.0] * len(teams) + [0.0] * (p - len(teams))

    def solve(
        weights: Sequence[float],
        lambdas: Mapping[str, float],
    ) -> Tuple[List[float], List[float]]:
        rows = [(coeffs[i], targets[i], weights[i]) for i in range(len(used))]
        for t in teams:
            reg = [0.0] * p
            reg[idx[t]] = 1.0
            rows.append((reg, float(priors.get(t, 0.0)), float(lambdas[t])))
        rows.append((gauge, 0.0, tr.GAUGE_WEIGHT))
        sol = tr._solve_weighted(rows, p)
        resid = [
            (sum(coeffs[i][k] * sol[k] for k in range(p)) - targets[i])
            for i in range(len(used))
        ]
        return sol, resid

    def refresh_lambdas(
        match_w: Sequence[float],
        resid: Optional[Sequence[float]],
    ) -> Dict[str, Tuple[float, float, float, Optional[float]]]:
        tw = hwls.compute_team_weights(
            teams,
            [m.home_id for m in used],
            [m.away_id for m in used],
            match_w,
        )
        vols: Optional[Dict[str, float]] = None
        if resid is not None and rcfg.volatility.enabled:
            vols = {}
            for t in teams:
                vals: List[float] = []
                ws: List[float] = []
                for i, m in enumerate(used):
                    if m.home_id == t or m.away_id == t:
                        # home residual sign as team residual contribution
                        sign = 1.0 if m.home_id == t else -1.0
                        vals.append(sign * float(resid[i]))
                        ws.append(float(match_w[i]))
                vols[t] = hwls.weighted_std(vals, ws)
        return hwls.build_team_lambdas(tw, rcfg, residual_vols=vols)

    # Initial effective_n without robust weights
    base_w = [
        _strength_observation_weight(m, cfg, rcfg, include_robust=False) for m in used
    ]
    team_stats = refresh_lambdas(base_w, None)
    lambdas = {t: team_stats[t][2] for t in teams}

    weights = list(base_w)
    sol, resid = solve(weights, lambdas)

    # IRLS + optional recompute of effective_n / λ_team
    outer = max(1, rcfg.effective_n_iters)
    for outer_i in range(outer):
        for _ in range(cfg.max_iter):
            new_w: List[float] = []
            for i, m in enumerate(used):
                m.w_robust = _huber_weight(resid[i], cfg.delta_ah)
                new_w.append(_strength_observation_weight(m, cfg, rcfg, include_robust=True))
            delta = max(abs(new_w[i] - weights[i]) for i in range(len(used)))
            weights = new_w
            sol, resid = solve(weights, lambdas)
            if delta < cfg.tolerance:
                break
        # refresh λ from robust-weighted effective_n (except after last if only 1 pass needed)
        if outer_i + 1 < outer:
            team_stats = refresh_lambdas(weights, resid)
            lambdas = {t: team_stats[t][2] for t in teams}
            sol, resid = solve(weights, lambdas)

    # Final meta from last weights
    team_stats = refresh_lambdas(weights, resid if rcfg.volatility.enabled else None)
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

    team_meta: Dict[str, hwls.TeamRatingMeta] = {}
    for t in teams:
        n_eff, conf, lam, vol = team_stats[t]
        team_meta[t] = hwls.TeamRatingMeta(
            prior_rating=float(priors.get(t, 0.0)),
            effective_n=n_eff,
            rating_confidence=conf,
            lambda_team=lam,
            residual_volatility=vol,
        )

    half = hwls.resolve_half_life(rcfg, league_id=league_id, league_name=league_name)
    fit = hwls.HierarchicalFitResult(
        ratings=ratings,
        home_advantage=h_league,
        derby_home_delta=delta_shrunk,
        derby_n=n_derby,
        derby_shrink_w=shrink_w,
        mae=mae,
        rmse=rmse,
        n=len(used),
        team_meta=team_meta,
        rating_mode=hwls.MODE_HIERARCHICAL,
        rating_algorithm_version=hwls.RATING_ALGORITHM_VERSION,
        priors=dict(priors),
        regularization_parameters={
            "lambda_mode": rcfg.lambda_mode,
            "lambda_min": rcfg.lambda_min,
            "lambda_max": rcfg.lambda_max,
            "lambda_base": rcfg.lambda_base,
            "confidence_k": rcfg.confidence_k,
            "n_floor": rcfg.n_floor,
            "effective_n_iters": rcfg.effective_n_iters,
            "volatility_enabled": rcfg.volatility.enabled,
        },
        time_decay_parameters={
            "enabled": rcfg.time_decay.enabled,
            "half_life_days": half,
            "suppress_season_weight": rcfg.time_decay.suppress_season_weight,
        },
        residuals=list(resid),
    )
    return _strength_model_from_hierarchical(fit)


def fit_strength_ratings(
    matches: Sequence[PreparedMatch],
    cfg: ModelConfig,
    prior_ratings: Optional[Dict[str, float]] = None,
    *,
    force_mode: Optional[str] = None,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> StrengthModel:
    rcfg = resolve_rating_config(cfg, league_id=league_id, league_name=league_name)
    mode = (force_mode or rcfg.mode or hwls.MODE_STANDARD).strip().lower()

    # Production hierarchical, or forced hierarchical (e.g. shadow side-fit).
    # Shadow production path uses force_mode=standard_wls.
    if mode == hwls.MODE_HIERARCHICAL:
        return fit_hierarchical_strength_ratings(
            matches,
            cfg,
            prior_ratings=prior_ratings,
            league_id=league_id,
            league_name=league_name,
            rcfg=rcfg,
        )

    used = [m for m in matches if m.diff_goals is not None]
    if len(used) < 2:
        raise ValueError("Недостаточно матчей с восстановленной разницей D_m")
    teams = sorted({m.home_id for m in used} | {m.away_id for m in used})
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
        row[idx[m.home_id]] += 1.0
        row[idx[m.away_id]] -= 1.0
        row[h_col] = float(m.i_home)
        if d_col is not None:
            row[d_col] = float(_derby_home_indicator(m))
        coeffs.append(row)
        targets.append(m.diff_goals)

    _apply_line_ah_weights(used, cfg)

    gauge = [1.0] * len(teams) + [0.0] * (p - len(teams))

    reg_rows: List[Tuple[List[float], float, float]] = []
    if cfg.reg_lambda > 0:
        for t in teams:
            row = [0.0] * p
            row[idx[t]] = 1.0
            reg_rows.append((row, 0.0, cfg.reg_lambda))

    def solve(weights: Sequence[float]) -> Tuple[List[float], List[float]]:
        rows = [(coeffs[i], targets[i], weights[i]) for i in range(len(used))]
        rows.extend(prior_rows)
        rows.extend(reg_rows)
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
        rating_mode=hwls.MODE_STANDARD,
        rating_algorithm_version="standard_wls_v1",
        regularization_parameters={"reg_lambda": cfg.reg_lambda, "prior_weight": cfg.prior_weight},
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
    teams = sorted({m.home_id for m in used} | {m.away_id for m in used})
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
        rh[a_idx[m.home_id]] += 1.0
        rh[d_idx[m.away_id]] -= 1.0
        rh[mu_col] = 1.0
        rh[hg_col] = float(m.i_home)
        rows.append(rh)
        targets.append(math.log(m.lambda_home))
        base_w.append(m.w_base * m.w_line_t)
        # строка гостей: log λ_a = μ + A_away − Df_home
        ra = [0.0] * p
        ra[a_idx[m.away_id]] += 1.0
        ra[d_idx[m.home_id]] -= 1.0
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

    reg_rows: List[Tuple[List[float], float, float]] = []
    if cfg.reg_lambda_attack > 0 or cfg.reg_lambda_defense > 0:
        for t in teams:
            if cfg.reg_lambda_attack > 0:
                row_a = [0.0] * p
                row_a[a_idx[t]] = 1.0
                reg_rows.append((row_a, 0.0, cfg.reg_lambda_attack))
            if cfg.reg_lambda_defense > 0:
                row_d = [0.0] * p
                row_d[d_idx[t]] = 1.0
                reg_rows.append((row_d, 0.0, cfg.reg_lambda_defense))

    def solve(weights: Sequence[float]) -> Tuple[List[float], List[float]]:
        wrows = [(rows[i], targets[i], weights[i]) for i in range(len(rows))]
        wrows.extend(prior_rows)
        wrows.extend(reg_rows)
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
# Этап 6: калибровка dA,dB,sA,sB,γ (Nelder–Mead по 1X2 Shin)
# --------------------------------------------------------------------------- #

@dataclass
class Calibration:
    """S/D calibration: D_cal = d_a + d_b·D_model; S_cal = s_a + s_b·S_model.

    Legacy aliases a/b/c/d kept as properties (a=d_a, b=d_b, c=s_a, d=s_b).
    Constructor also accepts legacy kwargs a/b/c/d.
    """
    d_a: float = 0.0  # D intercept (aka dA)
    d_b: float = 1.0  # D slope (aka dB)
    s_a: float = 0.0  # S intercept (aka sA)
    s_b: float = 1.0  # S slope (aka sB)
    gamma: float = 0.0
    loss: float = 0.0
    n_1x2: int = 0

    def __init__(
        self,
        d_a: float = 0.0,
        d_b: float = 1.0,
        s_a: float = 0.0,
        s_b: float = 1.0,
        gamma: float = 0.0,
        loss: float = 0.0,
        n_1x2: int = 0,
        *,
        a: Optional[float] = None,
        b: Optional[float] = None,
        c: Optional[float] = None,
        d: Optional[float] = None,
    ) -> None:
        if a is not None:
            d_a = a
        if b is not None:
            d_b = b
        if c is not None:
            s_a = c
        if d is not None:
            s_b = d
        self.d_a = float(d_a)
        self.d_b = float(d_b)
        self.s_a = float(s_a)
        self.s_b = float(s_b)
        self.gamma = float(gamma)
        self.loss = float(loss)
        self.n_1x2 = int(n_1x2)

    @property
    def a(self) -> float:
        return self.d_a

    @property
    def b(self) -> float:
        return self.d_b

    @property
    def c(self) -> float:
        return self.s_a

    @property
    def d(self) -> float:
        return self.s_b


@dataclass
class CalibrationDiagnostics:
    """Насколько dA/dB/sA/sB близки к идеалу (0,1,0,1) — сигнал качества базы S/D."""

    stable: bool
    deviations: List[str]
    n_1x2: int
    summary: str


def assess_calibration_stability(
    cal: Calibration,
    *,
    tol_a: float = 0.35,
    tol_b: float = 0.30,
    tol_c: float = 0.35,
    tol_d: float = 0.30,
    min_1x2_stable: int = 30,
    cfg: Optional[ModelConfig] = None,
) -> CalibrationDiagnostics:
    """Калибровка стабильна, если dA≈0, dB≈1, sA≈0, sB≈1 в пределах допусков."""
    if cal.n_1x2 == 0:
        return CalibrationDiagnostics(
            stable=True, deviations=[], n_1x2=0,
            summary="нет 1X2 в выборке (калибровка не выполнялась)",
        )
    dev: List[str] = []
    if abs(cal.d_a) > tol_a:
        dev.append(f"dA={cal.d_a:+.3f} (ожид. ≈0, допуск ±{tol_a})")
    if abs(cal.d_b - 1.0) > tol_b:
        dev.append(f"dB={cal.d_b:.3f} (ожид. ≈1, допуск ±{tol_b})")
    skip_cd = cfg is not None and cfg.s_calibration_mode == S_CALIBRATION_OFF
    if not skip_cd:
        if abs(cal.s_a) > tol_c:
            dev.append(f"sA={cal.s_a:+.3f} (ожид. ≈0, допуск ±{tol_c})")
        if abs(cal.s_b - 1.0) > tol_d:
            dev.append(f"sB={cal.s_b:.3f} (ожид. ≈1, допуск ±{tol_d})")
    if cfg and cfg.use_dixon_coles:
        g_lim = max(abs(cfg.dc_gamma_min), abs(cfg.dc_gamma_max))
        if abs(cal.gamma) > g_lim + 1e-9:
            dev.append(f"γ={cal.gamma:+.3f} (лимит ±{g_lim:.2f})")
    if cal.n_1x2 < min_1x2_stable:
        dev.append(f"мало 1X2: {cal.n_1x2} матч. (<{min_1x2_stable})")
    stable = len(dev) == 0
    if stable:
        summary = f"стабильна ({cal.n_1x2} матч. с 1X2)"
    else:
        summary = "нестабильна — " + "; ".join(dev)
    return CalibrationDiagnostics(stable=stable, deviations=dev, n_1x2=cal.n_1x2, summary=summary)


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
        strength.ratings.get(m.home_id, 0.0)
        - strength.ratings.get(m.away_id, 0.0)
        + h_eff
    )
    k_hg = (h_eff / strength.home_advantage) if strength.home_advantage > 1e-9 else 1.0
    lh = math.exp(goals.mu + goals.attack.get(m.home_id, 0.0)
                  - goals.defense.get(m.away_id, 0.0) + goals.home_goal_adv * m.i_home * k_hg)
    la = math.exp(goals.mu + goals.attack.get(m.away_id, 0.0)
                  - goals.defense.get(m.home_id, 0.0))
    return d_model, lh + la


S_CALIBRATION_OFF = "off"
S_CALIBRATION_SOFT = "soft"
S_CALIBRATION_FREE = "free"


def apply_s_calibration(
    s_model: float,
    s_a: float,
    s_b: float,
    cfg: ModelConfig,
) -> float:
    """S после калибровки: off держит S_model; soft/free — sA+sB·S с опц. лимитом ΔS."""
    if cfg.s_calibration_mode == S_CALIBRATION_OFF:
        return s_model
    s_lin = s_a + s_b * s_model
    if cfg.s_calibration_mode == S_CALIBRATION_SOFT and cfg.s_cal_max_delta > 0:
        lo = s_model - cfg.s_cal_max_delta
        hi = s_model + cfg.s_cal_max_delta
        return max(lo, min(hi, s_lin))
    return s_lin


def calibrate_s_penalty(s_a: float, s_b: float, cfg: ModelConfig) -> float:
    if cfg.s_calibration_mode != S_CALIBRATION_SOFT:
        return 0.0
    return cfg.s_cal_penalty_c * s_a * s_a + cfg.s_cal_penalty_d * (s_b - 1.0) ** 2


def clip_dc_gamma(gamma: float, cfg: ModelConfig) -> float:
    if not cfg.use_dixon_coles:
        return 0.0
    return max(cfg.dc_gamma_min, min(cfg.dc_gamma_max, gamma))


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
    s_off = cfg.s_calibration_mode == S_CALIBRATION_OFF

    def loss_core(d_a: float, d_b: float, s_a: float, s_b: float, gamma: float) -> float:
        if s_b <= 0:
            return 1e9
        g = clip_dc_gamma(gamma, cfg)
        total = calibrate_s_penalty(s_a, s_b, cfg)
        for m, d_model, s_model, w in pre:
            s_final = apply_s_calibration(s_model, s_a, s_b, cfg)
            if s_final <= 0.2:
                return 1e9
            d_final = gm.clamp_goal_diff(d_a + d_b * d_model, s_final, cfg.lambda_epsilon)
            lh = (s_final + d_final) / 2.0
            la = (s_final - d_final) / 2.0
            if lh <= 0 or la <= 0:
                return 1e9
            matrix = gm.build_score_matrix(lh, la, cfg.max_goals)
            if cfg.use_dixon_coles and g:
                matrix = gm.apply_dixon_coles(matrix, lh, la, g)
            p1, px, p2 = gm.compute_1x2(matrix)
            total += w * (
                (p1 - m.p1_shin) ** 2
                + cfg.draw_loss_weight * (px - m.px_shin) ** 2
                + (p2 - m.p2_shin) ** 2
            )
        return total

    if s_off:
        if cfg.use_dixon_coles:
            x = nelder_mead(lambda p: loss_core(p[0], p[1], 0.0, 1.0, p[2]), [0.0, 1.0, 0.0])
            full = [x[0], x[1], 0.0, 1.0, clip_dc_gamma(x[2], cfg)]
        else:
            x = nelder_mead(lambda p: loss_core(p[0], p[1], 0.0, 1.0, 0.0), [0.0, 1.0])
            full = [x[0], x[1], 0.0, 1.0, 0.0]
    elif cfg.use_dixon_coles:
        x = nelder_mead(
            lambda p: loss_core(p[0], p[1], p[2], p[3], p[4]),
            [0.0, 1.0, 0.0, 1.0, 0.0],
        )
        full = [x[0], x[1], x[2], x[3], clip_dc_gamma(x[4], cfg)]
    else:
        x4 = nelder_mead(
            lambda p: loss_core(p[0], p[1], p[2], p[3], 0.0),
            [0.0, 1.0, 0.0, 1.0],
        )
        full = [x4[0], x4[1], x4[2], x4[3], 0.0]

    return Calibration(
        d_a=full[0], d_b=full[1], s_a=full[2], s_b=full[3], gamma=full[4],
        loss=loss_core(full[0], full[1], full[2], full[3], full[4]),
        n_1x2=len(used),
    )


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
    mode: str = "legacy"  # legacy | residual_dc

    def target_px(self, d_final: float, s_final: float) -> float:
        z = self._logit_z(d_final, s_final)
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)

    def _logit_z(self, d_final: float, s_final: float) -> float:
        return (
            self.alpha
            + self.beta_d * abs(d_final)
            + self.beta_s * s_final
            + self.beta_s2 * s_final * s_final
            + self.beta_dxs * abs(d_final) * s_final
        )

    def target_q_multiplier(self, d_final: float, s_final: float, cfg: ModelConfig) -> float:
        """Residual DC: q = exp(z), clamp в [q_min, q_max]. Legacy: P_X / P_X_dc через target."""
        z = self._logit_z(d_final, s_final)
        q = math.exp(z)
        q_min, q_max = effective_draw_q_bounds(cfg, self.mode)
        return min(q_max, max(q_min, q))


def effective_draw_q_bounds(
    cfg: ModelConfig,
    draw_mode: Optional[str] = None,
) -> Tuple[float, float]:
    """q_min/q_max для legacy или residual draw."""
    mode = draw_mode or cfg.draw_model_mode
    if mode == "residual_dc":
        return cfg.draw_residual_multiplier_min, cfg.draw_residual_multiplier_max
    return cfg.draw_diag_multiplier_min, cfg.draw_diag_multiplier_max


_DRAW_DEFAULT = DrawModel(
    alpha=-0.95, beta_d=-0.55, beta_s=0.0, beta_s2=0.0,
    beta_dxs=0.0, n=0, source="default", mode="legacy",
)


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
    dm = DrawModel(
        alpha=beta[0], beta_d=beta[1], beta_s=beta[2],
        beta_s2=beta[3], beta_dxs=beta[4], n=len(rows), source="fitted", mode="legacy",
    )
    # Санити: базовая ничья (D=0, S=2.6) в разумных пределах, иначе дефолт.
    base = dm.target_px(0.0, 2.6)
    if not (0.10 <= base <= 0.45):
        return _DRAW_DEFAULT
    return dm


def _calibrated_sd_for_match(
    m: PreparedMatch,
    strength: StrengthModel,
    goals: GoalModel,
    cal: Calibration,
    cfg: ModelConfig,
) -> Tuple[float, float, float, float]:
    """d_model, s_model, s_cal, d_cal для матча при обучении."""
    d_model, s_model = _model_d_s(m, strength, goals, cfg)
    s_cal = apply_s_calibration(s_model, cal.s_a, cal.s_b, cfg)
    d_cal = gm.clamp_goal_diff(cal.d_a + cal.d_b * d_model, s_cal, cfg.lambda_epsilon)
    return d_model, s_model, s_cal, d_cal


def _px_stages_from_calibrated(
    s_cal: float,
    d_cal: float,
    cfg: ModelConfig,
    gamma: float,
) -> Tuple[float, float]:
    """(PX_poisson, PX_after_DC) без draw-q."""
    _, px_pois, _ = _prob_1x2_from_sd(s_cal, d_cal, cfg, gamma=0.0)
    _, px_dc, _ = _prob_1x2_from_sd(s_cal, d_cal, cfg, gamma=gamma)
    return px_pois, px_dc


def fit_draw_residual_model(
    matches: Sequence[PreparedMatch],
    strength: StrengthModel,
    goals: GoalModel,
    calibration: Calibration,
    cfg: ModelConfig,
) -> DrawModel:
    """log(q) ~ features, q = PX_market / PX_after_DC на истории (остаток после DC)."""
    gamma = calibration.gamma if cfg.use_dixon_coles else 0.0
    rows: List[Tuple[List[float], float, float]] = []
    for m in matches:
        if m.px_shin is None:
            continue
        w = m.w_base * w_1x2_weight(m, cfg)
        if w <= 0:
            continue
        _, _, s_cal, d_cal = _calibrated_sd_for_match(
            m, strength, goals, calibration, cfg,
        )
        _, px_dc = _px_stages_from_calibrated(s_cal, d_cal, cfg, gamma)
        if px_dc <= 1e-9:
            continue
        q = m.px_shin / px_dc
        q_lo, q_hi = effective_draw_q_bounds(cfg, "residual_dc")
        q = min(q_hi * 1.5, max(q_lo * 0.5, q))
        if q <= 0:
            continue
        feats = [1.0, abs(d_cal), s_cal, s_cal * s_cal, abs(d_cal) * s_cal]
        rows.append((feats, math.log(q), w))
    if len(rows) < 6:
        return replace(_DRAW_DEFAULT, mode="residual_dc", source="default")
    try:
        beta = tr._solve_weighted(rows, 5)
    except ValueError:
        return replace(_DRAW_DEFAULT, mode="residual_dc", source="default")
    dm = DrawModel(
        alpha=beta[0], beta_d=beta[1], beta_s=beta[2],
        beta_s2=beta[3], beta_dxs=beta[4], n=len(rows), source="fitted", mode="residual_dc",
    )
    # Санити: при S=2.6, D=0 множитель q близок к 1
    q0 = dm.target_q_multiplier(0.0, 2.6, cfg)
    if not (0.85 <= q0 <= 1.15):
        return replace(_DRAW_DEFAULT, mode="residual_dc", source="default")
    return dm


def fit_draw_for_config(
    matches: Sequence[PreparedMatch],
    strength: StrengthModel,
    goals: GoalModel,
    calibration: Calibration,
    cfg: ModelConfig,
) -> DrawModel:
    if not cfg.use_draw_model:
        return _DRAW_DEFAULT
    if cfg.draw_model_mode == "residual_dc":
        return fit_draw_residual_model(matches, strength, goals, calibration, cfg)
    return fit_draw_model(matches, cfg)


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
    team_names: Dict[str, str] = field(default_factory=dict)
    d_clamp: Optional[DClampDiagnostics] = None
    cal_diag: Optional[CalibrationDiagnostics] = None
    draw_q_diag: Optional["DrawQDiagnostics"] = None
    sd_diag: Optional["Sd1x2Diagnostics"] = None
    draw_harm_diag: Optional["DrawHarmDiagnostics"] = None
    momentum_book: Optional["mom.MomentumBook"] = None
    momentum_cfg: Optional["mom.MomentumConfig"] = None
    s_momentum_book: Optional["smom.SMomentumBook"] = None
    s_momentum_cfg: Optional["smom.SMomentumConfig"] = None
    d_correction_book: Optional["dcorr.DCorrectionBook"] = None
    d_correction_cfg: Optional["dcorr.DCorrectionConfig"] = None
    d_model_version: Optional[str] = None
    # hierarchical / shadow extras (never mixed across versions)
    shadow_strength: Optional[StrengthModel] = None
    shadow_d_correction_book: Optional["dcorr.DCorrectionBook"] = None
    rating_shadow_monitor: Optional[Dict[str, Any]] = None
    rating_model_version: Optional[str] = None
    sfa_book: Optional["sfa.FavoriteOddsBook"] = None
    sfa_cfg: Optional["sfa.SfaConfig"] = None


def resolve_d_correction_config(cfg: ModelConfig) -> dcorr.DCorrectionConfig:
    return dcorr.DCorrectionConfig(
        mode=cfg.d_correction_mode,
        slow=dcorr.SlowLayerConfig(
            enabled=cfg.d_correction_slow_enabled,
            alpha=cfg.d_correction_slow_alpha,
            shrink_k=cfg.d_correction_slow_shrink_k,
            min_observations=cfg.d_correction_slow_min_observations,
            max_abs_correction=cfg.d_correction_slow_max_abs,
        ),
        fast=dcorr.FastLayerConfig(
            enabled=cfg.d_correction_fast_enabled,
            alpha=cfg.d_correction_fast_alpha,
            shrink_k=cfg.d_correction_fast_shrink_k,
            min_observations=cfg.d_correction_fast_min_observations,
            max_abs_correction=cfg.d_correction_fast_max_abs,
        ),
        total_max_abs_correction=cfg.d_correction_total_max_abs,
        cache=dcorr.DCorrectionCacheConfig(
            fallback=cfg.d_correction_cache_fallback,
            versions_to_keep=cfg.d_correction_cache_versions_to_keep,
        ),
    ).validated()


def apply_d_correction_config_from_mapping(
    cfg: ModelConfig,
    raw: Optional[Dict[str, Any]],
) -> ModelConfig:
    """Наложить блок d_correction из model_config.json на ModelConfig."""
    if not raw:
        return cfg
    parsed = dcorr.d_correction_config_from_mapping(raw)
    return replace(
        cfg,
        d_correction_mode=parsed.mode,
        d_correction_slow_enabled=parsed.slow.enabled,
        d_correction_slow_alpha=parsed.slow.alpha,
        d_correction_slow_shrink_k=parsed.slow.shrink_k,
        d_correction_slow_min_observations=parsed.slow.min_observations,
        d_correction_slow_max_abs=parsed.slow.max_abs_correction,
        d_correction_fast_enabled=parsed.fast.enabled,
        d_correction_fast_alpha=parsed.fast.alpha,
        d_correction_fast_shrink_k=parsed.fast.shrink_k,
        d_correction_fast_min_observations=parsed.fast.min_observations,
        d_correction_fast_max_abs=parsed.fast.max_abs_correction,
        d_correction_total_max_abs=parsed.total_max_abs_correction,
        d_correction_cache_fallback=parsed.cache.fallback,
        d_correction_cache_versions_to_keep=parsed.cache.versions_to_keep,
    )


def build_d_correction_book_for_matches(
    matches: Sequence[PreparedMatch],
    strength: StrengthModel,
    cfg: ModelConfig,
) -> dcorr.DCorrectionBook:
    dcfg = resolve_d_correction_config(cfg)
    walk: List[dcorr.DCorrectionWalkMatch] = []
    for m in matches:
        if m.home_id not in strength.ratings or m.away_id not in strength.ratings:
            continue
        if m.diff_goals is None:
            continue
        derby = _is_derby_match(m.raw) and m.i_home == 1
        h_eff = effective_home_advantage(strength, cfg, neutral=m.i_home == 0, derby=derby)
        d_base = (
            strength.ratings.get(m.home_id, 0.0)
            - strength.ratings.get(m.away_id, 0.0)
            + h_eff
        )
        walk.append(
            dcorr.DCorrectionWalkMatch(
                match_date=m.raw.date,
                home_id=m.home_id,
                away_id=m.away_id,
                d_model_base=d_base,
                d_market=float(m.diff_goals),
            )
        )
    walk.sort(key=lambda w: (w.match_date is None, w.match_date or date.min, w.home_id, w.away_id))
    return dcorr.build_d_correction_walk(walk, dcfg)


def resolve_momentum_config(
    cfg: ModelConfig,
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> mom.MomentumConfig:
    base = mom.MomentumConfig(
        enabled=cfg.momentum_enabled,
        alpha=cfg.momentum_alpha,
        k=cfg.momentum_k,
        max_ema=cfg.momentum_max_ema,
        min_matches=cfg.momentum_min_matches,
    )
    overrides = cfg.momentum_by_league or {}
    for key in (league_id, league_name, str(league_id or ""), str(league_name or "")):
        if key and key in overrides and isinstance(overrides[key], dict):
            o = overrides[key]
            base = mom.MomentumConfig(
                enabled=bool(o["enabled"]) if "enabled" in o else base.enabled,
                alpha=float(o["alpha"]) if "alpha" in o else base.alpha,
                k=float(o["k"]) if "k" in o else base.k,
                max_ema=float(o.get("max_ema", o.get("maxEma", base.max_ema))),
                min_matches=int(o.get("min_matches", o.get("minMatches", base.min_matches))),
            )
            break
    return base.validated()


def build_momentum_book_for_matches(
    matches: Sequence[PreparedMatch],
    strength: StrengthModel,
    cfg: ModelConfig,
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> mom.MomentumBook:
    """Walk-forward EMA по подготовленным матчам; D_model из текущего strength (WLS без изменений)."""
    mcfg = resolve_momentum_config(cfg, league_id=league_id, league_name=league_name)
    walk: List[mom.MomentumWalkMatch] = []
    for m in matches:
        if m.home_id not in strength.ratings or m.away_id not in strength.ratings:
            continue
        # матч без D_market может участвовать только если есть дата — но в книгу для обновления
        # попадают только с d_market; без AH пропускаем полностью (нечего считать)
        if m.diff_goals is None:
            continue
        derby = _is_derby_match(m.raw) and m.i_home == 1
        h_eff = effective_home_advantage(strength, cfg, neutral=m.i_home == 0, derby=derby)
        d_base = (
            strength.ratings.get(m.home_id, 0.0)
            - strength.ratings.get(m.away_id, 0.0)
            + h_eff
        )
        walk.append(
            mom.MomentumWalkMatch(
                match_date=m.raw.date,
                home_id=m.home_id,
                away_id=m.away_id,
                d_model_base=d_base,
                d_market=float(m.diff_goals),
            )
        )
    # chronological order (build_momentum_walk also groups by date)
    walk.sort(key=lambda w: (w.match_date is None, w.match_date or date.min, w.home_id, w.away_id))
    return mom.build_momentum_walk(walk, mcfg)


def resolve_s_momentum_config(
    cfg: ModelConfig,
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> smom.SMomentumConfig:
    base = smom.SMomentumConfig(
        enabled=cfg.s_momentum_enabled,
        alpha=cfg.s_momentum_alpha,
        k=cfg.s_momentum_k,
        min_team_matches=cfg.s_momentum_min_matches,
        max_abs_team_ema=cfg.s_momentum_max_abs_team_ema,
        max_abs_correction=cfg.s_momentum_max_abs_correction,
        lambda_min=cfg.s_momentum_lambda_min,
        reset_on_new_season=cfg.s_momentum_reset_on_new_season,
    )
    overrides = cfg.s_momentum_by_league or {}
    for key in (league_id, league_name, str(league_id or ""), str(league_name or "")):
        if key and key in overrides and isinstance(overrides[key], dict):
            o = overrides[key]
            base = smom.SMomentumConfig(
                enabled=bool(o["enabled"]) if "enabled" in o else base.enabled,
                alpha=float(o["alpha"]) if "alpha" in o else base.alpha,
                k=float(o["k"]) if "k" in o else base.k,
                min_team_matches=int(
                    o.get("min_team_matches", o.get("minTeamMatches", o.get("minMatches", base.min_team_matches)))
                ),
                max_abs_team_ema=(
                    float(o["max_abs_team_ema"])
                    if "max_abs_team_ema" in o and o["max_abs_team_ema"] is not None
                    else (
                        float(o["maxAbsTeamEma"])
                        if "maxAbsTeamEma" in o and o["maxAbsTeamEma"] is not None
                        else base.max_abs_team_ema
                    )
                ),
                max_abs_correction=(
                    float(o["max_abs_correction"])
                    if "max_abs_correction" in o and o["max_abs_correction"] is not None
                    else (
                        float(o["maxAbsCorrection"])
                        if "maxAbsCorrection" in o and o["maxAbsCorrection"] is not None
                        else base.max_abs_correction
                    )
                ),
                lambda_min=float(o.get("lambda_min", o.get("lambdaMin", base.lambda_min))),
                reset_on_new_season=bool(
                    o.get("reset_on_new_season", o.get("resetOnNewSeason", base.reset_on_new_season))
                ),
            )
            break
    return base.validated()


def build_s_momentum_book_for_matches(
    matches: Sequence[PreparedMatch],
    strength: StrengthModel,
    goals: GoalModel,
    cfg: ModelConfig,
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> smom.SMomentumBook:
    """Walk-forward S-EMA; S_model_base из текущего A/D (без обратной связи от S-EMA)."""
    scfg = resolve_s_momentum_config(cfg, league_id=league_id, league_name=league_name)
    walk: List[smom.SMomentumWalkMatch] = []
    for m in matches:
        if m.sum_goals is None:
            continue
        if m.home_id not in goals.attack or m.away_id not in goals.attack:
            continue
        if m.home_id not in goals.defense or m.away_id not in goals.defense:
            continue
        try:
            _, s_base = _model_d_s(m, strength, goals, cfg)
        except Exception:  # noqa: BLE001
            continue
        walk.append(
            smom.SMomentumWalkMatch(
                match_date=m.raw.date,
                home_id=m.home_id,
                away_id=m.away_id,
                s_model_base=float(s_base),
                s_market=float(m.sum_goals),
                match_weight=float(m.w_base) if m.w_base is not None else 1.0,
            )
        )
    walk.sort(key=lambda w: (w.match_date is None, w.match_date or date.min, w.home_id, w.away_id))
    return smom.build_s_momentum_walk(walk, scfg)


def _build_team_names(matches: Sequence[PreparedMatch]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for m in matches:
        names[m.home_id] = m.home_team
        names[m.away_id] = m.away_team
    return names


def _slow_fast_map_from_book(
    book: Optional[dcorr.DCorrectionBook],
    ratings: Mapping[str, float],
    dcorr_cfg: dcorr.DCorrectionConfig,
) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    if book is None or dcorr_cfg.mode != dcorr.MODE_SLOW_FAST:
        for tid in ratings:
            out[tid] = {"slow_bias": 0.0, "fast_bias": 0.0}
        return out
    for tid in ratings:
        view = book.team_view(tid)
        out[tid] = {"slow_bias": float(view.slow_bias), "fast_bias": float(view.fast_bias)}
    return out


def _publish_strength_rating_cache(
    *,
    strength: StrengthModel,
    d_correction_book: Optional[dcorr.DCorrectionBook],
    dcorr_cfg: dcorr.DCorrectionConfig,
    rcfg: hwls.HierarchicalWlsConfig,
    league_id: Optional[str],
    league_name: Optional[str],
    model_version: str,
    rating_mode_label: str,
    training_cutoff: Optional[str],
    monitor: Optional[Dict[str, Any]] = None,
) -> None:
    if not rcfg.publish_cache:
        return
    # Build a HierarchicalFitResult-shaped payload even for standard_wls packages
    fit = hwls.HierarchicalFitResult(
        ratings=dict(strength.ratings),
        home_advantage=strength.home_advantage,
        derby_home_delta=strength.derby_home_delta,
        derby_n=strength.derby_n,
        derby_shrink_w=strength.derby_shrink_w,
        mae=strength.mae,
        rmse=strength.rmse,
        n=strength.n,
        team_meta=dict(strength.team_meta),
        rating_mode=rating_mode_label,
        rating_algorithm_version=strength.rating_algorithm_version,
        priors=dict(strength.priors),
        regularization_parameters=dict(strength.regularization_parameters),
        time_decay_parameters=dict(strength.time_decay_parameters),
    )
    # Ensure team_meta exists for standard packages
    if not fit.team_meta:
        lam0 = float(strength.regularization_parameters.get("reg_lambda", 0.0))
        for tid in strength.ratings:
            fit.team_meta[tid] = hwls.TeamRatingMeta(
                prior_rating=0.0,
                effective_n=0.0,
                rating_confidence=0.0,
                lambda_team=lam0,
            )
    mode_cfg = rcfg
    if rating_mode_label in hwls.VALID_MODES:
        mode_cfg = replace(rcfg, mode=rating_mode_label)
    payload = hwls.build_rating_cache_payload(
        fit=fit,
        cfg=mode_cfg,
        league_id=league_id,
        league_name=league_name,
        model_version=model_version,
        training_cutoff=training_cutoff,
        slow_fast=_slow_fast_map_from_book(d_correction_book, strength.ratings, dcorr_cfg),
        monitor=monitor,
    )
    payload.rating_mode = rating_mode_label
    hwls.publish_rating_model_cache(payload, replace(rcfg, publish_cache=True))


def train_full_model(
    raw: Sequence[RawMatch],
    cfg: Optional[ModelConfig] = None,
    prior: Optional["TrainedModel"] = None,
) -> Tuple[TrainedModel, List[PreparedMatch]]:
    """Обучить модель. prior — модель прошлого сезона для ridge-стягивания (§15)."""
    cfg = cfg or ModelConfig()
    matches = prepare_matches(raw, cfg)
    devig_and_infer(matches, cfg)
    d_clamp = d_clamp_diagnostics(matches, cfg)
    log_d_clamp_diagnostics(d_clamp)
    prior_r = prior.strength.ratings if prior else None
    prior_a = prior.goals.attack if prior else None
    prior_d = prior.goals.defense if prior else None

    league_name = next((m.raw.league for m in matches if m.raw.league), None)
    league_id = next((m.raw.league_id for m in matches if m.raw.league_id), None)
    rcfg = resolve_rating_config(cfg, league_id=league_id, league_name=league_name)
    logging.getLogger(__name__).info(
        "Training configuration lineWeightMode = %s",
        resolve_line_weight_config(cfg).mode.upper(),
    )

    # --- Strength fit(s): WLS remains the solver; hierarchical adds team λ ---
    shadow_strength: Optional[StrengthModel] = None
    shadow_d_correction_book: Optional[dcorr.DCorrectionBook] = None
    rating_shadow_monitor: Optional[Dict[str, Any]] = None

    if rcfg.mode == hwls.MODE_HIERARCHICAL:
        strength = fit_strength_ratings(
            matches, cfg, prior_ratings=prior_r,
            force_mode=hwls.MODE_HIERARCHICAL,
            league_id=league_id, league_name=league_name,
        )
        # Also keep a standard package available for config rollback (AC9)
        try:
            standard_side = fit_strength_ratings(
                matches, cfg, prior_ratings=prior_r,
                force_mode=hwls.MODE_STANDARD,
                league_id=league_id, league_name=league_name,
            )
        except Exception:
            logging.getLogger(__name__).exception("standard_wls side-fit failed")
            standard_side = None
    elif rcfg.mode == hwls.MODE_SHADOW:
        strength = fit_strength_ratings(
            matches, cfg, prior_ratings=prior_r,
            force_mode=hwls.MODE_STANDARD,
            league_id=league_id, league_name=league_name,
        )
        shadow_strength = fit_strength_ratings(
            matches, cfg, prior_ratings=prior_r,
            force_mode=hwls.MODE_HIERARCHICAL,
            league_id=league_id, league_name=league_name,
        )
        rating_shadow_monitor = hwls.shadow_compare_monitor(
            standard_ratings=strength.ratings,
            hierarchical_ratings=shadow_strength.ratings,
            standard_mae=strength.mae,
            hierarchical_mae=shadow_strength.mae,
        )
        logging.getLogger(__name__).info(
            "hierarchical_wls_shadow monitor %s", rating_shadow_monitor
        )
        standard_side = strength
    else:
        strength = fit_strength_ratings(
            matches, cfg, prior_ratings=prior_r,
            force_mode=hwls.MODE_STANDARD,
            league_id=league_id, league_name=league_name,
        )
        standard_side = strength

    log_line_weight_diagnostics(matches, cfg)
    sfa_book = build_sfa_book_for_matches(matches, cfg)
    sfa_cfg = resolve_sfa_config(cfg)

    goals = fit_attack_defense(matches, cfg, prior_attack=prior_a, prior_defense=prior_d)
    calibration = calibrate(matches, strength, goals, cfg)
    cal_diag = assess_calibration_stability(calibration, cfg=cfg)
    draw = fit_draw_for_config(matches, strength, goals, calibration, cfg)
    team_names = _build_team_names(matches)
    mcfg = resolve_momentum_config(cfg, league_id=league_id, league_name=league_name)
    momentum_book = build_momentum_book_for_matches(
        matches, strength, cfg, league_id=league_id, league_name=league_name,
    )
    scfg = resolve_s_momentum_config(cfg, league_id=league_id, league_name=league_name)
    s_momentum_book = build_s_momentum_book_for_matches(
        matches, strength, goals, cfg, league_id=league_id, league_name=league_name,
    )
    dcorr_cfg = resolve_d_correction_config(cfg)
    # Slow/fast MUST be rebuilt from the production strength's D_base (AC7)
    d_correction_book = build_d_correction_book_for_matches(matches, strength, cfg)
    if shadow_strength is not None:
        shadow_d_correction_book = build_d_correction_book_for_matches(
            matches, shadow_strength, cfg
        )
    model_version = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dates = [m.raw.date for m in matches if m.raw.date is not None]
    training_cutoff = max(dates).isoformat() if dates else None

    if cfg.d_correction_publish_cache and dcorr_cfg.mode == dcorr.MODE_SLOW_FAST:
        try:
            payload = dcorr.build_cache_payload(
                strength_ratings=strength.ratings,
                home_advantage=strength.home_advantage,
                book=d_correction_book,
                cfg=dcorr_cfg,
                league_id=str(league_id) if league_id is not None else None,
                league_name=league_name,
                model_version=model_version,
            )
            dcorr.publish_d_model_cache(payload, dcorr_cfg)
            log_d_correction_monitor(d_correction_book, model_version)
        except Exception:
            logging.getLogger(__name__).exception(
                "D model cache publish failed — keeping previous active version"
            )

    # Versioned rating packages (filesystem only). Never mix R/H/slow/fast versions.
    try:
        if rcfg.publish_cache:
            if standard_side is not None and rcfg.mode in (
                hwls.MODE_STANDARD, hwls.MODE_SHADOW, hwls.MODE_HIERARCHICAL
            ):
                std_book = (
                    d_correction_book
                    if strength is standard_side
                    else build_d_correction_book_for_matches(matches, standard_side, cfg)
                )
                _publish_strength_rating_cache(
                    strength=standard_side,
                    d_correction_book=std_book,
                    dcorr_cfg=dcorr_cfg,
                    rcfg=rcfg,
                    league_id=str(league_id) if league_id is not None else None,
                    league_name=league_name,
                    model_version=model_version,
                    rating_mode_label=hwls.MODE_STANDARD,
                    training_cutoff=training_cutoff,
                )
            hier_src = strength if rcfg.mode == hwls.MODE_HIERARCHICAL else shadow_strength
            hier_book = (
                d_correction_book
                if rcfg.mode == hwls.MODE_HIERARCHICAL
                else shadow_d_correction_book
            )
            if hier_src is not None and rcfg.mode in (hwls.MODE_HIERARCHICAL, hwls.MODE_SHADOW):
                _publish_strength_rating_cache(
                    strength=hier_src,
                    d_correction_book=hier_book,
                    dcorr_cfg=dcorr_cfg,
                    rcfg=rcfg,
                    league_id=str(league_id) if league_id is not None else None,
                    league_name=league_name,
                    model_version=model_version,
                    rating_mode_label=hwls.MODE_HIERARCHICAL,
                    training_cutoff=training_cutoff,
                    monitor=rating_shadow_monitor,
                )
    except Exception:
        logging.getLogger(__name__).exception(
            "Rating model cache publish failed — keeping previous active version"
        )

    model = TrainedModel(
        strength, goals, calibration, draw, cfg, team_names, d_clamp, cal_diag, None, None, None,
        momentum_book=momentum_book, momentum_cfg=mcfg,
        s_momentum_book=s_momentum_book, s_momentum_cfg=scfg,
        d_correction_book=d_correction_book, d_correction_cfg=dcorr_cfg,
        d_model_version=model_version,
        shadow_strength=shadow_strength,
        shadow_d_correction_book=shadow_d_correction_book,
        rating_shadow_monitor=rating_shadow_monitor,
        rating_model_version=model_version,
        sfa_book=sfa_book, sfa_cfg=sfa_cfg,
    )
    draw_q_diag = draw_q_diagnostics(model, matches)
    log_draw_q_diagnostics(draw_q_diag)
    sd_diag = sd_1x2_diagnostics(model, matches)
    log_sd_1x2_diagnostics(sd_diag)
    draw_harm_diag = draw_harm_diagnostics(model, matches)
    log_draw_harm_diagnostics(draw_harm_diag)
    return TrainedModel(
        strength, goals, calibration, draw, cfg, team_names,
        d_clamp, cal_diag, draw_q_diag, sd_diag, draw_harm_diag,
        momentum_book=momentum_book, momentum_cfg=mcfg,
        s_momentum_book=s_momentum_book, s_momentum_cfg=scfg,
        d_correction_book=d_correction_book, d_correction_cfg=dcorr_cfg,
        d_model_version=model_version,
        shadow_strength=shadow_strength,
        shadow_d_correction_book=shadow_d_correction_book,
        rating_shadow_monitor=rating_shadow_monitor,
        rating_model_version=model_version,
        sfa_book=sfa_book, sfa_cfg=sfa_cfg,
    ), matches


def log_d_correction_monitor(book: dcorr.DCorrectionBook, model_version: str) -> None:
    mon = book.monitor or {}
    logging.getLogger(__name__).info(
        "d_correction monitor version=%s mode=%s teams=%s slow=%s fast=%s "
        "mean|slow|=%.3f max|slow|=%.3f mean|fast|=%.3f max|fast|=%.3f clamp_hits=%s",
        model_version,
        mon.get("mode"),
        mon.get("n_teams"),
        mon.get("n_teams_with_slow"),
        mon.get("n_teams_with_fast"),
        float(mon.get("mean_abs_slow") or 0.0),
        float(mon.get("max_abs_slow") or 0.0),
        float(mon.get("mean_abs_fast") or 0.0),
        float(mon.get("max_abs_fast") or 0.0),
        mon.get("clamp_hits"),
    )


@dataclass
class Prediction:
    home_team_id: str
    away_team_id: str
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
    d_model_base: Optional[float] = None
    d_model_dynamic: Optional[float] = None
    dynamic_correction: Optional[float] = None
    momentum: Optional[mom.MomentumSnapshot] = None
    s_model_base: Optional[float] = None
    s_model_dynamic: Optional[float] = None
    s_dynamic_correction: Optional[float] = None
    s_momentum: Optional[smom.SMomentumSnapshot] = None
    lambda_home_raw: Optional[float] = None
    lambda_away_raw: Optional[float] = None
    lambda_clipping_applied: bool = False
    # Dixon–Coles dynamic γ diagnostics
    gamma_season: Optional[float] = None
    gamma_effective: Optional[float] = None
    gamma_segment: Optional[str] = None
    abs_d_model_final: Optional[float] = None
    dynamic_dc_gamma_enabled: Optional[bool] = None
    dc_applied: Optional[bool] = None
    dc_fallback_used: Optional[bool] = None
    dc_fallback_reason: Optional[str] = None
    home_probability_poisson: Optional[float] = None
    draw_probability_poisson: Optional[float] = None
    away_probability_poisson: Optional[float] = None
    home_probability_final: Optional[float] = None
    draw_probability_final: Optional[float] = None
    away_probability_final: Optional[float] = None
    dc_home_probability_delta: Optional[float] = None
    dc_draw_probability_delta: Optional[float] = None
    dc_away_probability_delta: Optional[float] = None
    score_00_poisson: Optional[float] = None
    score_11_poisson: Optional[float] = None
    score_00_final: Optional[float] = None
    score_11_final: Optional[float] = None
    # slow/fast D correction diagnostics (AC10)
    d_correction_mode: Optional[str] = None
    model_version: Optional[str] = None
    d_slow: Optional[float] = None
    slow_bias_home: Optional[float] = None
    slow_bias_away: Optional[float] = None
    fast_bias_home: Optional[float] = None
    fast_bias_away: Optional[float] = None
    d_fast_correction: Optional[float] = None
    slow_observations_home: Optional[int] = None
    slow_observations_away: Optional[int] = None
    fast_observations_home: Optional[int] = None
    fast_observations_away: Optional[int] = None
    slow_shrink_factor_home: Optional[float] = None
    slow_shrink_factor_away: Optional[float] = None
    d_correction: Optional[dcorr.DCorrectionSnapshot] = None
    # Strong Favorite Adjustment diagnostics (AC7)
    sfa: Optional[sfa.SfaDiagnostics] = None
    d_before_sfa: Optional[float] = None


def resolve_dynamic_dc_config(cfg: ModelConfig) -> ddc.DynamicDcGammaConfig:
    return ddc.config_from_model_fields(
        enabled=cfg.dynamic_dc_gamma_enabled,
        source=cfg.dynamic_dc_gamma_source,
        default_gamma=cfg.dynamic_dc_default_gamma,
        segments=cfg.dynamic_dc_segments,
    )


def _promoted_rating(values: Dict[str, float], n: int) -> float:
    """Стартовый рейтинг новичка лиги = среднее n слабейших команд (§15)."""
    if not values:
        return 0.0
    weakest = sorted(values.values())[: max(1, n)]
    return sum(weakest) / len(weakest)


def predict_match(
    model: TrainedModel,
    home_id: str,
    away_id: str,
    *,
    neutral: bool = False,
    derby: bool = False,
    match_date: Optional[date] = None,
    use_momentum_lookup: bool = True,
    apply_momentum: bool = True,
    apply_s_momentum: bool = True,
    league: Optional[str] = None,
    season: Optional[str] = None,
    apply_sfa: bool = True,
) -> Prediction:
    cfg = model.config
    s, g, cal = model.strength, model.goals, model.calibration
    i_home = 0 if neutral else 1
    h_eff = effective_home_advantage(s, cfg, neutral=neutral, derby=derby)
    names = model.team_names or {}
    home_name = names.get(home_id, home_id)
    away_name = names.get(away_id, away_id)

    unknown = [t for t in (home_id, away_id) if t not in s.ratings]
    if unknown and not cfg.allow_unknown_teams:
        missing = ", ".join(names.get(t, t) for t in unknown)
        raise ValueError(f"Команда не найдена в модели: {missing}")

    def rating(t: str) -> float:
        return s.ratings[t] if t in s.ratings else _promoted_rating(s.ratings, cfg.promoted_reference_n)

    def attack(t: str) -> float:
        return g.attack[t] if t in g.attack else _promoted_rating(g.attack, cfg.promoted_reference_n)

    def defense(t: str) -> float:
        return g.defense[t] if t in g.defense else _promoted_rating(g.defense, cfg.promoted_reference_n)

    k_hg = (h_eff / s.home_advantage) if s.home_advantage > 1e-9 else 1.0
    d_model_base = rating(home_id) - rating(away_id) + h_eff
    lh_ad = math.exp(g.mu + attack(home_id) - defense(away_id) + g.home_goal_adv * i_home * k_hg)
    la_ad = math.exp(g.mu + attack(away_id) - defense(home_id))
    s_model_base = lh_ad + la_ad

    mom_snap: Optional[mom.MomentumSnapshot] = None
    dcorr_snap: Optional[dcorr.DCorrectionSnapshot] = None
    d_for_cal = d_model_base
    dcorr_cfg = model.d_correction_cfg or resolve_d_correction_config(cfg)
    mode = dcorr_cfg.mode

    # --- D correction architecture ---
    if mode == dcorr.MODE_DISABLED or not apply_momentum:
        # disabled: plain base; also honor apply_momentum=False as "no dynamic D"
        if mode == dcorr.MODE_DISABLED or (
            mode == dcorr.MODE_SLOW_FAST and not apply_momentum
        ):
            dcorr_snap = dcorr.DCorrectionSnapshot(
                d_model_base=d_model_base,
                slow_bias_home=0.0, slow_bias_away=0.0, d_slow=d_model_base,
                fast_bias_home=0.0, fast_bias_away=0.0, d_fast_correction=0.0,
                d_model_dynamic=d_model_base, total_correction=0.0, mode=mode,
                slow_observations_home=0, slow_observations_away=0,
                fast_observations_home=0, fast_observations_away=0,
                slow_shrink_factor_home=0.0, slow_shrink_factor_away=0.0,
                fast_shrink_factor_home=0.0, fast_shrink_factor_away=0.0,
            )
            d_for_cal = d_model_base
            mom_snap = mom.MomentumSnapshot(
                ema_home_before=0.0, ema_away_before=0.0,
                home_matches_count=0, away_matches_count=0,
                dynamic_correction=0.0,
                d_model_base=d_model_base, d_model_dynamic=d_model_base,
                momentum_enabled=False,
                momentum_alpha=0.0, momentum_k=0.0, momentum_max_ema=0.0,
                ema_home_limited=0.0, ema_away_limited=0.0,
            )
        elif mode == dcorr.MODE_LEGACY_EMA and not apply_momentum:
            mom_snap = mom.MomentumSnapshot(
                ema_home_before=0.0, ema_away_before=0.0,
                home_matches_count=0, away_matches_count=0,
                dynamic_correction=0.0,
                d_model_base=d_model_base, d_model_dynamic=d_model_base,
                momentum_enabled=False,
                momentum_alpha=(model.momentum_cfg or resolve_momentum_config(cfg)).alpha,
                momentum_k=(model.momentum_cfg or resolve_momentum_config(cfg)).k,
                momentum_max_ema=(model.momentum_cfg or resolve_momentum_config(cfg)).max_ema,
                ema_home_limited=0.0, ema_away_limited=0.0,
            )
            d_for_cal = d_model_base

    if mode == dcorr.MODE_SLOW_FAST and apply_momentum:
        book_sf = model.d_correction_book
        if book_sf is not None:
            key = dcorr.match_key(match_date, home_id, away_id) if match_date else None
            rec = book_sf.records.get(key) if (use_momentum_lookup and key) else None
            if rec is not None:
                dcorr_snap = dcorr.DCorrectionSnapshot(
                    d_model_base=d_model_base,
                    slow_bias_home=rec.slow_bias_home,
                    slow_bias_away=rec.slow_bias_away,
                    d_slow=d_model_base + rec.slow_bias_home - rec.slow_bias_away,
                    fast_bias_home=rec.fast_bias_home,
                    fast_bias_away=rec.fast_bias_away,
                    d_fast_correction=rec.d_fast_correction,
                    d_model_dynamic=d_model_base + rec.total_correction,
                    total_correction=rec.total_correction,
                    mode=rec.mode,
                    slow_observations_home=rec.slow_observations_home,
                    slow_observations_away=rec.slow_observations_away,
                    fast_observations_home=rec.fast_observations_home,
                    fast_observations_away=rec.fast_observations_away,
                    slow_shrink_factor_home=rec.slow_shrink_factor_home,
                    slow_shrink_factor_away=rec.slow_shrink_factor_away,
                    fast_shrink_factor_home=rec.fast_shrink_factor_home,
                    fast_shrink_factor_away=rec.fast_shrink_factor_away,
                    clamped_total=rec.clamped_total,
                )
            else:
                dcorr_snap = book_sf.peek(
                    home_id=home_id, away_id=away_id, d_model_base=d_model_base,
                )
            d_for_cal = dcorr_snap.d_model_dynamic
        else:
            dcorr_snap = dcorr.apply_slow_fast_to_d(
                d_model_base,
                dcorr.TeamCorrectionState(),
                dcorr.TeamCorrectionState(),
                dcorr_cfg,
            )
            d_for_cal = d_model_base
        # legacy momentum snapshot left empty / zero for compatibility
        mom_snap = mom.MomentumSnapshot(
            ema_home_before=0.0, ema_away_before=0.0,
            home_matches_count=0, away_matches_count=0,
            dynamic_correction=dcorr_snap.total_correction,
            d_model_base=d_model_base, d_model_dynamic=d_for_cal,
            momentum_enabled=False,
            momentum_alpha=0.0, momentum_k=0.0, momentum_max_ema=0.0,
            ema_home_limited=0.0, ema_away_limited=0.0,
        )
    elif mode == dcorr.MODE_LEGACY_EMA and apply_momentum:
        book = model.momentum_book
        mcfg = model.momentum_cfg or resolve_momentum_config(cfg)
        if book is not None:
            key = mom.match_key(match_date, home_id, away_id) if match_date else None
            rec = book.records.get(key) if (use_momentum_lookup and key) else None
            if rec is not None:
                mom_snap = mom.apply_momentum_to_d(
                    d_model_base,
                    rec.ema_home_before,
                    rec.ema_away_before,
                    mcfg,
                    home_matches=rec.home_matches_count,
                    away_matches=rec.away_matches_count,
                )
            else:
                mom_snap = book.peek(
                    home_id=home_id, away_id=away_id, d_model_base=d_model_base, match_date=match_date,
                )
            d_for_cal = mom_snap.d_model_dynamic
        elif mom_snap is None:
            mom_snap = mom.MomentumSnapshot(
                ema_home_before=0.0, ema_away_before=0.0,
                home_matches_count=0, away_matches_count=0,
                dynamic_correction=0.0,
                d_model_base=d_model_base, d_model_dynamic=d_model_base,
                momentum_enabled=False,
                momentum_alpha=mcfg.alpha, momentum_k=mcfg.k, momentum_max_ema=mcfg.max_ema,
                ema_home_limited=0.0, ema_away_limited=0.0,
            )
    elif mode == dcorr.MODE_DISABLED:
        d_for_cal = d_model_base
        if mom_snap is None:
            mom_snap = mom.MomentumSnapshot(
                ema_home_before=0.0, ema_away_before=0.0,
                home_matches_count=0, away_matches_count=0,
                dynamic_correction=0.0,
                d_model_base=d_model_base, d_model_dynamic=d_model_base,
                momentum_enabled=False,
                momentum_alpha=0.0, momentum_k=0.0, momentum_max_ema=0.0,
                ema_home_limited=0.0, ema_away_limited=0.0,
            )

    s_mom_snap: Optional[smom.SMomentumSnapshot] = None
    s_for_cal = s_model_base
    s_book = model.s_momentum_book
    scfg = model.s_momentum_cfg or resolve_s_momentum_config(cfg)
    if apply_s_momentum and s_book is not None:
        key_s = smom.match_key(match_date, home_id, away_id) if match_date else None
        rec_s = s_book.records.get(key_s) if (use_momentum_lookup and key_s) else None
        if rec_s is not None:
            s_mom_snap = smom.apply_s_momentum(
                s_model_base,
                rec_s.ema_home_before,
                rec_s.ema_away_before,
                scfg,
                home_matches=rec_s.home_matches_count,
                away_matches=rec_s.away_matches_count,
            )
        else:
            s_mom_snap = s_book.peek(
                home_id=home_id, away_id=away_id, s_model_base=s_model_base,
            )
        s_for_cal = s_mom_snap.s_model_dynamic
    elif not apply_s_momentum:
        s_mom_snap = smom.apply_s_momentum(
            s_model_base, 0.0, 0.0,
            smom.SMomentumConfig(
                enabled=False, alpha=scfg.alpha, k=scfg.k,
                min_team_matches=scfg.min_team_matches,
                max_abs_team_ema=scfg.max_abs_team_ema,
                max_abs_correction=scfg.max_abs_correction,
                lambda_min=scfg.lambda_min,
            ),
            home_matches=0, away_matches=0,
        )

    d_final = cal.d_a + cal.d_b * d_for_cal
    s_final = apply_s_calibration(s_for_cal, cal.s_a, cal.s_b, cfg)
    d_before_sfa = float(d_final)
    sfa_diag: Optional[sfa.SfaDiagnostics] = None
    if apply_sfa:
        sfa_cfg_use = model.sfa_cfg or resolve_sfa_config(cfg)
        lg = league
        if lg is None and model.sfa_book is not None:
            lg = model.sfa_book.league_key or None
        d_final, sfa_diag = sfa.apply_strong_favorite_adjustment(
            d_final,
            s_final,
            sfa_cfg_use,
            model.sfa_book,
            league=lg,
            season=season,
            max_goals=cfg.max_goals,
            lambda_min=float(
                (model.s_momentum_cfg or resolve_s_momentum_config(cfg)).lambda_min
            ),
            already_applied=False,
        )
    d_final = gm.clamp_goal_diff(d_final, s_final, cfg.lambda_epsilon)

    lam_min = scfg.lambda_min
    lh_raw, la_raw, lambda_home, lambda_away, clipped, _, _ = smom.lambdas_from_sd(
        s_final, d_final, lambda_min=lam_min,
    )
    if clipped:
        s_final = lambda_home + lambda_away
        d_final = lambda_home - lambda_away

    matrix_poisson = gm.build_score_matrix(lambda_home, lambda_away, cfg.max_goals)
    p1_pois, px_pois, p2_pois = gm.compute_1x2(matrix_poisson)
    score_00_pois = matrix_poisson[0][0] if matrix_poisson else 0.0
    score_11_pois = matrix_poisson[1][1] if len(matrix_poisson) > 1 else 0.0

    gamma_season = float(cal.gamma or 0.0) if cfg.use_dixon_coles else 0.0
    ddc_cfg = resolve_dynamic_dc_config(cfg)
    dc_res = ddc.resolve_gamma_effective(
        d_final, gamma_season=gamma_season, cfg=ddc_cfg,
    )
    gamma_eff = float(dc_res.gamma_effective) if cfg.use_dixon_coles else 0.0
    # if DC globally off, force zero
    if not cfg.use_dixon_coles:
        gamma_eff = 0.0
        dc_res = ddc.DcGammaResolution(
            gamma_season=0.0,
            dynamic_dc_gamma_enabled=False,
            d_model_final=d_final,
            abs_d_model_final=abs(d_final),
            gamma_segment=ddc.SEGMENT_DISABLED,
            gamma_effective=0.0,
            dc_applied=False,
            dc_fallback_used=False,
            dc_fallback_reason=None,
        )

    matrix = matrix_poisson
    if cfg.use_dixon_coles and abs(gamma_eff) > 1e-15:
        matrix = gm.apply_dixon_coles(matrix_poisson, lambda_home, lambda_away, gamma_eff)

    draw_target = None
    draw_diag = None
    if cfg.use_draw_model:
        px_matrix = gm.draw_probability(matrix)
        if model.draw.mode == "residual_dc":
            q = model.draw.target_q_multiplier(d_final, s_final, cfg)
            draw_target = q * px_matrix
        else:
            draw_target = model.draw.target_px(d_final, s_final)
        q_min, q_max = effective_draw_q_bounds(cfg, model.draw.mode)
        matrix, draw_diag = gm.adjust_matrix_to_draw_target(
            matrix, draw_target,
            q_min=q_min,
            q_max=q_max,
        )

    p1_fin, px_fin, p2_fin = gm.compute_1x2(matrix)
    score_00_fin = matrix[0][0] if matrix else 0.0
    score_11_fin = matrix[1][1] if len(matrix) > 1 else 0.0

    markets = gm.markets_from_matrix(matrix)
    return Prediction(
        home_team_id=home_id, away_team_id=away_id,
        home_team=home_name, away_team=away_name,
        lambda_home=lambda_home, lambda_away=lambda_away,
        d_model=d_for_cal, s_model=s_for_cal, d_final=d_final, s_final=s_final,
        markets=markets,
        draw_target=draw_target,
        draw_diagnostics=draw_diag,
        d_model_base=d_model_base,
        d_model_dynamic=(
            dcorr_snap.d_model_dynamic if dcorr_snap is not None
            else (mom_snap.d_model_dynamic if mom_snap else d_model_base)
        ),
        dynamic_correction=(
            dcorr_snap.total_correction if dcorr_snap is not None
            else (mom_snap.dynamic_correction if mom_snap else 0.0)
        ),
        momentum=mom_snap,
        s_model_base=s_model_base,
        s_model_dynamic=s_mom_snap.s_model_dynamic if s_mom_snap else s_model_base,
        s_dynamic_correction=s_mom_snap.dynamic_correction if s_mom_snap else 0.0,
        s_momentum=s_mom_snap,
        lambda_home_raw=lh_raw,
        lambda_away_raw=la_raw,
        lambda_clipping_applied=clipped,
        gamma_season=dc_res.gamma_season,
        gamma_effective=gamma_eff,
        gamma_segment=dc_res.gamma_segment,
        abs_d_model_final=dc_res.abs_d_model_final,
        dynamic_dc_gamma_enabled=dc_res.dynamic_dc_gamma_enabled,
        dc_applied=bool(dc_res.dc_applied and cfg.use_dixon_coles),
        dc_fallback_used=dc_res.dc_fallback_used,
        dc_fallback_reason=dc_res.dc_fallback_reason,
        home_probability_poisson=p1_pois,
        draw_probability_poisson=px_pois,
        away_probability_poisson=p2_pois,
        home_probability_final=p1_fin,
        draw_probability_final=px_fin,
        away_probability_final=p2_fin,
        dc_home_probability_delta=p1_fin - p1_pois,
        dc_draw_probability_delta=px_fin - px_pois,
        dc_away_probability_delta=p2_fin - p2_pois,
        score_00_poisson=score_00_pois,
        score_11_poisson=score_11_pois,
        score_00_final=score_00_fin,
        score_11_final=score_11_fin,
        d_correction_mode=mode,
        model_version=model.d_model_version,
        d_slow=dcorr_snap.d_slow if dcorr_snap else None,
        slow_bias_home=dcorr_snap.slow_bias_home if dcorr_snap else None,
        slow_bias_away=dcorr_snap.slow_bias_away if dcorr_snap else None,
        fast_bias_home=dcorr_snap.fast_bias_home if dcorr_snap else None,
        fast_bias_away=dcorr_snap.fast_bias_away if dcorr_snap else None,
        d_fast_correction=dcorr_snap.d_fast_correction if dcorr_snap else None,
        slow_observations_home=dcorr_snap.slow_observations_home if dcorr_snap else None,
        slow_observations_away=dcorr_snap.slow_observations_away if dcorr_snap else None,
        fast_observations_home=dcorr_snap.fast_observations_home if dcorr_snap else None,
        fast_observations_away=dcorr_snap.fast_observations_away if dcorr_snap else None,
        slow_shrink_factor_home=dcorr_snap.slow_shrink_factor_home if dcorr_snap else None,
        slow_shrink_factor_away=dcorr_snap.slow_shrink_factor_away if dcorr_snap else None,
        d_correction=dcorr_snap,
        sfa=sfa_diag,
        d_before_sfa=d_before_sfa,
    )


# --------------------------------------------------------------------------- #
# Диагностика q (DC → draw model): clamp rate
# --------------------------------------------------------------------------- #

@dataclass
class DrawQDiagnosticRow:
    home_team: str
    away_team: str
    q_raw: float
    q_used: float
    clamp_low: bool
    clamp_high: bool


@dataclass
class DrawQDiagnostics:
    n_eval: int
    n_clamped: int
    clamp_pct: float
    n_near_one: int
    near_one_pct: float
    q_min: float
    q_max: float
    warn_pct: float
    stable: bool
    summary: str
    rows: List[DrawQDiagnosticRow] = field(default_factory=list)


def _is_q_clamped(q_raw: float, q_min: float, q_max: float, eps: float = 1e-6) -> bool:
    return q_raw < q_min - eps or q_raw > q_max + eps


def draw_q_diagnostics(
    model: TrainedModel,
    prepared: Sequence[PreparedMatch],
    *,
    warn_pct: float = 12.0,
) -> DrawQDiagnostics:
    """Доля матчей, где q = clamp(P_X^target/P_X^matrix) упёрся в q_min/q_max."""
    cfg = model.config
    q_min, q_max = effective_draw_q_bounds(cfg, model.draw.mode if cfg.use_draw_model else None)
    if not cfg.use_draw_model:
        return DrawQDiagnostics(
            0, 0, 0.0, 0, 0.0, q_min, q_max, warn_pct, True,
            "модель ничьи выкл", [],
        )
    clamped_rows: List[DrawQDiagnosticRow] = []
    n_eval = 0
    n_near_one = 0
    for m in prepared:
        pred = predict_match(
            model, m.home_id, m.away_id,
            neutral=m.i_home == 0,
            derby=_is_derby_match(m.raw) and m.i_home == 1,
            apply_momentum=False,
            apply_s_momentum=False,
        )
        diag = pred.draw_diagnostics
        if not diag:
            continue
        n_eval += 1
        q_raw = diag["diag_multiplier_raw"]
        q_used = diag["diag_multiplier_used"]
        if 0.98 <= q_used <= 1.03:
            n_near_one += 1
        if _is_q_clamped(q_raw, q_min, q_max):
            clamped_rows.append(DrawQDiagnosticRow(
                home_team=m.home_team,
                away_team=m.away_team,
                q_raw=q_raw,
                q_used=q_used,
                clamp_low=abs(q_used - q_min) < 1e-6,
                clamp_high=abs(q_used - q_max) < 1e-6,
            ))
    clamped_rows.sort(key=lambda r: abs(r.q_raw - r.q_used), reverse=True)
    n_clamped = len(clamped_rows)
    clamp_pct = 100.0 * n_clamped / n_eval if n_eval else 0.0
    near_one_pct = 100.0 * n_near_one / n_eval if n_eval else 0.0
    stable = clamp_pct <= warn_pct
    if n_eval == 0:
        summary = "нет матчей для оценки q"
    elif stable:
        summary = (
            f"стабильно: q clamp {clamp_pct:.0f}% ({n_clamped}/{n_eval}), "
            f"q≈1 у {near_one_pct:.0f}%"
        )
    else:
        dc_note = " (DC+ничья)" if cfg.use_dixon_coles else " (матрица→ничья)"
        summary = (
            f"q clamp {clamp_pct:.0f}% ({n_clamped}/{n_eval}) — >{warn_pct:.0f}%, "
            f"проверьте базовую матрицу и draw model{dc_note}"
        )
    return DrawQDiagnostics(
        n_eval=n_eval, n_clamped=n_clamped, clamp_pct=clamp_pct,
        n_near_one=n_near_one, near_one_pct=near_one_pct,
        q_min=q_min, q_max=q_max, warn_pct=warn_pct, stable=stable,
        summary=summary, rows=clamped_rows,
    )


def log_draw_q_diagnostics(diag: DrawQDiagnostics) -> None:
    log = logging.getLogger(__name__)
    if diag.n_eval == 0:
        log.info("Draw q: %s", diag.summary)
        return
    level = logging.WARNING if not diag.stable else logging.INFO
    log.log(level, "Draw q clamp: %s", diag.summary)
    for row in diag.rows[:12]:
        bound = "q_min" if row.clamp_low else ("q_max" if row.clamp_high else "?")
        log.log(
            level,
            "  %s — %s: q_raw=%.3f → q=%.3f [%s]",
            row.home_team, row.away_team, row.q_raw, row.q_used, bound,
        )
    if len(diag.rows) > 12:
        log.log(level, "  … и ещё %d матчей", len(diag.rows) - 12)


# --------------------------------------------------------------------------- #
# Диагностика: модель ничьи ухудшает Dixon–Coles?
# --------------------------------------------------------------------------- #

@dataclass
class DrawHarmDiagnostics:
    n_matches: int
    mean_err_px_poisson: float
    mean_err_px_after_dc: float
    mean_err_px_final: float
    mean_abs_err_poisson: float
    mean_abs_err_after_dc: float
    mean_abs_err_final: float
    n_draw_harms: int
    pct_draw_harms: float
    harms_worse_than_after_dc: bool
    low_s_threshold: float
    low_s_n: int
    low_s_mean_px_market: float
    low_s_mean_px_poisson: float
    low_s_mean_px_after_dc: float
    low_s_mean_px_final: float
    summary: str


def _match_draw_px_stages(
    model: TrainedModel,
    m: PreparedMatch,
) -> Optional[Tuple[float, float, float]]:
    """(PX_poisson, PX_after_DC, PX_final) для матча."""
    if m.px_shin is None:
        return None
    cfg = model.config
    cal = model.calibration
    gamma_season = float(cal.gamma or 0.0) if cfg.use_dixon_coles else 0.0
    _, _, s_cal, d_cal = _calibrated_sd_for_match(
        m, model.strength, model.goals, cal, cfg,
    )
    ddc_cfg = resolve_dynamic_dc_config(cfg)
    dc_res = ddc.resolve_gamma_effective(
        d_cal, gamma_season=gamma_season, cfg=ddc_cfg,
    )
    gamma_eff = float(dc_res.gamma_effective) if cfg.use_dixon_coles else 0.0
    _, px_poisson, _ = _prob_1x2_from_sd(s_cal, d_cal, cfg, gamma=0.0)
    _, px_after_dc, _ = _prob_1x2_from_sd(s_cal, d_cal, cfg, gamma=gamma_eff)
    pred = predict_match(
        model, m.home_id, m.away_id,
        neutral=m.i_home == 0,
        derby=_is_derby_match(m.raw) and m.i_home == 1,
        apply_momentum=False,
        apply_s_momentum=False,
        apply_sfa=False,  # isolate draw-model harm from SFA
    )
    return px_poisson, px_after_dc, pred.markets.px


def draw_harm_diagnostics(
    model: TrainedModel,
    prepared: Sequence[PreparedMatch],
    *,
    low_s_threshold: float = 2.3,
    harm_eps: float = 1e-9,
) -> DrawHarmDiagnostics:
    """Сравнение ошибки ничьи: Poisson → DC → финал (с draw model)."""
    poisson_errs: List[float] = []
    after_dc_errs: List[float] = []
    final_errs: List[float] = []
    n_harms = 0
    low_s_mkt: List[float] = []
    low_s_pois: List[float] = []
    low_s_dc: List[float] = []
    low_s_fin: List[float] = []

    for m in prepared:
        stages = _match_draw_px_stages(model, m)
        if stages is None:
            continue
        px_pois, px_dc, px_fin = stages
        px_mkt = m.px_shin
        e_pois = px_pois - px_mkt
        e_dc = px_dc - px_mkt
        e_fin = px_fin - px_mkt
        poisson_errs.append(e_pois)
        after_dc_errs.append(e_dc)
        final_errs.append(e_fin)
        if abs(e_fin) > abs(e_dc) + harm_eps:
            n_harms += 1
        if m.sum_goals is not None and m.sum_goals < low_s_threshold:
            low_s_mkt.append(px_mkt)
            low_s_pois.append(px_pois)
            low_s_dc.append(px_dc)
            low_s_fin.append(px_fin)

    def _avg(vals: Sequence[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    n = len(final_errs)
    mean_pois = _avg(poisson_errs)
    mean_dc = _avg(after_dc_errs)
    mean_fin = _avg(final_errs)
    mae_pois = _avg([abs(e) for e in poisson_errs])
    mae_dc = _avg([abs(e) for e in after_dc_errs])
    mae_fin = _avg([abs(e) for e in final_errs])
    pct_harms = 100.0 * n_harms / n if n else 0.0
    harms_worse = (
        abs(mean_fin) > abs(mean_dc) + harm_eps
        or mae_fin > mae_dc + harm_eps
    )

    parts = [
        f"err_PX: Пуассон {mean_pois:+.3f}",
        f"после DC {mean_dc:+.3f}",
        f"финал {mean_fin:+.3f}",
    ]
    if n == 0:
        summary = "нет матчей с 1X2"
    elif not model.config.use_draw_model:
        body = "; ".join(parts) + " (модель ничьи выкл)"
        if mean_pois < -0.01 and abs(mean_dc) < 0.008:
            summary = (
                "Пуассон системно занижает ничью, DC исправляет среднюю ошибку. "
                + body
            )
        else:
            summary = body
        low_s_gap = _avg(low_s_dc) - _avg(low_s_mkt) if low_s_mkt else 0.0
        if low_s_mkt and low_s_gap < -0.008:
            summary += (
                f"; S<{low_s_threshold}: недобор ничьи после DC "
                f"({low_s_gap * 100:+.1f} п.п.) — возможна residual-поправка"
            )
    elif harms_worse:
        summary = (
            "; ".join(parts)
            + f"; модель ничьи ухудшает DC на этой выборке ({pct_harms:.0f}% матчей)"
        )
    else:
        summary = "; ".join(parts) + f"; draw OK ({pct_harms:.0f}% хуже DC)"

    return DrawHarmDiagnostics(
        n_matches=n,
        mean_err_px_poisson=mean_pois,
        mean_err_px_after_dc=mean_dc,
        mean_err_px_final=mean_fin,
        mean_abs_err_poisson=mae_pois,
        mean_abs_err_after_dc=mae_dc,
        mean_abs_err_final=mae_fin,
        n_draw_harms=n_harms,
        pct_draw_harms=pct_harms,
        harms_worse_than_after_dc=harms_worse and model.config.use_draw_model,
        low_s_threshold=low_s_threshold,
        low_s_n=len(low_s_mkt),
        low_s_mean_px_market=_avg(low_s_mkt),
        low_s_mean_px_poisson=_avg(low_s_pois),
        low_s_mean_px_after_dc=_avg(low_s_dc),
        low_s_mean_px_final=_avg(low_s_fin),
        summary=summary,
    )


def log_draw_harm_diagnostics(diag: DrawHarmDiagnostics) -> None:
    log = logging.getLogger(__name__)
    if diag.n_matches == 0:
        log.info("Draw harm: %s", diag.summary)
        return
    level = logging.WARNING if diag.harms_worse_than_after_dc else logging.INFO
    log.log(level, "Draw harm: %s", diag.summary)
    if diag.low_s_n > 0:
        log.log(
            level,
            "  S<%.1f (%d): PX рынок %.3f, Пуассон %.3f, после DC %.3f, финал %.3f",
            diag.low_s_threshold, diag.low_s_n,
            diag.low_s_mean_px_market, diag.low_s_mean_px_poisson,
            diag.low_s_mean_px_after_dc, diag.low_s_mean_px_final,
        )


# --------------------------------------------------------------------------- #
# Диагностика S/D → матрица → 1X2 (до и после калибровки)
# --------------------------------------------------------------------------- #

def _prob_1x2_from_sd(
    s: float,
    d: float,
    cfg: ModelConfig,
    *,
    gamma: float = 0.0,
) -> Tuple[float, float, float]:
    """1X2 из S,D без draw-q: чистая Poisson-матрица (+ DC при gamma≠0)."""
    d_use = gm.clamp_goal_diff(d, s, cfg.lambda_epsilon)
    lh = (s + d_use) / 2.0
    la = (s - d_use) / 2.0
    if lh <= 0 or la <= 0:
        return 0.0, 0.0, 0.0
    matrix = gm.build_score_matrix(lh, la, cfg.max_goals)
    if cfg.use_dixon_coles and gamma:
        matrix = gm.apply_dixon_coles(matrix, lh, la, gamma)
    return gm.compute_1x2(matrix)


@dataclass
class Sd1x2DiagnosticRow:
    date: Optional[date]
    home_team: str
    away_team: str
    closing_total_line: Optional[float]
    over_odds: Optional[float]
    under_odds: Optional[float]
    s_market: float
    closing_ah_home: Optional[float]
    d_market: float
    p1_market: float
    px_market: float
    p2_market: float
    p1_market_sd: float
    px_market_sd: float
    p2_market_sd: float
    err_x_market: float
    s_model: float
    d_model: float
    p1_model: float
    px_model: float
    p2_model: float
    err_x_model: float
    s_cal: float
    d_cal: float
    p1_cal: float
    px_cal: float
    p2_cal: float
    err_x_cal: float
    delta_s_cal: float
    d_infer_source: str
    d_clamp_hit: bool


@dataclass
class Sd1x2GroupStat:
    label: str
    n: int
    avg_err_x_market: float
    avg_err_x_model: float
    avg_err_x_cal: float


@dataclass
class Sd1x2Diagnostics:
    n_eval: int
    bias_px_market_sd: float   # A: рынок S/D → матрица
    bias_px_model: float       # B: модель S/D → матрица (до калибровки)
    bias_px_cal: float         # C: после калибровки
    mae_px_model: float
    mae_p1_model: float
    mae_p2_model: float
    mae_px_cal: float
    avg_s_market: float
    avg_s_model: float
    avg_s_cal: float
    avg_delta_s: float
    avg_s_minus_line: float    # S_from_OU − closing_total_line
    n_px_under_model: int      # PX_matrix < PX_market (вариант B)
    pct_px_under_model: float
    stable: bool
    summary: str
    by_s: List[Sd1x2GroupStat] = field(default_factory=list)
    by_d: List[Sd1x2GroupStat] = field(default_factory=list)
    rows: List[Sd1x2DiagnosticRow] = field(default_factory=list)


def _sd_group_stats(
    rows: Sequence[Sd1x2DiagnosticRow],
    key_fn: Callable[[Sd1x2DiagnosticRow], float],
    buckets: Sequence[Tuple[str, Callable[[float], bool]]],
) -> List[Sd1x2GroupStat]:
    out: List[Sd1x2GroupStat] = []
    for label, pred in buckets:
        grp = [r for r in rows if pred(key_fn(r))]
        if not grp:
            continue
        n = len(grp)
        out.append(Sd1x2GroupStat(
            label=label,
            n=n,
            avg_err_x_market=sum(r.err_x_market for r in grp) / n,
            avg_err_x_model=sum(r.err_x_model for r in grp) / n,
            avg_err_x_cal=sum(r.err_x_cal for r in grp) / n,
        ))
    return out


def sd_1x2_diagnostics(
    model: TrainedModel,
    prepared: Sequence[PreparedMatch],
    *,
    bias_warn: float = -0.015,
    s_line_warn: float = 0.20,
) -> Sd1x2Diagnostics:
    """Сравнение 1X2: рынок S/D, модель S/D и калибровка vs Shin 1X2."""
    cfg = model.config
    cal = model.calibration
    strength = model.strength
    goals = model.goals
    gamma = cal.gamma if cfg.use_dixon_coles else 0.0

    rows: List[Sd1x2DiagnosticRow] = []
    for m in prepared:
        if (
            m.p1_shin is None or m.px_shin is None or m.p2_shin is None
            or m.sum_goals is None or m.diff_goals is None
        ):
            continue
        d_model, s_model = _model_d_s(m, strength, goals, cfg)
        s_cal = apply_s_calibration(s_model, cal.s_a, cal.s_b, cfg)
        d_cal = gm.clamp_goal_diff(cal.d_a + cal.d_b * d_model, s_cal, cfg.lambda_epsilon)

        p1_mkt_sd, px_mkt_sd, p2_mkt_sd = _prob_1x2_from_sd(
            m.sum_goals, m.diff_goals, cfg, gamma=0.0,
        )
        p1_mod, px_mod, p2_mod = _prob_1x2_from_sd(
            s_model, d_model, cfg, gamma=0.0,
        )
        p1_cal, px_cal, p2_cal = _prob_1x2_from_sd(
            s_cal, cal.d_a + cal.d_b * d_model, cfg, gamma=gamma,
        )

        r = m.raw
        rows.append(Sd1x2DiagnosticRow(
            date=r.date,
            home_team=m.home_team,
            away_team=m.away_team,
            closing_total_line=r.closing_total_line,
            over_odds=r.over_odds,
            under_odds=r.under_odds,
            s_market=m.sum_goals,
            closing_ah_home=r.closing_ah_home,
            d_market=m.diff_goals,
            p1_market=m.p1_shin,
            px_market=m.px_shin,
            p2_market=m.p2_shin,
            p1_market_sd=p1_mkt_sd,
            px_market_sd=px_mkt_sd,
            p2_market_sd=p2_mkt_sd,
            err_x_market=px_mkt_sd - m.px_shin,
            s_model=s_model,
            d_model=d_model,
            p1_model=p1_mod,
            px_model=px_mod,
            p2_model=p2_mod,
            err_x_model=px_mod - m.px_shin,
            s_cal=s_cal,
            d_cal=d_cal,
            p1_cal=p1_cal,
            px_cal=px_cal,
            p2_cal=p2_cal,
            err_x_cal=px_cal - m.px_shin,
            delta_s_cal=s_cal - s_model,
            d_infer_source=m.d_infer_source or "",
            d_clamp_hit=m.d_clamp_hit,
        ))

    rows.sort(key=lambda r: r.err_x_model)
    n = len(rows)
    if n == 0:
        return Sd1x2Diagnostics(
            n_eval=0, bias_px_market_sd=0.0, bias_px_model=0.0, bias_px_cal=0.0,
            mae_px_model=0.0, mae_p1_model=0.0, mae_p2_model=0.0, mae_px_cal=0.0,
            avg_s_market=0.0, avg_s_model=0.0, avg_s_cal=0.0, avg_delta_s=0.0,
            avg_s_minus_line=0.0, n_px_under_model=0, pct_px_under_model=0.0,
            stable=True, summary="нет матчей с 1X2 и S/D",
        )

    def _avg(vals: Sequence[float]) -> float:
        return sum(vals) / len(vals)

    bias_mkt = _avg([r.err_x_market for r in rows])
    bias_mod = _avg([r.err_x_model for r in rows])
    bias_cal = _avg([r.err_x_cal for r in rows])
    n_under = sum(1 for r in rows if r.err_x_model < -1e-9)
    pct_under = 100.0 * n_under / n

    line_diffs = [
        r.s_market - r.closing_total_line
        for r in rows
        if r.closing_total_line is not None
    ]
    avg_s_line = _avg(line_diffs) if line_diffs else 0.0

    by_s = _sd_group_stats(
        rows, lambda r: r.s_market,
        [
            ("S<2.3", lambda s: s < 2.3),
            ("2.3≤S≤2.7", lambda s: 2.3 <= s <= 2.7),
            ("S>2.7", lambda s: s > 2.7),
        ],
    )
    by_d = _sd_group_stats(
        rows, lambda r: abs(r.d_market),
        [
            ("|D|<0.25", lambda ad: ad < 0.25),
            ("0.25≤|D|≤0.75", lambda ad: 0.25 <= ad <= 0.75),
            ("|D|>0.75", lambda ad: ad > 0.75),
        ],
    )

    stable = not (bias_mod < bias_warn or abs(avg_s_line) > s_line_warn)
    parts = [
        f"PX до калибр.: {bias_mod:+.3f} (модель S/D)",
        f"после: {bias_cal:+.3f}",
        f"заниж. ничьи {pct_under:.0f}%",
    ]
    if line_diffs:
        parts.append(f"S−линия {avg_s_line:+.3f}")
    if not stable:
        if bias_mod < bias_warn:
            parts.append("матрица занижает ничью")
        if abs(avg_s_line) > s_line_warn:
            parts.append("проверьте OU settlement")
    summary = "; ".join(parts)

    return Sd1x2Diagnostics(
        n_eval=n,
        bias_px_market_sd=bias_mkt,
        bias_px_model=bias_mod,
        bias_px_cal=bias_cal,
        mae_px_model=_avg([abs(r.err_x_model) for r in rows]),
        mae_p1_model=_avg([abs(r.p1_model - r.p1_market) for r in rows]),
        mae_p2_model=_avg([abs(r.p2_model - r.p2_market) for r in rows]),
        mae_px_cal=_avg([abs(r.err_x_cal) for r in rows]),
        avg_s_market=_avg([r.s_market for r in rows]),
        avg_s_model=_avg([r.s_model for r in rows]),
        avg_s_cal=_avg([r.s_cal for r in rows]),
        avg_delta_s=_avg([r.delta_s_cal for r in rows]),
        avg_s_minus_line=avg_s_line,
        n_px_under_model=n_under,
        pct_px_under_model=pct_under,
        stable=stable,
        summary=summary,
        by_s=by_s,
        by_d=by_d,
        rows=rows,
    )


def log_sd_1x2_diagnostics(diag: Sd1x2Diagnostics) -> None:
    log = logging.getLogger(__name__)
    if diag.n_eval == 0:
        log.info("S/D→1X2: %s", diag.summary)
        return
    level = logging.WARNING if not diag.stable else logging.INFO
    log.log(level, "S/D→1X2: %s", diag.summary)
    for g in diag.by_s:
        log.log(
            level,
            "  по S %s (%d): err_X mkt=%+.3f model=%+.3f cal=%+.3f",
            g.label, g.n, g.avg_err_x_market, g.avg_err_x_model, g.avg_err_x_cal,
        )
    for g in diag.by_d:
        log.log(
            level,
            "  по |D| %s (%d): err_X mkt=%+.3f model=%+.3f cal=%+.3f",
            g.label, g.n, g.avg_err_x_market, g.avg_err_x_model, g.avg_err_x_cal,
        )
    for row in diag.rows[:10]:
        log.log(
            level,
            "  %s — %s: err_X=%+.3f (S_m=%.2f S_mod=%.2f ΔS=%+.2f)",
            row.home_team, row.away_team, row.err_x_model,
            row.s_market, row.s_model, row.delta_s_cal,
        )
    if len(diag.rows) > 10:
        log.log(level, "  … и ещё %d матчей", len(diag.rows) - 10)


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
        if m.home_id not in s.ratings or m.away_id not in s.ratings:
            continue
        d_model = (
            s.ratings[m.home_id] - s.ratings[m.away_id]
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
        home_id = team_key(target.home_team_id, target.home_team)
        away_id = team_key(target.away_team_id, target.away_team)
        train = ordered[:k]
        try:
            model, _ = train_full_model(train, cfg)
            pred = predict_match(
                model, home_id, away_id,
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
    cal_line = (
        f"Калибровка: dA={_fmt(c.d_a,3)} dB={_fmt(c.d_b,3)} "
        f"sA={_fmt(c.s_a,3)} sB={_fmt(c.s_b,3)}  loss={_fmt(c.loss,5)}"
    )
    if model.config.use_dixon_coles:
        cal_line += f"  γ={_fmt(c.gamma,4)}"
    else:
        cal_line += "  Dixon-Coles: выкл, γ не используется"
    print(cal_line)
    if model.sd_diag and model.sd_diag.n_eval > 0:
        sd = model.sd_diag
        print(f"S/D→1X2: {sd.summary}")
        print(f"  средн. S: рынок={_fmt(sd.avg_s_market,3)} модель={_fmt(sd.avg_s_model,3)} "
              f"калибр.={_fmt(sd.avg_s_cal,3)} ΔS={_fmt(sd.avg_delta_s,3)}")
    print("\nРейтинги (сила, нейтраль):")
    names = model.team_names or {}
    for tid, r in sorted(model.strength.ratings.items(), key=lambda kv: kv[1], reverse=True):
        label = f"{tid} — {names[tid]}" if names.get(tid) else tid
        print(f"  {label:<28} r={_fmt(r,3):>7}  A={_fmt(model.goals.attack[tid],3):>7}  "
              f"Df={_fmt(model.goals.defense[tid],3):>7}")
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
