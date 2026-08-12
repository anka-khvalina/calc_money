"""Causal market-strength paths and pre-match features (no future leakage)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from .data import Row


@dataclass
class TeamPoint:
    match_id: str
    match_date: date
    signal: float  # team-centric market signal at this match


@dataclass
class MatchFeatures:
    match_id: str
    league: str
    match_date: date
    abs_d: float
    s_market: float
    season_stage: float  # 0..1 within season_label
    rest_days_home: Optional[float]
    rest_days_away: Optional[float]
    vol_home: Optional[float]
    vol_away: Optional[float]
    vol_max: Optional[float]
    delta1_home: Optional[float]
    delta1_away: Optional[float]
    delta3_mean_home: Optional[float]
    delta3_mean_away: Optional[float]
    sign_consistency_home: Optional[float]
    sign_consistency_away: Optional[float]
    # labels (train-only; may be None if not enough future)
    future_reval_home: Optional[float] = None
    future_reval_away: Optional[float] = None
    future_reval_max: Optional[float] = None


def _signal_for_team(row: Row, team_id: str) -> float:
    """Rough market strength observation for a team in a match.

    Home: −AH (expected GD from home view).
    Away: +AH (mirror). Neutral-ish H is absorbed into noise — OK for volatility/change.
    """
    if team_id == row.home_team_id:
        return row.d_market
    return -row.d_market


def build_team_histories(rows: Sequence[Row]) -> Dict[str, List[TeamPoint]]:
    hist: Dict[str, List[TeamPoint]] = defaultdict(list)
    for r in sorted(rows, key=lambda x: (x.match_date, x.match_id)):
        for tid in (r.home_team_id, r.away_team_id):
            hist[tid].append(
                TeamPoint(match_id=r.match_id, match_date=r.match_date, signal=_signal_for_team(r, tid))
            )
    return hist


def _std(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return var ** 0.5


def _deltas(signals: Sequence[float]) -> List[float]:
    return [signals[i] - signals[i - 1] for i in range(1, len(signals))]


def _sign_consistency(deltas: Sequence[float]) -> Optional[float]:
    if not deltas:
        return None
    pos = sum(1 for d in deltas if d > 1e-9)
    neg = sum(1 for d in deltas if d < -1e-9)
    n = len(deltas)
    return max(pos, neg) / n


def _rest_days(prev: Optional[date], cur: date) -> Optional[float]:
    if prev is None:
        return None
    return float((cur - prev).days)


def _season_stage_map(rows: Sequence[Row]) -> Dict[str, float]:
    """Match_id → stage in [0,1] within league+season_label by chronological index."""
    by: Dict[Tuple[str, str], List[Row]] = defaultdict(list)
    for r in rows:
        by[(r.league_name, r.season_label or "")].append(r)
    out: Dict[str, float] = {}
    for key, grp in by.items():
        grp = sorted(grp, key=lambda x: (x.match_date, x.match_id))
        n = max(len(grp) - 1, 1)
        for i, r in enumerate(grp):
            out[r.match_id] = i / n
    return out


def compute_features(
    rows: Sequence[Row],
    *,
    vol_window: int = 8,
    future_horizon: int = 3,
    past_horizon: int = 3,
) -> Dict[str, MatchFeatures]:
    """Causal features at kickoff of each match; future_reval labels attached for research."""
    rows_sorted = sorted(rows, key=lambda x: (x.match_date, x.match_id))
    stage = _season_stage_map(rows_sorted)
    # per-team running history of signals BEFORE appending current
    team_signals: Dict[str, List[float]] = defaultdict(list)
    team_dates: Dict[str, List[date]] = defaultdict(list)
    # store index of each match in team history after append for future label
    team_match_index: Dict[str, Dict[str, int]] = defaultdict(dict)

    feats: Dict[str, MatchFeatures] = {}

    for r in rows_sorted:
        # features from history before this match
        def side(tid: str) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
            sigs = team_signals[tid]
            dates = team_dates[tid]
            rest = _rest_days(dates[-1] if dates else None, r.match_date)
            deltas = _deltas(sigs)
            vol = _std(deltas[-vol_window:]) if deltas else None
            d1 = deltas[-1] if deltas else None
            d3 = None
            if len(deltas) >= 1:
                tail = deltas[-min(3, len(deltas)) :]
                d3 = sum(tail) / len(tail)
            cons = _sign_consistency(deltas[-5:]) if deltas else None
            return rest, vol, d1, d3, cons

        rh, vh, d1h, d3h, ch = side(r.home_team_id)
        ra, va, d1a, d3a, ca = side(r.away_team_id)
        vols = [v for v in (vh, va) if v is not None]
        feats[r.match_id] = MatchFeatures(
            match_id=r.match_id,
            league=r.league_name,
            match_date=r.match_date,
            abs_d=abs(r.d_market),
            s_market=r.s_market,
            season_stage=stage.get(r.match_id, 0.5),
            rest_days_home=rh,
            rest_days_away=ra,
            vol_home=vh,
            vol_away=va,
            vol_max=max(vols) if vols else None,
            delta1_home=d1h,
            delta1_away=d1a,
            delta3_mean_home=d3h,
            delta3_mean_away=d3a,
            sign_consistency_home=ch,
            sign_consistency_away=ca,
        )

        # append current observations
        for tid in (r.home_team_id, r.away_team_id):
            team_match_index[tid][r.match_id] = len(team_signals[tid])
            team_signals[tid].append(_signal_for_team(r, tid))
            team_dates[tid].append(r.match_date)

    # future revaluation labels (for correlation / train of predicted weight only)
    for r in rows_sorted:
        f = feats[r.match_id]

        def reval(tid: str) -> Optional[float]:
            idx = team_match_index[tid].get(r.match_id)
            if idx is None:
                return None
            sigs = team_signals[tid]
            # before: median of past_horizon signals strictly before current
            before_slice = sigs[max(0, idx - past_horizon) : idx]
            after_slice = sigs[idx + 1 : idx + 1 + future_horizon]
            if len(before_slice) < 1 or len(after_slice) < future_horizon:
                return None
            return abs(median(after_slice) - median(before_slice))

        fh = reval(r.home_team_id)
        fa = reval(r.away_team_id)
        f.future_reval_home = fh
        f.future_reval_away = fa
        vals = [x for x in (fh, fa) if x is not None]
        f.future_reval_max = max(vals) if vals else None

    return feats


def short_long_ema_break(
    signals: Sequence[float],
    *,
    short: int = 3,
    long: int = 8,
    threshold: float = 0.35,
    persist: int = 2,
) -> List[bool]:
    """Return per-index flag: change_point detected using only signals[:i] (causal).

    Flag at index i means: before observing i, a break was already active
    (based on history ending at i-1).
    """
    flags = [False] * len(signals)
    if len(signals) < long + persist:
        return flags

    def ema(vals: Sequence[float], n: int) -> float:
        a = 2 / (n + 1)
        e = vals[0]
        for v in vals[1:]:
            e = a * v + (1 - a) * e
        return e

    streak = 0
    broken = False
    for i in range(len(signals)):
        # detection uses history before i
        hist = list(signals[:i])
        flags[i] = broken
        if len(hist) < long:
            continue
        e_s = ema(hist[-short:], short) if len(hist) >= short else ema(hist, len(hist))
        e_l = ema(hist[-long:], long)
        if abs(e_s - e_l) >= threshold:
            streak += 1
        else:
            streak = 0
        if streak >= persist:
            broken = True
        # optional: recover if gap closes for a while — keep sticky once broken for simplicity
    return flags
