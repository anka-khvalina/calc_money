#!/usr/bin/env python3
"""Compare production goal model vs experimental Poisson Attack/Defence.

Read-only DB. Console + JSON/MD artifacts under /opt/cursor/artifacts/ad_compare/.
Does not modify production configs or entrypoints.

Modes
-----
- MARKET_LINE : closing total / -AH as naive S/D (baseline)
- PROD        : current train_full_model + predict_match (full stack)
- AD_POISSON  : experiments.attack_defence Poisson A/D on FT goals
- AD_POISSON_H0 : same fit but H forced to 0 at predict (home-effect ablation)
- AD_POISSON_NO_H_FIT : refit with home_advantage clamped ~0 (EXP-039 style)

Primary questions
-----------------
1) Which predicts actual FT goals better (MAE D/S/λ)?
2) Which tracks closing AH/OU lines better (pricing relevance)?
3) Does league home effect help the Poisson AD model?
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path("/workspace")
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

import goal_model as gm  # noqa: E402
import goal_model_train as gmt  # noqa: E402

from experiments.attack_defence.metrics import compute_metrics  # noqa: E402
from experiments.attack_defence.model import (  # noqa: E402
    LeagueFit,
    MatchRow,
    fit_league_poisson,
    predict_matches,
    rows_from_db_dicts,
)
from experiments.attack_defence.parse_ft import parse_ft_from_note  # noqa: E402
from experiments.attack_defence.readonly_db import ReadOnlySupabase  # noqa: E402

OUT = Path("/opt/cursor/artifacts/ad_compare")
OUT.mkdir(parents=True, exist_ok=True)

SELECT = (
    "match_id,match_date,league_id,league_name,season_id,season_label,"
    "home_team_id,home_team,away_team_id,away_team,"
    "closing_ah_home,closing_total_line,ah_home_odds,ah_away_odds,"
    "over_odds,under_odds,home_odds,draw_odds,away_odds,"
    "is_neutral,match_weight,derby_weight,neutral_weight,"
    "home_rotation_code,away_rotation_code,note,active,motivation"
)

HOLDOUT_FROM = date(2025, 3, 1)
HOLDOUT_TO = date(2025, 5, 25)
TRAIN_TO = HOLDOUT_FROM  # exclusive


@dataclass
class EvalRow:
    match_id: str
    date: str
    league: str
    home: str
    away: str
    mode: str
    D_model: float
    S_model: float
    lh: float
    la: float
    D_act: float
    S_act: float
    hg: int
    ag: int
    AH_mkt: Optional[float]
    Tot_mkt: Optional[float]
    AH_model: Optional[float]
    Tot_model: Optional[float]


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _raw_from_db(r: dict) -> Optional[gmt.RawMatch]:
    if any(
        _f(r.get(k)) is None
        for k in (
            "closing_ah_home",
            "closing_total_line",
            "ah_home_odds",
            "ah_away_odds",
            "over_odds",
            "under_odds",
        )
    ):
        return None
    ds = (r.get("match_date") or "")[:10]
    try:
        dt = date.fromisoformat(ds) if ds else None
    except ValueError:
        dt = None
    if dt is None or r.get("home_team_id") is None or r.get("away_team_id") is None:
        return None
    derby_w = _f(r.get("derby_weight"))
    # Same semantics as production: derby_weight==1 → derby.
    # Note: Ligue 1 2023-24/2024-25 rows are mostly 1.0 (data quality); train
    # path disables derby H coef when the system becomes singular.
    derby_flag = gmt._derby_flag_from_weight(derby_w)
    return gmt.RawMatch(
        date=dt,
        league=str(r.get("league_name") or ""),
        home_team=str(r.get("home_team") or r.get("home_team_id")),
        away_team=str(r.get("away_team") or r.get("away_team_id")),
        home_team_id=str(r["home_team_id"]),
        away_team_id=str(r["away_team_id"]),
        league_id=str(r.get("league_id") or ""),
        closing_ah_home=_f(r.get("closing_ah_home")),
        closing_total_line=_f(r.get("closing_total_line")),
        ah_home_odds=_f(r.get("ah_home_odds")),
        ah_away_odds=_f(r.get("ah_away_odds")),
        over_odds=_f(r.get("over_odds")),
        under_odds=_f(r.get("under_odds")),
        home_odds=_f(r.get("home_odds")),
        draw_odds=_f(r.get("draw_odds")),
        away_odds=_f(r.get("away_odds")),
        neutral_flag=bool(r.get("is_neutral")),
        derby_flag=derby_flag,
        quality_match_weight=_f(r.get("match_weight")) or 1.0,
        derby_match_weight=derby_w,
        neutral_match_weight=_f(r.get("neutral_weight")) or 1.0,
        home_rotation_code=(r.get("home_rotation_code") or "none"),
        away_rotation_code=(r.get("away_rotation_code") or "none"),
        season_id=str(r["season_id"]) if r.get("season_id") is not None else None,
        season_label=r.get("season_label"),
    )


def load_data(client: ReadOnlySupabase) -> Tuple[List[MatchRow], Dict[str, gmt.RawMatch], List[gmt.RawMatch]]:
    """AD rows (FT+closing), map match_id→RawMatch for those, and ALL closing RawMatches for PROD train."""
    raw = client.fetch_matches_paged(select=SELECT, extra_query="active=eq.true")
    raw = [r for r in raw if r.get("motivation") is not False and str(r.get("motivation")).lower() != "false"]

    ft_rows, _ex = rows_from_db_dicts(raw)
    by_ft = {m.match_id: m for m in ft_rows}

    ad_rows: List[MatchRow] = []
    id_to_prod: Dict[str, gmt.RawMatch] = {}
    prod_all: List[gmt.RawMatch] = []

    for r in raw:
        pr = _raw_from_db(r)
        if pr is None:
            continue
        prod_all.append(pr)
        mid = str(r.get("match_id") or "")
        if mid in by_ft:
            ad_rows.append(by_ft[mid])
            id_to_prod[mid] = pr
    return ad_rows, id_to_prod, prod_all


def season_weights(raw: Sequence[gmt.RawMatch]) -> List[gmt.SeasonWeight]:
    labels = sorted({m.season_label for m in raw if m.season_label}, reverse=True)
    defaults = [1.0, 0.7, 0.5]
    out: List[gmt.SeasonWeight] = []
    for i, lab in enumerate(labels):
        dates = [m.date for m in raw if m.season_label == lab and m.date]
        if not dates:
            continue
        w = defaults[i] if i < len(defaults) else 0.5
        out.append(gmt.SeasonWeight(label=str(lab), date_from=min(dates), date_to=max(dates), base_weight=w))
    return out


def line_from_sd(s: float, d: float) -> Tuple[float, float]:
    """Rough main lines: AH ≈ -D, Total ≈ S (quarter-rounded like markets)."""
    def q(x: float) -> float:
        return round(x * 4) / 4.0

    return q(-d), q(s)


def eval_mode_rows(rows: Sequence[EvalRow]) -> Dict[str, Any]:
    def avg(xs: List[float]) -> Optional[float]:
        return sum(xs) / len(xs) if xs else None

    d_err = [abs(r.D_model - r.D_act) for r in rows]
    s_err = [abs(r.S_model - r.S_act) for r in rows]
    lh_err = [abs(r.lh - r.hg) for r in rows]
    la_err = [abs(r.la - r.ag) for r in rows]
    ah_err = [abs(r.AH_model - r.AH_mkt) for r in rows if r.AH_model is not None and r.AH_mkt is not None]
    tot_err = [abs(r.Tot_model - r.Tot_mkt) for r in rows if r.Tot_model is not None and r.Tot_mkt is not None]
    return {
        "n": len(rows),
        "mae_D_ft": avg(d_err),
        "mae_S_ft": avg(s_err),
        "mae_lh_ft": avg(lh_err),
        "mae_la_ft": avg(la_err),
        "bias_D": avg([r.D_model - r.D_act for r in rows]),
        "bias_S": avg([r.S_model - r.S_act for r in rows]),
        "mae_AH_mkt": avg(ah_err),
        "mae_Tot_mkt": avg(tot_err),
        "n_ah_ge_0_5": sum(1 for e in ah_err if e >= 0.5),
        "n_tot_ge_0_5": sum(1 for e in tot_err if e >= 0.5),
    }


def run_league(
    league: str,
    ad_all: Sequence[MatchRow],
    id_to_prod: Dict[str, gmt.RawMatch],
    prod_all: Sequence[gmt.RawMatch],
) -> Tuple[List[EvalRow], List[Dict[str, Any]]]:
    ad = [m for m in ad_all if m.league_name == league]
    train_ad = [m for m in ad if m.match_date < TRAIN_TO]
    hold_ad = [m for m in ad if HOLDOUT_FROM <= m.match_date <= HOLDOUT_TO]
    # PROD trains on ALL closing-line matches (connectivity); eval only FT holdout
    train_prod = [m for m in prod_all if m.league == league and m.date and m.date < TRAIN_TO]
    if len(train_ad) < 30 or not hold_ad or len(train_prod) < 30:
        return [], [{
            "league": league, "skipped": True,
            "train_ad": len(train_ad), "hold": len(hold_ad), "train_prod": len(train_prod),
        }]

    print(
        f"\n=== {league} AD_train={len(train_ad)} PROD_train={len(train_prod)} hold={len(hold_ad)} ===",
        flush=True,
    )
    rows_out: List[EvalRow] = []
    summaries: List[Dict[str, Any]] = []

    # --- MARKET_LINE baseline ---
    market_rows: List[EvalRow] = []
    for m in hold_ad:
        pr = id_to_prod[m.match_id]
        ah = pr.closing_ah_home
        tot = pr.closing_total_line
        # D ≈ -AH, S ≈ total line (naive closing)
        D = -float(ah) if ah is not None else 0.0
        S = float(tot) if tot is not None else 2.5
        lh, la = (S + D) / 2.0, (S - D) / 2.0
        market_rows.append(
            EvalRow(
                match_id=m.match_id, date=str(m.match_date), league=league,
                home=m.home_team, away=m.away_team, mode="MARKET_LINE",
                D_model=D, S_model=S, lh=lh, la=la,
                D_act=m.home_goals - m.away_goals, S_act=m.home_goals + m.away_goals,
                hg=m.home_goals, ag=m.away_goals,
                AH_mkt=ah, Tot_mkt=tot, AH_model=ah, Tot_model=tot,
            )
        )
    rows_out.extend(market_rows)
    sm = eval_mode_rows(market_rows)
    sm.update({"league": league, "mode": "MARKET_LINE"})
    summaries.append(sm)
    print(f"  MARKET_LINE mae_D_ft={sm['mae_D_ft']:.3f} mae_S_ft={sm['mae_S_ft']:.3f}", flush=True)

    # --- AD_POISSON ---
    fit = fit_league_poisson(train_ad, regularization=0.5, min_team_matches=5)
    preds = predict_matches(fit, hold_ad)
    ad_rows: List[EvalRow] = []
    for p in preds:
        m = p.match
        pr = id_to_prod[m.match_id]
        ah_m, tot_m = line_from_sd(p.S, p.D)
        ad_rows.append(
            EvalRow(
                match_id=m.match_id, date=str(m.match_date), league=league,
                home=m.home_team, away=m.away_team, mode="AD_POISSON",
                D_model=p.D, S_model=p.S, lh=p.lambda_home, la=p.lambda_away,
                D_act=m.home_goals - m.away_goals, S_act=m.home_goals + m.away_goals,
                hg=m.home_goals, ag=m.away_goals,
                AH_mkt=pr.closing_ah_home, Tot_mkt=pr.closing_total_line,
                AH_model=ah_m, Tot_model=tot_m,
            )
        )
    rows_out.extend(ad_rows)
    sm = eval_mode_rows(ad_rows)
    sm.update({"league": league, "mode": "AD_POISSON", "converged": fit.converged, "H": fit.home_advantage, "mu": fit.mu})
    summaries.append(sm)
    print(f"  AD_POISSON mae_D_ft={sm['mae_D_ft']:.3f} mae_S_ft={sm['mae_S_ft']:.3f} mae_AH={sm['mae_AH_mkt']:.3f} mae_Tot={sm['mae_Tot_mkt']:.3f} H={fit.home_advantage:.3f}", flush=True)

    # --- AD_POISSON_H0 (predict with H=0) ---
    fit_h0 = LeagueFit(
        league_id=fit.league_id, league_name=fit.league_name, mu=fit.mu,
        home_advantage=0.0, ratings=fit.ratings, n_train=fit.n_train, n_teams=fit.n_teams,
        iterations=fit.iterations, converged=fit.converged, loss=fit.loss,
        train_from=fit.train_from, train_to=fit.train_to, warnings=list(fit.warnings),
    )
    preds0 = predict_matches(fit_h0, hold_ad)
    ad0: List[EvalRow] = []
    for p in preds0:
        m = p.match
        pr = id_to_prod[m.match_id]
        ah_m, tot_m = line_from_sd(p.S, p.D)
        ad0.append(
            EvalRow(
                match_id=m.match_id, date=str(m.match_date), league=league,
                home=m.home_team, away=m.away_team, mode="AD_POISSON_H0",
                D_model=p.D, S_model=p.S, lh=p.lambda_home, la=p.lambda_away,
                D_act=m.home_goals - m.away_goals, S_act=m.home_goals + m.away_goals,
                hg=m.home_goals, ag=m.away_goals,
                AH_mkt=pr.closing_ah_home, Tot_mkt=pr.closing_total_line,
                AH_model=ah_m, Tot_model=tot_m,
            )
        )
    rows_out.extend(ad0)
    sm = eval_mode_rows(ad0)
    sm.update({"league": league, "mode": "AD_POISSON_H0"})
    summaries.append(sm)
    print(f"  AD_POISSON_H0 mae_D_ft={sm['mae_D_ft']:.3f} mae_S_ft={sm['mae_S_ft']:.3f}", flush=True)

    # --- PROD full model ---
    sw = season_weights(train_prod)
    cfg = gmt.ModelConfig(season_weights=sw, default_season_weight=1.0)
    # Apply web config lightly for SFA/SFTC/d_corr if available
    try:
        doc = json.loads((ROOT / "web" / "model_config.json").read_text(encoding="utf-8"))
        cfg = gmt.apply_d_correction_config_from_mapping(cfg, doc)
        cfg = gmt.apply_sfa_config_from_mapping(cfg, doc)
        cfg = gmt.apply_sftc_config_from_mapping(cfg, doc)
        cfg = gmt.apply_rating_config_from_mapping(cfg, doc)
        # FT-filtered samples can be disconnected for hierarchical WLS — use standard for fair compare
        from dataclasses import replace
        cfg = replace(cfg, rating_mode="standard_wls", rating_by_league={})
        se = doc.get("dynamic_s_ema") or {}
        if se:
            cfg = replace(
                cfg,
                s_momentum_enabled=bool(se.get("enabled", False)),
                s_momentum_alpha=float(se.get("alpha", 0.3)),
                s_momentum_k=float(se.get("k", 1.0)),
            )
    except Exception as e:
        print(f"  [warn] config apply: {e}", flush=True)

    t0 = time.time()
    model = None
    prod_notes: List[str] = []
    for attempt, tweak in enumerate(
        (
            {},
            {"rating_mode": "standard_wls", "rating_by_league": {}},
            # Ligue 1 2023-25: almost all derby_weight=1 → H and derby cols collinear
            {"rating_mode": "standard_wls", "rating_by_league": {}, "derby_min_matches": 10**9},
        )
    ):
        from dataclasses import replace
        cfg_try = replace(cfg, **tweak) if tweak else cfg
        try:
            model, _ = gmt.train_full_model(train_prod, cfg_try)
            if tweak:
                prod_notes.append(f"train_ok_with={tweak}")
            cfg = cfg_try
            break
        except ValueError as e:
            msg = str(e)
            print(f"  PROD train attempt {attempt+1} failed: {msg}", flush=True)
            prod_notes.append(f"attempt{attempt+1}:{msg[:80]}")
    if model is None:
        summaries.append({
            "league": league, "mode": "PROD", "skipped": True,
            "reason": "train_singular", "notes": prod_notes,
            "train_prod": len(train_prod), "hold": len(hold_ad),
        })
        print("  PROD skipped (could not train)", flush=True)
    else:
        print(f"  PROD trained in {time.time()-t0:.1f}s {prod_notes}", flush=True)
        prod_rows: List[EvalRow] = []
        for m in hold_ad:
            pr = id_to_prod[m.match_id]
            hid = gmt.team_key(pr.home_team_id, pr.home_team)
            aid = gmt.team_key(pr.away_team_id, pr.away_team)
            try:
                pred = gmt.predict_match(
                    model, hid, aid,
                    neutral=pr.neutral_flag, derby=pr.derby_flag,
                    match_date=pr.date,
                    home_odds=pr.home_odds, away_odds=pr.away_odds,
                    league=pr.league, season=pr.season_label,
                )
            except (ValueError, ZeroDivisionError):
                continue
            mk = pred.markets
            prod_rows.append(
                EvalRow(
                    match_id=m.match_id, date=str(m.match_date), league=league,
                    home=m.home_team, away=m.away_team, mode="PROD",
                    D_model=float(pred.d_final), S_model=float(pred.s_final),
                    lh=float(pred.lambda_home), la=float(pred.lambda_away),
                    D_act=m.home_goals - m.away_goals, S_act=m.home_goals + m.away_goals,
                    hg=m.home_goals, ag=m.away_goals,
                    AH_mkt=pr.closing_ah_home, Tot_mkt=pr.closing_total_line,
                    AH_model=float(mk.main_ah.line), Tot_model=float(mk.main_total.line),
                )
            )
        rows_out.extend(prod_rows)
        sm = eval_mode_rows(prod_rows)
        sm.update({"league": league, "mode": "PROD", "notes": prod_notes})
        summaries.append(sm)
        print(
            f"  PROD mae_D_ft={sm['mae_D_ft']:.3f} mae_S_ft={sm['mae_S_ft']:.3f} "
            f"mae_AH={sm['mae_AH_mkt']:.3f} mae_Tot={sm['mae_Tot_mkt']:.3f}",
            flush=True,
        )

    # --- PROD static: d_correction off, s_ema off, sfa/sftc off (closer to pure ratings) ---
    try:
        from dataclasses import replace
        doc2 = json.loads((ROOT / "web" / "model_config.json").read_text(encoding="utf-8"))
        doc2 = dict(doc2)
        doc2["d_correction"] = {**(doc2.get("d_correction") or {}), "mode": "disabled"}
        doc2["dynamic_s_ema"] = {**(doc2.get("dynamic_s_ema") or {}), "enabled": False}
        doc2["strongFavoriteAdjustment"] = {"mode": "off"}
        doc2["strongFavouriteTotalCorrection"] = {"enabled": False}
        cfg2 = gmt.ModelConfig(season_weights=sw, default_season_weight=1.0)
        cfg2 = gmt.apply_d_correction_config_from_mapping(cfg2, doc2)
        cfg2 = gmt.apply_sfa_config_from_mapping(cfg2, doc2)
        cfg2 = gmt.apply_sftc_config_from_mapping(cfg2, doc2)
        cfg2 = gmt.apply_rating_config_from_mapping(cfg2, doc2)
        cfg2 = replace(
            cfg2,
            s_momentum_enabled=False,
            rating_mode="standard_wls",
            rating_by_league={},
            derby_min_matches=10**9 if "derby_min_matches" in str(prod_notes) else cfg2.derby_min_matches,
        )
        # Mirror PROD train fallbacks (esp. Ligue 1 derby collinearity)
        if any("derby_min_matches" in n for n in prod_notes):
            cfg2 = replace(cfg2, derby_min_matches=10**9)
        model2 = None
        for attempt, tweak in enumerate(
            (
                {},
                {"derby_min_matches": 10**9},
            )
        ):
            cfg2_try = replace(cfg2, **tweak) if tweak else cfg2
            try:
                model2, _ = gmt.train_full_model(train_prod, cfg2_try)
                cfg2 = cfg2_try
                break
            except ValueError as e:
                print(f"  PROD_STATIC attempt {attempt+1}: {e}", flush=True)
        if model2 is None:
            print("  PROD_STATIC skipped", flush=True)
        else:
            prod2: List[EvalRow] = []
            for m in hold_ad:
                pr = id_to_prod[m.match_id]
                hid = gmt.team_key(pr.home_team_id, pr.home_team)
                aid = gmt.team_key(pr.away_team_id, pr.away_team)
                try:
                    pred = gmt.predict_match(
                        model2, hid, aid,
                        neutral=pr.neutral_flag, derby=pr.derby_flag,
                        match_date=pr.date,
                        home_odds=pr.home_odds, away_odds=pr.away_odds,
                        league=pr.league, season=pr.season_label,
                        apply_momentum=False, apply_s_momentum=False, apply_sfa=False,
                    )
                except (ValueError, ZeroDivisionError):
                    continue
                mk = pred.markets
                prod2.append(
                    EvalRow(
                        match_id=m.match_id, date=str(m.match_date), league=league,
                        home=m.home_team, away=m.away_team, mode="PROD_STATIC",
                        D_model=float(pred.d_final), S_model=float(pred.s_final),
                        lh=float(pred.lambda_home), la=float(pred.lambda_away),
                        D_act=m.home_goals - m.away_goals, S_act=m.home_goals + m.away_goals,
                        hg=m.home_goals, ag=m.away_goals,
                        AH_mkt=pr.closing_ah_home, Tot_mkt=pr.closing_total_line,
                        AH_model=float(mk.main_ah.line), Tot_model=float(mk.main_total.line),
                    )
                )
            rows_out.extend(prod2)
            sm = eval_mode_rows(prod2)
            sm.update({"league": league, "mode": "PROD_STATIC"})
            summaries.append(sm)
            print(
                f"  PROD_STATIC mae_D_ft={sm['mae_D_ft']:.3f} mae_S_ft={sm['mae_S_ft']:.3f} "
                f"mae_AH={sm['mae_AH_mkt']:.3f} mae_Tot={sm['mae_Tot_mkt']:.3f}",
                flush=True,
            )
    except Exception as e:
        print(f"  PROD_STATIC failed: {e}", flush=True)

    return rows_out, summaries


def pool(summaries: Sequence[Dict[str, Any]], mode: str) -> Optional[Dict[str, Any]]:
    xs = [s for s in summaries if s.get("mode") == mode and s.get("mae_D_ft") is not None]
    if not xs:
        return None
    n = sum(s["n"] for s in xs)
    def wavg(key: str) -> float:
        return sum(s[key] * s["n"] for s in xs if s.get(key) is not None) / n
    return {
        "mode": mode,
        "n": n,
        "leagues": len(xs),
        "mae_D_ft": wavg("mae_D_ft"),
        "mae_S_ft": wavg("mae_S_ft"),
        "mae_lh_ft": wavg("mae_lh_ft"),
        "mae_la_ft": wavg("mae_la_ft"),
        "mae_AH_mkt": wavg("mae_AH_mkt"),
        "mae_Tot_mkt": wavg("mae_Tot_mkt"),
        "n_ah_ge_0_5": sum(s.get("n_ah_ge_0_5") or 0 for s in xs),
        "n_tot_ge_0_5": sum(s.get("n_tot_ge_0_5") or 0 for s in xs),
    }


def main() -> int:
    import logging
    logging.disable(logging.WARNING)
    t0 = time.time()
    print("AD vs PROD comparison (read-only)", flush=True)
    print(f"holdout {HOLDOUT_FROM}..{HOLDOUT_TO} train_to={TRAIN_TO}", flush=True)

    client = ReadOnlySupabase()
    client.verify_readonly()
    print("read-only: PASS", client.info().as_log_dict(), flush=True)

    _db_unused = None
    ad_rows, id_to_prod, prod_all = load_data(client)
    print(f"FT+closing rows: {len(ad_rows)}; all closing for PROD: {len(prod_all)}", flush=True)
    leagues = sorted({m.league_name for m in ad_rows})
    print(f"leagues: {leagues}", flush=True)

    all_rows: List[EvalRow] = []
    all_sum: List[Dict[str, Any]] = []
    for lg in leagues:
        rows, sums = run_league(lg, ad_rows, id_to_prod, prod_all)
        all_rows.extend(rows)
        all_sum.extend(sums)

    # save
    import csv
    if all_rows:
        with (OUT / "compare_rows.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(all_rows[0]).keys()))
            w.writeheader()
            for r in all_rows:
                w.writerow(asdict(r))
    (OUT / "summaries.json").write_text(json.dumps(all_sum, indent=2), encoding="utf-8")

    modes = ["MARKET_LINE", "AD_POISSON", "AD_POISSON_H0", "PROD", "PROD_STATIC"]
    pooled = {m: pool(all_sum, m) for m in modes}
    (OUT / "pooled.json").write_text(json.dumps(pooled, indent=2), encoding="utf-8")

    # Verdicts
    def better(a, b, key, eps=0.01):
        if not a or not b or a.get(key) is None or b.get(key) is None:
            return None
        if a[key] < b[key] - eps:
            return True
        if b[key] < a[key] - eps:
            return False
        return None  # tie

    verdicts = []
    # EXP-038 style: AD structure vs production
    p, a = pooled.get("PROD"), pooled.get("AD_POISSON")
    if p and a:
        ft_d = better(a, p, "mae_D_ft")
        ft_s = better(a, p, "mae_S_ft")
        mkt_ah = better(a, p, "mae_AH_mkt")
        mkt_t = better(a, p, "mae_Tot_mkt")
        # For fair-odds product, market-line MAE dominates
        if mkt_ah is False and mkt_t is False:
            v38 = "FAIL_FOR_PRICING"
            reason = "Poisson AD worse than PROD on closing AH and Total"
        elif (ft_d or ft_s) and (mkt_ah is False or mkt_t is False):
            v38 = "PARTIAL"
            reason = "AD may help FT goals but loses on closing-line pricing"
        elif mkt_ah or mkt_t:
            v38 = "PASS_AD"
            reason = "AD better on pricing metrics"
        elif ft_d is None and ft_s is None and mkt_ah is None and mkt_t is None:
            v38 = "NO EFFECT"
            reason = "differences within tolerance"
        else:
            v38 = "PARTIAL"
            reason = f"ft_D_better={ft_d} ft_S_better={ft_s} ah_better={mkt_ah} tot_better={mkt_t}"
        verdicts.append({"experiment": "EXP-038_AD_vs_PROD", "verdict": v38, "reason": reason, "PROD": p, "AD_POISSON": a})

    # EXP-039 home effect on Poisson AD
    a, h0 = pooled.get("AD_POISSON"), pooled.get("AD_POISSON_H0")
    if a and h0:
        d_helps = better(a, h0, "mae_D_ft")
        if d_helps is True:
            v39 = "PASS"
            reason = "league H improves FT D vs H=0"
        elif d_helps is False:
            v39 = "FAIL"
            reason = "H=0 better or equal on FT D — league H not needed / harmful"
        else:
            v39 = "NO EFFECT"
            reason = "H vs H=0 within tolerance on FT D"
        # also check AH tracking
        ah = better(a, h0, "mae_AH_mkt")
        reason += f"; AH_mkt H_better={ah}"
        verdicts.append({"experiment": "EXP-039_HOME_on_AD", "verdict": v39, "reason": reason, "AD": a, "H0": h0})

    # EXP-040 style: MARKET_LINE (closing) as FT oracle vs models
    mkt, a = pooled.get("MARKET_LINE"), pooled.get("AD_POISSON")
    if mkt and a:
        d_vs = better(a, mkt, "mae_D_ft")
        s_vs = better(a, mkt, "mae_S_ft")
        if d_vs is False and s_vs is not True:
            v40 = "FAIL"
            reason = "closing lines beat Poisson AD on FT D (market already good goals proxy)"
        elif d_vs is True or s_vs is True:
            v40 = "PASS"
            reason = "AD beats naive closing on FT goals"
        else:
            v40 = "NO EFFECT"
            reason = "AD ≈ MARKET_LINE on FT within tolerance"
        verdicts.append({"experiment": "EXP-040_AD_vs_MARKET_LINE", "verdict": v40, "reason": reason, "MARKET": mkt, "AD": a})

    # Overall recommendation
    p, a = pooled.get("PROD"), pooled.get("AD_POISSON")
    if p and a:
        # Product is FairOdds → prioritize closing-line MAE
        if p["mae_AH_mkt"] + p["mae_Tot_mkt"] < a["mae_AH_mkt"] + a["mae_Tot_mkt"] - 0.02:
            rec = "KEEP_PRODUCTION"
            rec_r = "Production tracks closing AH/OU much better — required for fair odds."
        elif a["mae_D_ft"] + a["mae_S_ft"] < p["mae_D_ft"] + p["mae_S_ft"] - 0.05:
            rec = "HYBRID_RESEARCH"
            rec_r = "Poisson AD better on FT goals; consider hybrid later, not replace pricing model now."
        else:
            rec = "KEEP_PRODUCTION"
            rec_r = "No clear FT gain that outweighs pricing fit."
        verdicts.append({"experiment": "OVERALL", "verdict": rec, "reason": rec_r})

    (OUT / "verdicts.json").write_text(json.dumps(verdicts, indent=2), encoding="utf-8")

    # Markdown report
    lines = [
        "# Production vs Attack/Defence Poisson — comparison",
        "",
        f"- Holdout: `{HOLDOUT_FROM}` … `{HOLDOUT_TO}` (train date < `{TRAIN_TO}`)",
        f"- Joined rows (FT in note + full closing line): `{len(ad_rows)}`",
        f"- Read-only: PASS; writes 0",
        f"- Runtime: {time.time()-t0:.0f}s",
        "",
        "## Data notes",
        "",
        "- Premier League / late 2025-26 often lack FT in `note` → holdout uses spring 2025 Big-4 with scores.",
        "- Ligue 1 seasons 2023-24/2024-25 have nearly all `derby_weight=1` → PROD train disables derby H coef to avoid singular WLS.",
        "",
        "## Modes",
        "",
        "| Mode | Meaning |",
        "|---|---|",
        "| MARKET_LINE | closing Total / −AH as S/D |",
        "| AD_POISSON | new experiment (FT Poisson A/D) |",
        "| AD_POISSON_H0 | same ratings, H=0 at predict (EXP-039) |",
        "| PROD | current full model |",
        "| PROD_STATIC | prod without dynamics/SFA/SFTC |",
        "",
        "## Pooled results (n-weighted)",
        "",
        "| Mode | n | MAE D(FT) | MAE S(FT) | MAE AH(mkt) | MAE Tot(mkt) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for m in modes:
        x = pooled.get(m)
        if not x:
            continue
        lines.append(
            f"| {m} | {x['n']} | {x['mae_D_ft']:.4f} | {x['mae_S_ft']:.4f} | "
            f"{x['mae_AH_mkt']:.4f} | {x['mae_Tot_mkt']:.4f} |"
        )
    lines += ["", "## Per league", ""]
    lines.append("| League | Mode | n | MAE D(FT) | MAE S(FT) | MAE AH | MAE Tot |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for s in sorted(all_sum, key=lambda z: (z.get("league") or "", z.get("mode") or "")):
        if s.get("skipped") or s.get("mae_D_ft") is None:
            continue
        lines.append(
            f"| {s['league']} | {s['mode']} | {s['n']} | {s['mae_D_ft']:.4f} | {s['mae_S_ft']:.4f} | "
            f"{s['mae_AH_mkt']:.4f} | {s['mae_Tot_mkt']:.4f} |"
        )
    lines += ["", "## Verdicts", ""]
    for v in verdicts:
        lines.append(f"### {v['experiment']}: **{v['verdict']}**")
        lines.append(v.get("reason", ""))
        lines.append("")
    lines += [
        "## Conclusions",
        "",
        "1. **Fair-odds product** needs forecasts close to **closing AH/OU**. "
        "Production is trained on market-implied S/D; Poisson AD is trained on FT goals — different targets.",
        "2. If AD wins on FT but loses on closing lines, it is a **goals model**, not a drop-in replacement for Line pricing.",
        "3. EXP-039 (home): compare AD vs AD_H0; keep league H only if it helps FT D and does not break AH.",
        "4. EXP-040: if MARKET_LINE already beats AD on FT, closing lines are a strong goals proxy — AD has limited edge.",
        "5. Recommended next step if AD shows FT value: **shadow / hybrid** (e.g. AD for S diagnostics) without replacing PROD pricing.",
        "",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    # also copy into repo experiments folder for PR visibility
    repo_out = ROOT / "experiments" / "attack_defence" / "COMPARE_REPORT.md"
    repo_out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:50]), flush=True)
    print("...", flush=True)
    for v in verdicts:
        print(f"VERDICT {v['experiment']}: {v['verdict']} — {v.get('reason')}", flush=True)
    print(f"Wrote {OUT} and {repo_out}", flush=True)
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
