"""Tests for regularized hierarchical WLS ratings (not sequential Elo)."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import hierarchical_wls as hwls  # noqa: E402
import goal_model_train as gmt  # noqa: E402


def test_effective_n_equal_weights():
    assert hwls.effective_n_from_weights([1, 1, 1, 1]) == pytest.approx(4.0)


def test_effective_n_unequal():
    # one dominant weight → effective_n closer to 1
    n = hwls.effective_n_from_weights([10.0, 0.1, 0.1])
    assert 1.0 < n < 2.0


def test_confidence_and_lambda_monotonic():
    k = 8.0
    c_lo = hwls.rating_confidence(4.0, k)
    c_hi = hwls.rating_confidence(24.0, k)
    assert c_lo == pytest.approx(4 / 12)
    assert c_hi == pytest.approx(24 / 32)
    lam_lo = hwls.lambda_team_from_confidence(c_lo, lambda_min=0.02, lambda_max=0.5)
    lam_hi = hwls.lambda_team_from_confidence(c_hi, lambda_min=0.02, lambda_max=0.5)
    assert lam_lo > lam_hi  # low confidence → stronger shrinkage


def test_time_weight_fresher_higher():
    import math

    as_of = date(2026, 7, 1)
    w_new = hwls.time_weight(date(2026, 6, 1), as_of=as_of, half_life_days=60)
    w_old = hwls.time_weight(date(2025, 7, 1), as_of=as_of, half_life_days=60)
    assert w_new > w_old
    assert w_new == pytest.approx(math.exp(-30 / 60))


def test_prior_previous_season_and_promoted():
    cfg = hwls.HierarchicalWlsConfig(
        prior=hwls.PriorConfig(mode=hwls.PRIOR_PREVIOUS_SEASON, promoted_team_prior=-0.4)
    ).validated()
    priors = hwls.resolve_team_priors(
        ["A", "B", "New"],
        cfg,
        previous_season={"A": 1.0, "B": -1.0},
    )
    # New gets promoted prior then re-centered
    assert "New" in priors
    assert abs(sum(priors.values())) < 1e-9


def test_config_modes():
    cfg = hwls.rating_config_from_mapping(
        {"rating": {"mode": "hierarchical_wls_shadow", "confidence_k": 10}}
    )
    assert cfg.mode == hwls.MODE_SHADOW
    assert cfg.uses_hierarchical_fit
    assert not cfg.production_is_hierarchical


def _toy_raw(n_rounds: int = 6) -> list:
    """Small balanced league with clear D_market signal."""
    teams = ["T1", "T2", "T3", "T4"]
    # latent strengths
    R = {"T1": 0.8, "T2": 0.2, "T3": -0.2, "T4": -0.8}
    H = 0.25
    raw = []
    d0 = date(2025, 8, 1)
    for rnd in range(n_rounds):
        for i, home in enumerate(teams):
            away = teams[(i + 1 + rnd) % len(teams)]
            if home == away:
                continue
            Dm = R[home] - R[away] + H
            # AH line ≈ -D, odds near even after de-vig path uses fallback
            raw.append(
                gmt.RawMatch(
                    date=d0 + timedelta(days=rnd * 7 + i),
                    league="Test",
                    league_id="TEST",
                    home_team=home,
                    away_team=away,
                    home_team_id=home,
                    away_team_id=away,
                    closing_ah_home=-Dm,
                    closing_total_line=2.5,
                    ah_home_odds=1.95,
                    ah_away_odds=1.95,
                    over_odds=1.90,
                    under_odds=1.90,
                    quality_match_weight=1.0,
                )
            )
    return raw


def test_hierarchical_fit_team_specific_lambda():
    raw = _toy_raw()
    # Drop most matches for T4 → low effective_n
    raw = [m for m in raw if not (m.home_team == "T4" or m.away_team == "T4")] + [
        m for m in raw if m.home_team == "T4" or m.away_team == "T4"
    ][:2]
    cfg = gmt.ModelConfig(
        rating_mode=hwls.MODE_HIERARCHICAL,
        rating_confidence_k=8.0,
        rating_lambda_min=0.05,
        rating_lambda_max=0.8,
        rating_prior_mode=hwls.PRIOR_LEAGUE_MEAN,
        rating_time_decay_enabled=False,
        use_dixon_coles=False,
        use_draw_model=False,
        d_correction_mode="disabled",
        momentum_enabled=False,
        s_momentum_enabled=False,
        reg_lambda=0.1,
    )
    matches = gmt.prepare_matches(raw, cfg)
    gmt.devig_and_infer(matches, cfg)
    # ensure D present (fallback_ah path)
    for m in matches:
        if m.diff_goals is None and m.raw.closing_ah_home is not None:
            m.diff_goals = -m.raw.closing_ah_home
            m.sum_goals = m.raw.closing_total_line or 2.5
    st = gmt.fit_hierarchical_strength_ratings(matches, cfg)
    assert st.rating_mode == hwls.MODE_HIERARCHICAL
    assert len(st.team_meta) >= 2
    ns = {t: meta.effective_n for t, meta in st.team_meta.items()}
    lams = {t: meta.lambda_team for t, meta in st.team_meta.items()}
    # team with fewer matches should have higher λ
    t_min = min(ns, key=ns.get)
    t_max = max(ns, key=ns.get)
    if ns[t_min] < ns[t_max] - 0.5:
        assert lams[t_min] >= lams[t_max] - 1e-9


def test_shadow_does_not_replace_production_strength():
    raw = _toy_raw(5)
    cfg = gmt.ModelConfig(
        rating_mode=hwls.MODE_SHADOW,
        rating_publish_cache=False,
        use_dixon_coles=False,
        use_draw_model=False,
        d_correction_mode="disabled",
        momentum_enabled=False,
        s_momentum_enabled=False,
        dynamic_dc_gamma_enabled=False,
    )
    model, _ = gmt.train_full_model(raw, cfg)
    assert model.strength.rating_mode == hwls.MODE_STANDARD
    assert model.shadow_strength is not None
    assert model.shadow_strength.rating_mode == hwls.MODE_HIERARCHICAL
    assert model.rating_shadow_monitor is not None


def test_standard_mode_unchanged_path():
    raw = _toy_raw(4)
    cfg = gmt.ModelConfig(
        rating_mode=hwls.MODE_STANDARD,
        use_dixon_coles=False,
        use_draw_model=False,
        d_correction_mode="disabled",
        momentum_enabled=False,
        s_momentum_enabled=False,
        dynamic_dc_gamma_enabled=False,
    )
    model, _ = gmt.train_full_model(raw, cfg)
    assert model.strength.rating_mode == hwls.MODE_STANDARD
    assert model.shadow_strength is None
    assert abs(sum(model.strength.ratings.values())) < 1e-6


def test_rating_cache_roundtrip(tmp_path):
    cfg = hwls.HierarchicalWlsConfig(
        mode=hwls.MODE_HIERARCHICAL,
        publish_cache=True,
        cache=hwls.RatingCacheConfig(versions_to_keep=2, root_dir=str(tmp_path)),
    ).validated()
    fit = hwls.HierarchicalFitResult(
        ratings={"A": 0.5, "B": -0.5},
        home_advantage=0.2,
        derby_home_delta=0.0,
        derby_n=0,
        derby_shrink_w=0.0,
        mae=0.1,
        rmse=0.12,
        n=10,
        team_meta={
            "A": hwls.TeamRatingMeta(0.0, 12.0, 0.6, 0.1),
            "B": hwls.TeamRatingMeta(0.0, 4.0, 0.33, 0.35),
        },
        rating_mode=hwls.MODE_HIERARCHICAL,
        rating_algorithm_version=hwls.RATING_ALGORITHM_VERSION,
        priors={"A": 0.0, "B": 0.0},
        regularization_parameters={"lambda_min": 0.02},
        time_decay_parameters={"enabled": False},
    )
    payload = hwls.build_rating_cache_payload(
        fit=fit,
        cfg=cfg,
        league_id="L1",
        league_name="League",
        model_version="2026-07-23T12:00:00Z",
        training_cutoff="2026-07-01",
        slow_fast={
            "A": {"slow_bias": 0.01, "fast_bias": -0.02},
            "B": {"slow_bias": 0.0, "fast_bias": 0.0},
        },
    )
    path = hwls.publish_rating_model_cache(payload, cfg, root=tmp_path)
    assert path.exists()
    loaded = hwls.load_active_rating_cache(
        rating_mode=hwls.MODE_HIERARCHICAL, league_id="L1", cfg=cfg, root=tmp_path
    )
    assert loaded is not None
    assert loaded.teams["A"].rating == pytest.approx(0.5)
    assert loaded.teams["A"].lambda_team == pytest.approx(0.1)
    assert loaded.teams["A"].slow_bias == pytest.approx(0.01)

    # second version + rollback
    payload2 = hwls.build_rating_cache_payload(
        fit=fit,
        cfg=cfg,
        league_id="L1",
        league_name="League",
        model_version="2026-07-23T13:00:00Z",
    )
    hwls.publish_rating_model_cache(payload2, cfg, root=tmp_path)
    hwls.activate_rating_cache_version(
        rating_mode=hwls.MODE_HIERARCHICAL,
        model_version="2026-07-23T12:00:00Z",
        league_id="L1",
        cfg=cfg,
        root=tmp_path,
    )
    rolled = hwls.load_active_rating_cache(
        rating_mode=hwls.MODE_HIERARCHICAL, league_id="L1", cfg=cfg, root=tmp_path
    )
    assert rolled.model_version == "2026-07-23T12:00:00Z"


def test_apply_rating_config_from_mapping():
    cfg = gmt.apply_rating_config_from_mapping(
        gmt.ModelConfig(),
        {
            "rating": {
                "mode": "hierarchical_wls",
                "confidence_k": 10,
                "prior": {"mode": "blended", "prior_reliability": 0.5},
                "time_decay": {"enabled": True, "half_life_days": 90},
            }
        },
    )
    assert cfg.rating_mode == hwls.MODE_HIERARCHICAL
    assert cfg.rating_confidence_k == 10
    assert cfg.rating_prior_mode == hwls.PRIOR_BLENDED
    assert cfg.rating_time_decay_enabled is True
    assert cfg.rating_half_life_days == 90


def test_by_league_mode_override():
    parsed = hwls.rating_config_from_mapping(
        {
            "rating": {
                "mode": "standard_wls",
                "byLeague": {
                    "Bundesliga": {"mode": "hierarchical_wls"},
                    "Serie A": {"mode": "hierarchical_wls"},
                    "Ligue 1": {"mode": "hierarchical_wls"},
                    "bundesliga": {"mode": "hierarchical_wls"},
                },
            }
        }
    )
    assert parsed.mode == hwls.MODE_STANDARD
    bl = hwls.apply_league_overrides(parsed, league_name="Bundesliga")
    assert bl.mode == hwls.MODE_HIERARCHICAL
    sa = hwls.apply_league_overrides(parsed, league_name="Serie A")
    assert sa.mode == hwls.MODE_HIERARCHICAL
    l1 = hwls.apply_league_overrides(parsed, league_name="Ligue 1")
    assert l1.mode == hwls.MODE_HIERARCHICAL
    pl = hwls.apply_league_overrides(parsed, league_name="Premier League")
    assert pl.mode == hwls.MODE_STANDARD
    by_id = hwls.apply_league_overrides(parsed, league_id="bundesliga")
    assert by_id.mode == hwls.MODE_HIERARCHICAL

    cfg = gmt.apply_rating_config_from_mapping(
        gmt.ModelConfig(),
        {
            "rating": {
                "mode": "standard_wls",
                "byLeague": {"La Liga": {"mode": "hierarchical_wls"}},
            }
        },
    )
    assert cfg.rating_mode == hwls.MODE_STANDARD
    assert "La Liga" in cfg.rating_by_league
    r_ll = gmt.resolve_rating_config(cfg, league_name="La Liga")
    assert r_ll.mode == hwls.MODE_HIERARCHICAL
    r_pl = gmt.resolve_rating_config(cfg, league_name="Premier League")
    assert r_pl.mode == hwls.MODE_STANDARD
