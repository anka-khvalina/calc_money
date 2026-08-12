"""Offline FULL production vs Aug-12 book cards (with margin on 1X2).

Method (same as prior early-season research, not the UI Combined Legacy/Auto path):
  - train per league on all closing history through 2025-26
  - FULL Dynamic D + S-EMA (production config)
  - 1X2: model fair probs × **same match overround** as the book quote
  - AH / Tot: compare **lines** (margin N/A)

Does not modify production code.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import goal_model_train as gmt

from experiments.market_weights.data import fetch_all_view_rows, parse_rows, to_raw_match
from experiments.market_weights.eval import load_baseline_config, season_weights_for
from experiments.early_season_dyn.metrics import (
    market_1x2_probs,
    model_odds_with_market_margin,
)

from .fixtures import FIXTURES

ROOT = Path("/workspace")
OUT = Path("/opt/cursor/artifacts/aug12_market_compare")
OUT.mkdir(parents=True, exist_ok=True)
REPO_OUT = ROOT / "experiments" / "aug12_market_compare"


def _train(league: str, rows) -> gmt.TrainedModel:
    league_rows = [r for r in rows if r.league_name == league]
    raw = [to_raw_match(r) for r in league_rows]
    if len(raw) < 80:
        raise ValueError(f"{league}: too few rows {len(raw)}")
    cfg = load_baseline_config(season_weights_for(raw))
    # silence diagnostics
    old = sys.stdout
    sys.stdout = open("/dev/null", "w")
    try:
        model, _ = gmt.train_full_model(raw, cfg)
    finally:
        sys.stdout.close()
        sys.stdout = old
    return model


def _pack(fx: Dict[str, Any], pred: gmt.Prediction, *, mode: str) -> Dict[str, Any]:
    p1 = float(pred.home_probability_final)
    px = float(pred.draw_probability_final)
    p2 = float(pred.away_probability_final)
    mk = pred.markets
    ah_pred = float(mk.main_ah.line) if mk.main_ah else float("nan")
    tot_pred = float(mk.main_total.line) if mk.main_total else float("nan")
    b1, bx, b2, over = model_odds_with_market_margin(p1, px, p2, fx["o1"], fx["ox"], fx["o2"])
    shin = market_1x2_probs(fx["o1"], fx["ox"], fx["o2"])
    out: Dict[str, Any] = {
        "mode": mode,
        "league": fx["league"],
        "home": fx["home"],
        "away": fx["away"],
        "ah_mkt": fx["ah"],
        "tot_mkt": fx["tot"],
        "ah_pred": ah_pred,
        "tot_pred": tot_pred,
        "dah": ah_pred - fx["ah"] if ah_pred == ah_pred else None,
        "dtot": tot_pred - fx["tot"] if tot_pred == tot_pred else None,
        "o1": fx["o1"],
        "ox": fx["ox"],
        "o2": fx["o2"],
        "p1": p1,
        "px": px,
        "p2": p2,
        "b1m": b1,
        "bxm": bx,
        "b2m": b2,
        "d1": b1 - fx["o1"],
        "dx": bx - fx["ox"],
        "d2": b2 - fx["o2"],
        "overround_pct": (over - 1.0) * 100.0,
        "lh": float(mk.lambda_home),
        "la": float(mk.lambda_away),
    }
    if shin is not None:
        out["m1"], out["mx"], out["m2"] = shin
        out["dp1_pp"] = (p1 - shin[0]) * 100
        out["dpx_pp"] = (px - shin[1]) * 100
        out["dp2_pp"] = (p2 - shin[2]) * 100
    return out


def _summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def mae(key: str) -> Optional[float]:
        xs = [abs(float(r[key])) for r in rows if r.get(key) is not None and r[key] == r[key]]
        return sum(xs) / len(xs) if xs else None

    return {
        "n": len(rows),
        "mae_ah": mae("dah"),
        "mae_tot": mae("dtot"),
        "mae_p1_pp": mae("dp1_pp"),
        "mae_px_pp": mae("dpx_pp"),
        "mae_p2_pp": mae("dp2_pp"),
        "mae_odds_1": mae("d1"),
        "mae_odds_x": mae("dx"),
        "mae_odds_2": mae("d2"),
        "n_ah_ge_05": sum(1 for r in rows if r.get("dah") is not None and abs(r["dah"]) >= 0.5 - 1e-9),
        "n_ah_ge_025": sum(1 for r in rows if r.get("dah") is not None and abs(r["dah"]) >= 0.25 - 1e-9),
    }


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _fmt_odds(x: float) -> str:
    return f"{x:.2f}"


def _report(full: List[Dict[str, Any]], base: List[Dict[str, Any]], summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Aug-12 market cards vs current production model")
    lines.append("")
    lines.append("Source: uploaded «кэфы на 12 августа.docx» (book screenshots).")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("- **Model:** production FULL (`train_full_model` + `predict_match`), Dynamic D + S-EMA on.")
    lines.append("- **Train:** all closing history per league through 2025–26 (not the UI single-season ~337 window).")
    lines.append("- **1X2 odds:** fair model probs × **same match overround** as the book → comparable to quoted odds.")
    lines.append("- **AH / Tot:** compare **main lines** (margin does not apply to the line itself).")
    lines.append("- **BASE** column: same ratings but Dynamic D / S-EMA off (diagnostic).")
    lines.append("- Excluded brand-new clubs without usable history (Racing Santander, Deportivo, Málaga, Troyes, Le Mans, …).")
    lines.append("")
    lines.append("## Summary (n=%d)" % summary["full"]["n"])
    lines.append("")
    lines.append("| arm | MAE AH | |ΔAH|≥0.5 | MAE Tot | MAE p1 pp | MAE odds 1 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for arm in ("full", "base"):
        s = summary[arm]
        lines.append(
            f"| {arm.upper()} | {s['mae_ah']:.3f} | {s['n_ah_ge_05']} | {s['mae_tot']:.3f} | "
            f"{s['mae_p1_pp']:.2f} | {s['mae_odds_1']:.3f} |"
        )
    lines.append("")
    lines.append("## Per match — FULL with margin")
    lines.append("")
    lines.append("| League | Match | AH mkt→mod | Tot mkt→mod | Mkt 1X2 | Model+margin 1X2 | Δodds |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in full:
        lines.append(
            f"| {r['league']} | {r['home']}–{r['away']} | "
            f"{r['ah_mkt']:+.2f}→{r['ah_pred']:+.2f} | "
            f"{r['tot_mkt']:.2f}→{r['tot_pred']:.2f} | "
            f"{_fmt_odds(r['o1'])}/{_fmt_odds(r['ox'])}/{_fmt_odds(r['o2'])} | "
            f"{_fmt_odds(r['b1m'])}/{_fmt_odds(r['bxm'])}/{_fmt_odds(r['b2m'])} | "
            f"{r['d1']:+.2f}/{r['dx']:+.2f}/{r['d2']:+.2f} |"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "UI «Рассчитать линию» can differ: it trains on the loaded season sample and uses "
        "Combined Legacy 1X2 + Auto AH/OU with a fixed league training margin (~3% LL), "
        "not match-specific overround on all-history FULL."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    print("loading history…")
    rows = parse_rows(fetch_all_view_rows())
    by_league = defaultdict(list)
    for fx in FIXTURES:
        by_league[fx["league"]].append(fx)

    models: Dict[str, gmt.TrainedModel] = {}
    for league in by_league:
        print(f"train {league}…")
        models[league] = _train(league, rows)

    full_rows: List[Dict[str, Any]] = []
    base_rows: List[Dict[str, Any]] = []
    for fx in FIXTURES:
        model = models[fx["league"]]
        pred_f = gmt.predict_match(
            model, fx["home_id"], fx["away_id"],
            neutral=False, derby=False, match_date=None, league=fx["league"],
            home_odds=fx["o1"], away_odds=fx["o2"],
        )
        pred_b = gmt.predict_match(
            model, fx["home_id"], fx["away_id"],
            neutral=False, derby=False, match_date=None, league=fx["league"],
            apply_momentum=False, apply_s_momentum=False,
            home_odds=fx["o1"], away_odds=fx["o2"],
        )
        full_rows.append(_pack(fx, pred_f, mode="full"))
        base_rows.append(_pack(fx, pred_b, mode="base"))
        print(
            f"  {fx['home']}-{fx['away']}: AH {fx['ah']:+.2f}→{full_rows[-1]['ah_pred']:+.2f} "
            f"1X2 {_fmt_odds(full_rows[-1]['b1m'])}/{_fmt_odds(full_rows[-1]['bxm'])}/{_fmt_odds(full_rows[-1]['b2m'])}"
        )

    summary = {"full": _summary(full_rows), "base": _summary(base_rows), "n_fixtures": len(FIXTURES)}
    _write_csv(OUT / "compare_full_margined.csv", full_rows)
    _write_csv(OUT / "compare_base_margined.csv", base_rows)
    _write_csv(REPO_OUT / "compare_full_margined.csv", full_rows)
    _write_csv(REPO_OUT / "compare_base_margined.csv", base_rows)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (REPO_OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = _report(full_rows, base_rows, summary)
    (OUT / "REPORT.md").write_text(report, encoding="utf-8")
    (REPO_OUT / "REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
