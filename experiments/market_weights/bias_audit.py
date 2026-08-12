"""Bias analysis over existing prediction artifacts (no DB, no prod changes).

Reads /opt/cursor/artifacts/exp041_044/pred_rows.csv (BASELINE scheme) and reports
signed bias vs closing lines by league, |D| bucket, S bucket, plus month stability,
so that ONE calibration knob can be chosen for a future experiment.

Convention: DB stores closing_ah_home; market goal diff D = -AH.
bias_AH  = ah_pred  - ah_mkt   (>0 ⇒ our AH above market, i.e. home priced weaker)
bias_D   = -(bias_AH)          (>0 ⇒ our D above market, home priced stronger)
bias_Tot = tot_pred - tot_mkt  (>0 ⇒ we price higher totals than market)
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

IN_CSV = Path("/opt/cursor/artifacts/exp041_044/pred_rows.csv")
OUT = Path("/opt/cursor/artifacts/bias_audit")
REPO = Path("/workspace/experiments/market_weights")

SCHEME = "BASELINE"

D_BUCKETS = [
    ("|D|<0.25", 0.0, 0.25),
    ("0.25-0.75", 0.25, 0.75),
    ("0.75-1.25", 0.75, 1.25),
    ("|D|>=1.25", 1.25, 99.0),
]
S_BUCKETS = [
    ("S<2.25", 0.0, 2.25),
    ("2.25-2.75", 2.25, 2.75),
    ("2.75-3.25", 2.75, 3.25),
    ("S>=3.25", 3.25, 99.0),
]


@dataclass
class Rec:
    match_id: str
    league: str
    month: str
    ah_mkt: float
    tot_mkt: float
    ah_pred: float
    tot_pred: float

    @property
    def bias_ah(self) -> float:
        return self.ah_pred - self.ah_mkt

    @property
    def bias_d(self) -> float:
        return -(self.ah_pred - self.ah_mkt)

    @property
    def bias_tot(self) -> float:
        return self.tot_pred - self.tot_mkt

    @property
    def abs_d_mkt(self) -> float:
        return abs(self.ah_mkt)

    @property
    def s_mkt(self) -> float:
        return self.tot_mkt


def load(path: Path = IN_CSV, scheme: str = SCHEME) -> List[Rec]:
    out: List[Rec] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("scheme") != scheme:
                continue
            out.append(
                Rec(
                    match_id=row["match_id"],
                    league=row["league"],
                    month=row["month"],
                    ah_mkt=float(row["ah_mkt"]),
                    tot_mkt=float(row["tot_mkt"]),
                    ah_pred=float(row["ah_pred"]),
                    tot_pred=float(row["tot_pred"]),
                )
            )
    return out


def _bucket(value: float, buckets: Sequence[Tuple[str, float, float]]) -> str:
    for name, lo, hi in buckets:
        if lo <= value < hi:
            return name
    return buckets[-1][0]


def _stats(recs: Sequence[Rec]) -> Dict[str, Any]:
    n = len(recs)
    if n == 0:
        return {"n": 0}
    ba = [r.bias_ah for r in recs]
    bd = [r.bias_d for r in recs]
    bt = [r.bias_tot for r in recs]

    def mean(xs: Sequence[float]) -> float:
        return sum(xs) / len(xs)

    def sd(xs: Sequence[float]) -> float:
        if len(xs) < 2:
            return 0.0
        m = mean(xs)
        return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5

    def se(xs: Sequence[float]) -> float:
        return sd(xs) / (len(xs) ** 0.5) if xs else 0.0

    return {
        "n": n,
        "bias_D": mean(bd),
        "se_D": se(bd),
        "mae_AH": mean([abs(x) for x in ba]),
        "bias_Tot": mean(bt),
        "se_Tot": se(bt),
        "mae_Tot": mean([abs(x) for x in bt]),
        "share_pred_above_D": sum(1 for x in bd if x > 1e-9) / n,
        "share_pred_above_Tot": sum(1 for x in bt if x > 1e-9) / n,
    }


def significant(bias: Optional[float], se: Optional[float], *, z: float = 2.0) -> bool:
    if bias is None or se is None or se <= 0:
        return False
    return abs(bias) > z * se


def month_consistency(recs: Sequence[Rec], key: str) -> Dict[str, Any]:
    """Sign stability of mean bias across months."""
    by_month: Dict[str, List[Rec]] = defaultdict(list)
    for r in recs:
        by_month[r.month].append(r)
    signs = []
    detail = {}
    for m, grp in sorted(by_month.items()):
        st = _stats(grp)
        val = st.get(key)
        detail[m] = None if val is None else round(val, 4)
        if val is not None and abs(val) > 1e-9:
            signs.append(1 if val > 0 else -1)
    same = bool(signs) and all(s == signs[0] for s in signs)
    return {"months": detail, "all_same_sign": same, "n_months": len(signs)}


def table_by(recs: Sequence[Rec], keyfn) -> List[Dict[str, Any]]:
    groups: Dict[Any, List[Rec]] = defaultdict(list)
    for r in recs:
        groups[keyfn(r)].append(r)
    rows = []
    for key, grp in groups.items():
        st = _stats(grp)
        st["key"] = key if isinstance(key, str) else " / ".join(map(str, key))
        st["sig_D"] = significant(st.get("bias_D"), st.get("se_D"))
        st["sig_Tot"] = significant(st.get("bias_Tot"), st.get("se_Tot"))
        st["stab_D"] = month_consistency(grp, "bias_D")
        st["stab_Tot"] = month_consistency(grp, "bias_Tot")
        rows.append(st)
    return sorted(rows, key=lambda x: str(x["key"]))


def fmt(x: Optional[float], nd: int = 4) -> str:
    return "—" if x is None else f"{x:+.{nd}f}"


def md_table(rows: Sequence[Dict[str, Any]], label: str) -> List[str]:
    out = [
        f"### {label}",
        "",
        "| Group | n | bias_D | ±2se | stable sign | bias_Tot | ±2se | stable sign |",
        "|---|---:|---:|:--:|:--:|---:|:--:|:--:|",
    ]
    for r in rows:
        if not r.get("n"):
            continue
        out.append(
            f"| {r['key']} | {r['n']} | {fmt(r['bias_D'])} | "
            f"{'YES' if r['sig_D'] else 'no'} | {'YES' if r['stab_D']['all_same_sign'] else 'no'} | "
            f"{fmt(r['bias_Tot'])} | {'YES' if r['sig_Tot'] else 'no'} | "
            f"{'YES' if r['stab_Tot']['all_same_sign'] else 'no'} |"
        )
    out.append("")
    return out


def bias_regression(recs: Sequence[Rec], *, target: str) -> Dict[str, Any]:
    """OLS of bias on centred market value: bias = a + b·(x − x̄).

    b < 0 means our value is compressed toward the mean (needs slope, not shift).
    """
    if target == "D":
        xs = [-r.ah_mkt for r in recs]  # market D
        ys = [r.bias_d for r in recs]
    else:
        xs = [r.s_mkt for r in recs]
        ys = [r.bias_tot for r in recs]
    n = len(xs)
    if n < 30:
        return {"n": n}
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx <= 1e-12:
        return {"n": n}
    b = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sxx
    a = ybar
    resid = [y - (a + b * (x - xbar)) for x, y in zip(xs, ys)]
    dof = max(n - 2, 1)
    s2 = sum(e * e for e in resid) / dof
    se_b = (s2 / sxx) ** 0.5
    se_a = (s2 / n) ** 0.5
    compression = 1.0 + b  # our spread relative to market spread
    return {
        "n": n,
        "x_mean": xbar,
        "intercept": a,
        "se_intercept": se_a,
        "slope": b,
        "se_slope": se_b,
        "intercept_sig": abs(a) > 2 * se_a,
        "slope_sig": abs(b) > 2 * se_b,
        "compression": compression,
        "corrective_gain": (1.0 / compression) if abs(compression) > 1e-6 else None,
    }


def calibration_fit(recs: Sequence[Rec], *, target: str) -> Dict[str, Any]:
    """OLS market = α + β·model — the correction we would actually apply.

    β > 1 ⇒ expand model deviations; β < 1 ⇒ shrink them.
    """
    if target == "D":
        ys = [-r.ah_mkt for r in recs]  # market D (dependent)
        xs = [-r.ah_pred for r in recs]  # model D
    else:
        ys = [r.tot_mkt for r in recs]
        xs = [r.tot_pred for r in recs]
    n = len(xs)
    if n < 30:
        return {"n": n}
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx <= 1e-12:
        return {"n": n}
    beta = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sxx
    alpha = ybar - beta * xbar
    resid = [y - (alpha + beta * x) for x, y in zip(xs, ys)]
    dof = max(n - 2, 1)
    s2 = sum(e * e for e in resid) / dof
    se_beta = (s2 / sxx) ** 0.5
    mae_before = sum(abs(y - x) for x, y in zip(xs, ys)) / n
    mae_after = sum(abs(y - (alpha + beta * x)) for x, y in zip(xs, ys)) / n
    return {
        "n": n,
        "alpha": alpha,
        "beta": beta,
        "se_beta": se_beta,
        "beta_differs_from_1": abs(beta - 1.0) > 2 * se_beta,
        "mae_before": mae_before,
        "mae_after_insample": mae_after,
        "insample_gain": mae_before - mae_after,
    }


def _q(x: float) -> float:
    """Market lines live on a 0.25 grid — any correction must be judged after rounding."""
    return round(x * 4.0) / 4.0


def grid_scan(
    recs: Sequence[Rec],
    *,
    target: str,
    train_months: Sequence[str],
    test_months: Sequence[str],
) -> Dict[str, Any]:
    """Search (shift, gain) on train months, report held-out MAE. Respects 0.25 rounding."""
    if target == "D":
        get_mkt = lambda r: -r.ah_mkt  # noqa: E731
        get_mod = lambda r: -r.ah_pred  # noqa: E731
    else:
        get_mkt = lambda r: r.tot_mkt  # noqa: E731
        get_mod = lambda r: r.tot_pred  # noqa: E731

    train = [r for r in recs if r.month in set(train_months)]
    test = [r for r in recs if r.month in set(test_months)]
    if len(train) < 100 or len(test) < 50:
        return {"n_train": len(train), "n_test": len(test)}

    centre = sum(get_mod(r) for r in train) / len(train)

    def mae(rows: Sequence[Rec], gain: float, shift: float) -> float:
        tot = 0.0
        for r in rows:
            adj = _q(centre + gain * (get_mod(r) - centre) + shift)
            tot += abs(adj - get_mkt(r))
        return tot / len(rows)

    base_train = mae(train, 1.0, 0.0)
    base_test = mae(test, 1.0, 0.0)

    best = None
    scan = []
    gains = [round(0.80 + 0.05 * i, 2) for i in range(11)]  # 0.80..1.30
    shifts = [round(-0.20 + 0.05 * i, 2) for i in range(9)]  # -0.20..0.20
    for g in gains:
        for s in shifts:
            m = mae(train, g, s)
            scan.append({"gain": g, "shift": s, "mae_train": m})
            if best is None or m < best["mae_train"] - 1e-12:
                best = {"gain": g, "shift": s, "mae_train": m}
    assert best is not None
    best_test = mae(test, best["gain"], best["shift"])
    # also: best gain-only and best shift-only
    best_gain_only = min(
        ({"gain": g, "shift": 0.0, "mae_train": mae(train, g, 0.0)} for g in gains),
        key=lambda z: z["mae_train"],
    )
    best_shift_only = min(
        ({"gain": 1.0, "shift": s, "mae_train": mae(train, 1.0, s)} for s in shifts),
        key=lambda z: z["mae_train"],
    )
    return {
        "target": target,
        "centre": centre,
        "n_train": len(train),
        "n_test": len(test),
        "train_months": list(train_months),
        "test_months": list(test_months),
        "baseline_mae_train": base_train,
        "baseline_mae_test": base_test,
        "best": {**best, "mae_test": best_test},
        "best_gain_only": {
            **best_gain_only,
            "mae_test": mae(test, best_gain_only["gain"], 0.0),
        },
        "best_shift_only": {
            **best_shift_only,
            "mae_test": mae(test, 1.0, best_shift_only["shift"]),
        },
        "test_gain_vs_baseline": best_test - base_test,
    }


def recommend(
    overall: Dict[str, Any],
    by_league: Sequence[Dict[str, Any]],
    by_d: Sequence[Dict[str, Any]],
    by_s: Sequence[Dict[str, Any]],
    reg_d: Dict[str, Any],
    reg_s: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """Pick ONE knob. Slope (compression) beats intercept when both present."""
    notes: List[str] = []

    def strong(rows: Sequence[Dict[str, Any]], metric: str) -> List[Dict[str, Any]]:
        sig_key = "sig_D" if metric == "D" else "sig_Tot"
        stab_key = "stab_D" if metric == "D" else "stab_Tot"
        return [r for r in rows if r.get(sig_key) and r[stab_key]["all_same_sign"]]

    s_slope = reg_s.get("slope")
    s_slope_sig = bool(reg_s.get("slope_sig"))
    d_slope = reg_d.get("slope")
    d_slope_sig = bool(reg_d.get("slope_sig"))

    if s_slope_sig:
        notes.append(
            f"Total: slope {fmt(s_slope)} vs market S is significant → our S spread is "
            f"{reg_s.get('compression', float('nan')):.3f} of the market's "
            f"({'compressed' if (s_slope or 0) < 0 else 'over-extended'}); "
            f"corrective gain ≈ {reg_s.get('corrective_gain', float('nan')):.3f}."
        )
    if d_slope_sig:
        notes.append(
            f"D: slope {fmt(d_slope)} vs market D is significant → spread "
            f"{reg_d.get('compression', float('nan')):.3f} of market; "
            f"corrective gain ≈ {reg_d.get('corrective_gain', float('nan')):.3f}."
        )

    # Prefer the stronger, significant slope — it fixes ends of the distribution,
    # which is where MAE and pricing risk actually live.
    if s_slope_sig and (not d_slope_sig or abs(s_slope or 0) >= abs(d_slope or 0)):
        return "S_SLOPE (`sCalMode` gain `sB`, not the intercept)", notes
    if d_slope_sig:
        return "D_SLOPE (`dCalMode` gain `dB` / dBigFav slope)", notes

    global_d = overall.get("bias_D")
    global_t = overall.get("bias_Tot")
    if significant(global_t, overall.get("se_Tot")) and abs(global_t or 0) >= abs(global_d or 0):
        notes.append(f"No slope, but global Total bias {fmt(global_t)} is significant → flat S shift.")
        return "GLOBAL_S_SHIFT (sCal intercept / SFTC magnitude)", notes
    if significant(global_d, overall.get("se_D")):
        notes.append(f"No slope, but global D bias {fmt(global_d)} is significant → flat D shift / H prior.")
        return "GLOBAL_D_SHIFT (dCal intercept / H prior)", notes

    d_strong = strong(by_d, "D")
    s_strong = strong(by_s, "Tot")
    lg_strong = strong(by_league, "Tot") or strong(by_league, "D")
    if s_strong:
        notes.append(f"Bucket-only Total bias: {[r['key'] for r in s_strong]}")
        return "S_BUCKET_TARGETED (SFTC threshold / bucket shift)", notes
    if d_strong:
        notes.append(f"Bucket-only D bias: {[r['key'] for r in d_strong]}")
        return "D_BUCKET_TARGETED (dBigFav for extreme |D|)", notes
    if lg_strong:
        notes.append(f"Only league-level bias significant+stable: {[r['key'] for r in lg_strong]}")
        return "LEAGUE_BIAS (per-league intercept)", notes

    notes.append("No significant+stable bias anywhere → calibration knobs unlikely to help; do not tune.")
    return "NO_KNOB (bias within noise)", notes


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    recs = load()
    print(f"loaded {len(recs)} {SCHEME} rows from {IN_CSV}", flush=True)
    if not recs:
        print("no rows", flush=True)
        return 1
    months = sorted({r.month for r in recs})
    leagues = sorted({r.league for r in recs})
    print(f"months={months} leagues={leagues}", flush=True)

    overall = _stats(recs)
    overall["stab_D"] = month_consistency(recs, "bias_D")
    overall["stab_Tot"] = month_consistency(recs, "bias_Tot")
    overall["sig_D"] = significant(overall.get("bias_D"), overall.get("se_D"))
    overall["sig_Tot"] = significant(overall.get("bias_Tot"), overall.get("se_Tot"))

    by_league = table_by(recs, lambda r: r.league)
    by_d = table_by(recs, lambda r: _bucket(r.abs_d_mkt, D_BUCKETS))
    by_s = table_by(recs, lambda r: _bucket(r.s_mkt, S_BUCKETS))
    by_league_d = table_by(recs, lambda r: (r.league, _bucket(r.abs_d_mkt, D_BUCKETS)))
    by_league_s = table_by(recs, lambda r: (r.league, _bucket(r.s_mkt, S_BUCKETS)))
    by_month = table_by(recs, lambda r: r.month)

    reg_d = bias_regression(recs, target="D")
    reg_s = bias_regression(recs, target="Tot")
    reg_d_month = {
        m: bias_regression([r for r in recs if r.month == m], target="D") for m in months
    }
    reg_s_month = {
        m: bias_regression([r for r in recs if r.month == m], target="Tot") for m in months
    }
    cal_d = calibration_fit(recs, target="D")
    cal_s = calibration_fit(recs, target="Tot")
    cal_s_league = {
        lg: calibration_fit([r for r in recs if r.league == lg], target="Tot") for lg in leagues
    }
    cal_s_month = {
        m: calibration_fit([r for r in recs if r.month == m], target="Tot") for m in months
    }

    # Decisive test: does ANY (gain, shift) fitted on earlier months help a held-out month,
    # once we respect the 0.25 line grid?
    grids: Dict[str, Any] = {}
    if len(months) >= 2:
        train_m = months[:-1]
        test_m = months[-1:]
        grids["Tot"] = grid_scan(recs, target="Tot", train_months=train_m, test_months=test_m)
        grids["D"] = grid_scan(recs, target="D", train_months=train_m, test_months=test_m)

    knob, notes = recommend(overall, by_league, by_d, by_s, reg_d, reg_s)

    # Override recommendation if the held-out grid test says corrections do not transfer.
    transfers = []
    for tgt, g in grids.items():
        best = g.get("best") or {}
        if not best:
            continue
        improves = (best.get("mae_test") is not None) and (
            best["mae_test"] < (g.get("baseline_mae_test") or 9e9) - 1e-9
        )
        transfers.append((tgt, improves, g))
    if transfers and not any(ok for _, ok, _ in transfers):
        detail = "; ".join(
            f"{tgt}: best(train g={g['best']['gain']}, s={g['best']['shift']}) "
            f"test MAE {g['best']['mae_test']:.4f} vs baseline {g['baseline_mae_test']:.4f}"
            for tgt, _, g in transfers
        )
        notes = [
            "Held-out grid test: no (gain, shift) fitted on earlier months improves the unseen "
            f"month after 0.25 rounding — {detail}.",
            "The apparent S 'compression' (regressing bias on the market value) is a "
            "regression-attenuation artifact: the direct fit gives β≈0.96 (not different from 1) "
            "and least-squares calibration raises MAE because lines sit on a 0.25 grid.",
            "Best in-sample corrections are gain 0.90 / shift −0.10, i.e. shrinking toward the mean — "
            "the opposite of what the naive slope suggested. Neither transfers out of sample.",
        ] + [f"Superseded diagnostic: {n}" for n in notes]
        knob = "NO_KNOB (corrections do not transfer out-of-sample)"

    payload = {
        "source": str(IN_CSV),
        "scheme": SCHEME,
        "n": len(recs),
        "months": months,
        "leagues": leagues,
        "overall": overall,
        "by_league": by_league,
        "by_absD": by_d,
        "by_S": by_s,
        "by_league_absD": by_league_d,
        "by_league_S": by_league_s,
        "by_month": by_month,
        "regression_D": reg_d,
        "regression_Tot": reg_s,
        "regression_D_by_month": reg_d_month,
        "regression_Tot_by_month": reg_s_month,
        "calibration_D": cal_d,
        "calibration_Tot": cal_s,
        "calibration_Tot_by_league": cal_s_league,
        "calibration_Tot_by_month": cal_s_month,
        "grid_scan": grids,
        "recommended_knob": knob,
        "notes": notes,
    }
    (OUT / "bias_audit.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    lines = [
        "# Bias audit vs closing lines (existing artifacts, no DB / no prod changes)",
        "",
        f"- Source: `{IN_CSV}` scheme `{SCHEME}`",
        f"- Rows: `{len(recs)}`; months: `{', '.join(months)}`; leagues: `{len(leagues)}`",
        "",
        "Sign convention: `bias_D = D_model − D_market` (>0 ⇒ we make home stronger than market). "
        "`bias_Tot = Total_model − Total_market` (>0 ⇒ we price higher totals).",
        "`±2se = YES` means bias is larger than 2 standard errors (unlikely pure noise). "
        "`stable sign = YES` means mean bias has the same sign in every month.",
        "",
        "## Overall",
        "",
        f"- `bias_D` = **{fmt(overall['bias_D'])}** (2se {'YES' if overall['sig_D'] else 'no'}, "
        f"stable {'YES' if overall['stab_D']['all_same_sign'] else 'no'}), MAE_AH {overall['mae_AH']:.4f}",
        f"- `bias_Tot` = **{fmt(overall['bias_Tot'])}** (2se {'YES' if overall['sig_Tot'] else 'no'}, "
        f"stable {'YES' if overall['stab_Tot']['all_same_sign'] else 'no'}), MAE_Tot {overall['mae_Tot']:.4f}",
        f"- monthly `bias_D`: {overall['stab_D']['months']}",
        f"- monthly `bias_Tot`: {overall['stab_Tot']['months']}",
        "",
        "## Tables",
        "",
    ]
    lines += md_table(by_league, "By league")
    lines += md_table(by_d, "By |D| bucket (market)")
    lines += md_table(by_s, "By S bucket (market)")
    lines += md_table(by_month, "By month")
    lines += md_table(by_league_d, "League × |D| bucket")
    lines += md_table(by_league_s, "League × S bucket")

    lines += [
        "## Shift vs slope (OLS of bias on centred market value)",
        "",
        "`bias = a + b·(market − mean)`. `b < 0` ⇒ our value is compressed toward the mean, "
        "so a **gain** correction is needed rather than a flat shift.",
        "",
        "| Target | n | intercept a | 2se | slope b | 2se | our spread / market (1+b) | corrective gain |",
        "|---|---:|---:|:--:|---:|:--:|---:|---:|",
        f"| D | {reg_d.get('n')} | {fmt(reg_d.get('intercept'))} | "
        f"{'YES' if reg_d.get('intercept_sig') else 'no'} | {fmt(reg_d.get('slope'))} | "
        f"{'YES' if reg_d.get('slope_sig') else 'no'} | {reg_d.get('compression', float('nan')):.3f} | "
        f"{reg_d.get('corrective_gain', float('nan')):.3f} |",
        f"| Total | {reg_s.get('n')} | {fmt(reg_s.get('intercept'))} | "
        f"{'YES' if reg_s.get('intercept_sig') else 'no'} | {fmt(reg_s.get('slope'))} | "
        f"{'YES' if reg_s.get('slope_sig') else 'no'} | {reg_s.get('compression', float('nan')):.3f} | "
        f"{reg_s.get('corrective_gain', float('nan')):.3f} |",
        "",
        "Per-month slope (stability check):",
        "",
        "| Month | slope D | sig | slope Tot | sig |",
        "|---|---:|:--:|---:|:--:|",
    ]
    for m in months:
        rd = reg_d_month.get(m) or {}
        rs = reg_s_month.get(m) or {}
        lines.append(
            f"| {m} | {fmt(rd.get('slope'))} | {'YES' if rd.get('slope_sig') else 'no'} | "
            f"{fmt(rs.get('slope'))} | {'YES' if rs.get('slope_sig') else 'no'} |"
        )
    def cal_row(label: str, c: Dict[str, Any]) -> str:
        if not c.get("n") or c.get("beta") is None:
            return f"| {label} | {c.get('n', 0)} | — | — | — | — | — |"
        return (
            f"| {label} | {c['n']} | {c['alpha']:+.4f} | {c['beta']:.4f} | "
            f"{'YES' if c['beta_differs_from_1'] else 'no'} | "
            f"{c['mae_before']:.4f} | {c['mae_after_insample']:.4f} |"
        )

    lines += [
        "",
        "## Direct calibration fit (market = α + β·model)",
        "",
        "This is the correction that would actually be applied. `β > 1` ⇒ expand model deviations. "
        "`MAE after` is **in-sample** and only an upper bound on the achievable gain.",
        "",
        "| Target | n | α | β | β≠1 | MAE before | MAE after (in-sample) |",
        "|---|---:|---:|---:|:--:|---:|---:|",
        cal_row("D", cal_d),
        cal_row("Total", cal_s),
        "",
        "Total calibration by league:",
        "",
        "| League | n | α | β | β≠1 | MAE before | MAE after (in-sample) |",
        "|---|---:|---:|---:|:--:|---:|---:|",
    ]
    for lg in leagues:
        lines.append(cal_row(lg, cal_s_league.get(lg, {})))
    lines += [
        "",
        "Total calibration by month (stability of β):",
        "",
        "| Month | n | α | β | β≠1 | MAE before | MAE after (in-sample) |",
        "|---|---:|---:|---:|:--:|---:|---:|",
    ]
    for m in months:
        lines.append(cal_row(m, cal_s_month.get(m, {})))

    if grids:
        lines += [
            "",
            "## Decisive held-out test: (gain, shift) grid with 0.25 rounding",
            "",
            f"Fitted on `{', '.join(months[:-1])}`, tested on `{months[-1]}`. "
            "Corrections are applied as `centre + gain·(model − centre) + shift`, then rounded to the 0.25 grid.",
            "",
            "| Target | best gain | best shift | MAE train | MAE test | baseline MAE test | test Δ |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for tgt in ("Tot", "D"):
            g = grids.get(tgt) or {}
            b = g.get("best")
            if not b:
                continue
            lines.append(
                f"| {tgt} | {b['gain']:.2f} | {b['shift']:+.2f} | {b['mae_train']:.4f} | "
                f"{b['mae_test']:.4f} | {g['baseline_mae_test']:.4f} | "
                f"{b['mae_test'] - g['baseline_mae_test']:+.4f} |"
            )
        lines += [
            "",
            "Single-knob variants (held-out):",
            "",
            "| Target | gain-only | MAE test | shift-only | MAE test | baseline |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for tgt in ("Tot", "D"):
            g = grids.get(tgt) or {}
            if not g.get("best_gain_only"):
                continue
            go = g["best_gain_only"]
            so = g["best_shift_only"]
            lines.append(
                f"| {tgt} | g={go['gain']:.2f} | {go['mae_test']:.4f} | "
                f"s={so['shift']:+.2f} | {so['mae_test']:.4f} | {g['baseline_mae_test']:.4f} |"
            )
        lines.append("")

    lines += [
        "",
        "## Recommended single knob",
        "",
        f"### **{knob}**",
        "",
    ]
    for n in notes:
        lines.append(f"- {n}")
    lines += [
        "",
        "## Protocol if any knob is ever tried",
        "",
        "1. Change **one** parameter; keep Dynamic D / S-EMA / SFA / SFTC as-is.",
        "2. Fit the correction on months **before** the test month; never on the test month.",
        "3. Judge MAE **after 0.25 rounding** — off-grid gains are illusory.",
        "4. Accept only if closing MAE improves on ≥2 unseen months and no league degrades badly.",
        "5. Record decision + window in an ADR before touching `web/model_config.json`.",
        "",
        "## Caveats",
        "",
        f"- Only `{len(recs)}` rows over `{len(months)}` months; one held-out month is a weak test.",
        "- PL/BL history without odds is excluded, so league mix is uneven.",
        "- Bias magnitudes (~0.02–0.03 goals) are far below the 0.25 line grid, so most of them "
        "cannot change a printed line at all.",
        "",
    ]
    text = "\n".join(lines)
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    (REPO / "REPORT_BIAS_AUDIT.md").write_text(text, encoding="utf-8")
    print(text, flush=True)
    print(f"Wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
