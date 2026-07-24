"""
Strong Favorite Adjustment (SFA) — configurable amplify of |D| for rare favorites.

Modes (strongFavoriteAdjustment.mode):
  off         — no adjustment
  threshold   — legacy absolute gate: favoriteFairOdds ≤ oddsThreshold → fixed beta
  percentile  — league×season left-tail CDF percentile → shape provider (DEFAULT)

Shape providers (strongFavoriteAdjustment.shape), used by percentile mode:
  stepwise | linear (DEFAULT) | logistic

Default schedule (EXP-008/009): linear βmax=0.06 —
  P10→0, P5→0.018, P2→0.036, P1→0.048, P0→0.06

Architecture: calculate_sfa(...) is the single provider entry; swap shape/mode via
config without touching the D→λ→markets pipeline.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_THRESHOLD = "threshold"
MODE_PERCENTILE = "percentile"
VALID_MODES = frozenset({MODE_OFF, MODE_THRESHOLD, MODE_PERCENTILE})

SHAPE_STEPWISE = "stepwise"
SHAPE_LINEAR = "linear"
SHAPE_LOGISTIC = "logistic"
VALID_SHAPES = frozenset({SHAPE_STEPWISE, SHAPE_LINEAR, SHAPE_LOGISTIC})

# EXP-008/009: linear βmax=0.06 (P10→0 … P0→βmax), relative knots 0 / 0.3 / 0.6 / 0.8 / 1.0
DEFAULT_THRESHOLDS: Tuple[Tuple[float, float], ...] = (
    (10.0, 0.000),
    (5.0, 0.018),
    (2.0, 0.036),
    (1.0, 0.048),
    (0.0, 0.060),
)


def _norm_mode(raw: Any) -> str:
    s = str(raw or MODE_PERCENTILE).strip().lower()
    if s not in VALID_MODES:
        raise ValueError(f"strongFavoriteAdjustment.mode must be one of {sorted(VALID_MODES)}, got {raw!r}")
    return s


def _norm_shape(raw: Any) -> str:
    s = str(raw or SHAPE_STEPWISE).strip().lower()
    if s not in VALID_SHAPES:
        raise ValueError(f"strongFavoriteAdjustment.shape must be one of {sorted(VALID_SHAPES)}, got {raw!r}")
    return s


def parse_percentile_thresholds(raw: Any) -> List[Tuple[float, float]]:
    """Parse [{percentile, beta}, ...] or {pct: beta} into sorted by percentile DESC."""
    pairs: List[Tuple[float, float]] = []
    if raw is None:
        return list(DEFAULT_THRESHOLDS)
    if isinstance(raw, Mapping):
        for k, v in raw.items():
            try:
                pairs.append((float(k), float(v)))
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, Mapping):
                try:
                    p = float(item.get("percentile", item.get("pct", item.get("p"))))
                    b = float(item.get("beta", item.get("sfa", item.get("w"))))
                    pairs.append((p, b))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    pairs.append((float(item[0]), float(item[1])))
                except (TypeError, ValueError):
                    continue
    if not pairs:
        return list(DEFAULT_THRESHOLDS)
    # clamp betas ≥ 0; percentiles in [0, 100]
    out = [(max(0.0, min(100.0, p)), max(0.0, b)) for p, b in pairs]
    out.sort(key=lambda t: t[0], reverse=True)  # high percentile first
    return out


@dataclass
class SfaConfig:
    mode: str = MODE_PERCENTILE
    shape: str = SHAPE_LINEAR
    min_matches: int = 40
    # threshold (legacy absolute) mode
    odds_threshold: float = 1.30
    beta: float = 0.06
    # percentile schedule
    thresholds: List[Tuple[float, float]] = field(
        default_factory=lambda: list(DEFAULT_THRESHOLDS)
    )
    # logistic shape params
    logistic_k: float = 0.8
    logistic_mid: float = 5.0
    logistic_max: float = 0.06

    def validated(self) -> "SfaConfig":
        mode = _norm_mode(self.mode)
        shape = _norm_shape(self.shape)
        if self.min_matches < 1:
            raise ValueError("strongFavoriteAdjustment.minMatches must be >= 1")
        if self.odds_threshold <= 1.0:
            raise ValueError("strongFavoriteAdjustment.oddsThreshold must be > 1")
        if self.beta < 0:
            raise ValueError("strongFavoriteAdjustment.beta must be >= 0")
        thr = parse_percentile_thresholds(self.thresholds)
        return SfaConfig(
            mode=mode,
            shape=shape,
            min_matches=int(self.min_matches),
            odds_threshold=float(self.odds_threshold),
            beta=float(self.beta),
            thresholds=thr,
            logistic_k=float(self.logistic_k),
            logistic_mid=float(self.logistic_mid),
            logistic_max=max(0.0, float(self.logistic_max)),
        )


def sfa_config_from_mapping(raw: Optional[Mapping[str, Any]]) -> SfaConfig:
    """Read strongFavoriteAdjustment from model_config (top-level or train.*)."""
    if not raw:
        return SfaConfig().validated()
    block: Mapping[str, Any] = {}
    if "strongFavoriteAdjustment" in raw or "sfa" in raw:
        cand = raw.get("strongFavoriteAdjustment") or raw.get("sfa")
        if isinstance(cand, Mapping):
            block = cand
    elif isinstance(raw.get("train"), Mapping):
        tr = raw["train"]
        cand = tr.get("strongFavoriteAdjustment") or tr.get("sfa")
        if isinstance(cand, Mapping):
            block = cand
    if not block and (
        "sfaMode" in raw or "strongFavoriteAdjustmentMode" in raw
    ):
        block = raw
    mode = block.get(
        "mode",
        block.get("sfaMode", block.get("strongFavoriteAdjustmentMode", MODE_PERCENTILE)),
    )
    return SfaConfig(
        mode=str(mode),
        shape=str(block.get("shape", block.get("provider", SHAPE_LINEAR))),
        min_matches=int(block.get("minMatches", block.get("min_matches", 40))),
        odds_threshold=float(
            block.get("oddsThreshold", block.get("odds_threshold", 1.30))
        ),
        beta=float(block.get("beta", block.get("fixedBeta", 0.06))),
        thresholds=parse_percentile_thresholds(
            block.get("thresholds", block.get("table", block.get("schedule")))
        ),
        logistic_k=float(block.get("logisticK", block.get("logistic_k", 0.8))),
        logistic_mid=float(block.get("logisticMid", block.get("logistic_mid", 5.0))),
        logistic_max=float(block.get("logisticMax", block.get("logistic_max", 0.06))),
    ).validated()


# --------------------------------------------------------------------------- #
# Shape providers → calculate_sfa(league, season, favorite_percentile)
# --------------------------------------------------------------------------- #


def beta_stepwise(percentile: float, thresholds: Sequence[Tuple[float, float]]) -> float:
    """
    thresholds: (boundary, beta_when_pct_above_boundary), sorted DESC.

    DEFAULT (10,0),(5,0.018),(2,0.036),(1,0.048),(0,0.06) ⇒
      pct>10→0; 5<pct≤10→0.018; 2<pct≤5→0.036; 1<pct≤2→0.048; pct≤1→0.06
    """
    if percentile is None or (isinstance(percentile, float) and math.isnan(percentile)):
        return 0.0
    pct = float(percentile)
    pts = sorted(((float(p), float(b)) for p, b in thresholds), key=lambda t: t[0], reverse=True)
    if not pts:
        return 0.0
    for p, b in pts:
        if pct > p:
            return float(b)
    return float(pts[-1][1])


def beta_linear(percentile: float, thresholds: Sequence[Tuple[float, float]]) -> float:
    """Piecewise-linear beta on percentile knots (ASC interpolate)."""
    if percentile is None or (isinstance(percentile, float) and math.isnan(percentile)):
        return 0.0
    pct = float(percentile)
    pts = sorted(((float(p), float(b)) for p, b in thresholds), key=lambda t: t[0])
    if not pts:
        return 0.0
    if pct >= pts[-1][0]:
        return float(pts[-1][1])
    if pct <= pts[0][0]:
        return float(pts[0][1])
    for i in range(1, len(pts)):
        p0, b0 = pts[i - 1]
        p1, b1 = pts[i]
        if pct <= p1:
            if abs(p1 - p0) < 1e-15:
                return float(b1)
            t = (pct - p0) / (p1 - p0)
            return float(b0 + t * (b1 - b0))
    return float(pts[-1][1])


def beta_logistic(percentile: float, cfg: SfaConfig) -> float:
    """Smooth logistic: higher beta for lower (rarer) percentiles."""
    if percentile is None or (isinstance(percentile, float) and math.isnan(percentile)):
        return 0.0
    pct = float(percentile)
    # β = βmax / (1 + exp(k*(pct - mid))); low pct → high beta
    k = float(cfg.logistic_k)
    mid = float(cfg.logistic_mid)
    bmax = float(cfg.logistic_max)
    return float(bmax / (1.0 + math.exp(k * (pct - mid))))


def calculate_sfa(
    league: Optional[str],
    season: Optional[str],
    favorite_percentile: Optional[float],
    cfg: SfaConfig,
    *,
    favorite_fair_odds: Optional[float] = None,
) -> float:
    """
    Provider entry: return SFA beta ≥ 0.

    league/season are part of the API for future league-specific schedules;
    percentile mode uses favorite_percentile (+ shape). Threshold mode uses odds.
    """
    c = cfg.validated()
    if c.mode == MODE_OFF:
        return 0.0
    if c.mode == MODE_THRESHOLD:
        if favorite_fair_odds is None or not math.isfinite(float(favorite_fair_odds)):
            return 0.0
        return float(c.beta) if float(favorite_fair_odds) <= c.odds_threshold else 0.0
    # percentile
    if favorite_percentile is None or (
        isinstance(favorite_percentile, float) and math.isnan(favorite_percentile)
    ):
        return 0.0
    if c.shape == SHAPE_LINEAR:
        return beta_linear(float(favorite_percentile), c.thresholds)
    if c.shape == SHAPE_LOGISTIC:
        return beta_logistic(float(favorite_percentile), c)
    return beta_stepwise(float(favorite_percentile), c.thresholds)


# --------------------------------------------------------------------------- #
# Favorite-odds distributions (league × season)
# --------------------------------------------------------------------------- #


def market_favorite_odds(p1: Optional[float], p2: Optional[float]) -> Optional[float]:
    if p1 is None or p2 is None:
        return None
    mx = max(float(p1), float(p2))
    if mx <= 1e-15:
        return None
    return 1.0 / mx


def favorite_fair_odds_from_sd(
    s: float,
    d: float,
    *,
    max_goals: int = 10,
    lambda_min: float = 0.05,
    build_matrix: Optional[Callable[..., Any]] = None,
    compute_1x2: Optional[Callable[..., Any]] = None,
) -> Optional[float]:
    """Model-implied favorite fair odds from (S, D) before SFA."""
    try:
        import goal_model as gm  # local / package
    except ImportError:  # pragma: no cover
        from . import goal_model as gm  # type: ignore
    build = build_matrix or gm.build_score_matrix
    ones = compute_1x2 or gm.compute_1x2
    d_c = gm.clamp_goal_diff(d, s)
    lh = max(lambda_min, (s + d_c) / 2.0)
    la = max(lambda_min, (s - d_c) / 2.0)
    mat = build(lh, la, max_goals)
    p1, _px, p2 = ones(mat)
    return market_favorite_odds(p1, p2)


def empirical_cdf_percentile(sorted_odds: Sequence[float], value: float) -> float:
    """Left-tail CDF% : 100 * P(odds_i <= value). Lower = rarer / stronger favorite."""
    if not sorted_odds:
        return float("nan")
    n = len(sorted_odds)
    cnt = sum(1 for v in sorted_odds if v <= value + 1e-12)
    return 100.0 * cnt / n


def apply_sfa_to_d(d_base: float, beta: float) -> float:
    """D_final = sign(D_base) * (|D_base| + beta). Applied once."""
    b = max(0.0, float(beta))
    if b <= 1e-15:
        return float(d_base)
    if abs(d_base) < 1e-15:
        return float(d_base)  # no favorite side to strengthen
    return math.copysign(abs(d_base) + b, d_base)


@dataclass
class SeasonFavDist:
    league: str
    season: str
    odds: Tuple[float, ...]  # sorted ascending
    n: int
    source_season: str  # may differ from season when fallback used

    def percentile_of(self, fav_odds: float) -> float:
        return empirical_cdf_percentile(self.odds, fav_odds)


@dataclass
class FavoriteOddsBook:
    """Per (league, season) sorted favorite-odds samples + config."""

    distributions: Dict[Tuple[str, str], SeasonFavDist] = field(default_factory=dict)
    # league → seasons ordered newest-first (by label/id string desc)
    seasons_by_league: Dict[str, List[str]] = field(default_factory=dict)
    min_matches: int = 40
    league_key: str = ""
    built_from: str = "market_shin"

    def resolve_dist(
        self,
        league: Optional[str],
        season: Optional[str],
    ) -> Optional[SeasonFavDist]:
        lg = str(league or self.league_key or "").strip() or "ALL"
        seasons = self.seasons_by_league.get(lg) or self.seasons_by_league.get("ALL") or []
        # Try exact season
        if season is not None and str(season) != "":
            key = (lg, str(season))
            dist = self.distributions.get(key)
            if dist is not None and dist.n >= self.min_matches:
                return dist
            # fallback: previous seasons in order
            if seasons:
                try:
                    idx = seasons.index(str(season))
                except ValueError:
                    idx = -1
                # seasons stored newest-first → previous = higher index
                start = idx + 1 if idx >= 0 else 0
                for s in seasons[start:]:
                    d2 = self.distributions.get((lg, s))
                    if d2 is not None and d2.n >= self.min_matches:
                        return SeasonFavDist(
                            league=lg,
                            season=str(season),
                            odds=d2.odds,
                            n=d2.n,
                            source_season=s,
                        )
        # No season / nothing usable → newest with enough matches
        for s in seasons:
            d2 = self.distributions.get((lg, s))
            if d2 is not None and d2.n >= self.min_matches:
                return SeasonFavDist(
                    league=lg,
                    season=str(season) if season else s,
                    odds=d2.odds,
                    n=d2.n,
                    source_season=s,
                )
        # pooled ALL
        pooled = self.distributions.get((lg, "ALL")) or self.distributions.get(("ALL", "ALL"))
        if pooled is not None and pooled.n >= max(5, self.min_matches // 2):
            return pooled
        if pooled is not None and pooled.n >= 5:
            return pooled
        return None


def build_favorite_odds_book(
    rows: Sequence[Mapping[str, Any]],
    *,
    min_matches: int = 40,
    default_league: str = "ALL",
) -> FavoriteOddsBook:
    """
    rows items: {league, season, fav_odds} (fav_odds = market or model favorite odds).
    """
    buckets: Dict[Tuple[str, str], List[float]] = {}
    for r in rows:
        fo = r.get("fav_odds")
        if fo is None:
            continue
        try:
            fo_f = float(fo)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(fo_f) or fo_f <= 1.0:
            continue
        lg = str(r.get("league") or default_league or "ALL").strip() or "ALL"
        season = str(r.get("season") or "ALL").strip() or "ALL"
        buckets.setdefault((lg, season), []).append(fo_f)
        buckets.setdefault((lg, "ALL"), []).append(fo_f)

    distributions: Dict[Tuple[str, str], SeasonFavDist] = {}
    seasons_by_league: Dict[str, List[str]] = {}
    for (lg, season), xs in buckets.items():
        xs_s = tuple(sorted(xs))
        distributions[(lg, season)] = SeasonFavDist(
            league=lg, season=season, odds=xs_s, n=len(xs_s), source_season=season
        )
        if season != "ALL":
            seasons_by_league.setdefault(lg, []).append(season)

    # newest-first: prefer lexicographic desc on season label (2025-26 > 2024-25)
    for lg, seasons in seasons_by_league.items():
        seasons_by_league[lg] = sorted(set(seasons), reverse=True)

    return FavoriteOddsBook(
        distributions=distributions,
        seasons_by_league=seasons_by_league,
        min_matches=int(min_matches),
        league_key=default_league,
    )


@dataclass
class SfaDiagnostics:
    league: Optional[str] = None
    season: Optional[str] = None
    source_season: Optional[str] = None
    favorite_fair_odds: Optional[float] = None
    favorite_percentile: Optional[float] = None
    sfa_beta: float = 0.0
    d_base: float = 0.0
    d_final: float = 0.0
    mode: str = MODE_PERCENTILE
    shape: str = SHAPE_STEPWISE
    applied: bool = False
    n_dist: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "league": self.league,
            "season": self.season,
            "sourceSeason": self.source_season,
            "favoriteFairOdds": self.favorite_fair_odds,
            "favoritePercentile": self.favorite_percentile,
            "sfaBeta": self.sfa_beta,
            "D_base": self.d_base,
            "D_final": self.d_final,
            "mode": self.mode,
            "shape": self.shape,
            "applied": self.applied,
            "nDist": self.n_dist,
        }


def apply_strong_favorite_adjustment(
    d_base: float,
    s_for_odds: float,
    cfg: SfaConfig,
    book: Optional[FavoriteOddsBook],
    *,
    league: Optional[str] = None,
    season: Optional[str] = None,
    max_goals: int = 10,
    lambda_min: float = 0.05,
    already_applied: bool = False,
) -> Tuple[float, SfaDiagnostics]:
    """
    Compute favoriteFairOdds from (S, D_base), resolve percentile, calculate_sfa, apply once.

    AC6: if already_applied, return d_base unchanged with applied=False.
    """
    c = cfg.validated()
    diag = SfaDiagnostics(
        league=league,
        season=season,
        d_base=float(d_base),
        d_final=float(d_base),
        mode=c.mode,
        shape=c.shape,
    )
    if already_applied or c.mode == MODE_OFF:
        return float(d_base), diag

    fav = favorite_fair_odds_from_sd(
        s_for_odds, d_base, max_goals=max_goals, lambda_min=lambda_min
    )
    diag.favorite_fair_odds = fav

    pct: Optional[float] = None
    if c.mode == MODE_PERCENTILE and book is not None and fav is not None:
        dist = book.resolve_dist(league, season)
        if dist is not None:
            pct = dist.percentile_of(fav)
            diag.source_season = dist.source_season
            diag.n_dist = dist.n
    diag.favorite_percentile = pct

    beta = calculate_sfa(
        league,
        season,
        pct,
        c,
        favorite_fair_odds=fav,
    )
    diag.sfa_beta = beta
    d_out = apply_sfa_to_d(d_base, beta)
    diag.d_final = d_out
    diag.applied = abs(beta) > 1e-15
    return d_out, diag
