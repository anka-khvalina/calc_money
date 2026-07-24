"""Strong Favorite Adjustment: percentile / threshold / off + shape providers."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import goal_model_train as gmt  # noqa: E402
import strong_favorite_adjustment as sfa  # noqa: E402


def test_default_mode_percentile():
    cfg = sfa.SfaConfig().validated()
    assert cfg.mode == sfa.MODE_PERCENTILE
    assert cfg.shape == sfa.SHAPE_STEPWISE


def test_stepwise_ac3_rarer_gets_more():
    cfg = sfa.SfaConfig(mode="percentile", shape="stepwise").validated()
    b8 = sfa.calculate_sfa("L", "S", 8.0, cfg)
    b3 = sfa.calculate_sfa("L", "S", 3.0, cfg)
    b1_5 = sfa.calculate_sfa("L", "S", 1.5, cfg)
    assert abs(b8 - 0.05) < 1e-12
    assert abs(b3 - 0.10) < 1e-12
    assert abs(b1_5 - 0.15) < 1e-12
    assert b1_5 > b3 > b8


def test_threshold_legacy_mode():
    cfg = sfa.SfaConfig(mode="threshold", odds_threshold=1.30, beta=0.15).validated()
    assert sfa.calculate_sfa("L", "S", None, cfg, favorite_fair_odds=1.25) == 0.15
    assert sfa.calculate_sfa("L", "S", None, cfg, favorite_fair_odds=1.40) == 0.0


def test_off_mode():
    cfg = sfa.SfaConfig(mode="off").validated()
    assert sfa.calculate_sfa("L", "S", 0.5, cfg, favorite_fair_odds=1.1) == 0.0


def test_apply_once_to_d():
    assert abs(sfa.apply_sfa_to_d(1.5, 0.1) - 1.6) < 1e-12
    assert abs(sfa.apply_sfa_to_d(-1.5, 0.1) - (-1.6)) < 1e-12
    assert abs(sfa.apply_sfa_to_d(0.0, 0.1) - 0.0) < 1e-12


def test_already_applied_guard():
    book = sfa.build_favorite_odds_book(
        [{"league": "L1", "season": "2025-26", "fav_odds": x} for x in [1.10, 1.15, 1.20, 1.25, 1.30, 1.40, 1.50] * 10],
        min_matches=20,
        default_league="L1",
    )
    cfg = sfa.SfaConfig(mode="percentile", min_matches=20).validated()
    d1, diag1 = sfa.apply_strong_favorite_adjustment(
        1.8, 2.6, cfg, book, league="L1", season="2025-26"
    )
    d2, diag2 = sfa.apply_strong_favorite_adjustment(
        d1, 2.6, cfg, book, league="L1", season="2025-26", already_applied=True
    )
    assert abs(d2 - d1) < 1e-12
    assert diag2.applied is False


def test_early_season_fallback():
    rows = []
    for o in [1.12, 1.18, 1.22, 1.28, 1.35, 1.45] * 10:
        rows.append({"league": "BL", "season": "2024-25", "fav_odds": o})
    # current season too small
    for o in [1.15, 1.20]:
        rows.append({"league": "BL", "season": "2025-26", "fav_odds": o})
    book = sfa.build_favorite_odds_book(rows, min_matches=40, default_league="BL")
    dist = book.resolve_dist("BL", "2025-26")
    assert dist is not None
    assert dist.source_season == "2024-25"
    assert dist.n >= 40


def test_config_json_default_percentile():
    import json

    raw = json.loads((ROOT / "config" / "model_config.json").read_text(encoding="utf-8"))
    cfg = sfa.sfa_config_from_mapping(raw)
    assert cfg.mode == sfa.MODE_PERCENTILE
    assert cfg.shape == sfa.SHAPE_STEPWISE


def test_apply_sfa_config_from_mapping():
    cfg = gmt.apply_sfa_config_from_mapping(
        gmt.ModelConfig(),
        {"strongFavoriteAdjustment": {"mode": "threshold", "beta": 0.2, "oddsThreshold": 1.25}},
    )
    assert cfg.sfa_mode == sfa.MODE_THRESHOLD
    assert abs(cfg.sfa_beta - 0.2) < 1e-12
    assert abs(cfg.sfa_odds_threshold - 1.25) < 1e-12


def test_linear_shape_between_knots():
    cfg = sfa.SfaConfig(mode="percentile", shape="linear").validated()
    # at P5 knot → 0.05; at P10 → 0
    assert abs(sfa.calculate_sfa("L", "S", 5.0, cfg) - 0.05) < 1e-12
    assert abs(sfa.calculate_sfa("L", "S", 10.0, cfg) - 0.0) < 1e-12
    mid = sfa.calculate_sfa("L", "S", 7.5, cfg)
    assert 0.0 < mid < 0.05


def test_diagnostics_fields():
    book = sfa.build_favorite_odds_book(
        [{"league": "X", "season": "S", "fav_odds": 1.1 + 0.05 * i} for i in range(50)],
        min_matches=20,
        default_league="X",
    )
    cfg = sfa.SfaConfig(mode="percentile", min_matches=20).validated()
    _d, diag = sfa.apply_strong_favorite_adjustment(2.0, 2.8, cfg, book, league="X", season="S")
    d = diag.as_dict()
    assert "favoriteFairOdds" in d
    assert "favoritePercentile" in d
    assert "sfaBeta" in d
    assert "D_base" in d
    assert "D_final" in d
