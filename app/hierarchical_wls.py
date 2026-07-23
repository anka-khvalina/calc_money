"""
Regularized hierarchical WLS for team strength ratings.

Not sequential Elo: one joint WLS/IRLS fit over D_market with
team-specific shrinkage λ_team · (R − R_prior)² driven by
effective_n → rating_confidence.

Modes (rating.mode):
  standard_wls            — current production WLS (caller path)
  hierarchical_wls        — this fit drives production ratings
  hierarchical_wls_shadow — fit + cache + compare; production stays standard
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

MODE_STANDARD = "standard_wls"
MODE_HIERARCHICAL = "hierarchical_wls"
MODE_SHADOW = "hierarchical_wls_shadow"
VALID_MODES = frozenset({MODE_STANDARD, MODE_HIERARCHICAL, MODE_SHADOW})

PRIOR_LEAGUE_MEAN = "league_mean"
PRIOR_PREVIOUS_SEASON = "previous_season"
PRIOR_BLENDED = "blended"
VALID_PRIOR_MODES = frozenset(
    {PRIOR_LEAGUE_MEAN, PRIOR_PREVIOUS_SEASON, PRIOR_BLENDED}
)

LAMBDA_MODE_CONFIDENCE = "confidence"
LAMBDA_MODE_INVERSE_N = "inverse_n"
VALID_LAMBDA_MODES = frozenset({LAMBDA_MODE_CONFIDENCE, LAMBDA_MODE_INVERSE_N})

RATING_ALGORITHM_VERSION = "hierarchical_wls_v1"
CACHE_SCHEMA_VERSION = 1
FALLBACK_LAST_LOCAL = "last_local"
FALLBACK_FAIL = "fail"


@dataclass
class TimeDecayConfig:
    enabled: bool = True
    half_life_days: float = 120.0
    # When True, season_weight is not multiplied again on the strength stage
    # (avoids double time decay with continuous half-life).
    suppress_season_weight: bool = True
    # Optional per-league half-life override: league_id/name → days
    by_league: Dict[str, float] = field(default_factory=dict)

    def validated(self) -> "TimeDecayConfig":
        if self.enabled and self.half_life_days <= 0:
            raise ValueError("rating.time_decay.half_life_days must be > 0 when enabled")
        return self


@dataclass
class PriorConfig:
    mode: str = PRIOR_LEAGUE_MEAN
    prior_reliability: float = 0.70  # blended: weight on previous season
    promoted_team_prior: Optional[float] = None  # None → 0 or mean of weakest
    promoted_reference_n: int = 3
    fallback_to_league_mean: bool = True

    def validated(self) -> "PriorConfig":
        mode = str(self.mode or PRIOR_LEAGUE_MEAN).strip().lower()
        if mode not in VALID_PRIOR_MODES:
            raise ValueError(f"rating.prior.mode must be one of {sorted(VALID_PRIOR_MODES)}")
        pr = float(self.prior_reliability)
        if not (0.0 <= pr <= 1.0):
            raise ValueError("rating.prior.prior_reliability must be in [0,1]")
        return PriorConfig(
            mode=mode,
            prior_reliability=pr,
            promoted_team_prior=self.promoted_team_prior,
            promoted_reference_n=max(1, int(self.promoted_reference_n)),
            fallback_to_league_mean=bool(self.fallback_to_league_mean),
        )


@dataclass
class VolatilityConfig:
    enabled: bool = False
    volatility_scale: float = 1.0

    def validated(self) -> "VolatilityConfig":
        if self.volatility_scale < 0:
            raise ValueError("rating.volatility.volatility_scale must be >= 0")
        return self


@dataclass
class RatingCacheConfig:
    fallback: str = FALLBACK_LAST_LOCAL
    versions_to_keep: int = 2
    root_dir: Optional[str] = None

    def validated(self) -> "RatingCacheConfig":
        fb = str(self.fallback or FALLBACK_LAST_LOCAL).strip().lower()
        if fb not in (FALLBACK_LAST_LOCAL, FALLBACK_FAIL):
            raise ValueError(f"rating.cache.fallback invalid: {fb}")
        keep = int(self.versions_to_keep)
        if keep < 1:
            raise ValueError("rating.cache.versions_to_keep must be >= 1")
        return RatingCacheConfig(fallback=fb, versions_to_keep=keep, root_dir=self.root_dir)


@dataclass
class HierarchicalWlsConfig:
    mode: str = MODE_HIERARCHICAL
    confidence_k: float = 8.0
    lambda_mode: str = LAMBDA_MODE_CONFIDENCE
    lambda_min: float = 0.02
    lambda_max: float = 0.50
    lambda_base: float = 0.10  # for inverse_n mode
    n_floor: float = 1.0
    effective_n_iters: int = 2  # IRLS outer loops that refresh λ from robust weights
    prior: PriorConfig = field(default_factory=PriorConfig)
    time_decay: TimeDecayConfig = field(
        default_factory=lambda: TimeDecayConfig(enabled=True, half_life_days=120.0)
    )
    volatility: VolatilityConfig = field(default_factory=VolatilityConfig)
    cache: RatingCacheConfig = field(default_factory=RatingCacheConfig)
    publish_cache: bool = True

    def validated(self) -> "HierarchicalWlsConfig":
        mode = str(self.mode or MODE_STANDARD).strip().lower()
        if mode not in VALID_MODES:
            raise ValueError(f"rating.mode must be one of {sorted(VALID_MODES)}, got {mode!r}")
        lm = str(self.lambda_mode or LAMBDA_MODE_CONFIDENCE).strip().lower()
        if lm not in VALID_LAMBDA_MODES:
            raise ValueError(f"rating.lambda_mode invalid: {lm}")
        if self.confidence_k < 0:
            raise ValueError("rating.confidence_k must be >= 0")
        if self.lambda_min < 0 or self.lambda_max < 0:
            raise ValueError("rating lambda_min/max must be >= 0")
        if self.lambda_max < self.lambda_min:
            raise ValueError("rating.lambda_max must be >= lambda_min")
        if self.n_floor <= 0:
            raise ValueError("rating.n_floor must be > 0")
        iters = max(1, int(self.effective_n_iters))
        return HierarchicalWlsConfig(
            mode=mode,
            confidence_k=float(self.confidence_k),
            lambda_mode=lm,
            lambda_min=float(self.lambda_min),
            lambda_max=float(self.lambda_max),
            lambda_base=float(self.lambda_base),
            n_floor=float(self.n_floor),
            effective_n_iters=iters,
            prior=self.prior.validated(),
            time_decay=self.time_decay.validated(),
            volatility=self.volatility.validated(),
            cache=self.cache.validated(),
            publish_cache=bool(self.publish_cache),
        )

    @property
    def uses_hierarchical_fit(self) -> bool:
        return self.mode in (MODE_HIERARCHICAL, MODE_SHADOW)

    @property
    def production_is_hierarchical(self) -> bool:
        return self.mode == MODE_HIERARCHICAL


def rating_config_from_mapping(raw: Optional[Mapping[str, Any]]) -> HierarchicalWlsConfig:
    """Read rating block from model_config (top-level)."""
    if not raw:
        return HierarchicalWlsConfig().validated()
    block: Mapping[str, Any] = raw
    if "rating" in raw and isinstance(raw.get("rating"), Mapping):
        block = raw["rating"]  # type: ignore[assignment]
    prior_raw = block.get("prior") if isinstance(block.get("prior"), Mapping) else {}
    td_raw = block.get("time_decay") if isinstance(block.get("time_decay"), Mapping) else {}
    if not td_raw and isinstance(block.get("timeDecay"), Mapping):
        td_raw = block["timeDecay"]
    vol_raw = block.get("volatility") if isinstance(block.get("volatility"), Mapping) else {}
    cache_raw = block.get("cache") if isinstance(block.get("cache"), Mapping) else {}

    by_league: Dict[str, float] = {}
    bl = td_raw.get("by_league") or td_raw.get("byLeague") or {}
    if isinstance(bl, Mapping):
        for k, v in bl.items():
            try:
                by_league[str(k)] = float(v)
            except (TypeError, ValueError):
                continue

    cfg = HierarchicalWlsConfig(
        mode=str(block.get("mode", MODE_HIERARCHICAL)),
        confidence_k=float(block.get("confidence_k", block.get("confidenceK", 8.0))),
        lambda_mode=str(block.get("lambda_mode", block.get("lambdaMode", LAMBDA_MODE_CONFIDENCE))),
        lambda_min=float(block.get("lambda_min", block.get("lambdaMin", 0.02))),
        lambda_max=float(block.get("lambda_max", block.get("lambdaMax", 0.50))),
        lambda_base=float(block.get("lambda_base", block.get("lambdaBase", 0.10))),
        n_floor=float(block.get("n_floor", block.get("nFloor", 1.0))),
        effective_n_iters=int(block.get("effective_n_iters", block.get("effectiveNIters", 2))),
        prior=PriorConfig(
            mode=str(prior_raw.get("mode", PRIOR_LEAGUE_MEAN)),
            prior_reliability=float(
                prior_raw.get("prior_reliability", prior_raw.get("priorReliability", 0.70))
            ),
            promoted_team_prior=(
                float(prior_raw["promoted_team_prior"])
                if prior_raw.get("promoted_team_prior") is not None
                else (
                    float(prior_raw["promotedTeamPrior"])
                    if prior_raw.get("promotedTeamPrior") is not None
                    else None
                )
            ),
            promoted_reference_n=int(
                prior_raw.get("promoted_reference_n", prior_raw.get("promotedReferenceN", 3))
            ),
            fallback_to_league_mean=bool(
                prior_raw.get("fallback_to_league_mean", prior_raw.get("fallbackToLeagueMean", True))
            ),
        ),
        time_decay=TimeDecayConfig(
            enabled=bool(td_raw.get("enabled", True)),
            half_life_days=float(td_raw.get("half_life_days", td_raw.get("halfLifeDays", 120.0))),
            suppress_season_weight=bool(
                td_raw.get("suppress_season_weight", td_raw.get("suppressSeasonWeight", True))
            ),
            by_league=by_league,
        ),
        volatility=VolatilityConfig(
            enabled=bool(vol_raw.get("enabled", False)),
            volatility_scale=float(
                vol_raw.get("volatility_scale", vol_raw.get("volatilityScale", 1.0))
            ),
        ),
        cache=RatingCacheConfig(
            fallback=str(cache_raw.get("fallback", FALLBACK_LAST_LOCAL)),
            versions_to_keep=int(cache_raw.get("versions_to_keep", cache_raw.get("versionsToKeep", 2))),
            root_dir=cache_raw.get("root_dir") or cache_raw.get("rootDir"),
        ),
        publish_cache=bool(block.get("publish_cache", block.get("publishCache", True))),
    )
    return cfg.validated()


# --------------------------------------------------------------------------- #
# Core formulas
# --------------------------------------------------------------------------- #


def effective_n_from_weights(weights: Sequence[float]) -> float:
    """Kish effective sample size: (Σw)² / Σw²."""
    s = 0.0
    s2 = 0.0
    for w in weights:
        ww = float(w)
        if ww <= 0:
            continue
        s += ww
        s2 += ww * ww
    if s2 <= 0.0:
        return 0.0
    return (s * s) / s2


def rating_confidence(effective_n: float, confidence_k: float) -> float:
    n = max(0.0, float(effective_n))
    k = max(0.0, float(confidence_k))
    return n / (n + k) if (n + k) > 0 else 0.0


def lambda_team_from_confidence(
    confidence: float,
    *,
    lambda_min: float,
    lambda_max: float,
) -> float:
    c = min(1.0, max(0.0, float(confidence)))
    return float(lambda_min + (lambda_max - lambda_min) * (1.0 - c))


def lambda_team_from_inverse_n(
    effective_n: float,
    *,
    lambda_base: float,
    n_floor: float,
    lambda_min: float,
    lambda_max: float,
) -> float:
    n = max(float(n_floor), float(effective_n))
    lam = float(lambda_base) / n
    return min(lambda_max, max(lambda_min, lam))


def time_weight(
    match_date: Optional[date],
    *,
    as_of: Optional[date],
    half_life_days: float,
) -> float:
    if match_date is None or as_of is None or half_life_days <= 0:
        return 1.0
    age = float((as_of - match_date).days)
    if age < 0:
        age = 0.0
    return math.exp(-age / float(half_life_days))


def resolve_half_life(
    cfg: HierarchicalWlsConfig,
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> float:
    td = cfg.time_decay
    for key in (league_id, league_name):
        if key and str(key) in td.by_league:
            return float(td.by_league[str(key)])
    return float(td.half_life_days)


def normalize_prior_ratings(raw: Mapping[str, float]) -> Dict[str, float]:
    """Center previous-season ratings to mean 0 (league scale)."""
    if not raw:
        return {}
    vals = {str(k): float(v) for k, v in raw.items()}
    mean_r = sum(vals.values()) / len(vals)
    return {t: r - mean_r for t, r in vals.items()}


def resolve_team_priors(
    teams: Sequence[str],
    cfg: HierarchicalWlsConfig,
    previous_season: Optional[Mapping[str, float]] = None,
) -> Dict[str, float]:
    """Build R_prior_team for each team in the current fit."""
    cfg = cfg.validated()
    mode = cfg.prior.mode
    prev = normalize_prior_ratings(previous_season or {})
    promoted = cfg.prior.promoted_team_prior
    if promoted is None and prev:
        weakest = sorted(prev.values())[: cfg.prior.promoted_reference_n]
        promoted = sum(weakest) / len(weakest) if weakest else 0.0
    if promoted is None:
        promoted = 0.0

    out: Dict[str, float] = {}
    for t in teams:
        if mode == PRIOR_LEAGUE_MEAN:
            out[t] = 0.0
            continue
        has_prev = t in prev
        if mode == PRIOR_PREVIOUS_SEASON:
            if has_prev:
                out[t] = prev[t]
            else:
                # new / promoted team without prior
                out[t] = float(promoted) if cfg.prior.fallback_to_league_mean else 0.0
        elif mode == PRIOR_BLENDED:
            if has_prev:
                rel = cfg.prior.prior_reliability
                out[t] = rel * prev[t] + (1.0 - rel) * 0.0
            else:
                out[t] = float(promoted)
        else:
            out[t] = 0.0
    # Re-center priors so gauge stays consistent
    if out:
        mean_p = sum(out.values()) / len(out)
        out = {t: v - mean_p for t, v in out.items()}
    return out


def adjust_confidence_for_volatility(
    confidence: float,
    residual_volatility: float,
    *,
    scale: float,
) -> float:
    return float(confidence) / (1.0 + float(scale) * max(0.0, float(residual_volatility)))


def weighted_std(values: Sequence[float], weights: Sequence[float]) -> float:
    sw = 0.0
    sx = 0.0
    for v, w in zip(values, weights):
        ww = float(w)
        if ww <= 0:
            continue
        sw += ww
        sx += ww * float(v)
    if sw <= 0:
        return 0.0
    mean = sx / sw
    var = 0.0
    for v, w in zip(values, weights):
        ww = float(w)
        if ww <= 0:
            continue
        d = float(v) - mean
        var += ww * d * d
    var /= sw
    return math.sqrt(max(0.0, var))


@dataclass
class TeamRatingMeta:
    prior_rating: float
    effective_n: float
    rating_confidence: float
    lambda_team: float
    residual_volatility: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prior_rating": self.prior_rating,
            "effective_n": self.effective_n,
            "rating_confidence": self.rating_confidence,
            "lambda_team": self.lambda_team,
            "residual_volatility": self.residual_volatility,
        }


@dataclass
class HierarchicalFitResult:
    ratings: Dict[str, float]
    home_advantage: float
    derby_home_delta: float
    derby_n: int
    derby_shrink_w: float
    mae: float
    rmse: float
    n: int
    team_meta: Dict[str, TeamRatingMeta]
    rating_mode: str
    rating_algorithm_version: str
    priors: Dict[str, float]
    regularization_parameters: Dict[str, Any]
    time_decay_parameters: Dict[str, Any]
    residuals: List[float] = field(default_factory=list)


def compute_team_weights(
    teams: Sequence[str],
    home_ids: Sequence[str],
    away_ids: Sequence[str],
    match_weights: Sequence[float],
) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {t: [] for t in teams}
    for h, a, w in zip(home_ids, away_ids, match_weights):
        if h in out:
            out[h].append(float(w))
        if a in out:
            out[a].append(float(w))
    return out


def build_team_lambdas(
    team_weights: Mapping[str, Sequence[float]],
    cfg: HierarchicalWlsConfig,
    *,
    residual_vols: Optional[Mapping[str, float]] = None,
) -> Dict[str, Tuple[float, float, float, Optional[float]]]:
    """
    Returns team → (effective_n, confidence, lambda_team, residual_volatility).
    """
    cfg = cfg.validated()
    result: Dict[str, Tuple[float, float, float, Optional[float]]] = {}
    for t, ws in team_weights.items():
        n_eff = effective_n_from_weights(ws)
        conf = rating_confidence(n_eff, cfg.confidence_k)
        vol: Optional[float] = None
        if residual_vols is not None and t in residual_vols:
            vol = float(residual_vols[t])
            if cfg.volatility.enabled:
                conf = adjust_confidence_for_volatility(
                    conf, vol, scale=cfg.volatility.volatility_scale
                )
                conf = min(1.0, max(0.0, conf))
        if cfg.lambda_mode == LAMBDA_MODE_INVERSE_N:
            lam = lambda_team_from_inverse_n(
                n_eff,
                lambda_base=cfg.lambda_base,
                n_floor=cfg.n_floor,
                lambda_min=cfg.lambda_min,
                lambda_max=cfg.lambda_max,
            )
        else:
            lam = lambda_team_from_confidence(
                conf, lambda_min=cfg.lambda_min, lambda_max=cfg.lambda_max
            )
        result[t] = (n_eff, conf, lam, vol)
    return result


# --------------------------------------------------------------------------- #
# Cache package (filesystem only — never main DB)
# --------------------------------------------------------------------------- #


@dataclass
class CachedRatingTeam:
    team_id: str
    rating: float
    prior_rating: float
    effective_n: float
    rating_confidence: float
    lambda_team: float
    slow_bias: float = 0.0
    fast_bias: float = 0.0
    residual_volatility: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "rating": self.rating,
            "prior_rating": self.prior_rating,
            "effective_n": self.effective_n,
            "rating_confidence": self.rating_confidence,
            "lambda_team": self.lambda_team,
            "slow_bias": self.slow_bias,
            "fast_bias": self.fast_bias,
            "residual_volatility": self.residual_volatility,
        }

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> "CachedRatingTeam":
        tid = str(raw.get("team_id") or raw.get("id") or "")
        return CachedRatingTeam(
            team_id=tid,
            rating=float(raw.get("rating", 0.0)),
            prior_rating=float(raw.get("prior_rating", raw.get("priorRating", 0.0))),
            effective_n=float(raw.get("effective_n", raw.get("effectiveN", 0.0))),
            rating_confidence=float(
                raw.get("rating_confidence", raw.get("ratingConfidence", 0.0))
            ),
            lambda_team=float(raw.get("lambda_team", raw.get("lambdaTeam", 0.0))),
            slow_bias=float(raw.get("slow_bias", raw.get("slowBias", 0.0))),
            fast_bias=float(raw.get("fast_bias", raw.get("fastBias", 0.0))),
            residual_volatility=(
                float(raw["residual_volatility"])
                if raw.get("residual_volatility") is not None
                else (
                    float(raw["residualVolatility"])
                    if raw.get("residualVolatility") is not None
                    else None
                )
            ),
        )


@dataclass
class RatingModelCachePayload:
    cache_schema_version: int
    model_version: str
    rating_mode: str
    rating_algorithm_version: str
    league_id: Optional[str]
    league_name: Optional[str]
    trained_at: str
    training_cutoff: Optional[str]
    home_advantage: float
    derby_home_delta: float
    prior_mode: str
    regularization_parameters: Dict[str, Any]
    time_decay_parameters: Dict[str, Any]
    teams: Dict[str, CachedRatingTeam]
    monitor: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cache_schema_version": self.cache_schema_version,
            "model_version": self.model_version,
            "rating_mode": self.rating_mode,
            "rating_algorithm_version": self.rating_algorithm_version,
            "league_id": self.league_id,
            "league_name": self.league_name,
            "trained_at": self.trained_at,
            "training_cutoff": self.training_cutoff,
            "H": self.home_advantage,
            "home_advantage": self.home_advantage,
            "derby_home_delta": self.derby_home_delta,
            "prior_mode": self.prior_mode,
            "regularization_parameters": self.regularization_parameters,
            "time_decay_parameters": self.time_decay_parameters,
            "teams": {tid: t.to_dict() for tid, t in self.teams.items()},
            "monitor": self.monitor,
        }

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> "RatingModelCachePayload":
        teams: Dict[str, CachedRatingTeam] = {}
        for tid, t in (raw.get("teams") or {}).items():
            if not isinstance(t, Mapping):
                continue
            ct = CachedRatingTeam.from_dict({**dict(t), "team_id": t.get("team_id", tid)})
            teams[str(tid)] = ct
        h = raw.get("home_advantage", raw.get("H", 0.0))
        return RatingModelCachePayload(
            cache_schema_version=int(raw.get("cache_schema_version", CACHE_SCHEMA_VERSION)),
            model_version=str(raw.get("model_version", "")),
            rating_mode=str(raw.get("rating_mode", MODE_HIERARCHICAL)),
            rating_algorithm_version=str(
                raw.get("rating_algorithm_version", RATING_ALGORITHM_VERSION)
            ),
            league_id=raw.get("league_id"),
            league_name=raw.get("league_name"),
            trained_at=str(raw.get("trained_at", "")),
            training_cutoff=raw.get("training_cutoff"),
            home_advantage=float(h),
            derby_home_delta=float(raw.get("derby_home_delta", 0.0)),
            prior_mode=str(raw.get("prior_mode", PRIOR_LEAGUE_MEAN)),
            regularization_parameters=dict(raw.get("regularization_parameters") or {}),
            time_decay_parameters=dict(raw.get("time_decay_parameters") or {}),
            teams=teams,
            monitor=dict(raw.get("monitor") or {}),
        )


def default_rating_cache_root() -> Path:
    env = os.environ.get("FOC_RATING_MODEL_CACHE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "model_cache" / "ratings"


def _league_mode_dir(root: Path, league_key: str, rating_mode: str) -> Path:
    safe_league = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(league_key))
    safe_mode = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(rating_mode))
    return root / safe_league / safe_mode


def build_rating_cache_payload(
    *,
    fit: HierarchicalFitResult,
    cfg: HierarchicalWlsConfig,
    league_id: Optional[str],
    league_name: Optional[str],
    model_version: Optional[str] = None,
    training_cutoff: Optional[str] = None,
    slow_fast: Optional[Mapping[str, Mapping[str, float]]] = None,
    monitor: Optional[Mapping[str, Any]] = None,
) -> RatingModelCachePayload:
    """slow_fast: team_id → {slow_bias, fast_bias}."""
    cfg = cfg.validated()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version = model_version or now
    teams: Dict[str, CachedRatingTeam] = {}
    for tid, r in fit.ratings.items():
        meta = fit.team_meta.get(tid)
        sf = (slow_fast or {}).get(tid) or {}
        teams[tid] = CachedRatingTeam(
            team_id=tid,
            rating=float(r),
            prior_rating=float(meta.prior_rating if meta else fit.priors.get(tid, 0.0)),
            effective_n=float(meta.effective_n if meta else 0.0),
            rating_confidence=float(meta.rating_confidence if meta else 0.0),
            lambda_team=float(meta.lambda_team if meta else 0.0),
            slow_bias=float(sf.get("slow_bias", 0.0)),
            fast_bias=float(sf.get("fast_bias", 0.0)),
            residual_volatility=meta.residual_volatility if meta else None,
        )
    return RatingModelCachePayload(
        cache_schema_version=CACHE_SCHEMA_VERSION,
        model_version=version,
        rating_mode=fit.rating_mode,
        rating_algorithm_version=fit.rating_algorithm_version,
        league_id=league_id,
        league_name=league_name,
        trained_at=now,
        training_cutoff=training_cutoff,
        home_advantage=float(fit.home_advantage),
        derby_home_delta=float(fit.derby_home_delta),
        prior_mode=cfg.prior.mode,
        regularization_parameters=dict(fit.regularization_parameters),
        time_decay_parameters=dict(fit.time_decay_parameters),
        teams=teams,
        monitor=dict(monitor or {}),
    )


def publish_rating_model_cache(
    payload: RatingModelCachePayload,
    cfg: HierarchicalWlsConfig,
    *,
    root: Optional[Path] = None,
) -> Path:
    """Atomic publish of a versioned rating package + active pointer."""
    cfg = cfg.validated()
    root = root or (
        Path(cfg.cache.root_dir) if cfg.cache.root_dir else default_rating_cache_root()
    )
    league_key = str(payload.league_id or payload.league_name or "unknown")
    ldir = _league_mode_dir(root, league_key, payload.rating_mode)
    ldir.mkdir(parents=True, exist_ok=True)

    version = payload.model_version.replace(":", "").replace("/", "-")
    target = ldir / f"model_{version}.json"
    active = ldir / "active.json"

    fd, tmp_name = tempfile.mkstemp(prefix=".model_", suffix=".json", dir=str(ldir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload.to_dict(), f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
        pointer = {
            "model_version": payload.model_version,
            "path": target.name,
            "rating_mode": payload.rating_mode,
            "published_at": time.time(),
        }
        fd2, tmp2 = tempfile.mkstemp(prefix=".active_", suffix=".json", dir=str(ldir))
        try:
            with os.fdopen(fd2, "w", encoding="utf-8") as f:
                json.dump(pointer, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp2, active)
        finally:
            if os.path.exists(tmp2):
                try:
                    os.unlink(tmp2)
                except OSError:
                    pass
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    versions = sorted(ldir.glob("model_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    keep = cfg.cache.versions_to_keep
    for old in versions[keep:]:
        try:
            old.unlink()
        except OSError:
            logger.warning("failed to prune old rating cache %s", old)
    logger.info(
        "Published rating cache league=%s mode=%s version=%s teams=%d",
        league_key,
        payload.rating_mode,
        payload.model_version,
        len(payload.teams),
    )
    return target


def load_active_rating_cache(
    *,
    rating_mode: str,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
    cfg: Optional[HierarchicalWlsConfig] = None,
    root: Optional[Path] = None,
) -> Optional[RatingModelCachePayload]:
    cfg = (cfg or HierarchicalWlsConfig()).validated()
    root = root or (Path(cfg.cache.root_dir) if cfg.cache.root_dir else default_rating_cache_root())
    for key in (league_id, league_name):
        if not key:
            continue
        ldir = _league_mode_dir(root, str(key), rating_mode)
        active = ldir / "active.json"
        if not active.exists():
            continue
        try:
            pointer = json.loads(active.read_text(encoding="utf-8"))
            path = ldir / str(pointer.get("path") or "")
            if not path.exists():
                continue
            return RatingModelCachePayload.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.error("Failed to load rating cache for %s/%s: %s", key, rating_mode, exc)
            if cfg.cache.fallback == FALLBACK_FAIL:
                raise
            return None
    return None


def activate_rating_cache_version(
    *,
    rating_mode: str,
    model_version: str,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
    cfg: Optional[HierarchicalWlsConfig] = None,
    root: Optional[Path] = None,
) -> Path:
    """Point active pointer to an existing version (rollback). Whole package only."""
    cfg = (cfg or HierarchicalWlsConfig()).validated()
    root = root or (Path(cfg.cache.root_dir) if cfg.cache.root_dir else default_rating_cache_root())
    league_key = str(league_id or league_name or "unknown")
    ldir = _league_mode_dir(root, league_key, rating_mode)
    safe_ver = model_version.replace(":", "").replace("/", "-")
    target = ldir / f"model_{safe_ver}.json"
    if not target.exists():
        # try exact filename match among models
        candidates = list(ldir.glob("model_*.json"))
        match = None
        for p in candidates:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if str(data.get("model_version")) == model_version:
                    match = p
                    break
            except (OSError, json.JSONDecodeError):
                continue
        if match is None:
            raise FileNotFoundError(
                f"Rating cache version not found: mode={rating_mode} version={model_version}"
            )
        target = match

    active = ldir / "active.json"
    pointer = {
        "model_version": model_version,
        "path": target.name,
        "rating_mode": rating_mode,
        "published_at": time.time(),
    }
    fd, tmp = tempfile.mkstemp(prefix=".active_", suffix=".json", dir=str(ldir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(pointer, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, active)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return target


def shadow_compare_monitor(
    *,
    standard_ratings: Mapping[str, float],
    hierarchical_ratings: Mapping[str, float],
    standard_mae: float,
    hierarchical_mae: float,
) -> Dict[str, Any]:
    common = sorted(set(standard_ratings) & set(hierarchical_ratings))
    deltas = [hierarchical_ratings[t] - standard_ratings[t] for t in common]
    mean_abs = float(sum(abs(d) for d in deltas) / len(deltas)) if deltas else 0.0
    max_abs = float(max((abs(d) for d in deltas), default=0.0))
    return {
        "n_teams_common": len(common),
        "mean_abs_rating_delta": mean_abs,
        "max_abs_rating_delta": max_abs,
        "standard_mae": float(standard_mae),
        "hierarchical_mae": float(hierarchical_mae),
        "mae_delta": float(hierarchical_mae - standard_mae),
    }
