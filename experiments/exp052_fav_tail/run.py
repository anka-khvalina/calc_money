"""EXP-052 — Strong-favourite tail decomposition (offline, no product edits).

Question: in matches with a market favourite around 1.50–2.00 the model gives the
underdog several pp less probability than the book. Is that caused by a wrong D,
or by the mapping (S, D) -> 1X2?

Decomposition per match (all in fair-probability space):

    D_market      continuous, same inference as training (AH price devig -> D)
    D_base        ratings + home advantage
    D_after_aging D_base + aged dynamic correction
    D_after_cal   dA + dB * D_after_aging            (= D_before_SFA)
    D_after_sfa   SFA applied
    D_final       after clamp / lambda clip

    A  legacy matrix @ model D , model S     (production python path)
    B  legacy matrix @ market D, model S     (oracle on D only)
    B2 legacy matrix @ market D, market S    (oracle on D and S)
    C  auto matrix   @ model D , model S     (alpha/rho grid)
    D  auto matrix   @ market D, model S

Arm A must reproduce predict_match exactly; that is asserted per match.

Dynamic State Aging is frozen ON at H_D = H_S = 60 for the whole run: this
diagnostic describes the model *after* aging, and no parameter is tuned here.

OOS design: expanding by calendar month. For each league and month of the eval
seasons the model trains on every earlier match of that league only.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import dynamic_dc_gamma as ddc
import goal_matrix_auto as gma
import goal_model as gm
import goal_model_train as gmt
import s_momentum as smom

from experiments.market_weights.data import Row, fetch_all_view_rows, parse_rows, to_raw_match
from experiments.market_weights.eval import load_baseline_config, season_weights_for

ROOT = Path("/workspace")
OUT = Path("/opt/cursor/artifacts/exp052_fav_tail")
OUT.mkdir(parents=True, exist_ok=True)
REPO = ROOT / "experiments" / "exp052_fav_tail"

EVAL_SEASONS = ("2024-25", "2025-26")
MIN_TRAIN_ROWS = 150
HALF_LIFE = 60.0
# Auto matrix has no fitted alpha/rho in the python pipeline; report a grid.
AUTO_GRID: Tuple[Tuple[float, float], ...] = ((0.0, 0.0), (0.05, 0.05), (0.10, 0.05))
AUTO_MAIN = (0.05, 0.05)


def train_model(raw: Sequence[gmt.RawMatch]) -> gmt.TrainedModel:
    cfg = load_baseline_config(season_weights_for(list(raw)))
    cfg = replace(
        cfg,
        d_correction_state_aging_enabled=True,
        d_correction_state_aging_half_life_days=HALF_LIFE,
        s_momentum_state_aging_enabled=True,
        s_momentum_state_aging_half_life_days=HALF_LIFE,
    )
    old = sys.stdout
    sys.stdout = open("/dev/null", "w")
    try:
        model, _ = gmt.train_full_model(list(raw), cfg)
    finally:
        sys.stdout.close()
        sys.stdout = old
    return model


# --------------------------------------------------------------------------- #
# Market side
# --------------------------------------------------------------------------- #


def market_state(row: Row, cfg: gmt.ModelConfig) -> Optional[Dict[str, float]]:
    """Continuous D_market / S_market, inferred exactly like training does."""
    if not (row.ah_home_odds and row.ah_away_odds and row.over_odds and row.under_odds):
        return None
    if row.closing_ah_home is None or row.closing_total_line is None:
        return None
    if not (row.home_odds and row.draw_odds and row.away_odds):
        return None
    if min(row.home_odds, row.draw_odds, row.away_odds) <= 1.0:
        return None
    try:
        p_ah_home, _ = gm.devig_two_way(row.ah_home_odds, row.ah_away_odds)
        p_over, _ = gm.devig_two_way(row.over_odds, row.under_odds)
        s_mkt = gm.infer_total_sum(row.closing_total_line, p_over, max_goals=cfg.max_goals)
        d_raw = gm.infer_goal_diff(
            row.closing_ah_home, p_ah_home, s_mkt,
            max_goals=cfg.max_goals, eps=cfg.lambda_epsilon,
        )
        d_mkt = gm.apply_goal_diff_clamp(d_raw, s_mkt, cfg.lambda_epsilon).value
        m1, mx, m2 = gm.shin_devig_1x2(row.home_odds, row.draw_odds, row.away_odds)
    except (ValueError, ZeroDivisionError):
        return None
    return {"d_market": d_mkt, "s_market": s_mkt, "m1": m1, "mx": mx, "m2": m2}


# --------------------------------------------------------------------------- #
# Matrix arms: same tail as predict_match, with S / D injected
# --------------------------------------------------------------------------- #


def _lambdas(model: gmt.TrainedModel, s_in: float, d_in: float) -> Tuple[float, float, float, float]:
    """clamp + lambda_min clip, mirroring predict_match. Returns lh, la, s_use, d_use."""
    cfg = model.config
    scfg = model.s_momentum_cfg or gmt.resolve_s_momentum_config(cfg)
    d = gm.clamp_goal_diff(d_in, s_in, cfg.lambda_epsilon)
    _, _, lh, la, clipped, _, _ = smom.lambdas_from_sd(s_in, d, lambda_min=scfg.lambda_min)
    if clipped:
        return lh, la, lh + la, lh - la
    return lh, la, s_in, d


def legacy_probs(model: gmt.TrainedModel, s_in: float, d_in: float) -> Tuple[float, float, float]:
    """Poisson + dynamic DC gamma + draw model — the python production matrix."""
    cfg = model.config
    lh, la, s_use, d_use = _lambdas(model, s_in, d_in)
    matrix = gm.build_score_matrix(lh, la, cfg.max_goals)
    gamma_season = float(model.calibration.gamma or 0.0) if cfg.use_dixon_coles else 0.0
    dc_res = ddc.resolve_gamma_effective(
        d_use, gamma_season=gamma_season, cfg=gmt.resolve_dynamic_dc_config(cfg),
    )
    gamma_eff = float(dc_res.gamma_effective) if cfg.use_dixon_coles else 0.0
    if cfg.use_dixon_coles and abs(gamma_eff) > 1e-15:
        matrix = gm.apply_dixon_coles(matrix, lh, la, gamma_eff)
    if cfg.use_draw_model:
        px_matrix = gm.draw_probability(matrix)
        if model.draw.mode == "residual_dc":
            target = model.draw.target_q_multiplier(d_use, s_use, cfg) * px_matrix
        else:
            target = model.draw.target_px(d_use, s_use)
        q_min, q_max = gmt.effective_draw_q_bounds(cfg, model.draw.mode)
        matrix, _ = gm.adjust_matrix_to_draw_target(matrix, target, q_min=q_min, q_max=q_max)
    return gm.compute_1x2(matrix)


def auto_probs(
    model: gmt.TrainedModel, s_in: float, d_in: float, *, alpha: float, rho: float,
) -> Tuple[float, float, float]:
    """NB marginals + gaussian copula (no DC, no draw model) — the Auto matrix."""
    cfg = model.config
    scfg = model.s_momentum_cfg or gmt.resolve_s_momentum_config(cfg)
    lh, la, _, _ = _lambdas(model, s_in, d_in)
    matrix = gma.build_joint_score_matrix(
        lh, la, alpha=alpha, rho=rho,
        max_goals=cfg.max_goals, lambda_min=scfg.lambda_min,
    )
    return gm.compute_1x2(matrix)


# --------------------------------------------------------------------------- #
# Segmentation
# --------------------------------------------------------------------------- #


def fav_bucket(fav_odds: float) -> str:
    if fav_odds <= 1.50:
        return "<=1.50"
    if fav_odds <= 1.75:
        return "1.50-1.75"
    if fav_odds <= 2.00:
        return "1.75-2.00"
    return ">2.00"


def pack_row(
    *,
    row: Row,
    league: str,
    season: str,
    pred: gmt.Prediction,
    model: gmt.TrainedModel,
    mkt: Dict[str, float],
) -> Dict[str, Any]:
    o1, o2 = float(row.home_odds), float(row.away_odds)
    fav_home = o1 <= o2
    fav_odds = min(o1, o2)
    d_market = mkt["d_market"]
    s_market = mkt["s_market"]
    s_model = float(pred.s_final)

    # sign so that positive = model overstates the market favourite
    sgn = 1.0 if fav_home else -1.0

    d_base = float(pred.d_model_base)
    d_aging = float(pred.d_model_dynamic)
    d_cal = float(pred.d_before_sfa)
    sfa_d = pred.sfa
    d_after_sfa = float(sfa_d.d_final) if sfa_d is not None else d_cal
    sfa_delta = d_after_sfa - d_cal
    d_final = float(pred.d_final)

    p_a = (
        float(pred.home_probability_final),
        float(pred.draw_probability_final),
        float(pred.away_probability_final),
    )
    p_b = legacy_probs(model, s_model, d_market)
    p_b2 = legacy_probs(model, s_market, d_market)
    auto_model = {
        f"a{int(a*100)}_{int(r*100)}": auto_probs(model, s_model, d_final, alpha=a, rho=r)
        for a, r in AUTO_GRID
    }
    auto_market = {
        f"a{int(a*100)}_{int(r*100)}": auto_probs(model, s_model, d_market, alpha=a, rho=r)
        for a, r in AUTO_GRID
    }
    am_key = f"a{int(AUTO_MAIN[0]*100)}_{int(AUTO_MAIN[1]*100)}"

    def dog(p: Tuple[float, float, float]) -> float:
        return p[2] if fav_home else p[0]

    def fav(p: Tuple[float, float, float]) -> float:
        return p[0] if fav_home else p[2]

    m = (mkt["m1"], mkt["mx"], mkt["m2"])
    out: Dict[str, Any] = {
        "league": league,
        "season": season,
        "match_date": row.match_date.isoformat(),
        "home": row.home_team,
        "away": row.away_team,
        "fav_side": "home" if fav_home else "away",
        "fav_odds": fav_odds,
        "fav_bucket": fav_bucket(fav_odds),
        # D trace, signed toward the market favourite
        "d_market": d_market,
        "dD_base": sgn * (d_base - d_market),
        "dD_aging": sgn * (d_aging - d_market),
        "dD_cal": sgn * (d_cal - d_market),
        "dD_preSFA": sgn * (d_cal - d_market),
        "sfa_effect": sgn * sfa_delta,
        "dD_afterSFA": sgn * (d_after_sfa - d_market),
        "dD_final": sgn * (d_final - d_market),
        "clip_applied": bool(pred.lambda_clipping_applied),
        "s_model": s_model,
        "s_market": s_market,
        "dS": s_model - s_market,
        # probabilities, pp
        "dP_fav_A": (fav(p_a) - fav(m)) * 100,
        "dP_dog_A": (dog(p_a) - dog(m)) * 100,
        "dP_x_A": (p_a[1] - m[1]) * 100,
        "dP_dog_B": (dog(p_b) - dog(m)) * 100,
        "dP_fav_B": (fav(p_b) - fav(m)) * 100,
        "dP_x_B": (p_b[1] - m[1]) * 100,
        "dP_dog_B2": (dog(p_b2) - dog(m)) * 100,
        "dP_dog_autoC": (dog(auto_model[am_key]) - dog(m)) * 100,
        "dP_dog_autoD": (dog(auto_market[am_key]) - dog(m)) * 100,
        "p_dog_market": dog(m) * 100,
    }
    for key, probs in auto_model.items():
        out[f"dP_dog_auto_model_{key}"] = (dog(probs) - dog(m)) * 100
    for key, probs in auto_market.items():
        out[f"dP_dog_auto_market_{key}"] = (dog(probs) - dog(m)) * 100
    return out


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #


def month_key(d: date) -> Tuple[int, int]:
    return d.year, d.month


def run(all_rows: List[Row], *, limit_leagues: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    cfg_probe = load_baseline_config([])
    leagues = sorted({r.league_name for r in all_rows})
    if limit_leagues:
        leagues = [lg for lg in leagues if lg in set(limit_leagues)]
    flat: List[Dict[str, Any]] = []
    checked = 0
    mismatch = 0

    for league in leagues:
        league_rows = sorted(
            [r for r in all_rows if r.league_name == league],
            key=lambda r: (r.match_date, r.match_id),
        )
        eval_rows = [r for r in league_rows if str(r.season_label) in EVAL_SEASONS]
        months = sorted({month_key(r.match_date) for r in eval_rows})
        for y, mth in months:
            hold = [r for r in eval_rows if month_key(r.match_date) == (y, mth)]
            first = min(r.match_date for r in hold)
            train_rows = [r for r in league_rows if r.match_date < first]
            if len(train_rows) < MIN_TRAIN_ROWS or not hold:
                continue
            model = train_model([to_raw_match(r) for r in train_rows])
            print(f"{league} {y}-{mth:02d}: train={len(train_rows)} hold={len(hold)}", flush=True)
            for r in hold:
                mkt = market_state(r, cfg_probe)
                if mkt is None:
                    continue
                hid = gmt.team_key(r.home_team_id, r.home_team)
                aid = gmt.team_key(r.away_team_id, r.away_team)
                try:
                    pred = gmt.predict_match(
                        model, hid, aid,
                        neutral=r.is_neutral,
                        derby=gmt._derby_flag_from_weight(r.derby_weight),
                        match_date=r.match_date,
                        league=league,
                        season=str(r.season_label),
                        home_odds=r.home_odds,
                        away_odds=r.away_odds,
                    )
                except (ValueError, KeyError):
                    continue
                # arm A must reproduce predict_match
                chk = legacy_probs(model, float(pred.s_final), float(pred.d_final))
                checked += 1
                if abs(chk[0] - float(pred.home_probability_final)) > 1e-9:
                    mismatch += 1
                flat.append(
                    pack_row(
                        row=r, league=league, season=str(r.season_label),
                        pred=pred, model=model, mkt=mkt,
                    )
                )
    print(f"arm-A reproduction check: {checked - mismatch}/{checked} exact", flush=True)
    return flat


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def _mean(xs: Sequence[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _share_negative(xs: Sequence[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(1 for x in xs if x < 0) / len(xs) if xs else None


SEGMENTS: List[Tuple[str, Any]] = [
    ("All", lambda r: True),
    ("Fav <=1.50", lambda r: r["fav_bucket"] == "<=1.50"),
    ("Fav 1.50-1.75", lambda r: r["fav_bucket"] == "1.50-1.75"),
    ("Fav 1.75-2.00", lambda r: r["fav_bucket"] == "1.75-2.00"),
    ("Fav >2.00", lambda r: r["fav_bucket"] == ">2.00"),
    ("Home fav 1.50-2.00", lambda r: r["fav_side"] == "home" and r["fav_bucket"] in ("1.50-1.75", "1.75-2.00")),
    ("Away fav 1.50-2.00", lambda r: r["fav_side"] == "away" and r["fav_bucket"] in ("1.50-1.75", "1.75-2.00")),
]


def segment_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seg_defs = list(SEGMENTS)
    for lg in sorted({r["league"] for r in rows}):
        seg_defs.append((f"Away fav 1.50-2.00 {lg}",
                         lambda r, lg=lg: r["league"] == lg and r["fav_side"] == "away"
                         and r["fav_bucket"] in ("1.50-1.75", "1.75-2.00")))
    for name, pred in seg_defs:
        sel = [r for r in rows if pred(r)]
        if not sel:
            continue
        out.append({
            "segment": name,
            "n": len(sel),
            "dD_base": _mean([r["dD_base"] for r in sel]),
            "dD_aging": _mean([r["dD_aging"] for r in sel]),
            "dD_preSFA": _mean([r["dD_preSFA"] for r in sel]),
            "sfa_effect": _mean([r["sfa_effect"] for r in sel]),
            "dD_final": _mean([r["dD_final"] for r in sel]),
            "dP_dog_A": _mean([r["dP_dog_A"] for r in sel]),
            "dP_dog_B": _mean([r["dP_dog_B"] for r in sel]),
            "dP_dog_B2": _mean([r["dP_dog_B2"] for r in sel]),
            "dP_dog_autoC": _mean([r["dP_dog_autoC"] for r in sel]),
            "dP_dog_autoD": _mean([r["dP_dog_autoD"] for r in sel]),
            "dP_x_A": _mean([r["dP_x_A"] for r in sel]),
            "share_neg_A": _share_negative([r["dP_dog_A"] for r in sel]),
            "share_neg_B": _share_negative([r["dP_dog_B"] for r in sel]),
        })
    return out


def season_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for season in sorted({r["season"] for r in rows}):
        for name, pred in (
            ("All", lambda r: True),
            ("Away fav 1.50-2.00", lambda r: r["fav_side"] == "away"
             and r["fav_bucket"] in ("1.50-1.75", "1.75-2.00")),
            ("Home fav 1.50-2.00", lambda r: r["fav_side"] == "home"
             and r["fav_bucket"] in ("1.50-1.75", "1.75-2.00")),
        ):
            sel = [r for r in rows if r["season"] == season and pred(r)]
            if not sel:
                continue
            out.append({
                "season": season,
                "segment": name,
                "n": len(sel),
                "dP_dog_A": _mean([r["dP_dog_A"] for r in sel]),
                "dP_dog_B": _mean([r["dP_dog_B"] for r in sel]),
                "share_neg_A": _share_negative([r["dP_dog_A"] for r in sel]),
                "dD_final": _mean([r["dD_final"] for r in sel]),
            })
    return out


def write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
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


def _fmt(x: Optional[float], nd: int = 2) -> str:
    return "—" if x is None else f"{x:+.{nd}f}"


def report(segs: Sequence[Dict[str, Any]], seasons: Sequence[Dict[str, Any]], n_rows: int) -> str:
    lines: List[str] = []
    lines.append("# EXP-052 — Strong favourite tail decomposition")
    lines.append("")
    lines.append("Diagnostic only: no parameter is tuned, aging / S / SFA are frozen.")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(f"- OOS expanding by month, leagues trained separately, eval seasons {', '.join(EVAL_SEASONS)}.")
    lines.append(f"- Dynamic State Aging frozen ON, `H_D = H_S = {HALF_LIFE:.0f}`.")
    lines.append("- `D_market` inferred from AH prices exactly like training (not `-AH`).")
    lines.append("- All D errors signed so that **positive = model overstates the market favourite**.")
    lines.append("- Probabilities in **pp** vs Shin-devigged market; decimal odds are not used.")
    lines.append("- Arms: A = model D, B = market D (same matrix), B2 = market D and market S,")
    lines.append(f"  auto C / D = Auto matrix (NB+copula, alpha={AUTO_MAIN[0]}, rho={AUTO_MAIN[1]}) at model / market D.")
    lines.append("")
    lines.append(f"n = {n_rows} matches")
    lines.append("")
    lines.append("## Main table")
    lines.append("")
    lines.append("| Segment | n | ΔD_base | ΔD_preSFA | SFA effect | ΔD_final | Auto ΔPdog @modelD | Auto ΔPdog @marketD | Legacy ΔPdog @modelD | Legacy ΔPdog @marketD |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for s in segs:
        lines.append(
            f"| {s['segment']} | {s['n']} | {_fmt(s['dD_base'], 3)} | {_fmt(s['dD_preSFA'], 3)} | "
            f"{_fmt(s['sfa_effect'], 3)} | {_fmt(s['dD_final'], 3)} | "
            f"{_fmt(s['dP_dog_autoC'])} | {_fmt(s['dP_dog_autoD'])} | "
            f"{_fmt(s['dP_dog_A'])} | {_fmt(s['dP_dog_B'])} |"
        )
    lines.append("")
    lines.append("## Draw and oracle-on-S control")
    lines.append("")
    lines.append("| Segment | n | ΔP_x (A) | Legacy ΔPdog @marketD+marketS | share ΔPdog<0 (A) | share ΔPdog<0 (B) |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for s in segs:
        lines.append(
            f"| {s['segment']} | {s['n']} | {_fmt(s['dP_x_A'])} | {_fmt(s['dP_dog_B2'])} | "
            f"{s['share_neg_A']:.2f} | {s['share_neg_B']:.2f} |"
        )
    lines.append("")
    lines.append("## Stability by season")
    lines.append("")
    lines.append("| Season | Segment | n | ΔPdog A | ΔPdog B | share<0 | ΔD_final |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for s in seasons:
        lines.append(
            f"| {s['season']} | {s['segment']} | {s['n']} | {_fmt(s['dP_dog_A'])} | "
            f"{_fmt(s['dP_dog_B'])} | {s['share_neg_A']:.2f} | {_fmt(s['dD_final'], 3)} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="*", default=None)
    args = ap.parse_args()
    print("loading history…", flush=True)
    rows = parse_rows(fetch_all_view_rows())
    flat = run(rows, limit_leagues=args.leagues)
    segs = segment_rows(flat)
    seasons = season_rows(flat)
    for dest in (OUT, REPO):
        dest.mkdir(parents=True, exist_ok=True)
        write_csv(dest / "preds_flat.csv", flat)
        write_csv(dest / "segments.csv", segs)
        write_csv(dest / "by_season.csv", seasons)
        (dest / "summary.json").write_text(
            json.dumps({"n": len(flat), "segments": segs, "by_season": seasons}, indent=2),
            encoding="utf-8",
        )
        (dest / "REPORT.md").write_text(report(segs, seasons, len(flat)), encoding="utf-8")
    print(report(segs, seasons, len(flat)))


if __name__ == "__main__":
    main()
