"""Predict with scaled Dynamic-D / S-EMA corrections (offline; no product edits)."""

from __future__ import annotations

from datetime import date
from typing import Optional

import goal_model as gm
import goal_model_train as gmt
import strong_favorite_adjustment as sfa
import strong_favourite_total as sft
import s_momentum as smom
import dynamic_dc_gamma as ddc
from goal_model_train import (
    Prediction,
    apply_s_calibration,
    effective_draw_q_bounds,
    resolve_dynamic_dc_config,
    resolve_s_momentum_config,
    resolve_sfa_config,
    resolve_sftc_config,
)


def finalize_from_ds(
    model: gmt.TrainedModel,
    home_id: str,
    away_id: str,
    *,
    d_for_cal: float,
    s_for_cal: float,
    d_model_base: float,
    s_model_base: float,
    d_model_dynamic: float,
    s_model_dynamic: float,
    dynamic_correction: float,
    s_dynamic_correction: float,
    league: Optional[str] = None,
    season: Optional[str] = None,
    home_odds: Optional[float] = None,
    away_odds: Optional[float] = None,
    apply_sfa: bool = True,
) -> Prediction:
    """Same post-D/S path as predict_match: calib → SFA → SFTC → matrix → markets."""
    cfg = model.config
    cal = model.calibration
    names = model.team_names or {}
    home_name = names.get(home_id, home_id)
    away_name = names.get(away_id, away_id)
    scfg = model.s_momentum_cfg or resolve_s_momentum_config(cfg)

    d_final = cal.d_a + cal.d_b * d_for_cal
    s_final = apply_s_calibration(s_for_cal, cal.s_a, cal.s_b, cfg)
    d_before_sfa = float(d_final)

    mkt_fav = sfa.favorite_odds_from_decimal(home_odds, away_odds)
    sfa_diag = None
    if apply_sfa:
        sfa_cfg_use = model.sfa_cfg or resolve_sfa_config(cfg)
        lg = league
        if lg is None and model.sfa_book is not None:
            lg = model.sfa_book.league_key or None
        d_final, sfa_diag = sfa.apply_strong_favorite_adjustment(
            d_final,
            s_final,
            sfa_cfg_use,
            model.sfa_book,
            league=lg,
            season=season,
            max_goals=cfg.max_goals,
            lambda_min=float(scfg.lambda_min),
            already_applied=False,
            market_favorite_odds=mkt_fav,
        )

    s_before_sftc = float(s_final)
    sftc_cfg = resolve_sftc_config(cfg)
    s_final, sftc_diag = sft.apply_strong_favourite_total_correction(
        s_final, mkt_fav, sftc_cfg,
    )
    d_final = gm.clamp_goal_diff(d_final, s_final, cfg.lambda_epsilon)

    lam_min = scfg.lambda_min
    lh_raw, la_raw, lambda_home, lambda_away, clipped, _, _ = smom.lambdas_from_sd(
        s_final, d_final, lambda_min=lam_min,
    )
    if clipped:
        s_final = lambda_home + lambda_away
        d_final = lambda_home - lambda_away

    matrix_poisson = gm.build_score_matrix(lambda_home, lambda_away, cfg.max_goals)
    p1_pois, px_pois, p2_pois = gm.compute_1x2(matrix_poisson)
    score_00_pois = matrix_poisson[0][0] if matrix_poisson else 0.0
    score_11_pois = matrix_poisson[1][1] if len(matrix_poisson) > 1 else 0.0

    gamma_season = float(cal.gamma or 0.0) if cfg.use_dixon_coles else 0.0
    ddc_cfg = resolve_dynamic_dc_config(cfg)
    dc_res = ddc.resolve_gamma_effective(
        d_final, gamma_season=gamma_season, cfg=ddc_cfg,
    )
    gamma_eff = float(dc_res.gamma_effective) if cfg.use_dixon_coles else 0.0
    if not cfg.use_dixon_coles:
        gamma_eff = 0.0

    matrix = matrix_poisson
    if cfg.use_dixon_coles and abs(gamma_eff) > 1e-15:
        matrix = gm.apply_dixon_coles(matrix_poisson, lambda_home, lambda_away, gamma_eff)

    draw_target = None
    draw_diag = None
    if cfg.use_draw_model:
        px_matrix = gm.draw_probability(matrix)
        if model.draw.mode == "residual_dc":
            q = model.draw.target_q_multiplier(d_final, s_final, cfg)
            draw_target = q * px_matrix
        else:
            draw_target = model.draw.target_px(d_final, s_final)
        q_min, q_max = effective_draw_q_bounds(cfg, model.draw.mode)
        matrix, draw_diag = gm.adjust_matrix_to_draw_target(
            matrix, draw_target, q_min=q_min, q_max=q_max,
        )

    p1_fin, px_fin, p2_fin = gm.compute_1x2(matrix)
    score_00_fin = matrix[0][0] if matrix else 0.0
    score_11_fin = matrix[1][1] if len(matrix) > 1 else 0.0
    markets = gm.markets_from_matrix(matrix)

    return Prediction(
        home_team_id=home_id,
        away_team_id=away_id,
        home_team=home_name,
        away_team=away_name,
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        d_model=d_for_cal,
        s_model=s_for_cal,
        d_final=d_final,
        s_final=s_final,
        markets=markets,
        draw_target=draw_target,
        draw_diagnostics=draw_diag,
        d_model_base=d_model_base,
        d_model_dynamic=d_model_dynamic,
        dynamic_correction=dynamic_correction,
        momentum=None,
        s_model_base=s_model_base,
        s_model_dynamic=s_model_dynamic,
        s_dynamic_correction=s_dynamic_correction,
        s_momentum=None,
        d_before_sfa=d_before_sfa,
        sfa=sfa_diag,
        s_before_sftc=s_before_sftc,
        sftc=sftc_diag,
        home_probability_final=p1_fin,
        draw_probability_final=px_fin,
        away_probability_final=p2_fin,
        home_probability_poisson=p1_pois,
        draw_probability_poisson=px_pois,
        away_probability_poisson=p2_pois,
        score_00_final=score_00_fin,
        score_11_final=score_11_fin,
        score_00_poisson=score_00_pois,
        score_11_poisson=score_11_pois,
        lambda_home_raw=lh_raw,
        lambda_away_raw=la_raw,
        lambda_clipping_applied=clipped,
        gamma_effective=gamma_eff,
        gamma_season=gamma_season,
        gamma_segment=dc_res.gamma_segment,
        dynamic_dc_gamma_enabled=dc_res.dynamic_dc_gamma_enabled,
        abs_d_model_final=abs(d_final),
        dc_applied=dc_res.dc_applied,
        dc_fallback_used=dc_res.dc_fallback_used,
        dc_fallback_reason=dc_res.dc_fallback_reason,
        dc_home_probability_delta=p1_fin - p1_pois,
        dc_draw_probability_delta=px_fin - px_pois,
        dc_away_probability_delta=p2_fin - p2_pois,
    )


def predict_scaled(
    model: gmt.TrainedModel,
    home_id: str,
    away_id: str,
    *,
    scale: float,
    match_date: Optional[date] = None,
    league: Optional[str] = None,
    season: Optional[str] = None,
    home_odds: Optional[float] = None,
    away_odds: Optional[float] = None,
    neutral: bool = False,
    derby: bool = False,
) -> Prediction:
    """
    scale=1 → full Dynamic D + S-EMA (production path).
    scale=0 → both off.
    else → d/s_for_cal = base + scale * (dynamic − base), then same calib/SFA/pricing.
    """
    kw = dict(
        neutral=neutral,
        derby=derby,
        match_date=match_date,
        league=league,
        season=season,
        home_odds=home_odds,
        away_odds=away_odds,
    )
    if abs(scale - 1.0) < 1e-12:
        return gmt.predict_match(
            model, home_id, away_id,
            apply_momentum=True, apply_s_momentum=True, **kw,
        )
    if abs(scale) < 1e-12:
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
    d_for_cal = d_base + scale * (d_dyn - d_base)
    s_for_cal = s_base + scale * (s_dyn - s_base)
    return finalize_from_ds(
        model, home_id, away_id,
        d_for_cal=d_for_cal,
        s_for_cal=s_for_cal,
        d_model_base=d_base,
        s_model_base=s_base,
        d_model_dynamic=d_dyn,
        s_model_dynamic=s_dyn,
        dynamic_correction=scale * (d_dyn - d_base),
        s_dynamic_correction=scale * (s_dyn - s_base),
        league=league,
        season=season,
        home_odds=home_odds,
        away_odds=away_odds,
    )
