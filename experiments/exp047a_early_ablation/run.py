"""EXP-047A — Early-season Dynamic D / S-EMA ablation (offline, no product edits).

Arms (everything else identical):
  A FULL : Dynamic D ON,  S-EMA ON
  B      : Dynamic D OFF, S-EMA ON
  C      : Dynamic D ON,  S-EMA OFF
  D BASE : Dynamic D OFF, S-EMA OFF

Buckets: GW1–2, GW3–4, GW5–6, GW7–8
Universe: La Liga / Serie A / Ligue 1 × 2024–25 & 2025–26 (prior season exists).

1X2 always uses same-match overround (never fair vs margined).
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import goal_model_train as gmt

from experiments.market_weights.data import Row, fetch_all_view_rows, parse_rows, to_raw_match
from experiments.market_weights.eval import load_baseline_config, season_weights_for
from experiments.early_season_dyn.gw import EVAL_SEASONS, prior_and_hold_bucket
from experiments.early_season_dyn.metrics import (
    mae,
    market_1x2_probs,
    model_odds_with_market_margin,
)

# Aug-12 openers to force-trace (train = that league's 2025-26 only).
from experiments.aug12_market_compare.fixtures import FIXTURES as AUG12

ROOT = Path("/workspace")
OUT = Path("/opt/cursor/artifacts/exp047a_early_ablation")
OUT.mkdir(parents=True, exist_ok=True)
REPO = ROOT / "experiments" / "exp047a_early_ablation"

ARMS: Dict[str, Tuple[bool, bool]] = {
    "A_FULL": (True, True),
    "B": (False, True),   # D OFF, S ON
    "C": (True, False),   # D ON,  S OFF
    "D_BASE": (False, False),
}

BUCKETS: List[Tuple[str, int, int]] = [
    ("GW1-2", 1, 2),
    ("GW3-4", 3, 4),
    ("GW5-6", 5, 6),
    ("GW7-8", 7, 8),
]

TRACE_KEYS = {
    ("Premier League", "Nott'm Forest", "Leeds"),
    ("Premier League", "Everton", "Crystal Palace"),
    ("Bundesliga", "FC Koln", "Hoffenheim"),
    ("Bundesliga", "Union Berlin", "Ein Frankfurt"),
    ("Serie A", "Udinese", "Como"),
    ("Serie A", "Genoa", "Napoli"),
}


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


def _fav_side_ah(ah: float, eps: float = 1e-9) -> str:
    if ah < -eps:
        return "H"
    if ah > eps:
        return "A"
    return "N"


def _fav_side_1x2(o1: float, o2: float) -> str:
    if o1 < o2:
        return "H"
    if o2 < o1:
        return "A"
    return "N"


def _pack_pred(
    *,
    arm: str,
    league: str,
    season: str,
    bucket: str,
    gw: int,
    row: Row,
    pred: gmt.Prediction,
) -> Dict[str, Any]:
    mk = pred.markets
    ah = float(mk.main_ah.line)
    tot = float(mk.main_total.line)
    p1 = float(pred.home_probability_final)
    px = float(pred.draw_probability_final)
    p2 = float(pred.away_probability_final)
    out: Dict[str, Any] = {
        "arm": arm,
        "league": league,
        "season": season,
        "bucket": bucket,
        "gw": gw,
        "match_id": row.match_id,
        "match_date": row.match_date.isoformat(),
        "home": row.home_team,
        "away": row.away_team,
        "ah_mkt": float(row.closing_ah_home),
        "tot_mkt": float(row.closing_total_line),
        "ah_pred": ah,
        "tot_pred": tot,
        "dah": ah - float(row.closing_ah_home),
        "dtot": tot - float(row.closing_total_line),
        "p1": p1,
        "px": px,
        "p2": p2,
        "o1": row.home_odds,
        "ox": row.draw_odds,
        "o2": row.away_odds,
        "fav_mkt_ah": _fav_side_ah(float(row.closing_ah_home)),
        "fav_mod_ah": _fav_side_ah(ah),
        "flip_ah": _fav_side_ah(float(row.closing_ah_home)) != _fav_side_ah(ah),
        # D / S trace (from this arm's prediction)
        "d_base": pred.d_model_base,
        "d_slow": pred.d_slow,
        "d_fast_corr": pred.d_fast_correction,
        "d_corr_total": pred.dynamic_correction,
        "d_dynamic": pred.d_model_dynamic,
        "d_before_sfa": pred.d_before_sfa,
        "d_final": pred.d_final,
        "d_market": -float(row.closing_ah_home),
        "s_base": pred.s_model_base,
        "s_dynamic": pred.s_model_dynamic,
        "s_corr": pred.s_dynamic_correction,
        "s_final": pred.s_final,
        "s_market": float(row.closing_total_line),
    }
    if (
        row.home_odds is not None
        and row.draw_odds is not None
        and row.away_odds is not None
        and min(row.home_odds, row.draw_odds, row.away_odds) > 1.0
    ):
        b1, bx, b2, over = model_odds_with_market_margin(
            p1, px, p2, float(row.home_odds), float(row.draw_odds), float(row.away_odds)
        )
        out.update(
            b1m=b1, bxm=bx, b2m=b2, overround=over,
            d1_odds=b1 - float(row.home_odds),
            dx_odds=bx - float(row.draw_odds),
            d2_odds=b2 - float(row.away_odds),
            fav_mkt_1x2=_fav_side_1x2(float(row.home_odds), float(row.away_odds)),
            fav_mod_1x2=_fav_side_1x2(b1, b2),
            flip_1x2=_fav_side_1x2(float(row.home_odds), float(row.away_odds)) != _fav_side_1x2(b1, b2),
        )
        shin = market_1x2_probs(row.home_odds, row.draw_odds, row.away_odds)
        if shin:
            out["m1"], out["mx"], out["m2"] = shin
    else:
        out.update(
            b1m=None, bxm=None, b2m=None, overround=None,
            d1_odds=None, dx_odds=None, d2_odds=None,
            fav_mkt_1x2=None, fav_mod_1x2=None, flip_1x2=None,
            m1=None, mx=None, m2=None,
        )
    return out


def _predict_arms(model: gmt.TrainedModel, row: Row) -> Dict[str, gmt.Prediction]:
    hid = gmt.team_key(row.home_team_id, row.home_team)
    aid = gmt.team_key(row.away_team_id, row.away_team)
    kw = dict(
        match_date=row.match_date,
        league=row.league_name,
        season=row.season_label,
        home_odds=row.home_odds,
        away_odds=row.away_odds,
        neutral=row.is_neutral,
        derby=gmt._derby_flag_from_weight(row.derby_weight),
    )
    out: Dict[str, gmt.Prediction] = {}
    for arm, (use_d, use_s) in ARMS.items():
        out[arm] = gmt.predict_match(
            model, hid, aid,
            apply_momentum=use_d,
            apply_s_momentum=use_s,
            **kw,
        )
    return out


def _summarize_arm(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    dah = [abs(float(r["dah"])) for r in rows]
    dtot = [abs(float(r["dtot"])) for r in rows]
    flips = [1 for r in rows if r.get("flip_ah")]
    flips1 = [1 for r in rows if r.get("flip_1x2")]
    out: Dict[str, Any] = {
        "n": len(rows),
        "mae_ah": mae([float(r["dah"]) for r in rows]),
        "mae_tot": mae([float(r["dtot"]) for r in rows]),
        "n_ah_ge_05": sum(1 for e in dah if e >= 0.5 - 1e-12),
        "n_tot_ge_05": sum(1 for e in dtot if e >= 0.5 - 1e-12),
        "flip_ah_n": sum(flips),
        "flip_ah_rate": sum(flips) / len(rows),
        "flip_1x2_n": sum(flips1),
        "flip_1x2_rate": (sum(flips1) / len(rows)) if rows else None,
    }
    # margined 1X2 MAE when available
    d1 = [float(r["d1_odds"]) for r in rows if r.get("d1_odds") is not None]
    if d1:
        out["mae_odds_1"] = mae(d1)
        out["mae_odds_x"] = mae([float(r["dx_odds"]) for r in rows if r.get("dx_odds") is not None])
        out["mae_odds_2"] = mae([float(r["d2_odds"]) for r in rows if r.get("d2_odds") is not None])
        out["n_1x2"] = len(d1)
    return out


def _harm_metrics(
    by_key: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    """Per-match harm of Dynamic D (A vs B on AH) and S-EMA (A vs C on Tot)."""
    harm_d: List[float] = []
    harm_s: List[float] = []
    for arms in by_key.values():
        if not all(k in arms for k in ("A_FULL", "B", "C")):
            continue
        a, b, c = arms["A_FULL"], arms["B"], arms["C"]
        hd = abs(float(a["dah"])) - abs(float(b["dah"]))
        hs = abs(float(a["dtot"])) - abs(float(c["dtot"]))
        harm_d.append(hd)
        harm_s.append(hs)
    def pack(xs: List[float], prefix: str) -> Dict[str, Any]:
        if not xs:
            return {f"{prefix}_n": 0}
        return {
            f"{prefix}_n": len(xs),
            f"{prefix}_mean": sum(xs) / len(xs),
            f"{prefix}_pct_pos": sum(1 for x in xs if x > 1e-12) / len(xs),
            f"{prefix}_pct_neg": sum(1 for x in xs if x < -1e-12) / len(xs),
        }
    out = {}
    out.update(pack(harm_d, "harm_D"))
    out.update(pack(harm_s, "harm_S"))
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


def run_historical(all_rows: List[Row]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Expanding holdout by GW bucket; return flat preds + bucket summaries."""
    flat: List[Dict[str, Any]] = []
    # group preds by (bucket, match_id) → arm → row for harm
    summaries: List[Dict[str, Any]] = []

    for league, season in EVAL_SEASONS:
        for bucket, gw_from, gw_to in BUCKETS:
            train_rows, hold = prior_and_hold_bucket(
                all_rows, league, season, gw_from=gw_from, gw_to=gw_to
            )
            if len(train_rows) < 80 or not hold:
                print(f"skip {league} {season} {bucket}: train={len(train_rows)} hold={len(hold)}")
                continue
            print(f"train {league} {season} {bucket}: n_train={len(train_rows)} hold={len(hold)}")
            model = _silence_train([to_raw_match(r) for r in train_rows])
            by_match: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
            arm_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for t in hold:
                preds = _predict_arms(model, t.row)
                key = (bucket, t.row.match_id)
                by_match[key] = {}
                for arm, pred in preds.items():
                    packed = _pack_pred(
                        arm=arm, league=league, season=season, bucket=bucket,
                        gw=t.gw, row=t.row, pred=pred,
                    )
                    flat.append(packed)
                    arm_rows[arm].append(packed)
                    by_match[key][arm] = packed
            harm = _harm_metrics(by_match)
            for arm, rs in arm_rows.items():
                s = _summarize_arm(rs)
                s.update({"league": league, "season": season, "bucket": bucket, "arm": arm})
                # attach harm only once (same for all arms in bucket) on A_FULL row
                if arm == "A_FULL":
                    s.update(harm)
                summaries.append(s)
            # also bucket-level harm row
            summaries.append({
                "league": league, "season": season, "bucket": bucket, "arm": "HARM",
                **harm,
                "n": harm.get("harm_D_n", 0),
            })
    return flat, summaries


def run_aug12_traces(all_rows: List[Row]) -> List[Dict[str, Any]]:
    """Trace D path for named Aug-12 openers; train = 2025-26 of that league."""
    want = []
    for fx in AUG12:
        if (fx["league"], fx["home"], fx["away"]) in TRACE_KEYS:
            want.append(fx)
    traces: List[Dict[str, Any]] = []
    by_lg: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for fx in want:
        by_lg[fx["league"]].append(fx)

    for league, fxs in by_lg.items():
        train = [r for r in all_rows if r.league_name == league and r.season_label == "2025-26"]
        print(f"trace-train {league} 2025-26 n={len(train)}")
        model = _silence_train([to_raw_match(r) for r in train])
        for fx in fxs:
            # synthetic Row-like predict via ids
            class _R:
                pass
            # use predict directly
            arm_preds = {}
            for arm, (use_d, use_s) in ARMS.items():
                arm_preds[arm] = gmt.predict_match(
                    model, fx["home_id"], fx["away_id"],
                    neutral=False, derby=False, match_date=None,
                    league=league, season="2025-26",
                    home_odds=fx["o1"], away_odds=fx["o2"],
                    apply_momentum=use_d, apply_s_momentum=use_s,
                )
            a = arm_preds["A_FULL"]
            b = arm_preds["B"]
            d = arm_preds["D_BASE"]
            c = arm_preds["C"]
            ah_m = float(fx["ah"])
            tot_m = float(fx["tot"])
            ah_a = float(a.markets.main_ah.line)
            ah_b = float(b.markets.main_ah.line)
            ah_d = float(d.markets.main_ah.line)
            tot_a = float(a.markets.main_total.line)
            tot_c = float(c.markets.main_total.line)
            p1 = float(a.home_probability_final)
            px = float(a.draw_probability_final)
            p2 = float(a.away_probability_final)
            b1, bx, b2, over = model_odds_with_market_margin(p1, px, p2, fx["o1"], fx["ox"], fx["o2"])
            # BASE margined
            p1d = float(d.home_probability_final)
            pxd = float(d.draw_probability_final)
            p2d = float(d.away_probability_final)
            d1m, dxm, d2m, _ = model_odds_with_market_margin(p1d, pxd, p2d, fx["o1"], fx["ox"], fx["o2"])
            traces.append({
                "league": league,
                "home": fx["home"],
                "away": fx["away"],
                "ah_market": ah_m,
                "tot_market": tot_m,
                "ah_FULL": ah_a,
                "ah_B_Doff": ah_b,
                "ah_BASE": ah_d,
                "tot_FULL": tot_a,
                "tot_C_Soff": tot_c,
                "tot_BASE": float(d.markets.main_total.line),
                "harm_D": abs(ah_a - ah_m) - abs(ah_b - ah_m),
                "harm_S": abs(tot_a - tot_m) - abs(tot_c - tot_m),
                "flip_ah_FULL": _fav_side_ah(ah_m) != _fav_side_ah(ah_a),
                "flip_ah_BASE": _fav_side_ah(ah_m) != _fav_side_ah(ah_d),
                "fav_mkt": _fav_side_ah(ah_m),
                "fav_FULL": _fav_side_ah(ah_a),
                "fav_BASE": _fav_side_ah(ah_d),
                # D trace from FULL
                "D_base": a.d_model_base,
                "slow_D": a.d_slow,
                "fast_D": a.d_fast_correction,
                "D_correction": a.dynamic_correction,
                "D_after_dynamic": a.d_model_dynamic,
                "D_after_cal": a.d_before_sfa,
                "D_after_SFA": a.d_final,  # after SFA (= d_final pre clamp w/ S)
                "D_final": a.d_final,
                "D_market": -ah_m,
                "slow_bias_home": a.slow_bias_home,
                "slow_bias_away": a.slow_bias_away,
                "fast_bias_home": a.fast_bias_home,
                "fast_bias_away": a.fast_bias_away,
                "slow_n_home": a.slow_observations_home,
                "slow_n_away": a.slow_observations_away,
                "fast_n_home": a.fast_observations_home,
                "fast_n_away": a.fast_observations_away,
                "s_base": a.s_model_base,
                "s_dynamic": a.s_model_dynamic,
                "s_corr": a.s_dynamic_correction,
                "s_final": a.s_final,
                # margined 1X2
                "o1": fx["o1"], "ox": fx["ox"], "o2": fx["o2"],
                "FULL_1x2_m": f"{b1:.3f}/{bx:.3f}/{b2:.3f}",
                "BASE_1x2_m": f"{d1m:.3f}/{dxm:.3f}/{d2m:.3f}",
                "overround_pct": (over - 1) * 100,
                "flip_1x2_FULL": _fav_side_1x2(fx["o1"], fx["o2"]) != _fav_side_1x2(b1, b2),
            })
            print(
                f"  TRACE {fx['home']}-{fx['away']}: "
                f"D_base={a.d_model_base:.3f} dyn_corr={a.dynamic_correction:.3f} "
                f"D_dyn={a.d_model_dynamic:.3f} D_final={a.d_final:.3f} D_mkt={-ah_m:.3f} "
                f"AH {ah_m:+.2f}→FULL{ah_a:+.2f}/BASE{ah_d:+.2f} "
                f"flip={_fav_side_ah(ah_m)!=_fav_side_ah(ah_a)} harm_D={abs(ah_a-ah_m)-abs(ah_b-ah_m):+.2f}"
            )
    return traces


def aggregate_by_bucket(summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pool across leagues/seasons within bucket×arm."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for s in summaries:
        if s.get("arm") == "HARM":
            continue
        if not s.get("n"):
            continue
        groups[(s["bucket"], s["arm"])].append(s)
    out: List[Dict[str, Any]] = []
    for (bucket, arm), xs in sorted(groups.items()):
        n = sum(int(x["n"]) for x in xs)
        def wavg(key: str) -> Optional[float]:
            num = den = 0.0
            for x in xs:
                if x.get(key) is None:
                    continue
                num += float(x[key]) * int(x["n"])
                den += int(x["n"])
            return num / den if den else None
        row = {
            "bucket": bucket,
            "arm": arm,
            "n": n,
            "mae_ah": wavg("mae_ah"),
            "mae_tot": wavg("mae_tot"),
            "n_ah_ge_05": sum(int(x.get("n_ah_ge_05") or 0) for x in xs),
            "flip_ah_n": sum(int(x.get("flip_ah_n") or 0) for x in xs),
            "flip_ah_rate": wavg("flip_ah_rate"),
            "flip_1x2_rate": wavg("flip_1x2_rate"),
            "mae_odds_1": wavg("mae_odds_1"),
        }
        out.append(row)
    # harm pooled: recompute from flat would be better — done in report from flat
    return out


def harm_by_bucket(flat: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by: Dict[str, Dict[Tuple[str, str], Dict[str, Dict[str, Any]]]] = defaultdict(dict)
    for r in flat:
        key = (r["season"], r["match_id"])
        by[r["bucket"]].setdefault(key, {})[r["arm"]] = r
    out = []
    for bucket, matches in sorted(by.items()):
        harm = _harm_metrics(matches)
        # also fav flip rates on FULL
        fulls = [arms["A_FULL"] for arms in matches.values() if "A_FULL" in arms]
        bases = [arms["D_BASE"] for arms in matches.values() if "D_BASE" in arms]
        out.append({
            "bucket": bucket,
            **harm,
            "flip_ah_FULL": sum(1 for r in fulls if r.get("flip_ah")) / len(fulls) if fulls else None,
            "flip_ah_BASE": sum(1 for r in bases if r.get("flip_ah")) / len(bases) if bases else None,
            "n": len(fulls),
        })
    return out


def build_report(
    agg: List[Dict[str, Any]],
    harm: List[Dict[str, Any]],
    traces: List[Dict[str, Any]],
) -> str:
    lines = [
        "# EXP-047A — Early-season ablation (Dynamic D × S-EMA)",
        "",
        "Offline only. Ratings / A-D / calib / SFA / SFTC / weights unchanged across arms.",
        "1X2 uses **same match overround** as the book.",
        "",
        "## Arms",
        "",
        "| Arm | Dynamic D | S-EMA |",
        "|---|---|---|",
        "| A FULL | ON | ON |",
        "| B | OFF | ON |",
        "| C | ON | OFF |",
        "| D BASE | OFF | OFF |",
        "",
        "harm_D = |AH_A − AH_mkt| − |AH_B − AH_mkt|  (isolates Dynamic D)",
        "",
        "harm_S = |Tot_A − Tot_mkt| − |Tot_C − Tot_mkt|  (isolates S-EMA)",
        "",
        "## Pooled by GW bucket",
        "",
        "| Bucket | Arm | n | MAE AH | N(≥0.5) | MAE Tot | flip AH% | MAE odds1 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in agg:
        lines.append(
            f"| {r['bucket']} | {r['arm']} | {r['n']} | "
            f"{r['mae_ah']:.3f} | {r['n_ah_ge_05']} | {r['mae_tot']:.3f} | "
            f"{100*(r['flip_ah_rate'] or 0):.1f}% | "
            f"{(r['mae_odds_1'] if r['mae_odds_1'] is not None else float('nan')):.3f} |"
        )
    lines += [
        "",
        "## Dynamic harm by bucket",
        "",
        "| Bucket | n | harm_D mean | harm_D %pos | harm_S mean | harm_S %pos | flip FULL | flip BASE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in harm:
        lines.append(
            f"| {r['bucket']} | {r['n']} | {r.get('harm_D_mean', float('nan')):.4f} | "
            f"{100*r.get('harm_D_pct_pos', 0):.1f}% | {r.get('harm_S_mean', float('nan')):.4f} | "
            f"{100*r.get('harm_S_pct_pos', 0):.1f}% | "
            f"{100*(r.get('flip_ah_FULL') or 0):.1f}% | {100*(r.get('flip_ah_BASE') or 0):.1f}% |"
        )
    lines += [
        "",
        "## D-trace — Aug-12 named openers (train 2025–26)",
        "",
        "| Match | D_base | slow_D | fast_D | D_corr | D_dyn | D_cal/SFA | D_mkt | AH BASE/FULL/mkt | harm_D | flip |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for t in traces:
        lines.append(
            f"| {t['home']}–{t['away']} | "
            f"{float(t['D_base']):+.3f} | {float(t['slow_D'] or 0):+.3f} | "
            f"{float(t['fast_D'] or 0):+.3f} | {float(t['D_correction'] or 0):+.3f} | "
            f"{float(t['D_after_dynamic']):+.3f} | {float(t['D_final']):+.3f} | "
            f"{float(t['D_market']):+.3f} | "
            f"{t['ah_BASE']:+.2f}/{t['ah_FULL']:+.2f}/{t['ah_market']:+.2f} | "
            f"{t['harm_D']:+.2f} | {t['flip_ah_FULL']} |"
        )
    lines += [
        "",
        "### Margined 1X2 on traces",
        "",
        "| Match | Market | FULL+margin | BASE+margin | flip 1X2 |",
        "|---|---|---|---|---|",
    ]
    for t in traces:
        lines.append(
            f"| {t['home']}–{t['away']} | "
            f"{t['o1']:.2f}/{t['ox']:.2f}/{t['o2']:.2f} | "
            f"{t['FULL_1x2_m']} | {t['BASE_1x2_m']} | {t['flip_1x2_FULL']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    print("loading…")
    rows = parse_rows(fetch_all_view_rows())
    print(f"rows={len(rows)}")
    flat, summaries = run_historical(rows)
    traces = run_aug12_traces(rows)
    agg = aggregate_by_bucket(summaries)
    harm = harm_by_bucket(flat)
    report = build_report(agg, harm, traces)

    for dest in (OUT, REPO):
        dest.mkdir(parents=True, exist_ok=True)
        _write_csv(dest / "preds_flat.csv", flat)
        _write_csv(dest / "summaries_league.csv", summaries)
        _write_csv(dest / "summaries_bucket.csv", agg)
        _write_csv(dest / "harm_by_bucket.csv", harm)
        _write_csv(dest / "d_traces_aug12.csv", traces)
        (dest / "REPORT.md").write_text(report, encoding="utf-8")
        (dest / "summary.json").write_text(
            json.dumps({"agg": agg, "harm": harm, "traces": traces}, indent=2, default=str),
            encoding="utf-8",
        )
    print(report)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
