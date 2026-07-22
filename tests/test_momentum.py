"""Acceptance tests for momentum / market-drift EMA (AC-1 … AC-9)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import momentum as mom  # noqa: E402


def test_ac1_residual_sides():
    rm, rh, ra = mom.residual_sides(0.80, 0.50)
    assert abs(rm - 0.30) < 1e-12
    assert abs(rh - 0.30) < 1e-12
    assert abs(ra - (-0.30)) < 1e-12


def test_ac2_ema_update():
    assert abs(mom.update_ema(0.10, 0.30, 0.30) - 0.16) < 1e-12


def test_ac3_dynamic_d():
    snap = mom.apply_momentum_to_d(
        0.60, 0.20, -0.10,
        mom.MomentumConfig(enabled=True, alpha=0.30, k=0.80, max_ema=0.50, min_matches=1),
        home_matches=2, away_matches=2,
    )
    assert abs(snap.dynamic_correction - 0.24) < 1e-12
    assert abs(snap.d_model_dynamic - 0.84) < 1e-12


def test_ac4_no_current_match_leak_in_ema():
    cfg = mom.MomentumConfig(alpha=0.30, k=0.80)
    book = mom.build_momentum_walk(
        [
            mom.MomentumWalkMatch(date(2025, 8, 1), "A", "B", 0.5, 0.8),
            mom.MomentumWalkMatch(date(2025, 8, 8), "A", "C", 0.4, 0.7),
        ],
        cfg,
    )
    # EMA for match 2 must not include residual of match 2
    key2 = mom.match_key(date(2025, 8, 8), "A", "C")
    rec2 = book.records[key2]
    # after match1: resid = 0.8-0.5=0.3 → EMA_A = 0.3*0.3=0.09
    assert abs(rec2.ema_home_before - 0.09) < 1e-9
    assert abs(rec2.residual_match - (0.7 - 0.4)) < 1e-9


def test_ac5_same_slice_no_cross_leak():
    cfg = mom.MomentumConfig(alpha=0.5, k=1.0)
    d = date(2025, 9, 1)
    book = mom.build_momentum_walk(
        [
            mom.MomentumWalkMatch(d, "A", "B", 0.0, 0.4),
            mom.MomentumWalkMatch(d, "C", "A", 0.0, -0.2),
        ],
        cfg,
    )
    r1 = book.records[mom.match_key(d, "A", "B")]
    r2 = book.records[mom.match_key(d, "C", "A")]
    # both start from 0 EMA (same slice)
    assert r1.ema_home_before == 0.0 and r1.ema_away_before == 0.0
    assert r2.ema_home_before == 0.0 and r2.ema_away_before == 0.0
    # after slice, A updated twice in order of list
    assert book.teams["A"].matches_count == 2


def test_ac6_missing_d_market_no_update():
    cfg = mom.MomentumConfig(alpha=0.30, k=0.80)
    book = mom.build_momentum_walk(
        [
            mom.MomentumWalkMatch(date(2025, 8, 1), "A", "B", 0.5, None),
            mom.MomentumWalkMatch(date(2025, 8, 8), "A", "C", 0.4, 0.7),
        ],
        cfg,
    )
    r1 = book.records[mom.match_key(date(2025, 8, 1), "A", "B")]
    assert r1.updated_ema is False
    assert book.teams["A"].matches_count == 1  # only second match
    r2 = book.records[mom.match_key(date(2025, 8, 8), "A", "C")]
    assert r2.ema_home_before == 0.0  # first match did not update


def test_ac7_disabled_zero_correction():
    snap = mom.apply_momentum_to_d(
        0.60, 0.20, -0.10,
        mom.MomentumConfig(enabled=False, alpha=0.30, k=0.80, max_ema=0.50, min_matches=1),
        home_matches=5, away_matches=5,
    )
    assert snap.dynamic_correction == 0.0
    assert abs(snap.d_model_dynamic - 0.60) < 1e-12


def test_ac8_ema_clamp():
    assert abs(mom.limited_ema(0.72, 0.50) - 0.50) < 1e-12
    snap = mom.apply_momentum_to_d(
        0.0, 0.72, 0.0,
        mom.MomentumConfig(k=1.0, max_ema=0.50, min_matches=1),
        home_matches=3, away_matches=3,
    )
    assert abs(snap.ema_home_limited - 0.50) < 1e-12
    assert abs(snap.dynamic_correction - 0.50) < 1e-12


def test_ac9_new_season_starts_zero():
    cfg = mom.MomentumConfig(alpha=0.30, k=0.80)
    book1 = mom.build_momentum_walk(
        [mom.MomentumWalkMatch(date(2024, 5, 1), "A", "B", 0.0, 0.5)],
        cfg,
    )
    assert book1.teams["A"].ema != 0.0
    book2 = mom.build_momentum_walk(
        [mom.MomentumWalkMatch(date(2025, 8, 1), "A", "B", 0.0, 0.5)],
        cfg,
    )
    # новый вызов = новый сезон / нулевое состояние
    r = book2.records[mom.match_key(date(2025, 8, 1), "A", "B")]
    assert r.ema_home_before == 0.0


def test_min_matches_gate():
    snap = mom.apply_momentum_to_d(
        1.0, 0.4, -0.2,
        mom.MomentumConfig(k=1.0, max_ema=1.0, min_matches=3),
        home_matches=1, away_matches=5,
    )
    # home below min → treated as 0
    assert abs(snap.dynamic_correction - (0.0 - (-0.2))) < 1e-12
