"""Динамическая корректировка D по EMA рыночных остатков (momentum / market drift).

WLS не меняется: D_dynamic = D_model_base + k·(clamp(EMA_h)−clamp(EMA_a)).
EMA обновляется только по матчам с корректным D_market, строго walk-forward
по временным срезам (одинаковая дата — сначала все прогнозы, потом обновления).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class MomentumConfig:
    enabled: bool = True
    alpha: float = 0.30
    k: float = 0.80
    max_ema: float = 0.50
    min_matches: int = 1

    def validated(self) -> "MomentumConfig":
        a = float(self.alpha)
        if not (0.0 < a <= 1.0):
            raise ValueError(f"momentum_alpha must be in (0, 1], got {a}")
        kk = float(self.k)
        if not (0.0 <= kk <= 1.5):
            raise ValueError(f"momentum_k must be in [0, 1.5], got {kk}")
        mx = float(self.max_ema)
        if mx < 0:
            raise ValueError(f"momentum_max_ema must be >= 0, got {mx}")
        mm = int(self.min_matches)
        if mm < 0:
            raise ValueError(f"momentum_min_matches must be >= 0, got {mm}")
        return MomentumConfig(
            enabled=bool(self.enabled),
            alpha=a,
            k=kk,
            max_ema=mx,
            min_matches=mm,
        )


def momentum_config_from_mapping(
    raw: Optional[Mapping[str, Any]],
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> MomentumConfig:
    """Читает momentum из model_config (top-level / train / legacy.train) + byLeague."""
    base: Dict[str, Any] = {}
    if raw:
        for block in (
            raw.get("momentum"),
            (raw.get("train") or {}).get("momentum") if isinstance(raw.get("train"), dict) else None,
            ((raw.get("legacy") or {}).get("train") or {}).get("momentum")
            if isinstance(raw.get("legacy"), dict)
            else None,
        ):
            if isinstance(block, dict):
                base.update(block)
        by = base.get("byLeague") or base.get("by_league") or {}
        if isinstance(by, dict):
            for key in (league_id, league_name, str(league_id or ""), str(league_name or "")):
                if key and key in by and isinstance(by[key], dict):
                    base = {**base, **by[key]}
                    break
    return MomentumConfig(
        enabled=bool(base["enabled"]) if "enabled" in base else True,
        alpha=float(base["alpha"]) if "alpha" in base else 0.30,
        k=float(base["k"]) if "k" in base else 0.80,
        max_ema=float(base["maxEma"] if "maxEma" in base else base.get("max_ema", 0.50)),
        min_matches=int(
            base["minMatches"] if "minMatches" in base else base.get("min_matches", 1)
        ),
    ).validated()


@dataclass
class TeamMomentumState:
    ema: float = 0.0
    matches_count: int = 0


@dataclass
class MomentumSnapshot:
    """Состояние EMA до матча + результат применения поправки."""

    ema_home_before: float
    ema_away_before: float
    home_matches_count: int
    away_matches_count: int
    dynamic_correction: float
    d_model_base: float
    d_model_dynamic: float
    momentum_enabled: bool
    momentum_alpha: float
    momentum_k: float
    momentum_max_ema: float
    ema_home_limited: float
    ema_away_limited: float


@dataclass
class MatchMomentumRecord(MomentumSnapshot):
    """Полная диагностика матча (до + после обновления EMA)."""

    match_key: str
    match_date: Optional[date]
    home_id: str
    away_id: str
    d_market: Optional[float] = None
    residual_match: Optional[float] = None
    ema_home_after: Optional[float] = None
    ema_away_after: Optional[float] = None
    updated_ema: bool = False


def residual_sides(d_market: float, d_model_base: float) -> Tuple[float, float, float]:
    """AC-1: Residual_match, Residual_home, Residual_away."""
    residual_match = d_market - d_model_base
    return residual_match, residual_match, -residual_match


def update_ema(ema_previous: float, team_residual: float, alpha: float) -> float:
    """AC-2: EMA_new = α·resid + (1−α)·EMA_prev."""
    return alpha * team_residual + (1.0 - alpha) * ema_previous


def limited_ema(ema_raw: float, max_ema: float) -> float:
    """AC-8."""
    return clamp(ema_raw, -max_ema, max_ema)


def dynamic_correction(
    ema_home: float,
    ema_away: float,
    *,
    k: float,
    max_ema: float,
    enabled: bool,
    home_matches: int,
    away_matches: int,
    min_matches: int,
) -> Tuple[float, float, float]:
    """Возвращает (correction, ema_home_limited, ema_away_limited)."""
    if not enabled:
        return 0.0, limited_ema(ema_home, max_ema), limited_ema(ema_away, max_ema)
    eh = limited_ema(ema_home, max_ema) if home_matches >= min_matches else 0.0
    ea = limited_ema(ema_away, max_ema) if away_matches >= min_matches else 0.0
    # если порог не достигнут — в формуле используем 0, но limited всё равно возвращаем для диагностики
    eh_use = eh if home_matches >= min_matches else 0.0
    ea_use = ea if away_matches >= min_matches else 0.0
    return k * (eh_use - ea_use), eh, ea


def apply_momentum_to_d(
    d_model_base: float,
    ema_home: float,
    ema_away: float,
    cfg: MomentumConfig,
    *,
    home_matches: int,
    away_matches: int,
) -> MomentumSnapshot:
    """AC-3 / AC-7 / AC-8."""
    cfg = cfg.validated()
    corr, eh_lim, ea_lim = dynamic_correction(
        ema_home,
        ema_away,
        k=cfg.k,
        max_ema=cfg.max_ema,
        enabled=cfg.enabled,
        home_matches=home_matches,
        away_matches=away_matches,
        min_matches=cfg.min_matches,
    )
    if not cfg.enabled:
        corr = 0.0
    d_dyn = d_model_base + corr
    return MomentumSnapshot(
        ema_home_before=ema_home,
        ema_away_before=ema_away,
        home_matches_count=home_matches,
        away_matches_count=away_matches,
        dynamic_correction=corr,
        d_model_base=d_model_base,
        d_model_dynamic=d_dyn,
        momentum_enabled=cfg.enabled,
        momentum_alpha=cfg.alpha,
        momentum_k=cfg.k,
        momentum_max_ema=cfg.max_ema,
        ema_home_limited=eh_lim,
        ema_away_limited=ea_lim,
    )


def match_key(match_date: Optional[date], home_id: str, away_id: str) -> str:
    ds = match_date.isoformat() if match_date else ""
    return f"{ds}|{home_id}|{away_id}"


@dataclass
class MomentumBook:
    """Состояние EMA команд + журнал по матчам (shadow mode всегда считает остатки)."""

    cfg: MomentumConfig
    teams: Dict[str, TeamMomentumState] = field(default_factory=dict)
    records: Dict[str, MatchMomentumRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cfg = self.cfg.validated()

    def _team(self, team_id: str) -> TeamMomentumState:
        if team_id not in self.teams:
            self.teams[team_id] = TeamMomentumState()
        return self.teams[team_id]

    def peek(
        self,
        *,
        home_id: str,
        away_id: str,
        d_model_base: float,
        match_date: Optional[date] = None,
    ) -> MomentumSnapshot:
        ht = self._team(home_id)
        at = self._team(away_id)
        return apply_momentum_to_d(
            d_model_base,
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
        residual_match: float,
    ) -> Tuple[float, float]:
        """Обновить EMA после матча. Возвращает (ema_home_after, ema_away_after)."""
        ht = self._team(home_id)
        at = self._team(away_id)
        resid_h, resid_a = residual_match, -residual_match
        ht.ema = update_ema(ht.ema, resid_h, self.cfg.alpha)
        at.ema = update_ema(at.ema, resid_a, self.cfg.alpha)
        ht.matches_count += 1
        at.matches_count += 1
        return ht.ema, at.ema


@dataclass
class MomentumWalkMatch:
    """Вход для walk-forward построения книги."""

    match_date: Optional[date]
    home_id: str
    away_id: str
    d_model_base: float
    d_market: Optional[float]  # None → прогноз с EMA, без обновления (AC-6)


def build_momentum_walk(
    matches: Sequence[MomentumWalkMatch],
    cfg: MomentumConfig,
) -> MomentumBook:
    """Строго последовательный walk-forward со срезами по дате (AC-4, AC-5, AC-9).

    Новый сезон должен передаваться отдельным вызовом (книга с нуля).
    """
    book = MomentumBook(cfg=cfg)
    # group by date (None last)
    buckets: Dict[Optional[date], List[MomentumWalkMatch]] = {}
    order: List[Optional[date]] = []
    for m in matches:
        if m.match_date not in buckets:
            buckets[m.match_date] = []
            order.append(m.match_date)
        buckets[m.match_date].append(m)
    # stable: already insertion order; sort known dates, keep None at end if mixed
    dated = [d for d in order if d is not None]
    undated = [d for d in order if d is None]
    dated_sorted = sorted(set(dated), key=lambda d: d)
    # preserve first-seen among undated
    slice_keys: List[Optional[date]] = dated_sorted + undated

    for dkey in slice_keys:
        group = buckets[dkey]
        pending_updates: List[Tuple[MomentumWalkMatch, float, MomentumSnapshot]] = []
        # 1) все прогнозы среза с EMA до среза
        for m in group:
            snap = book.peek(
                home_id=m.home_id,
                away_id=m.away_id,
                d_model_base=m.d_model_base,
                match_date=m.match_date,
            )
            key = match_key(m.match_date, m.home_id, m.away_id)
            book.records[key] = MatchMomentumRecord(
                match_key=key,
                match_date=m.match_date,
                home_id=m.home_id,
                away_id=m.away_id,
                ema_home_before=snap.ema_home_before,
                ema_away_before=snap.ema_away_before,
                home_matches_count=snap.home_matches_count,
                away_matches_count=snap.away_matches_count,
                dynamic_correction=snap.dynamic_correction,
                d_model_base=snap.d_model_base,
                d_model_dynamic=snap.d_model_dynamic,
                momentum_enabled=snap.momentum_enabled,
                momentum_alpha=snap.momentum_alpha,
                momentum_k=snap.momentum_k,
                momentum_max_ema=snap.momentum_max_ema,
                ema_home_limited=snap.ema_home_limited,
                ema_away_limited=snap.ema_away_limited,
                d_market=m.d_market,
            )
            if m.d_market is not None and m.d_market == m.d_market:
                resid, _, _ = residual_sides(m.d_market, m.d_model_base)
                pending_updates.append((m, resid, snap))
        # 2) обновления после среза
        for m, resid, snap in pending_updates:
            eh_a, ea_a = book.update_pair(m.home_id, m.away_id, resid)
            key = match_key(m.match_date, m.home_id, m.away_id)
            old = book.records[key]
            book.records[key] = MatchMomentumRecord(
                match_key=old.match_key,
                match_date=old.match_date,
                home_id=old.home_id,
                away_id=old.away_id,
                ema_home_before=old.ema_home_before,
                ema_away_before=old.ema_away_before,
                home_matches_count=old.home_matches_count,
                away_matches_count=old.away_matches_count,
                dynamic_correction=old.dynamic_correction,
                d_model_base=old.d_model_base,
                d_model_dynamic=old.d_model_dynamic,
                momentum_enabled=old.momentum_enabled,
                momentum_alpha=old.momentum_alpha,
                momentum_k=old.momentum_k,
                momentum_max_ema=old.momentum_max_ema,
                ema_home_limited=old.ema_home_limited,
                ema_away_limited=old.ema_away_limited,
                d_market=m.d_market,
                residual_match=resid,
                ema_home_after=eh_a,
                ema_away_after=ea_a,
                updated_ema=True,
            )
    return book
