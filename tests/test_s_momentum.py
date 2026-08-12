"""Acceptance tests for S-EMA (dynamic_s_ema) AC-1 … AC-9."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import s_momentum as sm  # noqa: E402


def test_ac1_disabled_zero_correction():
    snap = sm.apply_s_momentum(
        2.50, 0.08, 0.12,
        sm.SMomentumConfig(enabled=False, alpha=0.30, k=1.0),
        home_matches=5, away_matches=5,
    )
    assert snap.dynamic_correction == 0.0
    assert abs(snap.s_model_dynamic - 2.50) < 1e-12


def test_ac2_positive_correction():
    snap = sm.apply_s_momentum(
        2.50, 0.08, 0.12,
        sm.SMomentumConfig(enabled=True, alpha=0.30, k=1.0),
        home_matches=2, away_matches=2,
    )
    assert abs(snap.s_momentum - 0.20) < 1e-12
    assert abs(snap.dynamic_correction - 0.20) < 1e-12
    assert abs(snap.s_model_dynamic - 2.70) < 1e-12


def test_ac3_negative_correction():
    snap = sm.apply_s_momentum(
        2.80, -0.10, -0.05,
        sm.SMomentumConfig(enabled=True, k=1.0),
        home_matches=2, away_matches=2,
    )
    assert abs(snap.s_momentum - (-0.15)) < 1e-12
    assert abs(snap.dynamic_correction - (-0.15)) < 1e-12
    assert abs(snap.s_model_dynamic - 2.65) < 1e-12


def test_ac4_ema_update():
    # 0.30*0.20 + 0.70*0.10 = 0.13
    assert abs(sm.update_ema(0.10, 0.20, 0.30) - 0.13) < 1e-12
    resid, half = sm.residual_s(2.90, 2.50)  # 0.40, 0.20
    assert abs(resid - 0.40) < 1e-12
    assert abs(half - 0.20) < 1e-12


def test_ac5_no_same_slice_leak():
    cfg = sm.SMomentumConfig(enabled=True, alpha=0.5, k=1.0)
    d = date(2025, 9, 1)
    book = sm.build_s_momentum_walk(
        [
            sm.SMomentumWalkMatch(d, "A", "B", 2.5, 2.9),
            sm.SMomentumWalkMatch(d, "C", "A", 2.4, 2.2),
        ],
        cfg,
    )
    r1 = book.records[sm.match_key(d, "A", "B")]
    r2 = book.records[sm.match_key(d, "C", "A")]
    assert r1.ema_home_before == 0.0 and r1.ema_away_before == 0.0
    assert r2.ema_home_before == 0.0 and r2.ema_away_before == 0.0


def test_ac5_no_current_match_in_next():
    cfg = sm.SMomentumConfig(enabled=True, alpha=0.30, k=1.0)
    book = sm.build_s_momentum_walk(
        [
            sm.SMomentumWalkMatch(date(2025, 8, 1), "A", "B", 2.5, 2.9),  # resid=0.4, half=0.2
            sm.SMomentumWalkMatch(date(2025, 8, 8), "A", "C", 2.4, 2.6),
        ],
        cfg,
    )
    r2 = book.records[sm.match_key(date(2025, 8, 8), "A", "C")]
    # after m1: EMA_A = 0.30*0.20 = 0.06
    assert abs(r2.ema_home_before - 0.06) < 1e-9


def test_ac6_lambdas_from_sd():
    lh_r, la_r, lh, la, clipped, s_a, d_a = sm.lambdas_from_sd(2.80, 0.60, lambda_min=0.05)
    assert abs(lh - 1.70) < 1e-12
    assert abs(la - 1.10) < 1e-12
    assert clipped is False


def test_ac7_min_team_matches():
    snap = sm.apply_s_momentum(
        2.50, 0.10, 0.20,
        sm.SMomentumConfig(enabled=True, k=1.0, min_team_matches=3),
        home_matches=5, away_matches=2,
    )
    assert abs(snap.ema_home_effective - 0.10) < 1e-12
    assert abs(snap.ema_away_effective - 0.0) < 1e-12
    assert abs(snap.s_momentum - 0.10) < 1e-12


def test_ac8_max_abs_correction():
    snap = sm.apply_s_momentum(
        2.50, 0.30, 0.15,
        sm.SMomentumConfig(enabled=True, k=1.0, max_abs_correction=0.30),
        home_matches=3, away_matches=3,
    )
    # raw = 0.45 → clamped 0.30
    assert abs(snap.dynamic_correction_raw - 0.45) < 1e-12
    assert abs(snap.dynamic_correction - 0.30) < 1e-12
    assert snap.correction_clamped is True


def test_ac9_record_fields_present():
    cfg = sm.SMomentumConfig(enabled=True, alpha=0.30, k=1.0)
    book = sm.build_s_momentum_walk(
        [sm.SMomentumWalkMatch(date(2025, 8, 1), "A", "B", 2.5, 2.8)],
        cfg,
    )
    r = book.records[sm.match_key(date(2025, 8, 1), "A", "B")]
    for attr in (
        "s_model_base", "ema_home_before", "ema_away_before", "s_momentum",
        "dynamic_correction", "s_model_dynamic", "residual_s_base",
        "ema_home_after", "ema_away_after",
    ):
        assert hasattr(r, attr)
    assert r.updated_ema is True
    assert abs(r.residual_s_base - 0.30) < 1e-12
    assert abs(r.team_residual_s - 0.15) < 1e-12


def test_missing_s_market_no_update():
    cfg = sm.SMomentumConfig(enabled=True, alpha=0.30, k=1.0)
    book = sm.build_s_momentum_walk(
        [
            sm.SMomentumWalkMatch(date(2025, 8, 1), "A", "B", 2.5, None),
            sm.SMomentumWalkMatch(date(2025, 8, 8), "A", "C", 2.4, 2.6),
        ],
        cfg,
    )
    r1 = book.records[sm.match_key(date(2025, 8, 1), "A", "B")]
    assert r1.updated_ema is False
    assert r1.update_skip_reason == "missing_s_market"
    r2 = book.records[sm.match_key(date(2025, 8, 8), "A", "C")]
    assert r2.ema_home_before == 0.0


def test_match_weight_scales_alpha():
    assert abs(sm.effective_alpha(0.30, 0.0) - 0.0) < 1e-12
    assert abs(sm.effective_alpha(0.30, 1.0) - 0.30) < 1e-12
    assert abs(sm.effective_alpha(0.30, 0.5) - 0.15) < 1e-12


def test_config_validation():
    try:
        sm.SMomentumConfig(alpha=0.0).validated()
        assert False, "expected error"
    except ValueError:
        pass
    try:
        sm.SMomentumConfig(k=-1).validated()
        assert False
    except ValueError:
        pass
    try:
        sm.SMomentumConfig(lambda_min=0).validated()
        assert False
    except ValueError:
        pass


def test_config_from_mapping():
    cfg = sm.s_momentum_config_from_mapping({
        "dynamic_s_ema": {
            "enabled": True, "alpha": 0.3, "k": 1.0,
            "min_team_matches": 1, "max_abs_team_ema": None,
            "max_abs_correction": None, "lambda_min": 0.05,
        }
    })
    assert cfg.enabled is True
    assert abs(cfg.k - 1.0) < 1e-12
    assert cfg.max_abs_team_ema is None


def test_s_state_aging_factor_and_independence():
    """AC-6: H_S independent from H_D; S aging formula."""
    import pytest

    cfg_s = sm.SMomentumConfig(
        enabled=True, alpha=0.5, k=1.0, min_team_matches=1,
        state_aging=sm.StateAgingConfig(enabled=True, half_life_days=30),
    ).validated()
    assert sm.state_aging_factor(30, cfg_s.state_aging) == pytest.approx(0.5)

    # Build book with gap
    walk = [
        sm.SMomentumWalkMatch(date(2026, 1, i), "H", "A", 2.5, 3.0)
        for i in range(1, 6)
    ]
    book = sm.build_s_momentum_walk(walk, cfg_s)
    snap30 = book.peek(home_id="H", away_id="A", s_model_base=2.5, match_date=date(2026, 2, 4))
    assert snap30.home_days_since_previous_match == 30
    assert snap30.home_dynamic_aging_factor == pytest.approx(0.5)
    assert abs(snap30.s_correction_after_aging) <= abs(snap30.s_correction_before_aging) + 1e-12

    # Different half-life changes S only
    cfg_s2 = sm.SMomentumConfig(
        enabled=True, alpha=0.5, k=1.0, min_team_matches=1,
        state_aging=sm.StateAgingConfig(enabled=True, half_life_days=60),
    ).validated()
    book2 = sm.build_s_momentum_walk(walk, cfg_s2)
    snap60 = book2.peek(home_id="H", away_id="A", s_model_base=2.5, match_date=date(2026, 2, 4))
    assert snap60.home_dynamic_aging_factor == pytest.approx(2 ** (-30 / 60))
    assert snap30.home_dynamic_aging_factor != pytest.approx(snap60.home_dynamic_aging_factor)


def test_s_aging_off_identity():
    import pytest

    cfg = sm.SMomentumConfig(
        enabled=True, alpha=0.5, k=1.0, min_team_matches=1,
        state_aging=sm.StateAgingConfig(enabled=False, half_life_days=60),
    ).validated()
    walk = [
        sm.SMomentumWalkMatch(date(2026, 1, i), "H", "A", 2.5, 3.2)
        for i in range(1, 5)
    ]
    book = sm.build_s_momentum_walk(walk, cfg)
    snap = book.peek(home_id="H", away_id="A", s_model_base=2.5, match_date=date(2026, 6, 1))
    assert snap.home_dynamic_aging_factor == pytest.approx(1.0)
    assert snap.s_correction_after_aging == pytest.approx(snap.s_correction_before_aging)


def test_s_config_parses_state_aging():
    import pytest

    cfg = sm.s_momentum_config_from_mapping({
        "dynamic_s": {
            "state_aging": {"enabled": True, "halfLifeDays": 40},
        },
        "dynamic_s_ema": {"enabled": True},
    })
    assert cfg.state_aging.enabled is True
    assert cfg.state_aging.half_life_days == pytest.approx(40.0)


def test_s_config_default_aging_off():
    import pytest

    cfg = sm.s_momentum_config_from_mapping({"dynamic_s_ema": {"enabled": True}})
    assert cfg.state_aging.enabled is False


def test_s_hd_hs_independent():
    """AC-6: changing H_S does not require / affect Dynamic D half-life config."""
    import pytest

    d_cfg = __import__("d_correction", fromlist=["*"])
    d = d_cfg.d_correction_config_from_mapping({
        "dynamic_d": {"state_aging": {"enabled": True, "half_life_days": 60}},
        "d_correction": {"mode": "slow_fast"},
    })
    s = sm.s_momentum_config_from_mapping({
        "dynamic_s": {"state_aging": {"enabled": True, "half_life_days": 30}},
        "dynamic_s_ema": {"enabled": True},
    })
    assert d.state_aging.half_life_days == pytest.approx(60.0)
    assert s.state_aging.half_life_days == pytest.approx(30.0)
    assert d.state_aging.half_life_days != s.state_aging.half_life_days
