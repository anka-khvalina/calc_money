"""EXP-048 — Cross-season Dynamic State Decay (offline, no product edits).

At season boundary keep D_base / S_base; scale only the past-season
dynamic correction:

  CURRENT 100% | DECAY75 75% | DECAY50 50% | DECAY25 25% | RESET 0%

Three series:
  BOTH  — same scale on Dynamic D and S-EMA
  D_ONLY — scale D, S-EMA full (100%)
  S_ONLY — scale S-EMA, Dynamic D full (100%)

Ratings / A-D / calib / SFA / SFTC / weights unchanged.
Train = **2025–26 only** per league.
Validate = Aug-12 uploaded book cards (34 fixtures).
1X2 uses **same match overround** as the book.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import goal_model_train as gmt

from experiments.market_weights.data import Row, fetch_all_view_rows, parse_rows, to_raw_match
from experiments.market_weights.eval import load_baseline_config, season_weights_for
from experiments.early_season_dyn.metrics import mae, model_odds_with_market_margin
from experiments.early_season_dyn.predict_scaled import finalize_from_ds
from experiments.aug12_market_compare.fixtures import FIXTURES

ROOT = Path("/workspace")
OUT = Path("/opt/cursor/artifacts/exp048_state_decay")
OUT.mkdir(parents=True, exist_ok=True)
REPO = ROOT / "experiments" / "exp048_state_decay"

SCALES: List[Tuple[str, float]] = [
    ("CURRENT", 1.00),
    ("DECAY75", 0.75),
    ("DECAY50", 0.50),
    ("DECAY25", 0.25),
    ("RESET", 0.00),
]

# Approximate kickoff dates for openers (for days-gap diagnostic only).
CARD_DATE = date(2026, 8, 15)


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


def _fav_ah(ah: float, eps: float = 1e-9) -> str:
    if ah < -eps:
        return "H"
    if ah > eps:
        return "A"
    return "N"


def _fav_1x2(o1: float, o2: float) -> str:
    if o1 < o2:
        return "H"
    if o2 < o1:
        return "A"
    return "N"


def predict_decay(
    model: gmt.TrainedModel,
    home_id: str,
    away_id: str,
    *,
    d_scale: float,
    s_scale: float,
    league: Optional[str] = None,
    season: Optional[str] = None,
    home_odds: Optional[float] = None,
    away_odds: Optional[float] = None,
    neutral: bool = False,
    derby: bool = False,
    match_date: Optional[date] = None,
) -> gmt.Prediction:
    """Scale past-season dynamic corrections independently; base ratings untouched."""
    kw = dict(
        neutral=neutral,
        derby=derby,
        match_date=match_date,
        league=league,
        season=season,
        home_odds=home_odds,
        away_odds=away_odds,
    )
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
    d_for_cal = d_base + d_scale * (d_dyn - d_base)
    s_for_cal = s_base + s_scale * (s_dyn - s_base)
    return finalize_from_ds(
        model, home_id, away_id,
        d_for_cal=d_for_cal,
        s_for_cal=s_for_cal,
        d_model_base=d_base,
        s_model_base=s_base,
        d_model_dynamic=d_dyn,
        s_model_dynamic=s_dyn,
        dynamic_correction=d_scale * (d_dyn - d_base),
        s_dynamic_correction=s_scale * (s_dyn - s_base),
        league=league,
        season=season,
        home_odds=home_odds,
        away_odds=away_odds,
    )


def _last_match_date(rows: Sequence[Row], team_id: str) -> Optional[date]:
    dates = [
        r.match_date for r in rows
        if r.home_team_id == team_id or r.away_team_id == team_id
    ]
    return max(dates) if dates else None


def _pack(
    *,
    series: str,
    arm: str,
    d_scale: float,
    s_scale: float,
    fx: Dict[str, Any],
    pred: gmt.Prediction,
    days_home: Optional[int],
    days_away: Optional[int],
) -> Dict[str, Any]:
    mk = pred.markets
    ah = float(mk.main_ah.line)
    tot = float(mk.main_total.line)
    p1 = float(pred.home_probability_final)
    px = float(pred.draw_probability_final)
    p2 = float(pred.away_probability_final)
    ah_m = float(fx["ah"])
    tot_m = float(fx["tot"])
    b1, bx, b2, over = model_odds_with_market_margin(p1, px, p2, fx["o1"], fx["ox"], fx["o2"])
    return {
        "series": series,
        "arm": arm,
        "d_scale": d_scale,
        "s_scale": s_scale,
        "league": fx["league"],
        "home": fx["home"],
        "away": fx["away"],
        "ah_mkt": ah_m,
        "tot_mkt": tot_m,
        "ah_pred": ah,
        "tot_pred": tot,
        "dah": ah - ah_m,
        "dtot": tot - tot_m,
        "abs_dah": abs(ah - ah_m),
        "abs_dtot": abs(tot - tot_m),
        "flip_ah": _fav_ah(ah_m) != _fav_ah(ah),
        "fav_mkt": _fav_ah(ah_m),
        "fav_mod": _fav_ah(ah),
        "o1": fx["o1"], "ox": fx["ox"], "o2": fx["o2"],
        "b1m": b1, "bxm": bx, "b2m": b2,
        "d1_odds": b1 - fx["o1"],
        "dx_odds": bx - fx["ox"],
        "d2_odds": b2 - fx["o2"],
        "overround_pct": (over - 1.0) * 100.0,
        "flip_1x2": _fav_1x2(fx["o1"], fx["o2"]) != _fav_1x2(b1, b2),
        "d_base": pred.d_model_base,
        "d_dyn_full": pred.d_model_dynamic,  # before our scale (from full path inside)
        "d_corr_applied": pred.dynamic_correction,
        "d_final": pred.d_final,
        "d_market": -ah_m,
        "s_base": pred.s_model_base,
        "s_corr_applied": pred.s_dynamic_correction,
        "s_final": pred.s_final,
        "days_since_home": days_home,
        "days_since_away": days_away,
        "days_since_min": None if days_home is None or days_away is None else min(days_home, days_away),
    }


def _summarize(rows: Sequence[Dict[str, Any]], *, current_rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    out: Dict[str, Any] = {
        "n": len(rows),
        "mae_ah": mae([float(r["dah"]) for r in rows]),
        "mae_tot": mae([float(r["dtot"]) for r in rows]),
        "n_ah_ge_05": sum(1 for r in rows if float(r["abs_dah"]) >= 0.5 - 1e-12),
        "n_ah_ge_025": sum(1 for r in rows if float(r["abs_dah"]) >= 0.25 - 1e-12),
        "flip_ah_n": sum(1 for r in rows if r["flip_ah"]),
        "flip_ah_rate": sum(1 for r in rows if r["flip_ah"]) / len(rows),
        "flip_1x2_n": sum(1 for r in rows if r["flip_1x2"]),
        "flip_1x2_rate": sum(1 for r in rows if r["flip_1x2"]) / len(rows),
        "mae_odds_1": mae([float(r["d1_odds"]) for r in rows]),
        "mae_odds_x": mae([float(r["dx_odds"]) for r in rows]),
        "mae_odds_2": mae([float(r["d2_odds"]) for r in rows]),
    }
    if current_rows and len(current_rows) == len(rows):
        # harm vs CURRENT: positive = this arm worse than CURRENT
        by = {(r["league"], r["home"], r["away"]): r for r in current_rows}
        harms = []
        for r in rows:
            c = by[(r["league"], r["home"], r["away"])]
            harms.append(float(r["abs_dah"]) - float(c["abs_dah"]))
        out["harm_vs_CURRENT_mean"] = sum(harms) / len(harms)
        out["harm_vs_CURRENT_pct_pos"] = sum(1 for h in harms if h > 1e-12) / len(harms)
        # benefit: negative harm
        out["benefit_vs_CURRENT_pct"] = sum(1 for h in harms if h < -1e-12) / len(harms)
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


def run() -> None:
    print("loading…")
    all_rows = parse_rows(fetch_all_view_rows())
    by_league_train: Dict[str, List[Row]] = defaultdict(list)
    for r in all_rows:
        if r.season_label == "2025-26":
            by_league_train[r.league_name].append(r)

    leagues = sorted({fx["league"] for fx in FIXTURES})
    models: Dict[str, gmt.TrainedModel] = {}
    last_dates: Dict[str, Dict[str, date]] = {}
    for lg in leagues:
        train = by_league_train.get(lg, [])
        print(f"train {lg} 2025-26 n={len(train)}")
        models[lg] = _silence_train([to_raw_match(r) for r in train])
        last_dates[lg] = {}
        for r in train:
            for tid in (r.home_team_id, r.away_team_id):
                prev = last_dates[lg].get(tid)
                if prev is None or r.match_date > prev:
                    last_dates[lg][tid] = r.match_date

    flat: List[Dict[str, Any]] = []
    # series definitions: name → (d_scale_from_arm, s_scale_from_arm) using SCALES arm value
    series_defs = [
        ("BOTH", lambda s: (s, s)),
        ("D_ONLY", lambda s: (s, 1.0)),
        ("S_ONLY", lambda s: (1.0, s)),
    ]

    for series, scale_fn in series_defs:
        for arm, scale in SCALES:
            d_s, s_s = scale_fn(scale)
            print(f"predict {series}/{arm} d={d_s} s={s_s}")
            for fx in FIXTURES:
                model = models[fx["league"]]
                ld = last_dates[fx["league"]]
                dh = ld.get(fx["home_id"])
                da = ld.get(fx["away_id"])
                days_h = (CARD_DATE - dh).days if dh else None
                days_a = (CARD_DATE - da).days if da else None
                pred = predict_decay(
                    model, fx["home_id"], fx["away_id"],
                    d_scale=d_s, s_scale=s_s,
                    league=fx["league"], season="2025-26",
                    home_odds=fx["o1"], away_odds=fx["o2"],
                    match_date=CARD_DATE,
                )
                # For diagnostics, also need full unscaled d_dyn — re-fetch from CURRENT path once
                flat.append(_pack(
                    series=series, arm=arm, d_scale=d_s, s_scale=s_s,
                    fx=fx, pred=pred, days_home=days_h, days_away=days_a,
                ))

    # Enrich d_dyn_full from BOTH/CURRENT for traces
    current_both = {
        (r["league"], r["home"], r["away"]): r
        for r in flat if r["series"] == "BOTH" and r["arm"] == "CURRENT"
    }
    # Actually d_model_dynamic on scaled preds is still the full dyn from finalize - check
    # finalize sets d_model_dynamic=d_dyn (unscaled). Good.

    summaries: List[Dict[str, Any]] = []
    for series, _ in series_defs:
        cur = [r for r in flat if r["series"] == series and r["arm"] == "CURRENT"]
        for arm, _ in SCALES:
            rs = [r for r in flat if r["series"] == series and r["arm"] == arm]
            s = _summarize(rs, current_rows=cur)
            s.update({"series": series, "arm": arm})
            summaries.append(s)
            print(
                f"  {series}/{arm}: MAE_AH={s['mae_ah']:.3f} n≥0.5={s['n_ah_ge_05']} "
                f"flip={100*s['flip_ah_rate']:.1f}% MAE_Tot={s['mae_tot']:.3f} "
                f"benefit_vs_CUR={100*s.get('benefit_vs_CURRENT_pct', 0):.1f}%"
            )

    # Named problem traces for BOTH series
    trace_keys = {
        ("Premier League", "Nott'm Forest", "Leeds"),
        ("Premier League", "Everton", "Crystal Palace"),
        ("Bundesliga", "FC Koln", "Hoffenheim"),
        ("Bundesliga", "Union Berlin", "Ein Frankfurt"),
        ("Serie A", "Udinese", "Como"),
        ("Serie A", "Genoa", "Napoli"),
    }
    traces = [
        r for r in flat
        if r["series"] == "BOTH" and (r["league"], r["home"], r["away"]) in trace_keys
    ]

    report = _report(summaries, traces, flat)
    for dest in (OUT, REPO):
        dest.mkdir(parents=True, exist_ok=True)
        _write_csv(dest / "preds_flat.csv", flat)
        _write_csv(dest / "summaries.csv", summaries)
        _write_csv(dest / "traces_named_BOTH.csv", traces)
        (dest / "REPORT.md").write_text(report, encoding="utf-8")
        (dest / "summary.json").write_text(
            json.dumps({"summaries": summaries}, indent=2), encoding="utf-8"
        )
    print(report)
    print("wrote", OUT)


def _report(
    summaries: List[Dict[str, Any]],
    traces: List[Dict[str, Any]],
    flat: List[Dict[str, Any]],
) -> str:
    lines = [
        "# EXP-048 — Cross-season Dynamic State Decay",
        "",
        "Offline only. Ratings trained on **2025–26 only**. Validate on Aug-12 book cards (n=34).",
        "D_base / S_base kept; only past-season dynamic correction is scaled.",
        "1X2 uses **same match overround** as the book.",
        "",
        "Arms: CURRENT=100%, DECAY75=75%, DECAY50=50%, DECAY25=25%, RESET=0%.",
        "",
        "Series: **BOTH** (D+S same scale), **D_ONLY** (S full), **S_ONLY** (D full).",
        "",
        "## Summary — BOTH (primary)",
        "",
        "| Arm | MAE AH | N(≥0.5) | flip AH% | MAE Tot | MAE odds1 | benefit vs CURRENT |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm, _ in SCALES:
        s = next(x for x in summaries if x["series"] == "BOTH" and x["arm"] == arm)
        lines.append(
            f"| {arm} | {s['mae_ah']:.3f} | {s['n_ah_ge_05']} | "
            f"{100*s['flip_ah_rate']:.1f}% | {s['mae_tot']:.3f} | {s['mae_odds_1']:.3f} | "
            f"{100*s.get('benefit_vs_CURRENT_pct', 0):.1f}% |"
        )

    lines += [
        "",
        "## Summary — D_ONLY (S-EMA full)",
        "",
        "| Arm | MAE AH | N(≥0.5) | flip AH% | MAE Tot | benefit vs CURRENT |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm, _ in SCALES:
        s = next(x for x in summaries if x["series"] == "D_ONLY" and x["arm"] == arm)
        lines.append(
            f"| {arm} | {s['mae_ah']:.3f} | {s['n_ah_ge_05']} | "
            f"{100*s['flip_ah_rate']:.1f}% | {s['mae_tot']:.3f} | "
            f"{100*s.get('benefit_vs_CURRENT_pct', 0):.1f}% |"
        )

    lines += [
        "",
        "## Summary — S_ONLY (Dynamic D full)",
        "",
        "| Arm | MAE AH | N(≥0.5) | MAE Tot | benefit vs CURRENT |",
        "|---|---:|---:|---:|---:|",
    ]
    for arm, _ in SCALES:
        s = next(x for x in summaries if x["series"] == "S_ONLY" and x["arm"] == arm)
        lines.append(
            f"| {arm} | {s['mae_ah']:.3f} | {s['n_ah_ge_05']} | {s['mae_tot']:.3f} | "
            f"{100*s.get('benefit_vs_CURRENT_pct', 0):.1f}% |"
        )

    # Per-match BOTH for named
    lines += [
        "",
        "## Named openers — BOTH arms AH",
        "",
        "| Match | mkt | CURRENT | DECAY75 | DECAY50 | DECAY25 | RESET | flip@CURRENT |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    named = sorted({(r["league"], r["home"], r["away"]) for r in traces})
    for key in named:
        by_arm = {
            r["arm"]: r for r in traces
            if (r["league"], r["home"], r["away"]) == key
        }
        c = by_arm["CURRENT"]
        lines.append(
            f"| {c['home']}–{c['away']} | {c['ah_mkt']:+.2f} | "
            + " | ".join(f"{by_arm[a]['ah_pred']:+.2f}" for a, _ in SCALES)
            + f" | {c['flip_ah']} |"
        )

    lines += [
        "",
        "### D-path at CURRENT (for context)",
        "",
        "| Match | D_base | D_corr(full) | D_final | D_mkt | days gap (min) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key in named:
        c = next(r for r in traces if (r["league"], r["home"], r["away"]) == key and r["arm"] == "CURRENT")
        # Need full correction — on CURRENT, dynamic_correction is full
        lines.append(
            f"| {c['home']}–{c['away']} | "
            f"{float(c['d_base'] or 0):+.3f} | {float(c['d_corr_applied'] or 0):+.3f} | "
            f"{float(c['d_final'] or 0):+.3f} | {float(c['d_market']):+.3f} | "
            f"{c['days_since_min']} |"
        )

    lines += [
        "",
        "## Notes",
        "",
        "- This run is the **season-boundary** case (2026–27 openers): no new-season matches yet,",
        "  so decay = pure carryover shrink. Within-season re-accumulation after GW1 is a follow-up.",
        "- Time-aware decay (by days since last match) is the natural next step; `days_since_*`",
        "  columns are already emitted for that design.",
        "- Ideal GW shape (RESET→DECAY→CURRENT) needs expanding-window historical eval; this card",
        "  test asks whether any decay beats CURRENT on early openers.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    run()
