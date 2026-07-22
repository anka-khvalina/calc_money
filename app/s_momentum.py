"""Динамическая корректировка S по EMA остатков тотала (S-momentum).

S_dynamic = S_model_base + k·(EMA_h + EMA_a)  — сумма, не разность.
Остаток матча делится поровну: TeamResidual = Residual_S / 2 обеим командам.
EMA обновляется только по матчам с валидным S_market, walk-forward по датам.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class SMomentumConfig:
    enabled: bool = False
    alpha: float = 0.30
    k: float = 1.00
    min_team_matches: int = 1
    max_abs_team_ema: Optional[float] = None
    max_abs_correction: Optional[float] = None
    lambda_min: float = 0.05
    reset_on_new_season: bool = True

    def validated(self) -> "SMomentumConfig":
        a = float(self.alpha)
        if not (0.0 < a <= 1.0):
            raise ValueError(f"dynamic_s_ema.alpha must be in (0, 1], got {a}")
        kk = float(self.k)
        if kk < 0:
            raise ValueError(f"dynamic_s_ema.k must be >= 0, got {kk}")
        mm = int(self.min_team_matches)
        if mm < 0:
            raise ValueError(f"dynamic_s_ema.min_team_matches must be >= 0, got {mm}")
        lam = float(self.lambda_min)
        if lam <= 0:
            raise ValueError(f"dynamic_s_ema.lambda_min must be > 0, got {lam}")
        max_ema = self.max_abs_team_ema
        if max_ema is not None:
            max_ema = float(max_ema)
            if max_ema <= 0:
                raise ValueError(f"dynamic_s_ema.max_abs_team_ema must be > 0, got {max_ema}")
        max_corr = self.max_abs_correction
        if max_corr is not None:
            max_corr = float(max_corr)
            if max_corr <= 0:
                raise ValueError(f"dynamic_s_ema.max_abs_correction must be > 0, got {max_corr}")
        return SMomentumConfig(
            enabled=bool(self.enabled),
            alpha=a,
            k=kk,
            min_team_matches=mm,
            max_abs_team_ema=max_ema,
            max_abs_correction=max_corr,
            lambda_min=lam,
            reset_on_new_season=bool(self.reset_on_new_season),
        )


def _opt_float(v: Any) -> Optional[float]:
    if v is None or v == "" or v == "null":
        return None
    return float(v)


def s_momentum_config_from_mapping(
    raw: Optional[Mapping[str, Any]],
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> SMomentumConfig:
    """Читает dynamic_s_ema / sMomentum / s_momentum из model_config."""
    base: Dict[str, Any] = {}
    if raw:
        for block in (
            raw.get("dynamic_s_ema"),
            raw.get("sMomentum"),
            raw.get("s_momentum"),
            (raw.get("train") or {}).get("dynamic_s_ema") if isinstance(raw.get("train"), dict) else None,
            (raw.get("momentum") or {}).get("s") if isinstance(raw.get("momentum"), dict) else None,
        ):
            if isinstance(block, dict):
                base.update(block)
        by = base.get("byLeague") or base.get("by_league") or {}
        if isinstance(by, dict):
            for key in (league_id, league_name, str(league_id or ""), str(league_name or "")):
                if key and key in by and isinstance(by[key], dict):
                    base = {**base, **by[key]}
                    break
    return SMomentumConfig(
        enabled=bool(base["enabled"]) if "enabled" in base else False,
        alpha=float(base["alpha"]) if "alpha" in base else 0.30,
        k=float(base["k"]) if "k" in base else 1.00,
        min_team_matches=int(
            base["min_team_matches"]
            if "min_team_matches" in base
            else base.get("minTeamMatches", base.get("minMatches", 1))
        ),
        max_abs_team_ema=_opt_float(
            base.get("max_abs_team_ema", base.get("maxAbsTeamEma", base.get("maxEma")))
        ),
        max_abs_correction=_opt_float(
            base.get("max_abs_correction", base.get("maxAbsCorrection"))
        ),
        lambda_min=float(
            base["lambda_min"] if "lambda_min" in base else base.get("lambdaMin", 0.05)
        ),
        reset_on_new_season=bool(
            base["reset_on_new_season"]
            if "reset_on_new_season" in base
            else base.get("resetOnNewSeason", True)
        ),
    ).validated()


@dataclass
class TeamSMomentumState:
    ema: float = 0.0
    matches_count: int = 0


@dataclass
class SMomentumSnapshot:
    ema_home_before: float
    ema_away_before: float
    ema_home_effective: float
    ema_away_effective: float
    home_matches_count: int
    away_matches_count: int
    s_momentum: float
    dynamic_correction_raw: float
    dynamic_correction: float
    s_model_base: float
    s_model_dynamic: float
    correction_clamped: bool
    ema_home_clamped: bool
    ema_away_clamped: bool
    enabled: bool
    alpha: float
    k: float
    max_abs_team_ema: Optional[float]
    max_abs_correction: Optional[float]
    lambda_min: float


@dataclass
class MatchSMomentumRecord(SMomentumSnapshot):
    match_key: str
    match_date: Optional[date]
    home_id: str
    away_id: str
    s_market: Optional[float] = None
    residual_s_base: Optional[float] = None
    team_residual_s: Optional[float] = None
    ema_home_after: Optional[float] = None
    ema_away_after: Optional[float] = None
    updated_ema: bool = False
    update_skip_reason: Optional[str] = None
    match_weight: float = 1.0


def residual_s(s_market: float, s_model_base: float) -> Tuple[float, float]:
    """Residual_S and TeamResidual_S (= half)."""
    resid = s_market - s_model_base
    return resid, resid / 2.0


def update_ema(ema_previous: float, team_residual: float, alpha: float) -> float:
    return alpha * team_residual + (1.0 - alpha) * ema_previous


def effective_alpha(alpha: float, match_weight: float) -> float:
    w = max(0.0, float(match_weight))
    return clamp(alpha * w, 0.0, 1.0)


def limit_team_ema(
    ema_raw: float,
    max_abs: Optional[float],
) -> Tuple[float, bool]:
    if max_abs is None:
        return ema_raw, False
    lim = clamp(ema_raw, -max_abs, max_abs)
    return lim, abs(lim - ema_raw) > 1e-15


def apply_s_momentum(
    s_model_base: float,
    ema_home: float,
    ema_away: float,
    cfg: SMomentumConfig,
    *,
    home_matches: int,
    away_matches: int,
) -> SMomentumSnapshot:
    """AC-2, AC-3, AC-7, AC-8."""
    cfg = cfg.validated()
    eh_lim, eh_cl = limit_team_ema(ema_home, cfg.max_abs_team_ema)
    ea_lim, ea_cl = limit_team_ema(ema_away, cfg.max_abs_team_ema)
    eh_eff = eh_lim if home_matches >= cfg.min_team_matches else 0.0
    ea_eff = ea_lim if away_matches >= cfg.min_team_matches else 0.0
    # если порог не достигнут — effective=0, но для диагностики оставляем limited
    if home_matches < cfg.min_team_matches:
        eh_eff = 0.0
    if away_matches < cfg.min_team_matches:
        ea_eff = 0.0
    momentum = eh_eff + ea_eff
    corr_raw = cfg.k * momentum if cfg.enabled else 0.0
    corr = corr_raw
    corr_clamped = False
    if cfg.enabled and cfg.max_abs_correction is not None:
        corr2 = clamp(corr_raw, -cfg.max_abs_correction, cfg.max_abs_correction)
        corr_clamped = abs(corr2 - corr_raw) > 1e-15
        corr = corr2
    if not cfg.enabled:
        corr = 0.0
        corr_raw = 0.0
        momentum = eh_eff + ea_eff  # still report
    s_dyn = s_model_base + corr
    return SMomentumSnapshot(
        ema_home_before=ema_home,
        ema_away_before=ema_away,
        ema_home_effective=eh_eff if cfg.enabled else 0.0,
        ema_away_effective=ea_eff if cfg.enabled else 0.0,
        home_matches_count=home_matches,
        away_matches_count=away_matches,
        s_momentum=momentum if cfg.enabled else 0.0,
        dynamic_correction_raw=corr_raw,
        dynamic_correction=corr,
        s_model_base=s_model_base,
        s_model_dynamic=s_dyn if cfg.enabled else s_model_base,
        correction_clamped=corr_clamped,
        ema_home_clamped=eh_cl,
        ema_away_clamped=ea_cl,
        enabled=cfg.enabled,
        alpha=cfg.alpha,
        k=cfg.k,
        max_abs_team_ema=cfg.max_abs_team_ema,
        max_abs_correction=cfg.max_abs_correction,
        lambda_min=cfg.lambda_min,
    )


def lambdas_from_sd(
    s_final: float,
    d_final: float,
    *,
    lambda_min: float,
) -> Tuple[float, float, float, float, bool, float, float]:
    """λ_raw, λ_final, clipping flag, S/D after clip.

    Returns:
      lh_raw, la_raw, lh_final, la_final, clipped, s_after, d_after
    """
    lh_raw = (s_final + d_final) / 2.0
    la_raw = (s_final - d_final) / 2.0
    lh = max(lambda_min, lh_raw)
    la = max(lambda_min, la_raw)
    clipped = (lh != lh_raw) or (la != la_raw)
    return lh_raw, la_raw, lh, la, clipped, lh + la, lh - la


def match_key(match_date: Optional[date], home_id: str, away_id: str) -> str:
    ds = match_date.isoformat() if match_date else ""
    return f"{ds}|{home_id}|{away_id}"


@dataclass
class SMomentumBook:
    cfg: SMomentumConfig
    teams: Dict[str, TeamSMomentumState] = field(default_factory=dict)
    records: Dict[str, MatchSMomentumRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cfg = self.cfg.validated()

    def _team(self, team_id: str) -> TeamSMomentumState:
        if team_id not in self.teams:
            self.teams[team_id] = TeamSMomentumState()
        return self.teams[team_id]

    def peek(
        self,
        *,
        home_id: str,
        away_id: str,
        s_model_base: float,
    ) -> SMomentumSnapshot:
        ht = self._team(home_id)
        at = self._team(away_id)
        return apply_s_momentum(
            s_model_base,
            ht.ema,
            at.ema,
            self.cfg,
            home_matches=ht.matches_count,
            away_matches=at.matches_count,
        )

    def update_pair(
        self,
        home_id: str,
        away_id: str,
        team_residual: float,
        *,
        match_weight: float = 1.0,
    ) -> Tuple[float, float, bool]:
        """Update both teams with the same TeamResidual_S. Returns (eh, ea, updated)."""
        a_eff = effective_alpha(self.cfg.alpha, match_weight)
        if a_eff <= 0.0:
            return self._team(home_id).ema, self._team(away_id).ema, False
        ht = self._team(home_id)
        at = self._team(away_id)
        ht.ema = update_ema(ht.ema, team_residual, a_eff)
        at.ema = update_ema(at.ema, team_residual, a_eff)
        ht.matches_count += 1
        at.matches_count += 1
        return ht.ema, at.ema, True


@dataclass
class SMomentumWalkMatch:
    match_date: Optional[date]
    home_id: str
    away_id: str
    s_model_base: float
    s_market: Optional[float]
    match_weight: float = 1.0


def build_s_momentum_walk(
    matches: Sequence[SMomentumWalkMatch],
    cfg: SMomentumConfig,
) -> SMomentumBook:
    """Walk-forward: same-date slice → predict all → then update (AC-5)."""
    book = SMomentumBook(cfg=cfg)
    buckets: Dict[Optional[date], List[SMomentumWalkMatch]] = {}
    order: List[Optional[date]] = []
    for m in matches:
        if m.match_date not in buckets:
            buckets[m.match_date] = []
            order.append(m.match_date)
        buckets[m.match_date].append(m)
    dated = sorted({d for d in order if d is not None})
    undated = [d for d in order if d is None]
    for dkey in dated + undated:
        group = buckets[dkey]
        pending: List[Tuple[SMomentumWalkMatch, float, float, SMomentumSnapshot]] = []
        for m in group:
            snap = book.peek(
                home_id=m.home_id, away_id=m.away_id, s_model_base=m.s_model_base,
            )
            key = match_key(m.match_date, m.home_id, m.away_id)
            skip = None
            resid = None
            half = None
            can_update = False
            if m.s_market is None or m.s_market != m.s_market:
                skip = "missing_s_market"
            elif m.s_model_base != m.s_model_base:
                skip = "invalid_s_model_base"
            elif m.match_weight is not None and float(m.match_weight) <= 0.0:
                skip = "match_weight_zero"
            else:
                resid, half = residual_s(float(m.s_market), m.s_model_base)
                can_update = True
            book.records[key] = MatchSMomentumRecord(
                match_key=key,
                match_date=m.match_date,
                home_id=m.home_id,
                away_id=m.away_id,
                ema_home_before=snap.ema_home_before,
                ema_away_before=snap.ema_away_before,
                ema_home_effective=snap.ema_home_effective,
                ema_away_effective=snap.ema_away_effective,
                home_matches_count=snap.home_matches_count,
                away_matches_count=snap.away_matches_count,
                s_momentum=snap.s_momentum,
                dynamic_correction_raw=snap.dynamic_correction_raw,
                dynamic_correction=snap.dynamic_correction,
                s_model_base=snap.s_model_base,
                s_model_dynamic=snap.s_model_dynamic,
                correction_clamped=snap.correction_clamped,
                ema_home_clamped=snap.ema_home_clamped,
                ema_away_clamped=snap.ema_away_clamped,
                enabled=snap.enabled,
                alpha=snap.alpha,
                k=snap.k,
                max_abs_team_ema=snap.max_abs_team_ema,
                max_abs_correction=snap.max_abs_correction,
                lambda_min=snap.lambda_min,
                s_market=m.s_market,
                residual_s_base=resid,
                team_residual_s=half,
                updated_ema=False,
                update_skip_reason=skip,
                match_weight=float(m.match_weight if m.match_weight is not None else 1.0),
            )
            if can_update and half is not None and resid is not None:
                pending.append((m, resid, half, snap))
        for m, resid, half, snap in pending:
            eh_a, ea_a, ok = book.update_pair(
                m.home_id, m.away_id, half, match_weight=m.match_weight,
            )
            key = match_key(m.match_date, m.home_id, m.away_id)
            old = book.records[key]
            book.records[key] = MatchSMomentumRecord(
                match_key=old.match_key,
                match_date=old.match_date,
                home_id=old.home_id,
                away_id=old.away_id,
                ema_home_before=old.ema_home_before,
                ema_away_before=old.ema_away_before,
                ema_home_effective=old.ema_home_effective,
                ema_away_effective=old.ema_away_effective,
                home_matches_count=old.home_matches_count,
                away_matches_count=old.away_matches_count,
                s_momentum=old.s_momentum,
                dynamic_correction_raw=old.dynamic_correction_raw,
                dynamic_correction=old.dynamic_correction,
                s_model_base=old.s_model_base,
                s_model_dynamic=old.s_model_dynamic,
                correction_clamped=old.correction_clamped,
                ema_home_clamped=old.ema_home_clamped,
                ema_away_clamped=old.ema_away_clamped,
                enabled=old.enabled,
                alpha=old.alpha,
                k=old.k,
                max_abs_team_ema=old.max_abs_team_ema,
                max_abs_correction=old.max_abs_correction,
                lambda_min=old.lambda_min,
                s_market=m.s_market,
                residual_s_base=resid,
                team_residual_s=half,
                ema_home_after=eh_a,
                ema_away_after=ea_a,
                updated_ema=ok,
                update_skip_reason=None if ok else "alpha_effective_zero",
                match_weight=old.match_weight,
            )
    return book
