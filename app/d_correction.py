"""Slow / fast D correction поверх WLS (отдельно от legacy D-EMA).

Режимы (d_correction_mode):
  legacy_ema — текущая D-EMA (обрабатывается в goal_model_train / momentum)
  slow_fast  — D_final = D_base + (slow_h−slow_a) + (fast_h−fast_a)
  disabled   — D_final = D_base (без динамических поправок D)

Slow: устойчивый team placement bias (causal EMA + shrinkage).
Fast: residual после slow (causal EMA, быстрее).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

MODE_LEGACY_EMA = "legacy_ema"
MODE_SLOW_FAST = "slow_fast"
MODE_DISABLED = "disabled"
VALID_MODES = frozenset({MODE_LEGACY_EMA, MODE_SLOW_FAST, MODE_DISABLED})

CACHE_SCHEMA_VERSION = 1
FALLBACK_FAIL = "fail"
FALLBACK_BASE_ONLY = "base_only"
FALLBACK_LAST_LOCAL = "last_local"


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class SlowLayerConfig:
    enabled: bool = True
    method: str = "causal_ema"
    alpha: float = 0.12
    shrink_k: float = 12.0
    min_observations: int = 5
    max_abs_correction: float = 0.35

    def validated(self) -> "SlowLayerConfig":
        a = float(self.alpha)
        if not (0.0 < a <= 1.0):
            raise ValueError(f"d_correction.slow.alpha must be in (0,1], got {a}")
        sk = float(self.shrink_k)
        if sk < 0:
            raise ValueError(f"d_correction.slow.shrink_k must be >= 0, got {sk}")
        mo = int(self.min_observations)
        if mo < 0:
            raise ValueError(f"d_correction.slow.min_observations must be >= 0, got {mo}")
        mx = float(self.max_abs_correction)
        if mx < 0:
            raise ValueError(f"d_correction.slow.max_abs_correction must be >= 0, got {mx}")
        return SlowLayerConfig(
            enabled=bool(self.enabled),
            method=str(self.method or "causal_ema"),
            alpha=a,
            shrink_k=sk,
            min_observations=mo,
            max_abs_correction=mx,
        )


@dataclass(frozen=True)
class FastLayerConfig:
    enabled: bool = True
    method: str = "residual_ema"
    alpha: float = 0.40
    shrink_k: float = 6.0
    min_observations: int = 2
    max_abs_correction: float = 0.40

    def validated(self) -> "FastLayerConfig":
        a = float(self.alpha)
        if not (0.0 < a <= 1.0):
            raise ValueError(f"d_correction.fast.alpha must be in (0,1], got {a}")
        sk = float(self.shrink_k)
        if sk < 0:
            raise ValueError(f"d_correction.fast.shrink_k must be >= 0, got {sk}")
        mo = int(self.min_observations)
        if mo < 0:
            raise ValueError(f"d_correction.fast.min_observations must be >= 0, got {mo}")
        mx = float(self.max_abs_correction)
        if mx < 0:
            raise ValueError(f"d_correction.fast.max_abs_correction must be >= 0, got {mx}")
        return FastLayerConfig(
            enabled=bool(self.enabled),
            method=str(self.method or "residual_ema"),
            alpha=a,
            shrink_k=sk,
            min_observations=mo,
            max_abs_correction=mx,
        )


@dataclass(frozen=True)
class DCorrectionCacheConfig:
    fallback: str = FALLBACK_LAST_LOCAL
    versions_to_keep: int = 2
    root_dir: Optional[str] = None  # None → default under data/model_cache/d

    def validated(self) -> "DCorrectionCacheConfig":
        fb = str(self.fallback or FALLBACK_LAST_LOCAL).strip().lower()
        if fb not in {FALLBACK_FAIL, FALLBACK_BASE_ONLY, FALLBACK_LAST_LOCAL}:
            raise ValueError(f"d_correction.cache.fallback invalid: {fb}")
        keep = int(self.versions_to_keep)
        if keep < 1:
            raise ValueError("d_correction.cache.versions_to_keep must be >= 1")
        return DCorrectionCacheConfig(
            fallback=fb,
            versions_to_keep=keep,
            root_dir=self.root_dir,
        )


@dataclass(frozen=True)
class DCorrectionConfig:
    mode: str = MODE_LEGACY_EMA
    slow: SlowLayerConfig = field(default_factory=SlowLayerConfig)
    fast: FastLayerConfig = field(default_factory=FastLayerConfig)
    total_max_abs_correction: float = 0.60
    cache: DCorrectionCacheConfig = field(default_factory=DCorrectionCacheConfig)

    def validated(self) -> "DCorrectionConfig":
        mode = str(self.mode or MODE_LEGACY_EMA).strip().lower()
        if mode not in VALID_MODES:
            raise ValueError(
                f"d_correction.mode must be one of {sorted(VALID_MODES)}, got {mode!r}"
            )
        tmax = float(self.total_max_abs_correction)
        if tmax < 0:
            raise ValueError("d_correction.total.max_abs_correction must be >= 0")
        return DCorrectionConfig(
            mode=mode,
            slow=self.slow.validated(),
            fast=self.fast.validated(),
            total_max_abs_correction=tmax,
            cache=self.cache.validated(),
        )

    @property
    def uses_slow_fast(self) -> bool:
        return self.mode == MODE_SLOW_FAST

    @property
    def uses_legacy_ema(self) -> bool:
        return self.mode == MODE_LEGACY_EMA

    @property
    def corrections_disabled(self) -> bool:
        return self.mode == MODE_DISABLED


def _layer_from_mapping(raw: Optional[Mapping[str, Any]], *, slow: bool) -> Mapping[str, Any]:
    return raw if isinstance(raw, Mapping) else {}


def d_correction_config_from_mapping(raw: Optional[Mapping[str, Any]]) -> DCorrectionConfig:
    """Читает d_correction из model_config (top-level)."""
    block: Dict[str, Any] = {}
    if isinstance(raw, Mapping):
        dc = raw.get("d_correction")
        if isinstance(dc, Mapping):
            block = dict(dc)
        # alias
        if "mode" not in block and "d_correction_mode" in raw:
            block["mode"] = raw.get("d_correction_mode")
    slow_raw = _layer_from_mapping(block.get("slow"), slow=True)
    fast_raw = _layer_from_mapping(block.get("fast"), slow=False)
    total_raw = block.get("total") if isinstance(block.get("total"), Mapping) else {}
    cache_raw = block.get("cache") if isinstance(block.get("cache"), Mapping) else {}

    def _f(m: Mapping[str, Any], *keys: str, default: Any) -> Any:
        for k in keys:
            if k in m and m[k] is not None:
                return m[k]
        return default

    slow = SlowLayerConfig(
        enabled=bool(_f(slow_raw, "enabled", default=True)),
        method=str(_f(slow_raw, "method", default="causal_ema")),
        alpha=float(_f(slow_raw, "alpha", default=0.12)),
        shrink_k=float(_f(slow_raw, "shrink_k", "shrinkK", default=12.0)),
        min_observations=int(_f(slow_raw, "min_observations", "minObservations", default=5)),
        max_abs_correction=float(
            _f(slow_raw, "max_abs_correction", "maxAbsCorrection", default=0.35)
        ),
    )
    fast = FastLayerConfig(
        enabled=bool(_f(fast_raw, "enabled", default=True)),
        method=str(_f(fast_raw, "method", default="residual_ema")),
        alpha=float(_f(fast_raw, "alpha", default=0.40)),
        shrink_k=float(_f(fast_raw, "shrink_k", "shrinkK", default=6.0)),
        min_observations=int(_f(fast_raw, "min_observations", "minObservations", default=2)),
        max_abs_correction=float(
            _f(fast_raw, "max_abs_correction", "maxAbsCorrection", default=0.40)
        ),
    )
    cache = DCorrectionCacheConfig(
        fallback=str(_f(cache_raw, "fallback", default=FALLBACK_LAST_LOCAL)),
        versions_to_keep=int(_f(cache_raw, "versions_to_keep", "versionsToKeep", default=2)),
        root_dir=_f(cache_raw, "root_dir", "rootDir", default=None),
    )
    return DCorrectionConfig(
        mode=str(block.get("mode", MODE_LEGACY_EMA)),
        slow=slow,
        fast=fast,
        total_max_abs_correction=float(
            _f(total_raw, "max_abs_correction", "maxAbsCorrection", default=0.60)
            if total_raw
            else block.get("total_max_abs_correction", block.get("totalMaxAbsCorrection", 0.60))
        ),
        cache=cache,
    ).validated()


def team_residual_from_match(d_market: float, d_ref: float, *, is_home: bool) -> float:
    """+ = команда недооценена относительно d_ref (D_base или D_slow)."""
    resid = d_market - d_ref
    return resid if is_home else -resid


def shrink_factor(n_effective: float, shrink_k: float) -> float:
    if n_effective <= 0:
        return 0.0
    return float(n_effective) / (float(n_effective) + float(shrink_k))


def update_ema(prev: float, residual: float, alpha: float) -> float:
    return alpha * residual + (1.0 - alpha) * prev


@dataclass
class TeamCorrectionState:
    slow_ema: float = 0.0
    slow_n: int = 0
    fast_ema: float = 0.0
    fast_n: int = 0


@dataclass
class TeamCorrectionView:
    """Сжатые bias для применения в матче."""

    slow_bias: float
    fast_bias: float
    slow_n: int
    fast_n: int
    slow_shrink: float
    fast_shrink: float
    slow_raw_ema: float
    fast_raw_ema: float


@dataclass
class DCorrectionSnapshot:
    d_model_base: float
    slow_bias_home: float
    slow_bias_away: float
    d_slow: float
    fast_bias_home: float
    fast_bias_away: float
    d_fast_correction: float
    d_model_dynamic: float  # = D before dCal (= D_final pre-cal in our naming)
    total_correction: float
    mode: str
    slow_observations_home: int
    slow_observations_away: int
    fast_observations_home: int
    fast_observations_away: int
    slow_shrink_factor_home: float
    slow_shrink_factor_away: float
    fast_shrink_factor_home: float
    fast_shrink_factor_away: float
    clamped_total: bool = False


@dataclass
class MatchDCorrectionRecord:
    match_key: str
    match_date: Optional[date]
    home_id: str
    away_id: str
    d_model_base: float
    slow_bias_home: float
    slow_bias_away: float
    d_slow: float
    fast_bias_home: float
    fast_bias_away: float
    d_fast_correction: float
    d_model_dynamic: float
    total_correction: float
    mode: str
    slow_observations_home: int
    slow_observations_away: int
    fast_observations_home: int
    fast_observations_away: int
    slow_shrink_factor_home: float
    slow_shrink_factor_away: float
    fast_shrink_factor_home: float
    fast_shrink_factor_away: float
    clamped_total: bool = False
    d_market: Optional[float] = None
    residual_base_home: Optional[float] = None
    residual_after_slow_home: Optional[float] = None
    updated: bool = False

    def as_snapshot(self) -> DCorrectionSnapshot:
        return DCorrectionSnapshot(
            d_model_base=self.d_model_base,
            slow_bias_home=self.slow_bias_home,
            slow_bias_away=self.slow_bias_away,
            d_slow=self.d_slow,
            fast_bias_home=self.fast_bias_home,
            fast_bias_away=self.fast_bias_away,
            d_fast_correction=self.d_fast_correction,
            d_model_dynamic=self.d_model_dynamic,
            total_correction=self.total_correction,
            mode=self.mode,
            slow_observations_home=self.slow_observations_home,
            slow_observations_away=self.slow_observations_away,
            fast_observations_home=self.fast_observations_home,
            fast_observations_away=self.fast_observations_away,
            slow_shrink_factor_home=self.slow_shrink_factor_home,
            slow_shrink_factor_away=self.slow_shrink_factor_away,
            fast_shrink_factor_home=self.fast_shrink_factor_home,
            fast_shrink_factor_away=self.fast_shrink_factor_away,
            clamped_total=self.clamped_total,
        )

def match_key(match_date: Optional[date], home_id: str, away_id: str) -> str:
    ds = match_date.isoformat() if match_date else ""
    return f"{ds}|{home_id}|{away_id}"


def bias_from_state(
    state: TeamCorrectionState,
    cfg: DCorrectionConfig,
    *,
    layer: str,
) -> Tuple[float, float]:
    """Возвращает (bias, shrink_factor)."""
    if layer == "slow":
        if not cfg.slow.enabled or state.slow_n < cfg.slow.min_observations:
            return 0.0, 0.0
        sh = shrink_factor(state.slow_n, cfg.slow.shrink_k)
        raw = state.slow_ema * sh
        return clamp(raw, -cfg.slow.max_abs_correction, cfg.slow.max_abs_correction), sh
    if not cfg.fast.enabled or state.fast_n < cfg.fast.min_observations:
        return 0.0, 0.0
    sh = shrink_factor(state.fast_n, cfg.fast.shrink_k)
    raw = state.fast_ema * sh
    return clamp(raw, -cfg.fast.max_abs_correction, cfg.fast.max_abs_correction), sh


def apply_slow_fast_to_d(
    d_model_base: float,
    home: TeamCorrectionState,
    away: TeamCorrectionState,
    cfg: DCorrectionConfig,
) -> DCorrectionSnapshot:
    cfg = cfg.validated()
    if cfg.mode != MODE_SLOW_FAST:
        return DCorrectionSnapshot(
            d_model_base=d_model_base,
            slow_bias_home=0.0,
            slow_bias_away=0.0,
            d_slow=d_model_base,
            fast_bias_home=0.0,
            fast_bias_away=0.0,
            d_fast_correction=0.0,
            d_model_dynamic=d_model_base,
            total_correction=0.0,
            mode=cfg.mode,
            slow_observations_home=home.slow_n,
            slow_observations_away=away.slow_n,
            fast_observations_home=home.fast_n,
            fast_observations_away=away.fast_n,
            slow_shrink_factor_home=0.0,
            slow_shrink_factor_away=0.0,
            fast_shrink_factor_home=0.0,
            fast_shrink_factor_away=0.0,
        )

    sh, sh_f = bias_from_state(home, cfg, layer="slow")
    sa, sa_f = bias_from_state(away, cfg, layer="slow")
    d_slow = d_model_base + sh - sa

    fh, fh_f = bias_from_state(home, cfg, layer="fast")
    fa, fa_f = bias_from_state(away, cfg, layer="fast")
    d_fast_corr = fh - fa
    total = (sh - sa) + d_fast_corr
    clamped = False
    if abs(total) > cfg.total_max_abs_correction:
        scale = cfg.total_max_abs_correction / abs(total)
        total *= scale
        d_fast_corr = total - (sh - sa)
        # rescale fast biases proportionally for diagnostics consistency
        fh *= scale
        fa *= scale
        clamped = True
    d_dyn = d_model_base + total
    return DCorrectionSnapshot(
        d_model_base=d_model_base,
        slow_bias_home=sh,
        slow_bias_away=sa,
        d_slow=d_slow,
        fast_bias_home=fh,
        fast_bias_away=fa,
        d_fast_correction=d_fast_corr,
        d_model_dynamic=d_dyn,
        total_correction=total,
        mode=cfg.mode,
        slow_observations_home=home.slow_n,
        slow_observations_away=away.slow_n,
        fast_observations_home=home.fast_n,
        fast_observations_away=away.fast_n,
        slow_shrink_factor_home=sh_f,
        slow_shrink_factor_away=sa_f,
        fast_shrink_factor_home=fh_f,
        fast_shrink_factor_away=fa_f,
        clamped_total=clamped,
    )


@dataclass
class DCorrectionWalkMatch:
    match_date: Optional[date]
    home_id: str
    away_id: str
    d_model_base: float
    d_market: Optional[float]


@dataclass
class DCorrectionBook:
    cfg: DCorrectionConfig
    teams: Dict[str, TeamCorrectionState] = field(default_factory=dict)
    records: Dict[str, MatchDCorrectionRecord] = field(default_factory=dict)
    monitor: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cfg = self.cfg.validated()

    def _team(self, team_id: str) -> TeamCorrectionState:
        if team_id not in self.teams:
            self.teams[team_id] = TeamCorrectionState()
        return self.teams[team_id]

    def peek(self, *, home_id: str, away_id: str, d_model_base: float) -> DCorrectionSnapshot:
        return apply_slow_fast_to_d(
            d_model_base, self._team(home_id), self._team(away_id), self.cfg
        )

    def team_view(self, team_id: str) -> TeamCorrectionView:
        st = self._team(team_id)
        sb, ss = bias_from_state(st, self.cfg, layer="slow")
        fb, fs = bias_from_state(st, self.cfg, layer="fast")
        return TeamCorrectionView(
            slow_bias=sb,
            fast_bias=fb,
            slow_n=st.slow_n,
            fast_n=st.fast_n,
            slow_shrink=ss,
            fast_shrink=fs,
            slow_raw_ema=st.slow_ema,
            fast_raw_ema=st.fast_ema,
        )

    def update_after_match(
        self,
        home_id: str,
        away_id: str,
        *,
        d_market: float,
        d_model_base: float,
        d_slow: float,
    ) -> None:
        """Обновить slow по residual vs D_base; fast по residual vs D_slow."""
        ht = self._team(home_id)
        at = self._team(away_id)
        rh_base = team_residual_from_match(d_market, d_model_base, is_home=True)
        ra_base = team_residual_from_match(d_market, d_model_base, is_home=False)
        rh_slow = team_residual_from_match(d_market, d_slow, is_home=True)
        ra_slow = team_residual_from_match(d_market, d_slow, is_home=False)

        if self.cfg.slow.enabled:
            ht.slow_ema = update_ema(ht.slow_ema, rh_base, self.cfg.slow.alpha)
            at.slow_ema = update_ema(at.slow_ema, ra_base, self.cfg.slow.alpha)
            ht.slow_n += 1
            at.slow_n += 1
        if self.cfg.fast.enabled:
            ht.fast_ema = update_ema(ht.fast_ema, rh_slow, self.cfg.fast.alpha)
            at.fast_ema = update_ema(at.fast_ema, ra_slow, self.cfg.fast.alpha)
            ht.fast_n += 1
            at.fast_n += 1


def build_d_correction_walk(
    matches: Sequence[DCorrectionWalkMatch],
    cfg: DCorrectionConfig,
) -> DCorrectionBook:
    """Causal walk-forward: same-date slice → all peeks, then updates (AC6/AC7)."""
    cfg = cfg.validated()
    book = DCorrectionBook(cfg=cfg)
    if cfg.mode != MODE_SLOW_FAST:
        book.monitor = {"mode": cfg.mode, "n_matches": 0, "skipped": True}
        return book

    buckets: Dict[Optional[date], List[DCorrectionWalkMatch]] = {}
    order: List[Optional[date]] = []
    for m in matches:
        if m.match_date not in buckets:
            buckets[m.match_date] = []
            order.append(m.match_date)
        buckets[m.match_date].append(m)
    dated = sorted({d for d in order if d is not None})
    undated = [d for d in order if d is None]
    slice_keys: List[Optional[date]] = dated + undated

    n_updated = 0
    clamp_hits = 0
    for dkey in slice_keys:
        group = buckets[dkey]
        pending: List[Tuple[DCorrectionWalkMatch, DCorrectionSnapshot]] = []
        for m in group:
            snap = book.peek(home_id=m.home_id, away_id=m.away_id, d_model_base=m.d_model_base)
            if snap.clamped_total:
                clamp_hits += 1
            key = match_key(m.match_date, m.home_id, m.away_id)
            book.records[key] = MatchDCorrectionRecord(
                match_key=key,
                match_date=m.match_date,
                home_id=m.home_id,
                away_id=m.away_id,
                d_model_base=snap.d_model_base,
                slow_bias_home=snap.slow_bias_home,
                slow_bias_away=snap.slow_bias_away,
                d_slow=snap.d_slow,
                fast_bias_home=snap.fast_bias_home,
                fast_bias_away=snap.fast_bias_away,
                d_fast_correction=snap.d_fast_correction,
                d_model_dynamic=snap.d_model_dynamic,
                total_correction=snap.total_correction,
                mode=snap.mode,
                slow_observations_home=snap.slow_observations_home,
                slow_observations_away=snap.slow_observations_away,
                fast_observations_home=snap.fast_observations_home,
                fast_observations_away=snap.fast_observations_away,
                slow_shrink_factor_home=snap.slow_shrink_factor_home,
                slow_shrink_factor_away=snap.slow_shrink_factor_away,
                fast_shrink_factor_home=snap.fast_shrink_factor_home,
                fast_shrink_factor_away=snap.fast_shrink_factor_away,
                clamped_total=snap.clamped_total,
                d_market=m.d_market,
            )
            if m.d_market is not None and m.d_market == m.d_market:
                pending.append((m, snap))
        for m, snap in pending:
            book.update_after_match(
                m.home_id,
                m.away_id,
                d_market=float(m.d_market),
                d_model_base=m.d_model_base,
                d_slow=snap.d_slow,
            )
            n_updated += 1
            key = match_key(m.match_date, m.home_id, m.away_id)
            old = book.records[key]
            rh = team_residual_from_match(float(m.d_market), m.d_model_base, is_home=True)
            rhs = team_residual_from_match(float(m.d_market), snap.d_slow, is_home=True)
            book.records[key] = replace(
                old,
                residual_base_home=rh,
                residual_after_slow_home=rhs,
                updated=True,
            )

    slow_biases = []
    fast_biases = []
    for tid, st in book.teams.items():
        view = book.team_view(tid)
        if st.slow_n >= cfg.slow.min_observations:
            slow_biases.append(abs(view.slow_bias))
        if st.fast_n >= cfg.fast.min_observations:
            fast_biases.append(abs(view.fast_bias))
    book.monitor = {
        "mode": cfg.mode,
        "n_matches_updated": n_updated,
        "n_teams": len(book.teams),
        "n_teams_with_slow": len(slow_biases),
        "n_teams_with_fast": len(fast_biases),
        "mean_abs_slow": float(sum(slow_biases) / len(slow_biases)) if slow_biases else 0.0,
        "max_abs_slow": float(max(slow_biases)) if slow_biases else 0.0,
        "mean_abs_fast": float(sum(fast_biases) / len(fast_biases)) if fast_biases else 0.0,
        "max_abs_fast": float(max(fast_biases)) if fast_biases else 0.0,
        "clamp_hits": clamp_hits,
        "n_insufficient_slow": sum(
            1 for st in book.teams.values() if st.slow_n < cfg.slow.min_observations
        ),
    }
    return book


# --------------------------------------------------------------------------- #
# Model cache (filesystem, atomic publish)
# --------------------------------------------------------------------------- #


@dataclass
class CachedTeamParams:
    rating: float
    slow_bias: float
    fast_bias: float
    slow_n: int = 0
    fast_n: int = 0


@dataclass
class DModelCachePayload:
    cache_schema_version: int
    model_version: str
    league_id: Optional[str]
    league_name: Optional[str]
    trained_at: str
    d_correction_mode: str
    home_advantage: float
    params: Dict[str, Any]
    teams: Dict[str, CachedTeamParams]
    monitor: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cache_schema_version": self.cache_schema_version,
            "model_version": self.model_version,
            "league_id": self.league_id,
            "league_name": self.league_name,
            "trained_at": self.trained_at,
            "d_correction_mode": self.d_correction_mode,
            "home_advantage": self.home_advantage,
            "params": self.params,
            "teams": {
                tid: {
                    "rating": t.rating,
                    "slow_bias": t.slow_bias,
                    "fast_bias": t.fast_bias,
                    "slow_n": t.slow_n,
                    "fast_n": t.fast_n,
                }
                for tid, t in self.teams.items()
            },
            "monitor": self.monitor,
        }

    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> "DModelCachePayload":
        teams: Dict[str, CachedTeamParams] = {}
        for tid, t in (raw.get("teams") or {}).items():
            if not isinstance(t, Mapping):
                continue
            teams[str(tid)] = CachedTeamParams(
                rating=float(t.get("rating", 0.0)),
                slow_bias=float(t.get("slow_bias", 0.0)),
                fast_bias=float(t.get("fast_bias", 0.0)),
                slow_n=int(t.get("slow_n", 0)),
                fast_n=int(t.get("fast_n", 0)),
            )
        return DModelCachePayload(
            cache_schema_version=int(raw.get("cache_schema_version", CACHE_SCHEMA_VERSION)),
            model_version=str(raw.get("model_version", "")),
            league_id=raw.get("league_id"),
            league_name=raw.get("league_name"),
            trained_at=str(raw.get("trained_at", "")),
            d_correction_mode=str(raw.get("d_correction_mode", MODE_LEGACY_EMA)),
            home_advantage=float(raw.get("home_advantage", 0.0)),
            params=dict(raw.get("params") or {}),
            teams=teams,
            monitor=dict(raw.get("monitor") or {}),
        )


def default_cache_root() -> Path:
    env = os.environ.get("FOC_D_MODEL_CACHE_DIR")
    if env:
        return Path(env)
    # repo-relative
    return Path(__file__).resolve().parents[1] / "data" / "model_cache" / "d"


def _league_dir(root: Path, league_key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(league_key))
    return root / safe


def build_cache_payload(
    *,
    strength_ratings: Mapping[str, float],
    home_advantage: float,
    book: Optional[DCorrectionBook],
    cfg: DCorrectionConfig,
    league_id: Optional[str],
    league_name: Optional[str],
    model_version: Optional[str] = None,
) -> DModelCachePayload:
    cfg = cfg.validated()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version = model_version or now
    teams: Dict[str, CachedTeamParams] = {}
    for tid, r in strength_ratings.items():
        if book is not None and cfg.mode == MODE_SLOW_FAST:
            view = book.team_view(tid)
            teams[tid] = CachedTeamParams(
                rating=float(r),
                slow_bias=view.slow_bias,
                fast_bias=view.fast_bias,
                slow_n=view.slow_n,
                fast_n=view.fast_n,
            )
        else:
            teams[tid] = CachedTeamParams(rating=float(r), slow_bias=0.0, fast_bias=0.0)
    return DModelCachePayload(
        cache_schema_version=CACHE_SCHEMA_VERSION,
        model_version=version,
        league_id=league_id,
        league_name=league_name,
        trained_at=now,
        d_correction_mode=cfg.mode,
        home_advantage=float(home_advantage),
        params={
            "slow": asdict(cfg.slow),
            "fast": asdict(cfg.fast),
            "total_max_abs_correction": cfg.total_max_abs_correction,
        },
        teams=teams,
        monitor=(book.monitor if book is not None else {}),
    )


def validate_cache_payload(payload: DModelCachePayload, cfg: DCorrectionConfig) -> List[str]:
    """Возвращает список критических нарушений (пустой = ok)."""
    errors: List[str] = []
    cfg = cfg.validated()
    for tid, t in payload.teams.items():
        if abs(t.slow_bias) > cfg.slow.max_abs_correction + 1e-9:
            errors.append(f"{tid}: |slow_bias|={abs(t.slow_bias):.4f} > limit")
        if abs(t.fast_bias) > cfg.fast.max_abs_correction + 1e-9:
            errors.append(f"{tid}: |fast_bias|={abs(t.fast_bias):.4f} > limit")
        if abs(t.slow_bias + t.fast_bias) > cfg.total_max_abs_correction + 1e-9:
            # per-team sum can exceed total match clamp; only warn if absurdly large
            if abs(t.slow_bias) + abs(t.fast_bias) > cfg.total_max_abs_correction + 0.25:
                errors.append(f"{tid}: |slow|+|fast| too large")
    mon = payload.monitor or {}
    if float(mon.get("max_abs_slow", 0.0)) > cfg.slow.max_abs_correction + 1e-6:
        errors.append("monitor max_abs_slow exceeds config")
    if float(mon.get("max_abs_fast", 0.0)) > cfg.fast.max_abs_correction + 1e-6:
        errors.append("monitor max_abs_fast exceeds config")
    return errors


def publish_d_model_cache(
    payload: DModelCachePayload,
    cfg: DCorrectionConfig,
    *,
    root: Optional[Path] = None,
) -> Path:
    """Атомарно публикует версию и переключает active pointer (AC2/AC3/AC8)."""
    cfg = cfg.validated()
    root = root or (
        Path(cfg.cache.root_dir) if cfg.cache.root_dir else default_cache_root()
    )
    league_key = str(payload.league_id or payload.league_name or "unknown")
    ldir = _league_dir(root, league_key)
    ldir.mkdir(parents=True, exist_ok=True)

    errors = validate_cache_payload(payload, cfg)
    if errors:
        raise ValueError("D model cache validation failed: " + "; ".join(errors[:5]))

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
        # atomic active pointer
        pointer = {"model_version": payload.model_version, "path": target.name, "published_at": time.time()}
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

    # prune old versions
    versions = sorted(ldir.glob("model_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    keep = cfg.cache.versions_to_keep
    for old in versions[keep:]:
        try:
            old.unlink()
        except OSError:
            logger.warning("failed to prune old D model cache %s", old)
    logger.info(
        "Published D model cache league=%s version=%s teams=%d mode=%s",
        league_key,
        payload.model_version,
        len(payload.teams),
        payload.d_correction_mode,
    )
    return target


def load_active_d_model_cache(
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
    cfg: Optional[DCorrectionConfig] = None,
    root: Optional[Path] = None,
) -> Optional[DModelCachePayload]:
    cfg = (cfg or DCorrectionConfig()).validated()
    root = root or (Path(cfg.cache.root_dir) if cfg.cache.root_dir else default_cache_root())
    for key in (league_id, league_name):
        if not key:
            continue
        ldir = _league_dir(root, str(key))
        active = ldir / "active.json"
        if not active.exists():
            continue
        try:
            pointer = json.loads(active.read_text(encoding="utf-8"))
            path = ldir / str(pointer.get("path") or "")
            if not path.exists():
                continue
            return DModelCachePayload.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.error("Failed to load D model cache for %s: %s", key, exc)
            if cfg.cache.fallback == FALLBACK_FAIL:
                raise
            return None
    return None


def d_from_cache_payload(
    payload: DModelCachePayload,
    home_id: str,
    away_id: str,
    *,
    h_eff: Optional[float] = None,
) -> DCorrectionSnapshot:
    """Online: D из кэша без пересчёта истории."""
    H = float(h_eff if h_eff is not None else payload.home_advantage)
    th = payload.teams.get(home_id) or CachedTeamParams(0.0, 0.0, 0.0)
    ta = payload.teams.get(away_id) or CachedTeamParams(0.0, 0.0, 0.0)
    # unknown team → rating 0 + bias 0 (AC9); caller may override rating via WLS fallback
    d_base = th.rating - ta.rating + H
    mode = payload.d_correction_mode
    if mode != MODE_SLOW_FAST:
        return DCorrectionSnapshot(
            d_model_base=d_base,
            slow_bias_home=0.0,
            slow_bias_away=0.0,
            d_slow=d_base,
            fast_bias_home=0.0,
            fast_bias_away=0.0,
            d_fast_correction=0.0,
            d_model_dynamic=d_base,
            total_correction=0.0,
            mode=mode,
            slow_observations_home=th.slow_n,
            slow_observations_away=ta.slow_n,
            fast_observations_home=th.fast_n,
            fast_observations_away=ta.fast_n,
            slow_shrink_factor_home=0.0,
            slow_shrink_factor_away=0.0,
            fast_shrink_factor_home=0.0,
            fast_shrink_factor_away=0.0,
        )
    sh, sa = th.slow_bias, ta.slow_bias
    fh, fa = th.fast_bias, ta.fast_bias
    d_slow = d_base + sh - sa
    d_fast = fh - fa
    d_dyn = d_slow + d_fast
    return DCorrectionSnapshot(
        d_model_base=d_base,
        slow_bias_home=sh,
        slow_bias_away=sa,
        d_slow=d_slow,
        fast_bias_home=fh,
        fast_bias_away=fa,
        d_fast_correction=d_fast,
        d_model_dynamic=d_dyn,
        total_correction=d_dyn - d_base,
        mode=mode,
        slow_observations_home=th.slow_n,
        slow_observations_away=ta.slow_n,
        fast_observations_home=th.fast_n,
        fast_observations_away=ta.fast_n,
        slow_shrink_factor_home=1.0 if th.slow_n else 0.0,
        slow_shrink_factor_away=1.0 if ta.slow_n else 0.0,
        fast_shrink_factor_home=1.0 if th.fast_n else 0.0,
        fast_shrink_factor_away=1.0 if ta.fast_n else 0.0,
    )
