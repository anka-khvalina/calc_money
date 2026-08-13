"""Offline FULL production vs Aug-12 book cards (with margin on 1X2).

Train modes:
  - all history (default legacy artifacts)
  - **only season 2025-26** (closer to UI loaded-season window)

Does not modify production code.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import goal_model_train as gmt

# Opening cards date for Dynamic State Aging (days since previous match).
MATCH_DATE = date(2026, 8, 12)

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


# Research candidate used for Aug-12 cards (prod default remains OFF).
AGING_HALF_LIFE = 60.0


def _train(league: str, rows, *, season: Optional[str]) -> tuple[gmt.TrainedModel, int]:
    from dataclasses import replace

    league_rows = [r for r in rows if r.league_name == league]
    if season:
        league_rows = [r for r in league_rows if str(r.season_label) == season]
    raw = [to_raw_match(r) for r in league_rows]
    if len(raw) < 80:
        raise ValueError(f"{league}: too few rows {len(raw)} (season={season})")
    cfg = load_baseline_config(season_weights_for(raw))
    # Force Dynamic State Aging H60 for this card compare.
    cfg = replace(
        cfg,
        d_correction_state_aging_enabled=True,
        d_correction_state_aging_half_life_days=AGING_HALF_LIFE,
        s_momentum_state_aging_enabled=True,
        s_momentum_state_aging_half_life_days=AGING_HALF_LIFE,
    )
    old_out, old_err = sys.stdout, sys.stderr
    devnull = open("/dev/null", "w")
    sys.stdout = sys.stderr = devnull
    try:
        model, _ = gmt.train_full_model(raw, cfg)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        devnull.close()
    return model, len(raw)


def _pack(fx: Dict[str, Any], pred: gmt.Prediction, *, mode: str) -> Dict[str, Any]:
    p1 = float(pred.home_probability_final)
    px = float(pred.draw_probability_final)
    p2 = float(pred.away_probability_final)
    mk = pred.markets
    ah_pred = float(mk.main_ah.line) if mk.main_ah else float("nan")
    tot_pred = float(mk.main_total.line) if mk.main_total else float("nan")
    # 1X2: fair model probs × same-match book overround (never raw fair odds).
    b1, bx, b2, over = model_odds_with_market_margin(p1, px, p2, fx["o1"], fx["ox"], fx["o2"])
    shin = market_1x2_probs(fx["o1"], fx["ox"], fx["o2"])
    dc = pred.d_correction
    sm = pred.s_momentum
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
        "home_days_since_prev": getattr(dc, "home_days_since_previous_match", None) if dc else None,
        "away_days_since_prev": getattr(dc, "away_days_since_previous_match", None) if dc else None,
        "home_d_aging": getattr(dc, "home_dynamic_aging_factor", None) if dc else None,
        "away_d_aging": getattr(dc, "away_dynamic_aging_factor", None) if dc else None,
        "d_corr_before_aging": getattr(dc, "d_correction_before_aging", None) if dc else None,
        "d_corr_after_aging": getattr(dc, "d_correction_after_aging", None) if dc else None,
        "home_s_aging": getattr(sm, "home_dynamic_aging_factor", None) if sm else None,
        "away_s_aging": getattr(sm, "away_dynamic_aging_factor", None) if sm else None,
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


def _tag(season: Optional[str]) -> str:
    return f"season_{season.replace('-', '')}" if season else "all_history"


def _report(
    full: List[Dict[str, Any]],
    summary: Dict[str, Any],
    *,
    season: Optional[str],
    train_n: Dict[str, int],
) -> str:
    train_desc = (
        f"only season **{season}** per league (UI-like window)"
        if season
        else "all closing history per league through 2025–26"
    )
    lines: List[str] = []
    lines.append(f"# Aug-12 market cards vs production model ({_tag(season)})")
    lines.append("")
    lines.append("Source: uploaded «кэфы на 12 августа.docx» (book screenshots).")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("- **Model:** production FULL (`train_full_model` + `predict_match`), Dynamic D + S-EMA on.")
    lines.append(
        f"- **Dynamic State Aging:** forced **ON**, `H_D = H_S = {AGING_HALF_LIFE:.0f}` "
        f"(research candidate; prod default remains OFF). "
        f"`match_date={MATCH_DATE.isoformat()}`."
    )
    lines.append(f"- **Train:** {train_desc}.")
    lines.append("- **1X2 odds:** fair model probs × **same match overround** as the book (маржа рынка).")
    lines.append("- **AH / Tot:** compare **main lines**.")
    lines.append("- **BASE:** same ratings, Dynamic D / S-EMA off.")
    lines.append("- Excluded brand-new clubs (Racing Santander, Deportivo, Málaga, Troyes, Le Mans, …).")
    lines.append("")
    lines.append("### Train sizes")
    lines.append("")
    for lg, n in sorted(train_n.items()):
        lines.append(f"- {lg}: **{n}** matches")
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
    lines.append("## 1X2 — рынок vs наши (aging H60 + маржа)")
    lines.append("")
    lines.append("| Лига | Матч | Рынок 1 | X | 2 | Наши+маржа 1 | X | 2 | Δ1 | ΔX | Δ2 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in full:
        lines.append(
            f"| {r['league']} | {r['home']}–{r['away']} | "
            f"{_fmt_odds(r['o1'])} | {_fmt_odds(r['ox'])} | {_fmt_odds(r['o2'])} | "
            f"{_fmt_odds(r['b1m'])} | {_fmt_odds(r['bxm'])} | {_fmt_odds(r['b2m'])} | "
            f"{r['d1']:+.2f} | {r['dx']:+.2f} | {r['d2']:+.2f} |"
        )
    lines.append("")
    lines.append("Δ = наши (fair × overround матча) − рынок. Положительная Δ2 на сильном фаворите = "
                 "мы даём андердогу более длинный кэф (занижаем вероятность dog).")
    lines.append("")
    return "\n".join(lines)


def write_1x2_table(full: List[Dict[str, Any]], dest: Path) -> None:
    """Focused 1X2 card: market vs aging+margin model + deltas."""
    lines = [
        "# Aug-12 — 1X2: рынок vs наши (Dynamic State Aging H60 + маржа)",
        "",
        f"match_date = {MATCH_DATE.isoformat()}; aging ON `H=60`; "
        "наши кэфы = fair probs × overround того же матча.",
        "",
        "| Лига | Матч | Рынок 1 | X | 2 | Наши+маржа 1 | X | 2 | Δ1 | ΔX | Δ2 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in full:
        lines.append(
            f"| {r['league']} | {r['home']}–{r['away']} | "
            f"{_fmt_odds(r['o1'])} | {_fmt_odds(r['ox'])} | {_fmt_odds(r['o2'])} | "
            f"{_fmt_odds(r['b1m'])} | {_fmt_odds(r['bxm'])} | {_fmt_odds(r['b2m'])} | "
            f"{r['d1']:+.2f} | {r['dx']:+.2f} | {r['d2']:+.2f} |"
        )
    lines.append("")
    dest.write_text("\n".join(lines), encoding="utf-8")


def run_once(rows: Sequence, *, season: Optional[str]) -> Dict[str, Any]:
    tag = _tag(season)
    by_league: Dict[str, List] = defaultdict(list)
    for fx in FIXTURES:
        by_league[fx["league"]].append(fx)

    models: Dict[str, gmt.TrainedModel] = {}
    train_n: Dict[str, int] = {}
    for league in by_league:
        print(f"train {league} season={season or 'ALL'}…")
        models[league], train_n[league] = _train(league, rows, season=season)

    full_rows: List[Dict[str, Any]] = []
    base_rows: List[Dict[str, Any]] = []
    for fx in FIXTURES:
        model = models[fx["league"]]
        pred_f = gmt.predict_match(
            model, fx["home_id"], fx["away_id"],
            neutral=False, derby=False, match_date=MATCH_DATE, league=fx["league"],
            home_odds=fx["o1"], away_odds=fx["o2"],
        )
        pred_b = gmt.predict_match(
            model, fx["home_id"], fx["away_id"],
            neutral=False, derby=False, match_date=MATCH_DATE, league=fx["league"],
            apply_momentum=False, apply_s_momentum=False,
            home_odds=fx["o1"], away_odds=fx["o2"],
        )
        full_rows.append(_pack(fx, pred_f, mode="full"))
        base_rows.append(_pack(fx, pred_b, mode="base"))
        print(
            f"  {fx['home']}-{fx['away']}: AH {fx['ah']:+.2f}→{full_rows[-1]['ah_pred']:+.2f} "
            f"1X2 {_fmt_odds(full_rows[-1]['b1m'])}/{_fmt_odds(full_rows[-1]['bxm'])}/{_fmt_odds(full_rows[-1]['b2m'])}"
        )

    summary = {
        "train_season": season or "ALL",
        "train_n": train_n,
        "full": _summary(full_rows),
        "base": _summary(base_rows),
        "n_fixtures": len(FIXTURES),
    }
    prefix = f"compare_{tag}"
    for dest in (OUT, REPO_OUT):
        _write_csv(dest / f"{prefix}_full_margined.csv", full_rows)
        _write_csv(dest / f"{prefix}_base_margined.csv", base_rows)
        (dest / f"summary_{tag}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        report = _report(full_rows, summary, season=season, train_n=train_n)
        (dest / f"REPORT_{tag}.md").write_text(report, encoding="utf-8")
        write_1x2_table(full_rows, dest / f"TABLE_1X2_{tag}.md")
        # keep default names pointing at the requested primary run
        if season == "2025-26":
            _write_csv(dest / "compare_full_margined.csv", full_rows)
            _write_csv(dest / "compare_base_margined.csv", base_rows)
            (dest / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            (dest / "REPORT.md").write_text(report, encoding="utf-8")
            write_1x2_table(full_rows, dest / "TABLE_1X2.md")
            # also a flat CSV for the 1X2 card
            slim = [
                {
                    "league": r["league"],
                    "home": r["home"],
                    "away": r["away"],
                    "mkt_1": r["o1"],
                    "mkt_x": r["ox"],
                    "mkt_2": r["o2"],
                    "ours_1": r["b1m"],
                    "ours_x": r["bxm"],
                    "ours_2": r["b2m"],
                    "d1": r["d1"],
                    "dx": r["dx"],
                    "d2": r["d2"],
                    "overround_pct": r["overround_pct"],
                }
                for r in full_rows
            ]
            _write_csv(dest / "table_1x2.csv", slim)
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--season",
        default="2025-26",
        help="Train season label, or 'ALL' for full history (default: 2025-26)",
    )
    args = ap.parse_args()
    season = None if str(args.season).upper() in {"ALL", "*", "NONE", ""} else str(args.season)
    print("loading history…")
    rows = parse_rows(fetch_all_view_rows())
    run_once(rows, season=season)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
