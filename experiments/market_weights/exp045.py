"""EXP-045 Stage 1 — predict future market revaluation (no weighting).

Does NOT change production forecasts. Offline research only.

Label = change in causal latent team market-strength (opponent/H adjusted),
not raw next-match D (avoids opponent-calendar leakage).
"""

from __future__ import annotations

import csv
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .data import Row, fetch_all_view_rows, parse_rows, probe_readonly

OUT = Path("/opt/cursor/artifacts/exp045")
REPO_DIR = Path("/workspace/experiments/market_weights")

# Expanding OOS folds for prediction of revaluation label
FROM_MONTH = "2025-12"
TO_MONTH = "2026-02"

H_PRIOR = 0.28
K_UPDATE = 0.18  # Elo-like step toward closing-implied relative strength
VOL_WINDOW = 8
HORIZON = 3

FEATURE_NAMES = [
    "abs_d",
    "s_market",
    "r_home_pre",
    "r_away_pre",
    "abs_r_gap",
    "line_residual",  # D_mkt - (r_h - r_a + H) at t
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
    "is_small_d",  # |D|<0.25 candidate from EXP-044
]


@dataclass
class Sample:
    match_id: str
    league: str
    match_date: date
    team_id: str  # team-level samples (home & away rows)
    role: str
    features: Dict[str, float]
    y_abs: float
    y_signed: float


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _std(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _sign_pers(deltas: Sequence[float]) -> Optional[float]:
    if not deltas:
        return None
    pos = sum(1 for d in deltas if d > 1e-9)
    neg = sum(1 for d in deltas if d < -1e-9)
    return max(pos, neg) / len(deltas)


def season_stage_map(rows: Sequence[Row]) -> Dict[str, float]:
    by: Dict[Tuple[str, str], List[Row]] = defaultdict(list)
    for r in rows:
        by[(r.league_name, r.season_label or "")].append(r)
    out: Dict[str, float] = {}
    for _, grp in by.items():
        grp = sorted(grp, key=lambda x: (x.match_date, x.match_id))
        n = max(len(grp) - 1, 1)
        for i, r in enumerate(grp):
            out[r.match_id] = i / n
    return out


def build_samples(rows: Sequence[Row]) -> List[Sample]:
    """Causal latent-strength walk; emit team-match samples with future labels."""
    rows_s = sorted(rows, key=lambda x: (x.match_date, x.match_id))
    stage = season_stage_map(rows_s)

    r: Dict[str, float] = defaultdict(float)
    # per team: history of rating AFTER each of its appearances (post-update)
    team_r_post: Dict[str, List[float]] = defaultdict(list)
    team_dates: Dict[str, List[date]] = defaultdict(list)
    # map (team, match_id) -> index in team appearance list
    team_idx: Dict[Tuple[str, str], int] = {}

    # First pass: walk & store pre-state features; labels filled in second pass
    pending: List[dict] = []

    for match in rows_s:
        h, a = match.home_team_id, match.away_team_id
        rh, ra = r[h], r[a]
        H = 0.0 if match.is_neutral else H_PRIOR
        pred = rh - ra + H
        resid = match.d_market - pred

        def side_feats(tid: str, r_pre: float, other_pre: float) -> Dict[str, Optional[float]]:
            posts = team_r_post[tid]
            # deltas of post ratings
            deltas = [posts[i] - posts[i - 1] for i in range(1, len(posts))]
            vol = _std(deltas[-VOL_WINDOW:]) if deltas else None
            d1 = deltas[-1] if deltas else None
            d3 = (sum(deltas[-3:]) / len(deltas[-3:])) if deltas else None
            sp = _sign_pers(deltas[-5:]) if deltas else None
            rest = None
            if team_dates[tid]:
                rest = float((match.match_date - team_dates[tid][-1]).days)
            return {
                "r_pre": r_pre,
                "vol": vol,
                "d1": d1,
                "d3": d3,
                "sign_pers": sp,
                "rest": rest,
            }

        fh = side_feats(h, rh, ra)
        fa = side_feats(a, ra, rh)
        rests = [x for x in (fh["rest"], fa["rest"]) if x is not None]
        vols = [x for x in (fh["vol"], fa["vol"]) if x is not None]
        d1s = [abs(x) for x in (fh["d1"], fa["d1"]) if x is not None]
        d3s = [abs(x) for x in (fh["d3"], fa["d3"]) if x is not None]
        sps = [x for x in (fh["sign_pers"], fa["sign_pers"]) if x is not None]

        base = {
            "abs_d": abs(match.d_market),
            "s_market": match.s_market,
            "abs_r_gap": abs(rh - ra),
            "line_residual": resid,
            "vol_max": max(vols) if vols else float("nan"),
            "abs_d1_max": max(d1s) if d1s else float("nan"),
            "abs_d3_max": max(d3s) if d3s else float("nan"),
            "sign_pers_max": max(sps) if sps else float("nan"),
            "season_stage": stage.get(match.match_id, 0.5),
            "rest_min": min(rests) if rests else float("nan"),
            "is_small_d": 1.0 if abs(match.d_market) < 0.25 else 0.0,
        }

        for role, tid, sf, r_pre in (
            ("home", h, fh, rh),
            ("away", a, fa, ra),
        ):
            feats = dict(base)
            feats.update(
                {
                    "r_home_pre": rh,
                    "r_away_pre": ra,
                    "vol_home": fh["vol"] if fh["vol"] is not None else float("nan"),
                    "vol_away": fa["vol"] if fa["vol"] is not None else float("nan"),
                    "d1_home": fh["d1"] if fh["d1"] is not None else float("nan"),
                    "d1_away": fa["d1"] if fa["d1"] is not None else float("nan"),
                    "d3_home": fh["d3"] if fh["d3"] is not None else float("nan"),
                    "d3_away": fa["d3"] if fa["d3"] is not None else float("nan"),
                    "sign_pers_home": fh["sign_pers"] if fh["sign_pers"] is not None else float("nan"),
                    "sign_pers_away": fa["sign_pers"] if fa["sign_pers"] is not None else float("nan"),
                    # team-centric mirrors for the focal team
                    "vol_focal": sf["vol"] if sf["vol"] is not None else float("nan"),
                    "d1_focal": sf["d1"] if sf["d1"] is not None else float("nan"),
                    "r_focal_pre": r_pre,
                }
            )
            idx = len(team_r_post[tid])
            team_idx[(tid, match.match_id)] = idx
            pending.append(
                {
                    "match_id": match.match_id,
                    "league": match.league_name,
                    "match_date": match.match_date,
                    "team_id": tid,
                    "role": role,
                    "features": feats,
                    "r_pre": r_pre,
                    "team_app_idx": idx,
                }
            )

        # update ratings toward this closing line (observation of relative strength)
        r[h] = rh + K_UPDATE * resid
        r[a] = ra - K_UPDATE * resid
        team_r_post[h].append(r[h])
        team_r_post[a].append(r[a])
        team_dates[h].append(match.match_date)
        team_dates[a].append(match.match_date)

    samples: List[Sample] = []
    for p in pending:
        tid = p["team_id"]
        idx = p["team_app_idx"]
        posts = team_r_post[tid]
        # future: ratings AFTER next HORIZON appearances
        fut = posts[idx + 1 : idx + 1 + HORIZON]
        if len(fut) < HORIZON:
            continue
        y_signed = float(median(fut) - p["r_pre"])
        y_abs = abs(y_signed)
        # keep only finite named features
        feats = {k: float(p["features"].get(k, float("nan"))) for k in FEATURE_NAMES}
        samples.append(
            Sample(
                match_id=p["match_id"],
                league=p["league"],
                match_date=p["match_date"],
                team_id=tid,
                role=p["role"],
                features=feats,
                y_abs=y_abs,
                y_signed=y_signed,
            )
        )
    return samples


def pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 30:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx < 1e-12 or dy < 1e-12:
        return None
    return num / (dx * dy)


def _design(samples: Sequence[Sample], means: Dict[str, float], stds: Dict[str, float]) -> Tuple[List[List[float]], List[float], List[float]]:
    X, ya, ys = [], [], []
    for s in samples:
        row = []
        for name in FEATURE_NAMES:
            v = s.features.get(name, float("nan"))
            if v is None or (isinstance(v, float) and math.isnan(v)):
                z = 0.0
            else:
                sd = stds[name] if stds[name] > 1e-12 else 1.0
                z = (float(v) - means[name]) / sd
            row.append(z)
        # bias
        row.append(1.0)
        X.append(row)
        ya.append(s.y_abs)
        ys.append(s.y_signed)
    return X, ya, ys


def _fit_stats(samples: Sequence[Sample]) -> Tuple[Dict[str, float], Dict[str, float]]:
    means, stds = {}, {}
    for name in FEATURE_NAMES:
        xs = [s.features[name] for s in samples if not math.isnan(s.features.get(name, float("nan")))]
        if not xs:
            means[name] = 0.0
            stds[name] = 1.0
            continue
        m = sum(xs) / len(xs)
        var = sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1)
        means[name] = m
        stds[name] = var ** 0.5 if var > 0 else 1.0
    return means, stds


def ridge_fit(X: List[List[float]], y: List[float], lam: float = 1.0) -> List[float]:
    """Solve (X'X + λI) β = X'y ; do not ridge the bias column (last)."""
    import numpy as np

    A = np.asarray(X, dtype=float)
    b = np.asarray(y, dtype=float)
    p = A.shape[1]
    XtX = A.T @ A
    reg = np.eye(p) * lam
    reg[-1, -1] = 0.0
    beta = np.linalg.solve(XtX + reg, A.T @ b)
    return beta.tolist()


def ridge_predict(X: List[List[float]], beta: List[float]) -> List[float]:
    return [sum(x[j] * beta[j] for j in range(len(beta))) for x in X]


def univariate_corr(samples: Sequence[Sample], target: str) -> List[dict]:
    out = []
    ys = [s.y_abs if target == "abs" else s.y_signed for s in samples]
    for name in FEATURE_NAMES:
        xs, yy = [], []
        for s, y in zip(samples, ys):
            v = s.features.get(name, float("nan"))
            if math.isnan(v):
                continue
            xs.append(float(v))
            yy.append(y)
        out.append({"feature": name, "target": target, "n": len(xs), "pearson": pearson(xs, yy)})
    return out


def expanding_oos(
    samples: Sequence[Sample],
    *,
    from_month: str,
    to_month: str,
    lam: float = 1.0,
) -> Dict[str, Any]:
    months = sorted({_month_key(s.match_date) for s in samples})
    months = [m for m in months if from_month <= m <= to_month]
    pred_abs, act_abs, pred_signed, act_signed = [], [], [], []
    fold_rows = []
    for m in months:
        y, mo = map(int, m.split("-"))
        cut = date(y, mo, 1)
        if mo == 12:
            end = date(y + 1, 1, 1)
        else:
            end = date(y, mo + 1, 1)
        train = [s for s in samples if s.match_date < cut]
        test = [s for s in samples if cut <= s.match_date < end]
        if len(train) < 200 or len(test) < 30:
            continue
        means, stds = _fit_stats(train)
        Xtr, ya_tr, ys_tr = _design(train, means, stds)
        Xte, ya_te, ys_te = _design(test, means, stds)
        b_abs = ridge_fit(Xtr, ya_tr, lam=lam)
        b_sgn = ridge_fit(Xtr, ys_tr, lam=lam)
        pa = ridge_predict(Xte, b_abs)
        ps = ridge_predict(Xte, b_sgn)
        pred_abs.extend(pa)
        act_abs.extend(ya_te)
        pred_signed.extend(ps)
        act_signed.extend(ys_te)
        fold_rows.append(
            {
                "month": m,
                "n_train": len(train),
                "n_test": len(test),
                "r_abs": pearson(pa, ya_te),
                "r_signed": pearson(ps, ys_te),
            }
        )
    return {
        "folds": fold_rows,
        "oos_r_abs": pearson(pred_abs, act_abs),
        "oos_r_signed": pearson(pred_signed, act_signed),
        "n_oos": len(pred_abs),
        "pred_abs_mean": sum(pred_abs) / len(pred_abs) if pred_abs else None,
        "act_abs_mean": sum(act_abs) / len(act_abs) if act_abs else None,
    }


def quantile_calibration(samples: Sequence[Sample], oos: Dict[str, Any], *, from_month: str, to_month: str, lam: float = 1.0) -> List[dict]:
    """Fit on pre-from_month, score holdout window, check monotonicity of actual abs reval by predicted quantile."""
    y0 = int(from_month[:4])
    m0 = int(from_month[5:7])
    cut0 = date(y0, m0, 1)
    y1 = int(to_month[:4])
    m1 = int(to_month[5:7])
    if m1 == 12:
        end = date(y1 + 1, 1, 1)
    else:
        end = date(y1, m1 + 1, 1)
    train = [s for s in samples if s.match_date < cut0]
    test = [s for s in samples if cut0 <= s.match_date < end]
    if len(train) < 200 or len(test) < 50:
        return []
    means, stds = _fit_stats(train)
    Xtr, ya_tr, _ = _design(train, means, stds)
    Xte, ya_te, _ = _design(test, means, stds)
    beta = ridge_fit(Xtr, ya_tr, lam=lam)
    pred = ridge_predict(Xte, beta)
    paired = sorted(zip(pred, ya_te), key=lambda z: z[0])
    qn = 5
    bins = []
    n = len(paired)
    for q in range(qn):
        lo = q * n // qn
        hi = (q + 1) * n // qn
        chunk = paired[lo:hi]
        if not chunk:
            continue
        bins.append(
            {
                "quantile": q + 1,
                "n": len(chunk),
                "pred_mean": sum(p for p, _ in chunk) / len(chunk),
                "actual_abs_mean": sum(a for _, a in chunk) / len(chunk),
            }
        )
    return bins


def coverage_report(raw: Sequence[dict]) -> List[dict]:
    by: Dict[Tuple[str, str], dict] = {}
    for r in raw:
        if r.get("active") is False:
            continue
        key = (str(r.get("league_name") or ""), str(r.get("season_label") or ""))
        d = by.setdefault(
            key,
            {
                "league": key[0],
                "season": key[1],
                "n_matches": 0,
                "closing_D": 0,
                "closing_S": 0,
                "opening_D": 0,
                "opening_S": 0,
                "open_close_drift": 0,
            },
        )
        d["n_matches"] += 1
        has_ah = r.get("closing_ah_home") is not None and r.get("ah_home_odds") is not None
        has_ou = r.get("closing_total_line") is not None and r.get("over_odds") is not None
        if has_ah:
            d["closing_D"] += 1
        if has_ou:
            d["closing_S"] += 1
        # opening columns absent in current schema
    rows = []
    for d in sorted(by.values(), key=lambda x: (x["league"], x["season"])):
        n = max(d["n_matches"], 1)
        rows.append(
            {
                **d,
                "closing_D_pct": round(100 * d["closing_D"] / n, 1),
                "closing_S_pct": round(100 * d["closing_S"] / n, 1),
                "opening_D_pct": 0.0,
                "opening_S_pct": 0.0,
                "open_close_drift_pct": 0.0,
            }
        )
    return rows


def verdict_from_oos(r: Optional[float]) -> Tuple[str, str]:
    if r is None:
        return "SKIP", "insufficient OOS sample"
    ar = abs(r)
    if ar >= 0.20:
        return "PASS", f"|r|={ar:.3f} ≥ 0.20 — predictive signal exists; proceed to EXP-046 design"
    if ar >= 0.10:
        return "PARTIAL", f"|r|={ar:.3f} in [0.10, 0.20) — weak; refine features/label before weighting"
    return "FAIL", f"|r|={ar:.3f} < 0.10 — cannot reliably predict informative matches; do not build weighting layer"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print("EXP-045 Stage 1: market revaluation prediction (no weighting)", flush=True)
    print("readonly:", probe_readonly(), flush=True)

    raw = fetch_all_view_rows()
    # coverage needs all active; fetch_all already active=true
    # also need seasons without odds — fetch_all_view_rows only has SELECT with odds fields; rows still present with nulls
    cov = coverage_report(raw)
    (OUT / "coverage.json").write_text(json.dumps(cov, indent=2), encoding="utf-8")
    with (OUT / "coverage.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(cov[0].keys()))
        w.writeheader()
        w.writerows(cov)
    print("\nOdds coverage:", flush=True)
    for r in cov:
        print(
            f"  {r['league']:16} {r['season']} n={r['n_matches']:4} "
            f"closeD={r['closing_D_pct']:5}% openD={r['opening_D_pct']:5}%",
            flush=True,
        )

    rows = parse_rows(raw)
    print(f"\nfull closing samples universe: {len(rows)}", flush=True)
    samples = build_samples(rows)
    print(f"labeled team-match samples: {len(samples)}", flush=True)

    # univariate on train < FROM_MONTH
    y0, m0 = map(int, FROM_MONTH.split("-"))
    cut0 = date(y0, m0, 1)
    train_univ = [s for s in samples if s.match_date < cut0]
    uni_abs = univariate_corr(train_univ, "abs")
    uni_sgn = univariate_corr(train_univ, "signed")
    (OUT / "univariate.json").write_text(
        json.dumps({"abs": uni_abs, "signed": uni_sgn}, indent=2), encoding="utf-8"
    )
    print("\nTop univariate |r| vs y_abs (train):", flush=True)
    for c in sorted(uni_abs, key=lambda z: -(abs(z["pearson"]) if z["pearson"] is not None else -1))[:8]:
        print(f"  {c['feature']:16} r={c['pearson']}", flush=True)

    oos = expanding_oos(samples, from_month=FROM_MONTH, to_month=TO_MONTH, lam=1.0)
    (OUT / "oos.json").write_text(json.dumps(oos, indent=2), encoding="utf-8")
    print(
        f"\nOOS ridge: n={oos['n_oos']} r_abs={oos['oos_r_abs']} r_signed={oos['oos_r_signed']}",
        flush=True,
    )
    for fr in oos["folds"]:
        print(f"  fold {fr['month']}: n_test={fr['n_test']} r_abs={fr['r_abs']} r_signed={fr['r_signed']}", flush=True)

    bins = quantile_calibration(samples, oos, from_month=FROM_MONTH, to_month=TO_MONTH)
    (OUT / "quantiles.json").write_text(json.dumps(bins, indent=2), encoding="utf-8")
    mono = True
    if len(bins) >= 2:
        acts = [b["actual_abs_mean"] for b in bins]
        mono = all(acts[i] <= acts[i + 1] + 1e-9 for i in range(len(acts) - 1))
    print(f"quantile monotonicity (actual abs reval): {mono} {bins}", flush=True)

    v_abs, reason_abs = verdict_from_oos(oos.get("oos_r_abs"))
    v_sgn, reason_sgn = verdict_from_oos(oos.get("oos_r_signed"))
    # overall: require abs task (information magnitude)
    overall = v_abs
    if v_abs == "PASS" and not mono:
        overall = "PARTIAL"
        reason_abs += "; quantile monotonicity FAILED"
    verdicts = [
        {
            "experiment": "EXP-045_STAGE1_ABS",
            "verdict": v_abs,
            "reason": reason_abs,
            "oos_r": oos.get("oos_r_abs"),
            "n_oos": oos.get("n_oos"),
            "monotonic_quantiles": mono,
        },
        {
            "experiment": "EXP-045_STAGE1_SIGNED",
            "verdict": v_sgn,
            "reason": reason_sgn,
            "oos_r": oos.get("oos_r_signed"),
            "n_oos": oos.get("n_oos"),
        },
        {
            "experiment": "EXP-045_OVERALL",
            "verdict": overall,
            "reason": (
                "Market Information Model Stage1 (predict revaluation only). "
                + reason_abs
                + (" | Do NOT start EXP-046." if overall != "PASS" else " | EXP-046 weighting policy may be designed next.")
            ),
        },
        {
            "experiment": "EXP-041",
            "verdict": "CLOSED_NO_EFFECT",
            "reason": "bucket predicted-info weights; superseded by EXP-045 framing",
        },
        {
            "experiment": "EXP-042",
            "verdict": "CLOSED_NO_EFFECT",
            "reason": "crude change-point weights duplicate Dynamic D / expanding",
        },
        {
            "experiment": "EXP-043",
            "verdict": "CLOSED_NO_EFFECT",
            "reason": "team volatility decay buckets",
        },
        {
            "experiment": "EXP-044",
            "verdict": "CLOSED_FAIL",
            "reason": "D buckets FAIL; SMALL_D kept as feature candidate (is_small_d in EXP-045)",
        },
    ]
    (OUT / "verdicts.json").write_text(json.dumps(verdicts, indent=2), encoding="utf-8")

    lines = [
        "# EXP-045 Stage 1 — Market Revaluation Prediction",
        "",
        "> Narrow prior conclusion (041–044): **crude manual multipliers on current baseline add no OOS signal.**",
        "> This experiment asks a different question: can we **OOS-predict** which observations the market will treat as informative?",
        "",
        f"- Runtime: {time.time() - t0:.0f}s",
        f"- Labeled team-match samples: `{len(samples)}`",
        f"- OOS window: `{FROM_MONTH}` … `{TO_MONTH}` (expanding monthly)",
        f"- Model: standardized ridge (λ=1), separate abs / signed targets",
        f"- Label: median latent strength over next {HORIZON} appearances − pre-match strength (opponent/H adjusted Elo-like path)",
        f"- Opening lines: **not in DB** → open/close drift features unavailable",
        "",
        "## Odds coverage",
        "",
        "| League | Season | n | closing D% | closing S% | opening D% |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in cov:
        lines.append(
            f"| {r['league']} | {r['season']} | {r['n_matches']} | {r['closing_D_pct']} | {r['closing_S_pct']} | {r['opening_D_pct']} |"
        )
    lines += [
        "",
        "## OOS ridge results",
        "",
        f"- n_oos team-rows: **{oos.get('n_oos')}**",
        f"- r(pred, actual abs revaluation): **{oos.get('oos_r_abs')}**",
        f"- r(pred, actual signed revaluation): **{oos.get('oos_r_signed')}**",
        f"- Quantile monotonicity (abs): **{mono}**",
        "",
        "| Month | n_test | r_abs | r_signed |",
        "|---|---:|---:|---:|",
    ]
    for fr in oos.get("folds") or []:
        lines.append(f"| {fr['month']} | {fr['n_test']} | {fr['r_abs']} | {fr['r_signed']} |")
    if bins:
        lines += ["", "## Holdout predicted-info quantiles vs actual |reval|", ""]
        lines += ["| Q | n | pred_mean | actual_abs_mean |", "|---:|---:|---:|---:|"]
        for b in bins:
            lines.append(
                f"| {b['quantile']} | {b['n']} | {b['pred_mean']:.4f} | {b['actual_abs_mean']:.4f} |"
            )
    lines += ["", "## Verdicts", ""]
    for v in verdicts:
        lines.append(f"### {v['experiment']}: **{v['verdict']}**")
        lines.append(v.get("reason", ""))
        lines.append("")
    lines += [
        "## Roadmap",
        "",
        "| EXP | Status |",
        "|---|---|",
        "| 041 Predicted info buckets | CLOSED — NO EFFECT |",
        "| 042 Change point weights | CLOSED — NO EFFECT |",
        "| 043 Volatility decay | CLOSED — NO EFFECT |",
        "| 044 D buckets | CLOSED — FAIL; SMALL_D feature candidate |",
        "| 045 Rich market-revaluation prediction | THIS RUN |",
        "| 046 Learned weighting policy | only if 045 PASS |",
        "| 047 Interactions / league-specific | after 045–046 |",
        "",
        "Layer name: **Market Information Model** (not just match weights).",
        "",
    ]
    text = "\n".join(lines)
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    (REPO_DIR / "REPORT_EXP045.md").write_text(text, encoding="utf-8")
    print(text[:2200], flush=True)
    for v in verdicts:
        print(f"VERDICT {v['experiment']}: {v['verdict']} — {v.get('reason')}", flush=True)
    print(f"Wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
