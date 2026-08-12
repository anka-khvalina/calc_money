"""Offline early-season dynamics arms vs production baseline.

Arms (policy):
  BASE — Dynamic D + S-EMA full always
  A    — both OFF on GW1–4, full after GW4
  B    — both ×0.25 on GW1–4, full after GW4
  C    — both ×0.50 on GW1–4, full after GW4

Also emits diagnostic fixed-scale metrics per GW bucket (scale 1/0.5/0.25/0)
so we can see when full dynamic becomes useful again.

Does not modify production code.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import goal_model_train as gmt

from experiments.market_weights.data import Row, fetch_all_view_rows, parse_rows, to_raw_match
from experiments.market_weights.eval import load_baseline_config, season_weights_for

from .gw import (
    DIAG_SCALES,
    EVAL_SEASONS,
    POLICY_ARMS,
    TaggedRow,
    gw_bucket,
    policy_scale,
    prior_and_hold,
)
from .metrics import market_1x2_probs, summarize_preds
from .predict_scaled import predict_scaled

ROOT = Path("/workspace")
OUT = Path("/opt/cursor/artifacts/early_season_dyn")
OUT.mkdir(parents=True, exist_ok=True)


def _train(train_rows: List[Row]) -> gmt.TrainedModel:
    raw = [to_raw_match(r, quality_mult=1.0) for r in train_rows]
    if len(raw) < 80:
        raise ValueError(f"too few train rows: {len(raw)}")
    cfg = load_baseline_config(season_weights_for(raw))
    model, _ = gmt.train_full_model(raw, cfg)
    return model


def _pack_pred(
    t: TaggedRow,
    pred: gmt.Prediction,
    *,
    scale: float,
    arm: str,
    mode: str,
) -> Dict[str, Any]:
    r = t.row
    mk = pred.markets
    shin = market_1x2_probs(r.home_odds, r.draw_odds, r.away_odds)
    out: Dict[str, Any] = {
        "mode": mode,
        "arm": arm,
        "scale": scale,
        "league": r.league_name,
        "season": r.season_label,
        "gw": t.gw,
        "bucket": gw_bucket(t.gw),
        "match_id": r.match_id,
        "match_date": r.match_date.isoformat(),
        "home": r.home_team,
        "away": r.away_team,
        "ah_mkt": r.closing_ah_home,
        "tot_mkt": r.closing_total_line,
        "ah_pred": float(mk.main_ah.line),
        "tot_pred": float(mk.main_total.line),
        "p1": float(pred.home_probability_final),
        "px": float(pred.draw_probability_final),
        "p2": float(pred.away_probability_final),
    }
    if shin is not None:
        out["m1"], out["mx"], out["m2"] = shin
    else:
        out["m1"] = out["mx"] = out["m2"] = None
    return out


def _predict_all_scales(
    model: gmt.TrainedModel,
    t: TaggedRow,
) -> Dict[float, gmt.Prediction]:
    """Compute scale∈{0,0.25,0.5,1} with one full + one base predict."""
    from .predict_scaled import finalize_from_ds

    r = t.row
    hid = gmt.team_key(r.home_team_id, r.home_team)
    aid = gmt.team_key(r.away_team_id, r.away_team)
    kw = dict(
        match_date=r.match_date,
        league=r.league_name,
        season=r.season_label,
        home_odds=r.home_odds,
        away_odds=r.away_odds,
        neutral=r.is_neutral,
        derby=gmt._derby_flag_from_weight(r.derby_weight),
    )
    full = predict_scaled(model, hid, aid, scale=1.0, **kw)
    base = predict_scaled(model, hid, aid, scale=0.0, **kw)
    d_base = float(full.d_model_base)
    d_dyn = float(full.d_model_dynamic)
    s_base = float(full.s_model_base)
    s_dyn = float(full.s_model_dynamic)
    out: Dict[float, gmt.Prediction] = {1.0: full, 0.0: base}
    for scale in (0.25, 0.5):
        d_for_cal = d_base + scale * (d_dyn - d_base)
        s_for_cal = s_base + scale * (s_dyn - s_base)
        out[scale] = finalize_from_ds(
            model, hid, aid,
            d_for_cal=d_for_cal,
            s_for_cal=s_for_cal,
            d_model_base=d_base,
            s_model_base=s_base,
            d_model_dynamic=d_dyn,
            s_model_dynamic=s_dyn,
            dynamic_correction=scale * (d_dyn - d_base),
            s_dynamic_correction=scale * (s_dyn - s_base),
            league=r.league_name,
            season=r.season_label,
            home_odds=r.home_odds,
            away_odds=r.away_odds,
        )
    return out


def run() -> Dict[str, Any]:
    print("fetching history…", flush=True)
    rows_all = parse_rows(fetch_all_view_rows())
    print(f"closing rows={len(rows_all)}", flush=True)

    pred_rows: List[Dict[str, Any]] = []
    # Cache trained models: (league, season, gw) → model
    model_cache: Dict[Tuple[str, str, int], gmt.TrainedModel] = {}

    for league, season in EVAL_SEASONS:
        for gw in range(1, 9):
            train_rows, hold = prior_and_hold(rows_all, league, season, gw)
            if len(hold) < 4:
                print(f"skip {league} {season} GW{gw}: hold={len(hold)}", flush=True)
                continue
            if len(train_rows) < 80:
                print(f"skip {league} {season} GW{gw}: train={len(train_rows)}", flush=True)
                continue
            key = (league, season, gw)
            t0 = time.time()
            try:
                model = _train(train_rows)
            except ValueError as e:
                print(f"skip train {key}: {e}", flush=True)
                continue
            model_cache[key] = model
            print(
                f"trained {league} {season} GW{gw}: train={len(train_rows)} "
                f"hold={len(hold)} ({time.time()-t0:.1f}s)",
                flush=True,
            )

            for t in hold:
                try:
                    by_scale = _predict_all_scales(model, t)
                except (ValueError, ZeroDivisionError, KeyError):
                    continue
                for arm in POLICY_ARMS:
                    scale = policy_scale(arm, gw)
                    pred_rows.append(
                        _pack_pred(t, by_scale[scale], scale=scale, arm=arm, mode="policy")
                    )
                for arm_name, scale in DIAG_SCALES:
                    pred_rows.append(
                        _pack_pred(
                            t, by_scale[scale], scale=scale, arm=arm_name, mode="diag"
                        )
                    )

    # --- aggregate ---
    policy = [p for p in pred_rows if p["mode"] == "policy"]
    diag = [p for p in pred_rows if p["mode"] == "diag"]

    buckets = ["GW1", "GW2", "GW3", "GW4", "GW5-8"]
    policy_table: List[Dict[str, Any]] = []
    for b in buckets:
        for arm in POLICY_ARMS:
            xs = [p for p in policy if p["arm"] == arm and p["bucket"] == b]
            sm = summarize_preds(xs)
            sm.update({"bucket": b, "arm": arm, "mode": "policy"})
            policy_table.append(sm)

    # overall GW1-4 policy
    for arm in POLICY_ARMS:
        xs = [p for p in policy if p["arm"] == arm and p["gw"] <= 4]
        sm = summarize_preds(xs)
        sm.update({"bucket": "GW1-4", "arm": arm, "mode": "policy"})
        policy_table.append(sm)

    diag_table: List[Dict[str, Any]] = []
    for b in buckets:
        for arm_name, _scale in DIAG_SCALES:
            xs = [p for p in diag if p["arm"] == arm_name and p["bucket"] == b]
            sm = summarize_preds(xs)
            sm.update({"bucket": b, "arm": arm_name, "mode": "diag"})
            diag_table.append(sm)

    # per-league GW1-4 policy
    by_league: List[Dict[str, Any]] = []
    for league in sorted({p["league"] for p in policy}):
        for arm in POLICY_ARMS:
            xs = [p for p in policy if p["league"] == league and p["arm"] == arm and p["gw"] <= 4]
            sm = summarize_preds(xs)
            sm.update({"league": league, "bucket": "GW1-4", "arm": arm})
            by_league.append(sm)

    summary = {
        "eval_seasons": [{"league": a, "season": b} for a, b in EVAL_SEASONS],
        "n_policy_preds": len(policy),
        "n_diag_preds": len(diag),
        "note": (
            "Policy A/B/C dampen only GW1–4 then use full dynamic (=BASE) on GW5+. "
            "Diag keeps fixed scale in every bucket to find crossover."
        ),
        "policy_table": policy_table,
        "diag_table": diag_table,
        "by_league_gw1_4": by_league,
    }

    # write artifacts
    OUT.mkdir(parents=True, exist_ok=True)
    pred_path = OUT / "pred_rows.csv"
    with pred_path.open("w", newline="", encoding="utf-8") as f:
        if pred_rows:
            w = csv.DictWriter(f, fieldnames=list(pred_rows[0].keys()))
            w.writeheader()
            w.writerows(pred_rows)

    def _write_table(path: Path, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        keys: List[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)

    _write_table(OUT / "policy_metrics.csv", policy_table)
    _write_table(OUT / "diag_metrics.csv", diag_table)
    _write_table(OUT / "by_league_gw1_4.csv", by_league)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # also copy report-friendly markdown stub path under experiments
    print("\n===== POLICY (AH) =====", flush=True)
    print(f"{'bucket':8} {'arm':6} {'n':>4} {'MAE_AH':>7} {'≥0.5':>5} {'≥0.75':>5} {'MAE_Tot':>8} {'≥0.5T':>5} {'MAE_P1':>7}", flush=True)
    for sm in policy_table:
        if sm.get("n", 0) == 0:
            continue
        print(
            f"{sm['bucket']:8} {sm['arm']:6} {sm['n']:4} "
            f"{sm['mae_AH']:.3f}   {sm['n_ah_ge_0_5']:5} {sm['n_ah_ge_0_75']:5} "
            f"{sm['mae_Tot']:.3f}    {sm['n_tot_ge_0_5']:5} "
            f"{(100*sm['mae_P1']) if sm.get('mae_P1') is not None else float('nan'):6.2f}",
            flush=True,
        )

    print("\n===== DIAG fixed scale =====", flush=True)
    print(f"{'bucket':8} {'arm':12} {'n':>4} {'MAE_AH':>7} {'≥0.5':>5} {'≥0.75':>5} {'MAE_Tot':>8}", flush=True)
    for sm in diag_table:
        if sm.get("n", 0) == 0:
            continue
        print(
            f"{sm['bucket']:8} {sm['arm']:12} {sm['n']:4} "
            f"{sm['mae_AH']:.3f}   {sm['n_ah_ge_0_5']:5} {sm['n_ah_ge_0_75']:5} "
            f"{sm['mae_Tot']:.3f}",
            flush=True,
        )

    print(f"\nwrote {OUT}", flush=True)
    return summary


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
