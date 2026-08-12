"""EXP-049 — Time-aware Dynamic State Aging (offline, no product edits).

Dynamic carryover ages with days since each team's previous official match:

    f(t) = 2^(-t / H)     half-life H ∈ {30, 45, 60, 90}
    D_for_cal = D_base + f(t) * (D_dyn − D_base)
    (same for S-EMA in BOTH series)

t = mean(days_home, days_away). CURRENT = f≡1, RESET = f≡0.

OOS expanding: LL / SA / L1 × 2024–25 & 2025–26, buckets GW1–2…GW7–8
(train = prior seasons + earlier GWs).

Also: Aug-12 cards with train=2025–26 (boundary check, margined 1X2).

Curves:
  days-since-prev → harm/benefit of CURRENT vs RESET / vs best H
  matches-since-long-break (≥60d gap) → same

Does not pick a production H — tests stability of the aging effect.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import goal_model_train as gmt

from experiments.market_weights.data import Row, fetch_all_view_rows, parse_rows, to_raw_match
from experiments.market_weights.eval import load_baseline_config, season_weights_for
from experiments.early_season_dyn.gw import EVAL_SEASONS, prior_and_hold_bucket, assign_gameweeks
from experiments.early_season_dyn.metrics import mae, model_odds_with_market_margin
from experiments.early_season_dyn.predict_scaled import finalize_from_ds
from experiments.aug12_market_compare.fixtures import FIXTURES

ROOT = Path("/workspace")
OUT = Path("/opt/cursor/artifacts/exp049_time_decay")
OUT.mkdir(parents=True, exist_ok=True)
REPO = ROOT / "experiments" / "exp049_time_decay"

HALF_LIVES = (30, 45, 60, 90)
LONG_BREAK_DAYS = 60
CARD_DATE = date(2026, 8, 15)

BUCKETS = [
    ("GW1-2", 1, 2),
    ("GW3-4", 3, 4),
    ("GW5-6", 5, 6),
    ("GW7-8", 7, 8),
]


def _silence_train(raw: List[gmt.RawMatch]) -> gmt.TrainedModel:
    cfg = load_baseline_config(season_weights_for(raw))
    old = sys.stdout
    sys.stdout = open("/dev/null", "w")
    try:
        model, _ = gmt.train_full_model(raw, cfg)
    finally:
        sys.stdout.close()
        sys.stdout = old
    return model


def f_half_life(t_days: float, H: float) -> float:
    if t_days < 0:
        t_days = 0.0
    return float(2.0 ** (-t_days / H))


def days_bucket(t: Optional[float]) -> str:
    if t is None:
        return "unknown"
    if t < 15:
        return "0-14"
    if t < 30:
        return "15-29"
    if t < 60:
        return "30-59"
    if t < 90:
        return "60-89"
    return "90+"


def mslb_bucket(n: Optional[int]) -> str:
    if n is None:
        return "unknown"
    if n <= 1:
        return "1"
    if n == 2:
        return "2"
    if n <= 4:
        return "3-4"
    return "5+"


def _fav_ah(ah: float, eps: float = 1e-9) -> str:
    if ah < -eps:
        return "H"
    if ah > eps:
        return "A"
    return "N"


def predict_decay(
    model: gmt.TrainedModel,
    home_id: str,
    away_id: str,
    *,
    d_scale: float,
    s_scale: float,
    **kw: Any,
) -> gmt.Prediction:
    if abs(d_scale - 1.0) < 1e-12 and abs(s_scale - 1.0) < 1e-12:
        return gmt.predict_match(
            model, home_id, away_id,
            apply_momentum=True, apply_s_momentum=True, **kw,
        )
    if abs(d_scale) < 1e-12 and abs(s_scale) < 1e-12:
        return gmt.predict_match(
            model, home_id, away_id,
            apply_momentum=False, apply_s_momentum=False, **kw,
        )
    full = gmt.predict_match(
        model, home_id, away_id,
        apply_momentum=True, apply_s_momentum=True, **kw,
    )
    d_base = float(full.d_model_base)
    d_dyn = float(full.d_model_dynamic)
    s_base = float(full.s_model_base)
    s_dyn = float(full.s_model_dynamic)
    return finalize_from_ds(
        model, home_id, away_id,
        d_for_cal=d_base + d_scale * (d_dyn - d_base),
        s_for_cal=s_base + s_scale * (s_dyn - s_base),
        d_model_base=d_base,
        s_model_base=s_base,
        d_model_dynamic=d_dyn,
        s_model_dynamic=s_dyn,
        dynamic_correction=d_scale * (d_dyn - d_base),
        s_dynamic_correction=s_scale * (s_dyn - s_base),
        league=kw.get("league"),
        season=kw.get("season"),
        home_odds=kw.get("home_odds"),
        away_odds=kw.get("away_odds"),
    )


def build_team_history(league_rows: Sequence[Row]) -> Dict[str, List[Tuple[date, str]]]:
    """team_id → sorted list of (match_date, match_id)."""
    hist: Dict[str, List[Tuple[date, str]]] = defaultdict(list)
    for r in sorted(league_rows, key=lambda x: (x.match_date, x.match_id)):
        hist[r.home_team_id].append((r.match_date, r.match_id))
        hist[r.away_team_id].append((r.match_date, r.match_id))
    return hist


def team_gap_features(
    hist: Dict[str, List[Tuple[date, str]]],
    team_id: str,
    match_date: date,
    match_id: str,
) -> Tuple[Optional[int], Optional[int]]:
    """Return (days_since_prev, matches_since_long_break) for this team at this match."""
    seq = hist.get(team_id, [])
    prev_date: Optional[date] = None
    # matches after last long break, counting this match
    mslb = 0
    for dt, mid in seq:
        if (dt, mid) >= (match_date, match_id):
            break
        if prev_date is not None:
            gap = (dt - prev_date).days
            if gap >= LONG_BREAK_DAYS:
                mslb = 0
        mslb += 1
        prev_date = dt
    # gap into current match
    if prev_date is None:
        return None, None
    days = (match_date - prev_date).days
    # if this match itself follows long break, mslb becomes 1
    if days >= LONG_BREAK_DAYS:
        mslb = 1
    else:
        mslb = mslb + 1  # include current
    return days, mslb


def _pack(
    *,
    series: str,
    arm: str,
    d_scale: float,
    s_scale: float,
    league: str,
    season: str,
    bucket: str,
    gw: int,
    row: Row,
    pred: gmt.Prediction,
    days_h: Optional[int],
    days_a: Optional[int],
    mslb_h: Optional[int],
    mslb_a: Optional[int],
) -> Dict[str, Any]:
    mk = pred.markets
    ah = float(mk.main_ah.line)
    tot = float(mk.main_total.line)
    ah_m = float(row.closing_ah_home)
    tot_m = float(row.closing_total_line)
    p1 = float(pred.home_probability_final)
    px = float(pred.draw_probability_final)
    p2 = float(pred.away_probability_final)
    t_mean = None
    if days_h is not None and days_a is not None:
        t_mean = 0.5 * (days_h + days_a)
    elif days_h is not None:
        t_mean = float(days_h)
    elif days_a is not None:
        t_mean = float(days_a)
    t_min = None
    if days_h is not None and days_a is not None:
        t_min = float(min(days_h, days_a))
    elif days_h is not None:
        t_min = float(days_h)
    elif days_a is not None:
        t_min = float(days_a)
    mslb = None
    if mslb_h is not None and mslb_a is not None:
        mslb = min(mslb_h, mslb_a)
    elif mslb_h is not None:
        mslb = mslb_h
    elif mslb_a is not None:
        mslb = mslb_a

    out: Dict[str, Any] = {
        "series": series,
        "arm": arm,
        "d_scale": d_scale,
        "s_scale": s_scale,
        "league": league,
        "season": season,
        "bucket": bucket,
        "gw": gw,
        "match_id": row.match_id,
        "match_date": row.match_date.isoformat(),
        "home": row.home_team,
        "away": row.away_team,
        "ah_mkt": ah_m,
        "tot_mkt": tot_m,
        "ah_pred": ah,
        "tot_pred": tot,
        "dah": ah - ah_m,
        "dtot": tot - tot_m,
        "abs_dah": abs(ah - ah_m),
        "abs_dtot": abs(tot - tot_m),
        "flip_ah": _fav_ah(ah_m) != _fav_ah(ah),
        "days_h": days_h,
        "days_a": days_a,
        "t_mean": t_mean,
        "t_min": t_min,
        "days_bucket": days_bucket(t_mean),
        "mslb_h": mslb_h,
        "mslb_a": mslb_a,
        "mslb": mslb,
        "mslb_bucket": mslb_bucket(mslb),
        "d_base": pred.d_model_base,
        "d_corr_applied": pred.dynamic_correction,
        "d_final": pred.d_final,
        "d_market": -ah_m,
        "o1": row.home_odds,
        "ox": row.draw_odds,
        "o2": row.away_odds,
    }
    if (
        row.home_odds and row.draw_odds and row.away_odds
        and min(row.home_odds, row.draw_odds, row.away_odds) > 1.0
    ):
        b1, bx, b2, over = model_odds_with_market_margin(
            p1, px, p2, float(row.home_odds), float(row.draw_odds), float(row.away_odds)
        )
        out.update(b1m=b1, bxm=bx, b2m=b2, overround=over,
                   d1_odds=b1 - float(row.home_odds),
                   dx_odds=bx - float(row.draw_odds),
                   d2_odds=b2 - float(row.away_odds))
    else:
        out.update(b1m=None, bxm=None, b2m=None, overround=None,
                   d1_odds=None, dx_odds=None, d2_odds=None)
    return out


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: List[str] = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    return {
        "n": len(rows),
        "mae_ah": mae([float(r["dah"]) for r in rows]),
        "mae_tot": mae([float(r["dtot"]) for r in rows]),
        "n_ah_ge_05": sum(1 for r in rows if float(r["abs_dah"]) >= 0.5 - 1e-12),
        "flip_ah_rate": sum(1 for r in rows if r["flip_ah"]) / len(rows),
        "mae_odds_1": mae([float(r["d1_odds"]) for r in rows if r.get("d1_odds") is not None]),
    }


def _arm_scales(arm: str, t_mean: Optional[float], series: str) -> Tuple[float, float]:
    if arm == "CURRENT":
        f = 1.0
    elif arm == "RESET":
        f = 0.0
    else:
        H = float(arm[1:])  # H30 → 30
        f = 1.0 if t_mean is None else f_half_life(t_mean, H)
    if series == "BOTH":
        return f, f
    if series == "D_ONLY":
        return f, 1.0
    if series == "S_ONLY":
        return 1.0, f
    raise KeyError(series)


def run_historical(all_rows: List[Row]) -> List[Dict[str, Any]]:
    flat: List[Dict[str, Any]] = []
    arms = ["CURRENT"] + [f"H{h}" for h in HALF_LIVES] + ["RESET"]
    series_list = ("BOTH", "D_ONLY", "S_ONLY")

    for league, season in EVAL_SEASONS:
        league_all = [r for r in all_rows if r.league_name == league]
        hist = build_team_history(league_all)

        for bucket, gw_from, gw_to in BUCKETS:
            train_rows, hold = prior_and_hold_bucket(
                all_rows, league, season, gw_from=gw_from, gw_to=gw_to
            )
            if len(train_rows) < 80 or not hold:
                print(f"skip {league} {season} {bucket}")
                continue
            print(f"train {league} {season} {bucket}: n={len(train_rows)} hold={len(hold)}")
            model = _silence_train([to_raw_match(r) for r in train_rows])

            for t in hold:
                r = t.row
                days_h, mslb_h = team_gap_features(hist, r.home_team_id, r.match_date, r.match_id)
                days_a, mslb_a = team_gap_features(hist, r.away_team_id, r.match_date, r.match_id)
                t_mean = None
                if days_h is not None and days_a is not None:
                    t_mean = 0.5 * (days_h + days_a)
                elif days_h is not None:
                    t_mean = float(days_h)
                elif days_a is not None:
                    t_mean = float(days_a)

                hid = gmt.team_key(r.home_team_id, r.home_team)
                aid = gmt.team_key(r.away_team_id, r.away_team)
                kw = dict(
                    match_date=r.match_date,
                    league=league,
                    season=season,
                    home_odds=r.home_odds,
                    away_odds=r.away_odds,
                    neutral=r.is_neutral,
                    derby=gmt._derby_flag_from_weight(r.derby_weight),
                )
                for series in series_list:
                    for arm in arms:
                        d_s, s_s = _arm_scales(arm, t_mean, series)
                        pred = predict_decay(model, hid, aid, d_scale=d_s, s_scale=s_s, **kw)
                        flat.append(_pack(
                            series=series, arm=arm, d_scale=d_s, s_scale=s_s,
                            league=league, season=season, bucket=bucket, gw=t.gw,
                            row=r, pred=pred,
                            days_h=days_h, days_a=days_a, mslb_h=mslb_h, mslb_a=mslb_a,
                        ))
    return flat


def run_aug12(all_rows: List[Row]) -> List[Dict[str, Any]]:
    """Boundary check: train 2025-26, predict Aug-12 cards with time-aware f(t)."""
    flat: List[Dict[str, Any]] = []
    arms = ["CURRENT"] + [f"H{h}" for h in HALF_LIVES] + ["RESET"]
    by_lg_train: Dict[str, List[Row]] = defaultdict(list)
    for r in all_rows:
        if r.season_label == "2025-26":
            by_lg_train[r.league_name].append(r)
    models = {}
    hist_by_lg = {}
    for lg in sorted({fx["league"] for fx in FIXTURES}):
        train = by_lg_train[lg]
        print(f"aug12-train {lg} n={len(train)}")
        models[lg] = _silence_train([to_raw_match(r) for r in train])
        hist_by_lg[lg] = build_team_history(train)

    for fx in FIXTURES:
        lg = fx["league"]
        hist = hist_by_lg[lg]
        # synthetic match_id for gap vs last train match
        days_h, mslb_h = team_gap_features(hist, fx["home_id"], CARD_DATE, "aug12-card")
        days_a, mslb_a = team_gap_features(hist, fx["away_id"], CARD_DATE, "aug12-card")
        # team_gap_features expects match in hist — for future card, manually:
        def gap_to_card(team_id: str) -> Tuple[Optional[int], Optional[int]]:
            seq = hist.get(team_id, [])
            if not seq:
                return None, None
            last_dt = seq[-1][0]
            days = (CARD_DATE - last_dt).days
            # count mslb ending at last match, then if days>=60 reset to 1
            prev = None
            mslb = 0
            for dt, _ in seq:
                if prev is not None and (dt - prev).days >= LONG_BREAK_DAYS:
                    mslb = 0
                mslb += 1
                prev = dt
            if days >= LONG_BREAK_DAYS:
                mslb = 1
            else:
                mslb = mslb + 1
            return days, mslb

        days_h, mslb_h = gap_to_card(fx["home_id"])
        days_a, mslb_a = gap_to_card(fx["away_id"])
        t_mean = None
        if days_h is not None and days_a is not None:
            t_mean = 0.5 * (days_h + days_a)

        # fake Row for pack
        class R:
            pass
        row = R()
        row.match_id = f"aug12-{fx['home']}-{fx['away']}"
        row.match_date = CARD_DATE
        row.home_team = fx["home"]
        row.away_team = fx["away"]
        row.closing_ah_home = fx["ah"]
        row.closing_total_line = fx["tot"]
        row.home_odds = fx["o1"]
        row.draw_odds = fx["ox"]
        row.away_odds = fx["o2"]

        for series in ("BOTH", "D_ONLY", "S_ONLY"):
            for arm in arms:
                d_s, s_s = _arm_scales(arm, t_mean, series)
                pred = predict_decay(
                    models[lg], fx["home_id"], fx["away_id"],
                    d_scale=d_s, s_scale=s_s,
                    match_date=CARD_DATE, league=lg, season="2025-26",
                    home_odds=fx["o1"], away_odds=fx["o2"],
                )
                flat.append(_pack(
                    series=series, arm=arm, d_scale=d_s, s_scale=s_s,
                    league=lg, season="2026-27-card", bucket="AUG12", gw=1,
                    row=row, pred=pred,
                    days_h=days_h, days_a=days_a, mslb_h=mslb_h, mslb_a=mslb_a,
                ))
    return flat


def harm_vs_reset(rows_by_arm: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """For each match, harm_CURRENT = |dah_CUR| - |dah_RESET|; positive = CURRENT worse."""
    cur = {(r["match_id"], r["season"]): r for r in rows_by_arm.get("CURRENT", [])}
    rst = {(r["match_id"], r["season"]): r for r in rows_by_arm.get("RESET", [])}
    harms = []
    for k, c in cur.items():
        if k not in rst:
            continue
        harms.append(float(c["abs_dah"]) - float(rst[k]["abs_dah"]))
    if not harms:
        return {"n": 0}
    return {
        "n": len(harms),
        "harm_CUR_vs_RESET_mean": sum(harms) / len(harms),
        "pct_CURRENT_worse": sum(1 for h in harms if h > 1e-12) / len(harms),
        "pct_CURRENT_better": sum(1 for h in harms if h < -1e-12) / len(harms),
    }


def curve_tables(flat: List[Dict[str, Any]], series: str = "BOTH") -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Aggregate by GW bucket, days_bucket, mslb_bucket."""
    sub = [r for r in flat if r["series"] == series and r["bucket"] != "AUG12"]
    arms = sorted({r["arm"] for r in sub}, key=lambda a: (a != "CURRENT", a != "RESET", a))

    def agg(group_key: str) -> List[Dict[str, Any]]:
        groups: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for r in sub:
            groups[str(r[group_key])][r["arm"]].append(r)
        out = []
        for g, by_arm in sorted(groups.items()):
            row: Dict[str, Any] = {group_key: g}
            for arm in arms:
                s = _summarize(by_arm.get(arm, []))
                row[f"{arm}_n"] = s.get("n", 0)
                row[f"{arm}_mae_ah"] = s.get("mae_ah")
                row[f"{arm}_n_ge05"] = s.get("n_ah_ge_05")
                row[f"{arm}_flip"] = s.get("flip_ah_rate")
                row[f"{arm}_mae_tot"] = s.get("mae_tot")
            # harm CURRENT vs RESET
            h = harm_vs_reset(by_arm)
            row.update(h)
            # mean t_mean in group
            tm = [float(r["t_mean"]) for r in by_arm.get("CURRENT", []) if r.get("t_mean") is not None]
            row["mean_t"] = sum(tm) / len(tm) if tm else None
            out.append(row)
        return out

    return agg("bucket"), agg("days_bucket"), agg("mslb_bucket")


def build_report(
    by_gw: List[Dict[str, Any]],
    by_days: List[Dict[str, Any]],
    by_mslb: List[Dict[str, Any]],
    aug_sum: List[Dict[str, Any]],
) -> str:
    lines = [
        "# EXP-049 — Time-aware Dynamic State Aging",
        "",
        "Offline. `f(t)=2^(-t/H)`, `t=mean(days_home, days_away)`.",
        "OOS expanding GW buckets on LL/SA/L1 × 2024–25 & 2025–26.",
        "1X2 uses same-match overround. Does **not** pick a production H.",
        "",
        "## BOTH — by GW bucket",
        "",
        "| Bucket | mean t | CURRENT MAE | H45 MAE | H60 MAE | RESET MAE | CURRENT worse than RESET |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in by_gw:
        lines.append(
            f"| {r['bucket']} | {r.get('mean_t') or float('nan'):.0f} | "
            f"{r.get('CURRENT_mae_ah') or float('nan'):.3f} | "
            f"{r.get('H45_mae_ah') or float('nan'):.3f} | "
            f"{r.get('H60_mae_ah') or float('nan'):.3f} | "
            f"{r.get('RESET_mae_ah') or float('nan'):.3f} | "
            f"{100*(r.get('pct_CURRENT_worse') or 0):.0f}% |"
        )

    lines += [
        "",
        "## BOTH — by days since previous match",
        "",
        "| Days | n | mean t | CURRENT | H30 | H45 | H60 | H90 | RESET | % CUR worse |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    order = ["0-14", "15-29", "30-59", "60-89", "90+", "unknown"]
    by_days_map = {r["days_bucket"]: r for r in by_days}
    for g in order:
        if g not in by_days_map:
            continue
        r = by_days_map[g]
        lines.append(
            f"| {g} | {r.get('CURRENT_n', 0)} | {r.get('mean_t') or float('nan'):.0f} | "
            f"{r.get('CURRENT_mae_ah') or float('nan'):.3f} | "
            f"{r.get('H30_mae_ah') or float('nan'):.3f} | "
            f"{r.get('H45_mae_ah') or float('nan'):.3f} | "
            f"{r.get('H60_mae_ah') or float('nan'):.3f} | "
            f"{r.get('H90_mae_ah') or float('nan'):.3f} | "
            f"{r.get('RESET_mae_ah') or float('nan'):.3f} | "
            f"{100*(r.get('pct_CURRENT_worse') or 0):.0f}% |"
        )

    lines += [
        "",
        "## BOTH — by matches since long break (≥60d)",
        "",
        "| MSLB | n | CURRENT | H45 | H60 | RESET | % CUR worse |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for g in ["1", "2", "3-4", "5+", "unknown"]:
        r = next((x for x in by_mslb if x["mslb_bucket"] == g), None)
        if not r:
            continue
        lines.append(
            f"| {g} | {r.get('CURRENT_n', 0)} | "
            f"{r.get('CURRENT_mae_ah') or float('nan'):.3f} | "
            f"{r.get('H45_mae_ah') or float('nan'):.3f} | "
            f"{r.get('H60_mae_ah') or float('nan'):.3f} | "
            f"{r.get('RESET_mae_ah') or float('nan'):.3f} | "
            f"{100*(r.get('pct_CURRENT_worse') or 0):.0f}% |"
        )

    lines += [
        "",
        "## Aug-12 cards (train 2025–26) — BOTH",
        "",
        "| Arm | MAE AH | N(≥0.5) | flip | MAE Tot |",
        "|---|---:|---:|---:|---:|",
    ]
    for s in aug_sum:
        if s["series"] != "BOTH":
            continue
        lines.append(
            f"| {s['arm']} | {s['mae_ah']:.3f} | {s['n_ah_ge_05']} | "
            f"{100*s['flip_ah_rate']:.1f}% | {s['mae_tot']:.3f} |"
        )

    lines += [
        "",
        "## Interpretation checklist",
        "",
        "- If days 0–14: CURRENT ≤ decay arms → short gaps should keep state.",
        "- If days 80+: RESET / short H beat CURRENT → summer aging confirmed OOS.",
        "- If MSLB 1–2: decay helps; MSLB 5+: CURRENT catches up → warm-up curve.",
        "- Do **not** crown a single H yet; look for a stable shape across seasons.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    print("loading…")
    rows = parse_rows(fetch_all_view_rows())
    print(f"rows={len(rows)}")
    hist_flat = run_historical(rows)
    aug_flat = run_aug12(rows)
    flat = hist_flat + aug_flat

    by_gw, by_days, by_mslb = curve_tables(flat, "BOTH")
    # Aug summaries
    aug_sum = []
    for series in ("BOTH", "D_ONLY", "S_ONLY"):
        for arm in ["CURRENT"] + [f"H{h}" for h in HALF_LIVES] + ["RESET"]:
            rs = [r for r in aug_flat if r["series"] == series and r["arm"] == arm]
            s = _summarize(rs)
            s.update({"series": series, "arm": arm})
            aug_sum.append(s)
            if series == "BOTH":
                print(f"AUG12 {arm}: MAE_AH={s['mae_ah']:.3f} n≥0.5={s['n_ah_ge_05']} flip={100*s['flip_ah_rate']:.1f}%")

    report = build_report(by_gw, by_days, by_mslb, aug_sum)
    for dest in (OUT, REPO):
        dest.mkdir(parents=True, exist_ok=True)
        _write_csv(dest / "preds_flat.csv", flat)
        _write_csv(dest / "curve_by_gw.csv", by_gw)
        _write_csv(dest / "curve_by_days.csv", by_days)
        _write_csv(dest / "curve_by_mslb.csv", by_mslb)
        _write_csv(dest / "aug12_summaries.csv", aug_sum)
        (dest / "REPORT.md").write_text(report, encoding="utf-8")
        (dest / "summary.json").write_text(
            json.dumps(
                {"by_gw": by_gw, "by_days": by_days, "by_mslb": by_mslb, "aug": aug_sum},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    print(report)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
