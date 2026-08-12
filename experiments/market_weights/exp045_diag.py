"""EXP-045A/B/C — last diagnostic round for Market Information Model.

No weighting. No production changes.

045A: compare labels L1–L4
045B: compare horizons (match-count + calendar days)
045C: raw ridge vs interactions vs shallow tree

Stop-rule (must ALL hold to continue toward weighting):
  OOS |r| ≳ 0.15
  AND reasonable quantile monotonicity
  AND repeats on several temporal splits
  AND not one-league-only
Otherwise CLOSE the MIM / weighting mine.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .data import Row, fetch_all_view_rows, parse_rows, probe_readonly
from .exp045 import (
    FROM_MONTH,
    TO_MONTH,
    H_PRIOR,
    K_UPDATE,
    VOL_WINDOW,
    _month_key,
    _sign_pers,
    _std,
    pearson,
    season_stage_map,
)

OUT = Path("/opt/cursor/artifacts/exp045_diag")
REPO = Path("/workspace/experiments/market_weights")

RAW_FEATURES = [
    "abs_d",
    "s_market",
    "r_home_pre",
    "r_away_pre",
    "abs_r_gap",
    "line_residual",
    "vol_home",
    "vol_away",
    "vol_max",
    "d1_home",
    "d1_away",
    "d3_home",
    "d3_away",
    "abs_d1_max",
    "abs_d3_max",
    "sign_pers_home",
    "sign_pers_away",
    "sign_pers_max",
    "season_stage",
    "rest_min",
    "is_small_d",
    "short_long_gap",
    "same_sign_count",
]

# ≤15 pre-stated interaction hypotheses (EXP-045C)
INTERACTIONS = [
    ("abs_d", "vol_max"),
    ("abs_d", "abs_d3_max"),
    ("abs_d", "season_stage"),
    ("vol_max", "sign_pers_max"),
    ("abs_d3_max", "abs_d1_max"),
    ("is_small_d", "line_residual"),
    ("is_small_d", "abs_d3_max"),
    ("vol_max", "line_residual"),
    ("short_long_gap", "vol_max"),
    ("same_sign_count", "abs_d3_max"),
    ("rest_min", "vol_max"),
    ("season_stage", "vol_max"),
]


@dataclass
class Event:
    """One team appearance with pre-match features and post-update strength."""

    match_id: str
    league: str
    match_date: date
    team_id: str
    role: str
    features: Dict[str, float]
    r_pre: float
    r_post: float
    app_idx: int


def _ema(vals: Sequence[float], n: int) -> float:
    if not vals:
        return 0.0
    a = 2.0 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = a * v + (1.0 - a) * e
    return float(e)


def build_events(rows: Sequence[Row]) -> List[Event]:
    rows_s = sorted(rows, key=lambda x: (x.match_date, x.match_id))
    stage = season_stage_map(rows_s)
    r: Dict[str, float] = defaultdict(float)
    team_r_post: Dict[str, List[float]] = defaultdict(list)
    team_dates: Dict[str, List[date]] = defaultdict(list)
    events: List[Event] = []

    for match in rows_s:
        h, a = match.home_team_id, match.away_team_id
        rh, ra = r[h], r[a]
        H = 0.0 if match.is_neutral else H_PRIOR
        resid = match.d_market - (rh - ra + H)

        def side(tid: str) -> Dict[str, Optional[float]]:
            posts = team_r_post[tid]
            deltas = [posts[i] - posts[i - 1] for i in range(1, len(posts))]
            vol = _std(deltas[-VOL_WINDOW:]) if deltas else None
            d1 = deltas[-1] if deltas else None
            d3 = (sum(deltas[-3:]) / len(deltas[-3:])) if deltas else None
            sp = _sign_pers(deltas[-5:]) if deltas else None
            rest = None
            if team_dates[tid]:
                rest = float((match.match_date - team_dates[tid][-1]).days)
            short = _ema(posts[-3:], 3) if len(posts) >= 2 else None
            long = _ema(posts[-8:], 8) if len(posts) >= 4 else None
            gap = abs(short - long) if short is not None and long is not None else None
            same = None
            if deltas:
                tail = deltas[-5:]
                pos = sum(1 for d in tail if d > 1e-9)
                neg = sum(1 for d in tail if d < -1e-9)
                same = float(max(pos, neg))
            return {
                "vol": vol,
                "d1": d1,
                "d3": d3,
                "sign_pers": sp,
                "rest": rest,
                "short_long_gap": gap,
                "same_sign_count": same,
            }

        fh, fa = side(h), side(a)
        rests = [x for x in (fh["rest"], fa["rest"]) if x is not None]
        vols = [x for x in (fh["vol"], fa["vol"]) if x is not None]
        d1s = [abs(x) for x in (fh["d1"], fa["d1"]) if x is not None]
        d3s = [abs(x) for x in (fh["d3"], fa["d3"]) if x is not None]
        sps = [x for x in (fh["sign_pers"], fa["sign_pers"]) if x is not None]
        gaps = [x for x in (fh["short_long_gap"], fa["short_long_gap"]) if x is not None]
        sames = [x for x in (fh["same_sign_count"], fa["same_sign_count"]) if x is not None]

        base = {
            "abs_d": abs(match.d_market),
            "s_market": match.s_market,
            "r_home_pre": rh,
            "r_away_pre": ra,
            "abs_r_gap": abs(rh - ra),
            "line_residual": resid,
            "vol_home": fh["vol"] if fh["vol"] is not None else float("nan"),
            "vol_away": fa["vol"] if fa["vol"] is not None else float("nan"),
            "vol_max": max(vols) if vols else float("nan"),
            "d1_home": fh["d1"] if fh["d1"] is not None else float("nan"),
            "d1_away": fa["d1"] if fa["d1"] is not None else float("nan"),
            "d3_home": fh["d3"] if fh["d3"] is not None else float("nan"),
            "d3_away": fa["d3"] if fa["d3"] is not None else float("nan"),
            "abs_d1_max": max(d1s) if d1s else float("nan"),
            "abs_d3_max": max(d3s) if d3s else float("nan"),
            "sign_pers_home": fh["sign_pers"] if fh["sign_pers"] is not None else float("nan"),
            "sign_pers_away": fa["sign_pers"] if fa["sign_pers"] is not None else float("nan"),
            "sign_pers_max": max(sps) if sps else float("nan"),
            "season_stage": stage.get(match.match_id, 0.5),
            "rest_min": min(rests) if rests else float("nan"),
            "is_small_d": 1.0 if abs(match.d_market) < 0.25 else 0.0,
            "short_long_gap": max(gaps) if gaps else float("nan"),
            "same_sign_count": max(sames) if sames else float("nan"),
        }

        # update after capturing pre features
        r[h] = rh + K_UPDATE * resid
        r[a] = ra - K_UPDATE * resid
        for role, tid, r_pre, r_post in (
            ("home", h, rh, r[h]),
            ("away", a, ra, r[a]),
        ):
            idx = len(team_r_post[tid])
            events.append(
                Event(
                    match_id=match.match_id,
                    league=match.league_name,
                    match_date=match.match_date,
                    team_id=tid,
                    role=role,
                    features=dict(base),
                    r_pre=r_pre,
                    r_post=r_post,
                    app_idx=idx,
                )
            )
            team_r_post[tid].append(r_post)
            team_dates[tid].append(match.match_date)

    # attach team post series for label computation
    by_team: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        by_team[e.team_id].append(e)
    for tid, evs in by_team.items():
        evs.sort(key=lambda x: (x.match_date, x.match_id))
    return events


def team_series(events: Sequence[Event]) -> Dict[str, List[Event]]:
    by: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        by[e.team_id].append(e)
    for tid in by:
        by[tid].sort(key=lambda x: (x.match_date, x.match_id))
    return by


def label_value(
    ev: Event,
    series: Sequence[Event],
    *,
    kind: str,
    horizon: int,
) -> Optional[float]:
    """Return absolute label for event; None if insufficient future."""
    i = ev.app_idx
    # series indexed by app_idx
    posts = [e.r_post for e in series]
    pres = [e.r_pre for e in series]
    if kind == "L1":  # |s(t+1)-s(t)| using post at t vs post at t+1? use r_pre next vs r_pre now
        if i + 1 >= len(series):
            return None
        # strength after this obs vs after next obs
        return abs(posts[i + 1] - posts[i])
    if kind == "L2":  # |median(t+1:t+H) - strength(t)|  strength(t)=r_pre or r_post?
        fut = posts[i + 1 : i + 1 + horizon]
        if len(fut) < horizon:
            return None
        return abs(median(fut) - ev.r_pre)
    if kind == "L3":  # |EMA_future_H - EMA_past_H|
        past = posts[max(0, i - (horizon - 1)) : i + 1]  # includes current post
        fut = posts[i + 1 : i + 1 + horizon]
        if len(past) < horizon or len(fut) < horizon:
            return None
        return abs(_ema(fut, horizon) - _ema(past, horizon))
    if kind == "L4":  # persistence-adjusted: |mean future deltas| * same-sign fraction
        fut = posts[i + 1 : i + 1 + horizon]
        if len(fut) < horizon:
            return None
        # shifts relative to current post
        shifts = [p - posts[i] for p in fut]
        mag = abs(sum(shifts) / len(shifts))
        pos = sum(1 for s in shifts if s > 1e-9)
        neg = sum(1 for s in shifts if s < -1e-9)
        pers = max(pos, neg) / len(shifts)
        return mag * pers
    raise KeyError(kind)


def label_calendar(
    ev: Event,
    series: Sequence[Event],
    *,
    days: int,
) -> Optional[float]:
    """|median strength in (t, t+days] - r_pre|."""
    end = ev.match_date + timedelta(days=days)
    fut = [e.r_post for e in series if ev.match_date < e.match_date <= end]
    if len(fut) < 1:
        return None
    return abs(median(fut) - ev.r_pre)


def expand_features(feat: Dict[str, float], *, with_interactions: bool) -> Dict[str, float]:
    out = {k: float(feat.get(k, float("nan"))) for k in RAW_FEATURES}
    if with_interactions:
        for a, b in INTERACTIONS:
            va, vb = out.get(a, float("nan")), out.get(b, float("nan"))
            key = f"{a}*{b}"
            if math.isnan(va) or math.isnan(vb):
                out[key] = float("nan")
            else:
                out[key] = va * vb
    return out


def _fit_stats(rows: Sequence[Dict[str, float]], names: Sequence[str]) -> Tuple[Dict[str, float], Dict[str, float]]:
    means, stds = {}, {}
    for n in names:
        xs = [r[n] for r in rows if not math.isnan(r.get(n, float("nan")))]
        if not xs:
            means[n], stds[n] = 0.0, 1.0
            continue
        m = sum(xs) / len(xs)
        var = sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1)
        means[n] = m
        stds[n] = var ** 0.5 if var > 0 else 1.0
    return means, stds


def _matrix(
    rows: Sequence[Dict[str, float]],
    ys: Sequence[float],
    names: Sequence[str],
    means: Dict[str, float],
    stds: Dict[str, float],
) -> Tuple[np.ndarray, np.ndarray]:
    X = []
    for r in rows:
        row = []
        for n in names:
            v = r.get(n, float("nan"))
            if math.isnan(v):
                z = 0.0
            else:
                sd = stds[n] if stds[n] > 1e-12 else 1.0
                z = (v - means[n]) / sd
            row.append(z)
        row.append(1.0)
        X.append(row)
    return np.asarray(X, float), np.asarray(ys, float)


def ridge_oos(
    feat_rows: Sequence[Dict[str, float]],
    ys: Sequence[float],
    dates: Sequence[date],
    leagues: Sequence[str],
    names: Sequence[str],
    *,
    lam: float = 1.0,
) -> Dict[str, Any]:
    months = sorted({_month_key(d) for d in dates})
    months = [m for m in months if FROM_MONTH <= m <= TO_MONTH]
    pred_all, act_all, lg_all, month_all = [], [], [], []
    folds = []
    for m in months:
        y, mo = map(int, m.split("-"))
        cut = date(y, mo, 1)
        end = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
        tr_idx = [i for i, d in enumerate(dates) if d < cut]
        te_idx = [i for i, d in enumerate(dates) if cut <= d < end]
        if len(tr_idx) < 200 or len(te_idx) < 30:
            continue
        tr_feats = [feat_rows[i] for i in tr_idx]
        te_feats = [feat_rows[i] for i in te_idx]
        ytr = [ys[i] for i in tr_idx]
        yte = [ys[i] for i in te_idx]
        means, stds = _fit_stats(tr_feats, names)
        Xtr, Ytr = _matrix(tr_feats, ytr, names, means, stds)
        Xte, Yte = _matrix(te_feats, yte, names, means, stds)
        p = Xtr.shape[1]
        reg = np.eye(p) * lam
        reg[-1, -1] = 0.0
        beta = np.linalg.solve(Xtr.T @ Xtr + reg, Xtr.T @ Ytr)
        pred = (Xte @ beta).tolist()
        act = Yte.tolist()
        r = pearson(pred, act)
        folds.append({"month": m, "n_test": len(act), "r": r})
        pred_all.extend(pred)
        act_all.extend(act)
        lg_all.extend([leagues[i] for i in te_idx])
        month_all.extend([m] * len(act))
    # quantiles on pooled OOS
    bins = []
    mono = None
    if len(pred_all) >= 50:
        paired = sorted(zip(pred_all, act_all), key=lambda z: z[0])
        n = len(paired)
        for q in range(5):
            lo, hi = q * n // 5, (q + 1) * n // 5
            chunk = paired[lo:hi]
            bins.append(
                {
                    "q": q + 1,
                    "n": len(chunk),
                    "pred_mean": sum(p for p, _ in chunk) / len(chunk),
                    "act_mean": sum(a for _, a in chunk) / len(chunk),
                }
            )
        acts = [b["act_mean"] for b in bins]
        # soft mono: allow tiny noise; fail if Q1–Q3 inverted vs trend
        mono = all(acts[i] <= acts[i + 1] + 0.002 for i in range(len(acts) - 1))
        soft_ok = acts[0] <= acts[-1] + 0.002 and not (acts[0] > acts[1] and acts[1] > acts[2])
    else:
        soft_ok = False
    # per-league OOS r
    by_lg: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for p, a, lg in zip(pred_all, act_all, lg_all):
        by_lg[lg].append((p, a))
    league_r = {
        lg: pearson([p for p, _ in xs], [a for _, a in xs]) for lg, xs in sorted(by_lg.items())
    }
    # temporal repeat: how many folds with |r|>=0.10
    fold_ok = sum(1 for f in folds if f["r"] is not None and abs(f["r"]) >= 0.10)
    return {
        "n_oos": len(pred_all),
        "r": pearson(pred_all, act_all),
        "folds": folds,
        "fold_ok_ge_0_10": fold_ok,
        "n_folds": len(folds),
        "quantiles": bins,
        "mono_strict": mono,
        "mono_soft": soft_ok if bins else False,
        "league_r": league_r,
    }


def tree_oos(
    feat_rows: Sequence[Dict[str, float]],
    ys: Sequence[float],
    dates: Sequence[date],
    leagues: Sequence[str],
    names: Sequence[str],
    *,
    max_depth: int = 3,
    min_leaf: int = 40,
) -> Dict[str, Any]:
    """Shallow regression tree via recursive MSE splits (numpy only)."""

    def build(X: np.ndarray, y: np.ndarray, depth: int) -> dict:
        node = {"pred": float(y.mean()), "n": int(len(y))}
        if depth >= max_depth or len(y) < 2 * min_leaf:
            return node
        best = None
        for j in range(X.shape[1]):
            col = X[:, j]
            # try a few quantiles as thresholds
            qs = np.unique(np.quantile(col, [0.25, 0.5, 0.75]))
            for thr in qs:
                left = col <= thr
                right = ~left
                if left.sum() < min_leaf or right.sum() < min_leaf:
                    continue
                sse = (
                    ((y[left] - y[left].mean()) ** 2).sum()
                    + ((y[right] - y[right].mean()) ** 2).sum()
                )
                if best is None or sse < best[0]:
                    best = (sse, j, float(thr))
        if best is None:
            return node
        _, j, thr = best
        left = X[:, j] <= thr
        node.update(
            {
                "feat": int(j),
                "thr": thr,
                "left": build(X[left], y[left], depth + 1),
                "right": build(X[~left], y[~left], depth + 1),
            }
        )
        return node

    def predict_one(node: dict, x: np.ndarray) -> float:
        if "feat" not in node:
            return node["pred"]
        if x[node["feat"]] <= node["thr"]:
            return predict_one(node["left"], x)
        return predict_one(node["right"], x)

    months = [m for m in sorted({_month_key(d) for d in dates}) if FROM_MONTH <= m <= TO_MONTH]
    pred_all, act_all, lg_all = [], [], []
    folds = []
    for m in months:
        y, mo = map(int, m.split("-"))
        cut = date(y, mo, 1)
        end = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
        tr_idx = [i for i, d in enumerate(dates) if d < cut]
        te_idx = [i for i, d in enumerate(dates) if cut <= d < end]
        if len(tr_idx) < 200 or len(te_idx) < 30:
            continue
        tr_feats = [feat_rows[i] for i in tr_idx]
        te_feats = [feat_rows[i] for i in te_idx]
        ytr = [ys[i] for i in tr_idx]
        yte = [ys[i] for i in te_idx]
        means, stds = _fit_stats(tr_feats, names)
        Xtr, Ytr = _matrix(tr_feats, ytr, names, means, stds)
        Xte, Yte = _matrix(te_feats, yte, names, means, stds)
        # drop bias col for tree
        tree = build(Xtr[:, :-1], Ytr, 0)
        pred = [predict_one(tree, x[:-1]) for x in Xte]
        act = Yte.tolist()
        r = pearson(pred, act)
        folds.append({"month": m, "n_test": len(act), "r": r})
        pred_all.extend(pred)
        act_all.extend(act)
        lg_all.extend([leagues[i] for i in te_idx])
    bins = []
    mono = soft_ok = False
    if len(pred_all) >= 50:
        paired = sorted(zip(pred_all, act_all), key=lambda z: z[0])
        n = len(paired)
        for q in range(5):
            lo, hi = q * n // 5, (q + 1) * n // 5
            chunk = paired[lo:hi]
            bins.append(
                {
                    "q": q + 1,
                    "n": len(chunk),
                    "pred_mean": sum(p for p, _ in chunk) / len(chunk),
                    "act_mean": sum(a for _, a in chunk) / len(chunk),
                }
            )
        acts = [b["act_mean"] for b in bins]
        mono = all(acts[i] <= acts[i + 1] + 0.002 for i in range(len(acts) - 1))
        soft_ok = acts[0] <= acts[-1] + 0.002 and not (acts[0] > acts[1] and acts[1] > acts[2])
    by_lg: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for p, a, lg in zip(pred_all, act_all, lg_all):
        by_lg[lg].append((p, a))
    league_r = {
        lg: pearson([p for p, _ in xs], [a for _, a in xs]) for lg, xs in sorted(by_lg.items())
    }
    fold_ok = sum(1 for f in folds if f["r"] is not None and abs(f["r"]) >= 0.10)
    return {
        "n_oos": len(pred_all),
        "r": pearson(pred_all, act_all),
        "folds": folds,
        "fold_ok_ge_0_10": fold_ok,
        "n_folds": len(folds),
        "quantiles": bins,
        "mono_strict": mono,
        "mono_soft": soft_ok,
        "league_r": league_r,
    }


def pass_stop_rule(res: Dict[str, Any]) -> Tuple[bool, str]:
    r = res.get("r")
    if r is None:
        return False, "no OOS r"
    checks = []
    ok_r = abs(r) >= 0.15
    checks.append(f"|r|={abs(r):.3f}{'≥' if ok_r else '<'}0.15")
    ok_mono = bool(res.get("mono_soft"))
    checks.append(f"mono_soft={ok_mono}")
    ok_folds = (res.get("fold_ok_ge_0_10") or 0) >= 2
    checks.append(f"folds|r|≥0.10: {res.get('fold_ok_ge_0_10')}/{res.get('n_folds')}")
    lg = res.get("league_r") or {}
    pos = [v for v in lg.values() if v is not None and v > 0.05]
    ok_lg = len(pos) >= 2
    checks.append(f"leagues_r>0.05: {len(pos)}/{len(lg)}")
    return all([ok_r, ok_mono, ok_folds, ok_lg]), "; ".join(checks)


def build_xy(
    events: Sequence[Event],
    by_team: Dict[str, List[Event]],
    *,
    label_kind: str,
    horizon: int,
    calendar_days: Optional[int],
    with_interactions: bool,
) -> Tuple[List[Dict[str, float]], List[float], List[date], List[str]]:
    feats, ys, dates, leagues = [], [], [], []
    for e in events:
        series = by_team[e.team_id]
        if calendar_days is not None:
            y = label_calendar(e, series, days=calendar_days)
        else:
            y = label_value(e, series, kind=label_kind, horizon=horizon)
        if y is None or math.isnan(y):
            continue
        feats.append(expand_features(e.features, with_interactions=with_interactions))
        ys.append(float(y))
        dates.append(e.match_date)
        leagues.append(e.league)
    return feats, ys, dates, leagues


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print("EXP-045A/B/C diagnostics (no weighting)", flush=True)
    print("readonly:", probe_readonly(), flush=True)

    rows = parse_rows(fetch_all_view_rows())
    events = build_events(rows)
    by_team = team_series(events)
    print(f"events={len(events)} teams={len(by_team)}", flush=True)

    results: Dict[str, Any] = {"045A": {}, "045B": {}, "045C": {}}

    # --- 045A labels (horizon=3, raw ridge) ---
    print("\n=== EXP-045A labels ===", flush=True)
    for kind in ("L1", "L2", "L3", "L4"):
        feats, ys, dates, leagues = build_xy(
            events, by_team, label_kind=kind, horizon=3, calendar_days=None, with_interactions=False
        )
        names = list(RAW_FEATURES)
        res = ridge_oos(feats, ys, dates, leagues, names)
        ok, detail = pass_stop_rule(res)
        results["045A"][kind] = {**res, "stop_pass": ok, "stop_detail": detail}
        print(f"  {kind}: r={res['r']} n={res['n_oos']} stop={ok} ({detail})", flush=True)

    # pick best label by |r| for subsequent B/C
    best_label = max(results["045A"].items(), key=lambda kv: abs(kv[1]["r"] or 0.0))[0]
    print(f"best label for B/C: {best_label}", flush=True)

    # --- 045B horizons ---
    print("\n=== EXP-045B horizons ===", flush=True)
    for h in (1, 2, 3, 5):
        feats, ys, dates, leagues = build_xy(
            events, by_team, label_kind=best_label, horizon=h, calendar_days=None, with_interactions=False
        )
        res = ridge_oos(feats, ys, dates, leagues, list(RAW_FEATURES))
        ok, detail = pass_stop_rule(res)
        results["045B"][f"next_{h}"] = {**res, "stop_pass": ok, "stop_detail": detail}
        print(f"  next_{h}: r={res['r']} n={res['n_oos']} stop={ok}", flush=True)
    for days in (14, 30, 45):
        feats, ys, dates, leagues = build_xy(
            events, by_team, label_kind=best_label, horizon=3, calendar_days=days, with_interactions=False
        )
        res = ridge_oos(feats, ys, dates, leagues, list(RAW_FEATURES))
        ok, detail = pass_stop_rule(res)
        results["045B"][f"days_{days}"] = {**res, "stop_pass": ok, "stop_detail": detail}
        print(f"  days_{days}: r={res['r']} n={res['n_oos']} stop={ok}", flush=True)

    best_h_key = max(results["045B"].items(), key=lambda kv: abs(kv[1]["r"] or 0.0))[0]
    print(f"best horizon: {best_h_key}", flush=True)

    # parse best horizon back
    if best_h_key.startswith("next_"):
        h_best = int(best_h_key.split("_")[1])
        cal_best = None
    else:
        h_best = 3
        cal_best = int(best_h_key.split("_")[1])

    # --- 045C features ---
    print("\n=== EXP-045C features ===", flush=True)
    # raw
    feats, ys, dates, leagues = build_xy(
        events, by_team, label_kind=best_label, horizon=h_best, calendar_days=cal_best, with_interactions=False
    )
    res_raw = ridge_oos(feats, ys, dates, leagues, list(RAW_FEATURES))
    ok, detail = pass_stop_rule(res_raw)
    results["045C"]["ridge_raw"] = {**res_raw, "stop_pass": ok, "stop_detail": detail}
    print(f"  ridge_raw: r={res_raw['r']} stop={ok}", flush=True)

    # interactions
    feats_i, ys_i, dates_i, leagues_i = build_xy(
        events, by_team, label_kind=best_label, horizon=h_best, calendar_days=cal_best, with_interactions=True
    )
    names_i = list(feats_i[0].keys()) if feats_i else list(RAW_FEATURES)
    res_int = ridge_oos(feats_i, ys_i, dates_i, leagues_i, names_i)
    ok, detail = pass_stop_rule(res_int)
    results["045C"]["ridge_interactions"] = {**res_int, "stop_pass": ok, "stop_detail": detail}
    print(f"  ridge_interactions: r={res_int['r']} stop={ok}", flush=True)

    # shallow tree on interaction feature set
    res_tree = tree_oos(feats_i, ys_i, dates_i, leagues_i, names_i, max_depth=3, min_leaf=40)
    ok, detail = pass_stop_rule(res_tree)
    results["045C"]["shallow_tree"] = {**res_tree, "stop_pass": ok, "stop_detail": detail}
    print(f"  shallow_tree: r={res_tree['r']} stop={ok}", flush=True)

    # Overall mine decision
    any_pass = any(v.get("stop_pass") for block in results.values() for v in block.values())
    best_overall = None
    best_abs = -1.0
    for block_name, block in results.items():
        for name, res in block.items():
            rr = abs(res.get("r") or 0.0)
            if rr > best_abs:
                best_abs = rr
                best_overall = (block_name, name, res)

    if any_pass:
        overall = "CONTINUE_MIM"
        reason = f"at least one config passes stop-rule; best={best_overall[0]}/{best_overall[1]} r={best_overall[2].get('r')}"
    else:
        overall = "CLOSE_MIM"
        reason = (
            "After label/horizon/interactions/tree diagnostics, no config jointly satisfies "
            "OOS |r|≳0.15 + soft quantile mono + multi-fold + multi-league. "
            f"Best seen: {best_overall[0]}/{best_overall[1]} r={best_overall[2].get('r')} "
            f"({best_overall[2].get('stop_detail')}). "
            "Do not build weighting. Next research = new observable team state "
            "(lineups/xG/injuries/coach/style), not match weights. "
            "Opening coverage still 0% — noted as missing feature, not a free pass to keep mining."
        )

    verdicts = [
        {"experiment": "EXP-045A_LABEL", "verdict": "DONE", "best": best_label, "results": {k: {"r": v.get("r"), "stop_pass": v.get("stop_pass"), "stop_detail": v.get("stop_detail")} for k, v in results["045A"].items()}},
        {"experiment": "EXP-045B_HORIZON", "verdict": "DONE", "best": best_h_key, "results": {k: {"r": v.get("r"), "stop_pass": v.get("stop_pass"), "stop_detail": v.get("stop_detail")} for k, v in results["045B"].items()}},
        {"experiment": "EXP-045C_FEATURES", "verdict": "DONE", "results": {k: {"r": v.get("r"), "stop_pass": v.get("stop_pass"), "stop_detail": v.get("stop_detail"), "mono_soft": v.get("mono_soft")} for k, v in results["045C"].items()}},
        {"experiment": "MIM_MINE", "verdict": overall, "reason": reason},
        {"experiment": "EXP-046", "verdict": "BLOCKED" if overall == "CLOSE_MIM" else "ALLOWED", "reason": "weighting only if MIM stop-rule passes"},
    ]

    # strip huge arrays before save — already only summaries
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (OUT / "verdicts.json").write_text(json.dumps(verdicts, indent=2), encoding="utf-8")

    def fmt_r(x):
        return "—" if x is None else f"{x:.4f}"

    lines = [
        "# EXP-045A/B/C — Last diagnostic round (Market Information Model)",
        "",
        "> No weighting. Question: can we reliably OOS-predict market-informative observations?",
        "",
        f"- Runtime: {time.time() - t0:.0f}s",
        f"- Events: `{len(events)}`",
        f"- OOS window: `{FROM_MONTH}` … `{TO_MONTH}`",
        f"- Opening lines: still **absent** (0% coverage)",
        "",
        "## Stop-rule",
        "",
        "Continue only if ALL hold: `|r|≳0.15`, soft quantile mono, ≥2 temporal folds with `|r|≥0.10`, ≥2 leagues with `r>0.05`.",
        "",
        "## EXP-045A — Labels",
        "",
        "| Label | OOS r | stop | detail |",
        "|---|---:|---|---|",
    ]
    for k, v in results["045A"].items():
        lines.append(f"| {k} | {fmt_r(v.get('r'))} | {v.get('stop_pass')} | {v.get('stop_detail')} |")
    lines += ["", f"Best label: **{best_label}**", "", "## EXP-045B — Horizons", "", "| Horizon | OOS r | stop | detail |", "|---|---:|---|---|"]
    for k, v in results["045B"].items():
        lines.append(f"| {k} | {fmt_r(v.get('r'))} | {v.get('stop_pass')} | {v.get('stop_detail')} |")
    lines += ["", f"Best horizon: **{best_h_key}**", "", "## EXP-045C — Features", "", "| Model | OOS r | mono_soft | stop | detail |", "|---|---:|---|---|---|"]
    for k, v in results["045C"].items():
        lines.append(
            f"| {k} | {fmt_r(v.get('r'))} | {v.get('mono_soft')} | {v.get('stop_pass')} | {v.get('stop_detail')} |"
        )
    lines += ["", "## Mine verdict", "", f"### **{overall}**", reason, ""]
    if overall == "CLOSE_MIM":
        lines += [
            "## What this means",
            "",
            "1. Market likely *does* revalue teams after some sequences — we still cannot **predict that OOS** from pre-match fields we have.",
            "2. Closing the weighting / MIM mine is methodological honesty, not a claim that information weights are philosophically wrong.",
            "3. Next useful research is **new observables** (lineups, xG, injuries, coach/tactical state), plus opening lines if/when available.",
            "",
        ]
    text = "\n".join(lines)
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    (REPO / "REPORT_EXP045_DIAG.md").write_text(text, encoding="utf-8")
    print(text, flush=True)
    for v in verdicts:
        print(f"VERDICT {v['experiment']}: {v['verdict']} — {v.get('reason', v.get('best', ''))}", flush=True)
    print(f"Wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
