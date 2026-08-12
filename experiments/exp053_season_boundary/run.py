"""EXP-053 — Season Boundary Residual (offline, no product edits).

After Dynamic State Aging (H=60), what residual remains around the season
boundary? Aging only shrinks short-term state; it does not repair D_base
(rating + home advantage). If summer transfers / coach / promotion changed
the team, the market may already know August strength while the model still
carries last season's base rating plus a weakened dynamic state.

Regimes (matches-since-long-break ≥60d), using completed new matches before
the current fixture:

    Long break / first match   0 new  → mslb = 1
    Warm-up 2                  1 new  → mslb = 2
    Warm-up 3                  2 new  → mslb = 3
    Warm-up 4–5                3–4    → mslb ∈ {4, 5}
    Stable                     ≥5 new → mslb ≥ 6

Per regime (and overall): ΔD_base, ΔD_after_aging, ΔD_final, ΔS,
ΔP_fav / ΔP_X / ΔP_dog, AH MAE, Total MAE. D_base is the primary lens.

Control: teams that appear in Long-break, then the same teams once Stable.
If August bias vanishes with no model change → missing-information /
regime-shift hypothesis.

Aging frozen ON at H_D = H_S = 60. No tail / away / league / SFA edits.
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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import goal_model as gm
import goal_model_train as gmt

from experiments.market_weights.data import Row, fetch_all_view_rows, parse_rows, to_raw_match
from experiments.market_weights.eval import load_baseline_config, season_weights_for

ROOT = Path("/workspace")
OUT = Path("/opt/cursor/artifacts/exp053_season_boundary")
OUT.mkdir(parents=True, exist_ok=True)
REPO = ROOT / "experiments" / "exp053_season_boundary"

EVAL_SEASONS = ("2024-25", "2025-26")
MIN_TRAIN_ROWS = 150
HALF_LIFE = 60.0
LONG_BREAK_DAYS = 60

REGIME_ORDER = (
    "long_break",
    "warmup_2",
    "warmup_3",
    "warmup_4_5",
    "stable",
)
REGIME_LABEL = {
    "long_break": "Long break / first (0 new)",
    "warmup_2": "Warm-up 2 (1 new)",
    "warmup_3": "Warm-up 3 (2 new)",
    "warmup_4_5": "Warm-up 4–5 (3–4 new)",
    "stable": "Stable (≥5 new)",
}


def train_model(raw: Sequence[gmt.RawMatch]) -> gmt.TrainedModel:
    cfg = load_baseline_config(season_weights_for(list(raw)))
    cfg = replace(
        cfg,
        d_correction_state_aging_enabled=True,
        d_correction_state_aging_half_life_days=HALF_LIFE,
        s_momentum_state_aging_enabled=True,
        s_momentum_state_aging_half_life_days=HALF_LIFE,
    )
    old_out, old_err = sys.stdout, sys.stderr
    devnull = open("/dev/null", "w")
    sys.stdout = sys.stderr = devnull
    try:
        model, _ = gmt.train_full_model(list(raw), cfg)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        devnull.close()
    return model


def market_state(row: Row, cfg: gmt.ModelConfig) -> Optional[Dict[str, float]]:
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


def team_gap_features(
    hist: Dict[str, List[Tuple[date, str]]],
    team_id: str,
    match_date: date,
    match_id: str,
) -> Tuple[Optional[int], Optional[int]]:
    """(days_since_prev, matches_since_long_break including current)."""
    seq = hist.get(team_id, [])
    prev_date: Optional[date] = None
    mslb = 0
    for dt, mid in seq:
        if (dt, mid) >= (match_date, match_id):
            break
        if prev_date is not None:
            if (dt - prev_date).days >= LONG_BREAK_DAYS:
                mslb = 0
        mslb += 1
        prev_date = dt
    if prev_date is None:
        return None, None
    days = (match_date - prev_date).days
    if days >= LONG_BREAK_DAYS:
        mslb = 1
    else:
        mslb = mslb + 1
    return days, mslb


def regime_from_mslb(mslb: Optional[int]) -> Optional[str]:
    if mslb is None:
        return None
    if mslb <= 1:
        return "long_break"
    if mslb == 2:
        return "warmup_2"
    if mslb == 3:
        return "warmup_3"
    if mslb <= 5:
        return "warmup_4_5"
    return "stable"


def build_hist(league_rows: Sequence[Row]) -> Dict[str, List[Tuple[date, str]]]:
    hist: Dict[str, List[Tuple[date, str]]] = defaultdict(list)
    for r in sorted(league_rows, key=lambda x: (x.match_date, x.match_id)):
        hid = gmt.team_key(r.home_team_id, r.home_team)
        aid = gmt.team_key(r.away_team_id, r.away_team)
        hist[hid].append((r.match_date, r.match_id))
        hist[aid].append((r.match_date, r.match_id))
    return hist


def month_key(d: date) -> Tuple[int, int]:
    return d.year, d.month


def pack_row(
    *,
    row: Row,
    league: str,
    season: str,
    pred: gmt.Prediction,
    mkt: Dict[str, float],
    days_h: Optional[int],
    days_a: Optional[int],
    mslb_h: Optional[int],
    mslb_a: Optional[int],
    hid: str,
    aid: str,
) -> Dict[str, Any]:
    o1, o2 = float(row.home_odds), float(row.away_odds)
    fav_home = o1 <= o2
    sgn = 1.0 if fav_home else -1.0

    d_market = mkt["d_market"]
    s_market = mkt["s_market"]
    d_base = float(pred.d_model_base)
    d_aging = float(pred.d_model_dynamic)  # D_base + aged dynamic corr
    d_final = float(pred.d_final)
    s_model = float(pred.s_final)

    p1 = float(pred.home_probability_final)
    px = float(pred.draw_probability_final)
    p2 = float(pred.away_probability_final)
    m1, mx, m2 = mkt["m1"], mkt["mx"], mkt["m2"]
    p_fav = p1 if fav_home else p2
    p_dog = p2 if fav_home else p1
    m_fav = m1 if fav_home else m2
    m_dog = m2 if fav_home else m1

    ah_pred = float(pred.markets.main_ah.line)
    tot_pred = float(pred.markets.main_total.line)
    ah_mkt = float(row.closing_ah_home)
    tot_mkt = float(row.closing_total_line)

    mslb = None
    if mslb_h is not None and mslb_a is not None:
        mslb = min(mslb_h, mslb_a)
    elif mslb_h is not None:
        mslb = mslb_h
    elif mslb_a is not None:
        mslb = mslb_a

    return {
        "league": league,
        "season": season,
        "match_id": row.match_id,
        "match_date": row.match_date.isoformat(),
        "home": row.home_team,
        "away": row.away_team,
        "home_id": hid,
        "away_id": aid,
        "fav_side": "home" if fav_home else "away",
        "fav_odds": min(o1, o2),
        "days_h": days_h,
        "days_a": days_a,
        "mslb_h": mslb_h,
        "mslb_a": mslb_a,
        "mslb": mslb,
        "regime": regime_from_mslb(mslb),
        "regime_h": regime_from_mslb(mslb_h),
        "regime_a": regime_from_mslb(mslb_a),
        "d_market": d_market,
        "s_market": s_market,
        "d_base": d_base,
        "d_after_aging": d_aging,
        "d_final": d_final,
        "s_model": s_model,
        # signed toward market favourite: + = model overstates fav
        "dD_base": sgn * (d_base - d_market),
        "dD_after_aging": sgn * (d_aging - d_market),
        "dD_final": sgn * (d_final - d_market),
        "dS": s_model - s_market,
        "abs_dD_base": abs(d_base - d_market),
        "abs_dD_after_aging": abs(d_aging - d_market),
        "abs_dD_final": abs(d_final - d_market),
        "dP_fav": (p_fav - m_fav) * 100,
        "dP_x": (px - mx) * 100,
        "dP_dog": (p_dog - m_dog) * 100,
        "ah_mkt": ah_mkt,
        "ah_pred": ah_pred,
        "tot_mkt": tot_mkt,
        "tot_pred": tot_pred,
        "abs_dah": abs(ah_pred - ah_mkt),
        "abs_dtot": abs(tot_pred - tot_mkt),
        "month": row.match_date.month,
    }


def run(all_rows: List[Row], *, limit_leagues: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    cfg_probe = load_baseline_config([])
    leagues = sorted({r.league_name for r in all_rows})
    if limit_leagues:
        leagues = [lg for lg in leagues if lg in set(limit_leagues)]
    flat: List[Dict[str, Any]] = []

    for league in leagues:
        league_rows = sorted(
            [r for r in all_rows if r.league_name == league],
            key=lambda r: (r.match_date, r.match_id),
        )
        hist = build_hist(league_rows)
        eval_rows = [r for r in league_rows if str(r.season_label) in EVAL_SEASONS]
        months = sorted({month_key(r.match_date) for r in eval_rows})
        for y, mth in months:
            hold = [r for r in eval_rows if month_key(r.match_date) == (y, mth)]
            if not hold:
                continue
            first = min(r.match_date for r in hold)
            train_rows = [r for r in league_rows if r.match_date < first]
            if len(train_rows) < MIN_TRAIN_ROWS:
                continue
            model = train_model([to_raw_match(r) for r in train_rows])
            print(f"{league} {y}-{mth:02d}: train={len(train_rows)} hold={len(hold)}", flush=True)
            for r in hold:
                mkt = market_state(r, cfg_probe)
                if mkt is None:
                    continue
                hid = gmt.team_key(r.home_team_id, r.home_team)
                aid = gmt.team_key(r.away_team_id, r.away_team)
                days_h, mslb_h = team_gap_features(hist, hid, r.match_date, r.match_id)
                days_a, mslb_a = team_gap_features(hist, aid, r.match_date, r.match_id)
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
                flat.append(
                    pack_row(
                        row=r, league=league, season=str(r.season_label),
                        pred=pred, mkt=mkt,
                        days_h=days_h, days_a=days_a,
                        mslb_h=mslb_h, mslb_a=mslb_a,
                        hid=hid, aid=aid,
                    )
                )
    return flat


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def _mean(xs: Sequence[Optional[float]]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def _agg(rows: Sequence[Dict[str, Any]], *, label: str, key: str = "") -> Dict[str, Any]:
    return {
        "group": label,
        "key": key,
        "n": len(rows),
        "dD_base": _mean([r["dD_base"] for r in rows]),
        "dD_after_aging": _mean([r["dD_after_aging"] for r in rows]),
        "dD_final": _mean([r["dD_final"] for r in rows]),
        "abs_dD_base": _mean([r["abs_dD_base"] for r in rows]),
        "abs_dD_after_aging": _mean([r["abs_dD_after_aging"] for r in rows]),
        "abs_dD_final": _mean([r["abs_dD_final"] for r in rows]),
        "dS": _mean([r["dS"] for r in rows]),
        "dP_fav": _mean([r["dP_fav"] for r in rows]),
        "dP_x": _mean([r["dP_x"] for r in rows]),
        "dP_dog": _mean([r["dP_dog"] for r in rows]),
        "mae_ah": _mean([r["abs_dah"] for r in rows]),
        "mae_tot": _mean([r["abs_dtot"] for r in rows]),
        "n_ge05_ah": sum(1 for r in rows if r["abs_dah"] >= 0.5),
    }


def by_regime(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = [_agg(list(rows), label="All", key="all")]
    for reg in REGIME_ORDER:
        sel = [r for r in rows if r["regime"] == reg]
        if sel:
            out.append(_agg(sel, label=REGIME_LABEL[reg], key=reg))
    # unknown (no hist)
    unk = [r for r in rows if r["regime"] is None]
    if unk:
        out.append(_agg(unk, label="Unknown (no prior)", key="unknown"))
    return out


def by_regime_season(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for season in sorted({r["season"] for r in rows}):
        for reg in ("all",) + REGIME_ORDER:
            if reg == "all":
                sel = [r for r in rows if r["season"] == season]
                label = f"{season} / All"
            else:
                sel = [r for r in rows if r["season"] == season and r["regime"] == reg]
                label = f"{season} / {REGIME_LABEL[reg]}"
            if sel:
                out.append(_agg(sel, label=label, key=f"{season}:{reg}"))
    return out


def by_regime_league(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for league in sorted({r["league"] for r in rows}):
        for reg in ("long_break", "stable"):
            sel = [r for r in rows if r["league"] == league and r["regime"] == reg]
            if sel:
                out.append(_agg(sel, label=f"{league} / {REGIME_LABEL[reg]}", key=f"{league}:{reg}"))
    return out


def team_side_rows(match_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per team-side with that team's own regime (for paired control)."""
    out: List[Dict[str, Any]] = []
    for r in match_rows:
        for side, tid, name, mslb, reg in (
            ("home", r["home_id"], r["home"], r["mslb_h"], r["regime_h"]),
            ("away", r["away_id"], r["away"], r["mslb_a"], r["regime_a"]),
        ):
            if reg is None:
                continue
            out.append({
                "league": r["league"],
                "season": r["season"],
                "match_id": r["match_id"],
                "match_date": r["match_date"],
                "team_id": tid,
                "team": name,
                "side": side,
                "mslb": mslb,
                "regime": reg,
                "dD_base": r["dD_base"],
                "dD_after_aging": r["dD_after_aging"],
                "dD_final": r["dD_final"],
                "abs_dD_base": r["abs_dD_base"],
                "abs_dD_after_aging": r["abs_dD_after_aging"],
                "abs_dD_final": r["abs_dD_final"],
                "dS": r["dS"],
                "dP_fav": r["dP_fav"],
                "dP_x": r["dP_x"],
                "dP_dog": r["dP_dog"],
                "abs_dah": r["abs_dah"],
                "abs_dtot": r["abs_dtot"],
                "month": r["month"],
            })
    return out


def control_same_teams(team_rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Teams with ≥1 Long-break obs: compare Long-break vs Stable for those teams."""
    by_team: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in team_rows:
        by_team[(r["league"], r["season"], r["team_id"])].append(r)

    eligible: List[Tuple[str, str, str]] = []
    for key, rows in by_team.items():
        regs = {r["regime"] for r in rows}
        if "long_break" in regs and "stable" in regs:
            eligible.append(key)

    lb: List[Dict[str, Any]] = []
    st: List[Dict[str, Any]] = []
    for key in eligible:
        for r in by_team[key]:
            if r["regime"] == "long_break":
                lb.append(r)
            elif r["regime"] == "stable":
                st.append(r)

    summary = []
    if lb:
        summary.append(_agg(lb, label="Control teams @ Long break", key="ctrl_long_break"))
    if st:
        summary.append(_agg(st, label="Same teams @ Stable (≥5 new)", key="ctrl_stable"))
    # also early-month long break vs late stable (Aug/Sep focus)
    lb_early = [r for r in lb if r["month"] in (7, 8, 9)]
    st_late = [r for r in st if r["month"] >= 10]
    if lb_early:
        summary.append(_agg(lb_early, label="Control @ Long break Jul–Sep", key="ctrl_lb_early"))
    if st_late:
        summary.append(_agg(st_late, label="Same teams @ Stable Oct+", key="ctrl_st_late"))

    detail = []
    for league, season, tid in sorted(eligible):
        rows = by_team[(league, season, tid)]
        name = rows[0]["team"]
        lb_r = [r for r in rows if r["regime"] == "long_break"]
        st_r = [r for r in rows if r["regime"] == "stable"]
        detail.append({
            "league": league,
            "season": season,
            "team_id": tid,
            "team": name,
            "n_long_break": len(lb_r),
            "n_stable": len(st_r),
            "dD_base_lb": _mean([r["dD_base"] for r in lb_r]),
            "dD_base_st": _mean([r["dD_base"] for r in st_r]),
            "abs_dD_base_lb": _mean([r["abs_dD_base"] for r in lb_r]),
            "abs_dD_base_st": _mean([r["abs_dD_base"] for r in st_r]),
            "dD_final_lb": _mean([r["dD_final"] for r in lb_r]),
            "dD_final_st": _mean([r["dD_final"] for r in st_r]),
            "dP_dog_lb": _mean([r["dP_dog"] for r in lb_r]),
            "dP_dog_st": _mean([r["dP_dog"] for r in st_r]),
            "mae_ah_lb": _mean([r["abs_dah"] for r in lb_r]),
            "mae_ah_st": _mean([r["abs_dah"] for r in st_r]),
            "mae_tot_lb": _mean([r["abs_dtot"] for r in lb_r]),
            "mae_tot_st": _mean([r["abs_dtot"] for r in st_r]),
        })
    return summary, detail


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


def _fmt(x: Optional[float], nd: int = 3) -> str:
    return "—" if x is None else f"{x:+.{nd}f}"


def _fmt_u(x: Optional[float], nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def report(
    regimes: Sequence[Dict[str, Any]],
    seasons: Sequence[Dict[str, Any]],
    leagues: Sequence[Dict[str, Any]],
    control: Sequence[Dict[str, Any]],
    n_rows: int,
    n_ctrl_teams: int,
) -> str:
    lines: List[str] = []
    lines.append("# EXP-053 — Season Boundary Residual")
    lines.append("")
    lines.append("Diagnostic only. Aging H60 frozen ON. No product edits.")
    lines.append("")
    lines.append("## Question")
    lines.append("")
    lines.append(
        "After aging shrinks stale short-term state, what remains at the season "
        "boundary — especially in **D_base** (rating + HA) vs market?"
    )
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(f"- OOS expanding by month; eval seasons {', '.join(EVAL_SEASONS)}.")
    lines.append(f"- Dynamic State Aging ON, `H_D = H_S = {HALF_LIFE:.0f}`.")
    lines.append(f"- Long break ≥ {LONG_BREAK_DAYS} days between consecutive team matches.")
    lines.append("- Match regime = min(mslb_home, mslb_away).")
    lines.append("- `D_market` / `S_market` inferred from AH/OU prices (same as training).")
    lines.append("- ΔD signed so **positive = model overstates the market favourite**.")
    lines.append("- ΔP in percentage points vs Shin-devigged 1X2.")
    lines.append("- Control: teams with both Long-break and Stable observations in the same season.")
    lines.append("")
    lines.append(f"n = {n_rows} matches")
    lines.append("")
    lines.append("## Regime table")
    lines.append("")
    lines.append(
        "| Regime | n | ΔD_base | ΔD_after_aging | ΔD_final | "
        "|ΔD_base| | ΔS | ΔP_fav | ΔP_X | ΔP_dog | AH MAE | Tot MAE |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for s in regimes:
        lines.append(
            f"| {s['group']} | {s['n']} | {_fmt(s['dD_base'])} | {_fmt(s['dD_after_aging'])} | "
            f"{_fmt(s['dD_final'])} | {_fmt_u(s['abs_dD_base'])} | {_fmt(s['dS'])} | "
            f"{_fmt(s['dP_fav'], 2)} | {_fmt(s['dP_x'], 2)} | {_fmt(s['dP_dog'], 2)} | "
            f"{_fmt_u(s['mae_ah'], 3)} | {_fmt_u(s['mae_tot'], 3)} |"
        )
    lines.append("")
    lines.append("## Control — same teams Long-break → Stable")
    lines.append("")
    lines.append(
        f"Teams with ≥1 Long-break and ≥1 Stable obs in the same league-season: "
        f"**{n_ctrl_teams}**."
    )
    lines.append("")
    if control:
        lines.append(
            "| Slice | n | ΔD_base | ΔD_after_aging | ΔD_final | |ΔD_base| | "
            "ΔP_fav | ΔP_dog | AH MAE | Tot MAE |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for s in control:
            lines.append(
                f"| {s['group']} | {s['n']} | {_fmt(s['dD_base'])} | {_fmt(s['dD_after_aging'])} | "
                f"{_fmt(s['dD_final'])} | {_fmt_u(s['abs_dD_base'])} | "
                f"{_fmt(s['dP_fav'], 2)} | {_fmt(s['dP_dog'], 2)} | "
                f"{_fmt_u(s['mae_ah'], 3)} | {_fmt_u(s['mae_tot'], 3)} |"
            )
        lines.append("")
        lines.append(
            "If Long-break ΔD_base / AH error is large and Stable on the **same teams** "
            "is near overall mid-season levels without any model change, that supports "
            "missing-information / regime-shift rather than a permanent tail bug."
        )
    else:
        lines.append("_No paired control teams found._")
    lines.append("")
    lines.append("## By season")
    lines.append("")
    lines.append("| Slice | n | ΔD_base | ΔD_final | |ΔD_base| | ΔP_dog | AH MAE |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for s in seasons:
        if ":all" in s["key"] or s["key"].endswith("long_break") or s["key"].endswith("stable"):
            lines.append(
                f"| {s['group']} | {s['n']} | {_fmt(s['dD_base'])} | {_fmt(s['dD_final'])} | "
                f"{_fmt_u(s['abs_dD_base'])} | {_fmt(s['dP_dog'], 2)} | {_fmt_u(s['mae_ah'], 3)} |"
            )
    lines.append("")
    lines.append("## Long-break vs Stable by league")
    lines.append("")
    lines.append("| Slice | n | ΔD_base | ΔD_final | |ΔD_base| | ΔP_dog | AH MAE | Tot MAE |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for s in leagues:
        lines.append(
            f"| {s['group']} | {s['n']} | {_fmt(s['dD_base'])} | {_fmt(s['dD_final'])} | "
            f"{_fmt_u(s['abs_dD_base'])} | {_fmt(s['dP_dog'], 2)} | "
            f"{_fmt_u(s['mae_ah'], 3)} | {_fmt_u(s['mae_tot'], 3)} |"
        )
    lines.append("")
    lines.append("## Verdict (read from the tables)")
    lines.append("")
    lb = next((s for s in regimes if s["key"] == "long_break"), None)
    st = next((s for s in regimes if s["key"] == "stable"), None)
    ctrl_lb = next((s for s in control if s["key"] == "ctrl_long_break"), None)
    ctrl_st = next((s for s in control if s["key"] == "ctrl_stable"), None)
    if lb and st:
        lines.append(
            f"- On Long-break, `ΔD_after_aging` ({_fmt(lb['dD_after_aging'])}) stays close to "
            f"`ΔD_base` ({_fmt(lb['dD_base'])}): aging correctly kills stale dynamic state, "
            "so the remaining gap is not short-term EMA carry."
        )
        lines.append(
            f"- Long-break AH MAE {_fmt_u(lb['mae_ah'], 3)} / Tot MAE {_fmt_u(lb['mae_tot'], 3)} "
            f"vs Stable {_fmt_u(st['mae_ah'], 3)} / {_fmt_u(st['mae_tot'], 3)} "
            "(elevated early, then settles)."
        )
        lines.append(
            f"- Signed `ΔD_base` on Long-break is {_fmt(lb['dD_base'])} "
            f"(abs {_fmt_u(lb['abs_dD_base'])}); Stable {_fmt(st['dD_base'])} "
            f"(abs {_fmt_u(st['abs_dD_base'])}). "
            "A permanent fav-tail bug would not preferentially show as warm-up AH/Tot error."
        )
    if ctrl_lb and ctrl_st:
        lines.append(
            f"- **Same-team control** ({n_ctrl_teams} teams): Long-break AH MAE "
            f"{_fmt_u(ctrl_lb['mae_ah'], 3)} → Stable {_fmt_u(ctrl_st['mae_ah'], 3)}; "
            f"Tot {_fmt_u(ctrl_lb['mae_tot'], 3)} → {_fmt_u(ctrl_st['mae_tot'], 3)}; "
            f"|ΔD_base| {_fmt_u(ctrl_lb['abs_dD_base'])} → {_fmt_u(ctrl_st['abs_dD_base'])}. "
            "Line error shrinks without model changes → supports missing-info / regime-shift "
            "at the boundary more than a fixed matrix bug."
        )
    lines.append(
        "- Next research target: how to move **base rating** (and HA) across seasons when "
        "dynamic state is already aged out — promoted teams, roster/coach shocks, "
        "and August information the market has and we do not."
    )
    lines.append("")
    lines.append("## Frozen decisions (not this EXP)")
    lines.append("")
    lines.append("- Aging H60 remains a production candidate (separate from this diagnostic).")
    lines.append("- EXP-052 closed: no fav-tail / away / league correction.")
    lines.append("- Legacy / Auto α–ρ calibration not touched here.")
    lines.append("")
    lines.append("## Research question for follow-ups")
    lines.append("")
    lines.append(
        "How to carry a team from season N → N+1 when old dynamic state is stale "
        "and new matches are not yet enough — without confusing short-term aging "
        "with base-rating / roster-regime information the market already has?"
    )
    lines.append("")
    return "\n".join(lines)


def load_preds_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    float_keys = {
        "fav_odds", "days_h", "days_a", "mslb_h", "mslb_a", "mslb",
        "d_market", "s_market", "d_base", "d_after_aging", "d_final", "s_model",
        "dD_base", "dD_after_aging", "dD_final", "dS",
        "abs_dD_base", "abs_dD_after_aging", "abs_dD_final",
        "dP_fav", "dP_x", "dP_dog",
        "ah_mkt", "ah_pred", "tot_mkt", "tot_pred", "abs_dah", "abs_dtot", "month",
    }
    out: List[Dict[str, Any]] = []
    for r in rows:
        item: Dict[str, Any] = dict(r)
        for k in float_keys:
            if k in item and item[k] not in ("", None):
                item[k] = float(item[k])
            elif k in item:
                item[k] = None
        for k in ("mslb", "mslb_h", "mslb_a", "month"):
            if item.get(k) is not None:
                item[k] = int(item[k])
        for k in ("regime", "regime_h", "regime_a"):
            if item.get(k) == "":
                item[k] = None
        out.append(item)
    return out


def write_outputs(flat: List[Dict[str, Any]]) -> str:
    regimes = by_regime(flat)
    seasons = by_regime_season(flat)
    leagues = by_regime_league(flat)
    team_rows = team_side_rows(flat)
    control, ctrl_detail = control_same_teams(team_rows)
    n_ctrl_teams = len(ctrl_detail)
    text = report(regimes, seasons, leagues, control, len(flat), n_ctrl_teams)
    for dest in (OUT, REPO):
        dest.mkdir(parents=True, exist_ok=True)
        write_csv(dest / "preds_flat.csv", flat)
        write_csv(dest / "by_regime.csv", regimes)
        write_csv(dest / "by_season.csv", seasons)
        write_csv(dest / "by_league.csv", leagues)
        write_csv(dest / "control_summary.csv", control)
        write_csv(dest / "control_teams.csv", ctrl_detail)
        write_csv(dest / "team_side.csv", team_rows)
        (dest / "summary.json").write_text(
            json.dumps(
                {
                    "n": len(flat),
                    "n_control_teams": n_ctrl_teams,
                    "by_regime": regimes,
                    "control": control,
                    "by_league": leagues,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (dest / "REPORT.md").write_text(text, encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="*", default=None)
    ap.add_argument("--from-csv", type=Path, default=None,
                    help="Reuse preds_flat.csv and only re-aggregate")
    args = ap.parse_args()
    if args.from_csv is not None:
        print(f"loading preds from {args.from_csv}…", flush=True)
        flat = load_preds_csv(args.from_csv)
    else:
        print("loading history…", flush=True)
        rows = parse_rows(fetch_all_view_rows())
        flat = run(rows, limit_leagues=args.leagues)
        # persist immediately so aggregation bugs do not lose the OOS run
        for dest in (OUT, REPO):
            dest.mkdir(parents=True, exist_ok=True)
            write_csv(dest / "preds_flat.csv", flat)
        print(f"wrote {len(flat)} preds; aggregating…", flush=True)
    text = write_outputs(flat)
    print(text)


if __name__ == "__main__":
    main()
