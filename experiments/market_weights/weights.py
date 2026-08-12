"""Weight multipliers for EXP-041..044 (applied on top of DB match_weight)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .data import Row
from .features import MatchFeatures, short_long_ema_break, _signal_for_team


def baseline_mult(_row: Row, _feats: Mapping[str, MatchFeatures]) -> float:
    return 1.0


# --- EXP-044 relative opponent ---

def exp044_small_d(row: Row, _feats: Mapping[str, MatchFeatures]) -> float:
    """Hypothesis A: close matches more informative."""
    ad = abs(row.d_market)
    if ad < 0.25:
        return 1.3
    if ad < 0.75:
        return 1.1
    if ad < 1.25:
        return 0.9
    return 0.7


def exp044_large_d(row: Row, _feats: Mapping[str, MatchFeatures]) -> float:
    """Hypothesis B: extremes set the scale."""
    ad = abs(row.d_market)
    if ad < 0.25:
        return 0.7
    if ad < 0.75:
        return 0.9
    if ad < 1.25:
        return 1.1
    return 1.3


def exp044_u_shape(row: Row, _feats: Mapping[str, MatchFeatures]) -> float:
    """Hypothesis C: equal + extreme useful; middle less."""
    ad = abs(row.d_market)
    if ad < 0.25 or ad >= 1.25:
        return 1.25
    if ad < 0.75:
        return 0.85
    return 0.85


# --- EXP-043 team volatility decay ---

def _vol_bucket(vol: Optional[float], q33: float, q66: float) -> str:
    if vol is None:
        return "medium"
    if vol <= q33:
        return "low"
    if vol <= q66:
        return "medium"
    return "high"


# Soft recency profiles by age buckets relative to train_cut (days)
# LOW / MED / HIGH volatility → multipliers for age>120 / 61-120 / 0-60
VOL_PROFILES = {
    "low": (0.8, 0.9, 1.0),
    "medium": (0.6, 0.8, 1.0),
    "high": (0.3, 0.6, 1.0),
}


def make_exp043_assigner(
    rows: Sequence[Row],
    feats: Mapping[str, MatchFeatures],
    train_cut: date,
) -> Dict[str, float]:
    """Precompute quality multipliers for matches with date < train_cut."""
    vols = [f.vol_max for f in feats.values() if f.vol_max is not None and f.match_date < train_cut]
    if len(vols) < 10:
        # fallback: all medium profile vs calendar age only
        q33, q66 = 0.0, 1e9
    else:
        sv = sorted(vols)
        q33 = sv[len(sv) // 3]
        q66 = sv[(2 * len(sv)) // 3]

    out: Dict[str, float] = {}
    for r in rows:
        if r.match_date >= train_cut:
            continue
        f = feats.get(r.match_id)
        vol = f.vol_max if f else None
        bucket = _vol_bucket(vol, q33, q66)
        old, mid, fresh = VOL_PROFILES[bucket]
        age = (train_cut - r.match_date).days
        if age <= 60:
            m = fresh
        elif age <= 120:
            m = mid
        else:
            m = old
        # Replace universal soft profile: use team-vol profile instead of stacking
        # on top of another 0.6/0.8/1 — DB may already have soft weights.
        # Protocol: multiplier ON TOP of DB match_weight. So we apply relative
        # adjustment vs "would-be medium profile" to avoid double soft decay.
        # medium is (0.6,0.8,1.0) — same as documented soft. Use ratio vs medium.
        med = VOL_PROFILES["medium"]
        if age <= 60:
            base_m = med[2]
            out[r.match_id] = m / base_m
        elif age <= 120:
            out[r.match_id] = m / med[1]
        else:
            out[r.match_id] = m / med[0]
    return out


# --- EXP-042 change point ---

def make_exp042_assigner(
    rows: Sequence[Row],
    train_cut: date,
    *,
    pre_break_mult: float = 0.3,
    threshold: float = 0.35,
) -> Dict[str, float]:
    """Downweight matches before a causally detected market regime break."""
    by_league: Dict[str, List[Row]] = defaultdict(list)
    for r in rows:
        by_league[r.league_name].append(r)

    # team -> list of (date, match_id, signal) chronologically within league universe
    team_series: Dict[str, List[Tuple[date, str, float]]] = defaultdict(list)
    for r in sorted(rows, key=lambda x: (x.match_date, x.match_id)):
        for tid in (r.home_team_id, r.away_team_id):
            team_series[tid].append((r.match_date, r.match_id, _signal_for_team(r, tid)))

    # For each team, causal break flags per match index
    pre_break_matches: Dict[str, float] = {}
    for tid, series in team_series.items():
        # Only use points before train_cut to define breaks for training weights
        sigs = [s for (d, mid, s) in series]
        flags = short_long_ema_break(sigs, threshold=threshold)
        # Find first index where broken becomes True; matches before that get downweight
        # Actually flags[i]=True means break already active before i.
        # We want: once break detected at some i0, all matches with index < i0 get pre_break_mult
        # when used as training data for cut.
        first_break = None
        for i, fr in enumerate(flags):
            if fr:
                first_break = i
                break
        if first_break is None:
            continue
        # sticky: everything strictly before first_break index is "old regime"
        for i, (d, mid, _s) in enumerate(series):
            if d >= train_cut:
                continue
            if i < first_break:
                # keep strongest downweight if multiple teams mark the match
                prev = pre_break_matches.get(mid, 1.0)
                pre_break_matches[mid] = min(prev, pre_break_mult)

    out: Dict[str, float] = {}
    for r in rows:
        if r.match_date >= train_cut:
            continue
        out[r.match_id] = pre_break_matches.get(r.match_id, 1.0)
    return out


# --- EXP-041 predicted information ---

FEATURE_KEYS = (
    "abs_d",
    "s_market",
    "season_stage",
    "rest_min",
    "vol_max",
    "abs_delta1_max",
    "abs_delta3_max",
    "sign_consistency_max",
)


def feature_vector(f: MatchFeatures) -> Dict[str, Optional[float]]:
    rests = [x for x in (f.rest_days_home, f.rest_days_away) if x is not None]
    d1 = [abs(x) for x in (f.delta1_home, f.delta1_away) if x is not None]
    d3 = [abs(x) for x in (f.delta3_mean_home, f.delta3_mean_away) if x is not None]
    sc = [x for x in (f.sign_consistency_home, f.sign_consistency_away) if x is not None]
    return {
        "abs_d": f.abs_d,
        "s_market": f.s_market,
        "season_stage": f.season_stage,
        "rest_min": min(rests) if rests else None,
        "vol_max": f.vol_max,
        "abs_delta1_max": max(d1) if d1 else None,
        "abs_delta3_max": max(d3) if d3 else None,
        "sign_consistency_max": max(sc) if sc else None,
    }


def pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 20:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx < 1e-12 or dy < 1e-12:
        return None
    return num / (dx * dy)


def correlation_study(
    rows: Sequence[Row],
    feats: Mapping[str, MatchFeatures],
    *,
    train_to: date,
) -> List[dict]:
    """Correlate pre-match features with future_reval_max on train window."""
    samples = []
    for r in rows:
        if r.match_date >= train_to:
            continue
        f = feats.get(r.match_id)
        if not f or f.future_reval_max is None:
            continue
        vec = feature_vector(f)
        samples.append((vec, f.future_reval_max, r.league_name))

    results = []
    for key in FEATURE_KEYS:
        xs, ys = [], []
        for vec, y, _lg in samples:
            if vec.get(key) is None:
                continue
            xs.append(float(vec[key]))
            ys.append(float(y))
        r = pearson(xs, ys)
        results.append({"feature": key, "n": len(xs), "pearson": r})

    # per-league for best overall feature later
    by_lg: Dict[str, List[Tuple[dict, float]]] = defaultdict(list)
    for vec, y, lg in samples:
        by_lg[lg].append((vec, y))
    return results


def make_exp041_assigner(
    rows: Sequence[Row],
    feats: Mapping[str, MatchFeatures],
    train_cut: date,
    *,
    feature: str = "vol_max",
) -> Dict[str, float]:
    """Tercile buckets on a single causal feature → 0.7 / 1.0 / 1.3.

    Thresholds fit on matches with date < train_cut and available label optional
    (thresholds from feature distribution only — no label leakage into buckets).
    """
    train_vals = []
    for r in rows:
        if r.match_date >= train_cut:
            continue
        f = feats.get(r.match_id)
        if not f:
            continue
        v = feature_vector(f).get(feature)
        if v is None:
            continue
        train_vals.append(float(v))
    if len(train_vals) < 30:
        return {r.match_id: 1.0 for r in rows if r.match_date < train_cut}

    sv = sorted(train_vals)
    t1 = sv[len(sv) // 3]
    t2 = sv[(2 * len(sv)) // 3]

    out: Dict[str, float] = {}
    for r in rows:
        if r.match_date >= train_cut:
            continue
        f = feats.get(r.match_id)
        v = feature_vector(f).get(feature) if f else None
        if v is None:
            out[r.match_id] = 1.0
        elif v <= t1:
            out[r.match_id] = 0.7
        elif v <= t2:
            out[r.match_id] = 1.0
        else:
            out[r.match_id] = 1.3
    return out


SCHEMES = {
    "BASELINE": None,  # all 1.0
    "EXP044_SMALL_D": exp044_small_d,
    "EXP044_LARGE_D": exp044_large_d,
    "EXP044_USHAPE": exp044_u_shape,
}
