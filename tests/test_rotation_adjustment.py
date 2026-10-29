"""Unit tests for Rotation Adjustment (post-process on base 1X2)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import rotation_adjustment as ra  # noqa: E402


def test_logit_residual_example():
    # Spec §13: P_model=0.60, P_close=0.55 → R ≈ -0.204
    r = ra.residual(0.55, 0.60)
    assert abs(r - (ra.logit(0.55) - ra.logit(0.60))) < 1e-12
    assert abs(r - (-0.204)) < 0.01


def test_no_rotation_identity():
    out = ra.apply_rotation_deltas(0.60, 0.24, 0.16, 0.0, 0.0)
    assert abs(out["p1"] - 0.60) < 1e-12
    assert abs(out["px"] - 0.24) < 1e-12
    assert abs(out["p2"] - 0.16) < 1e-12
    assert abs(out["p1"] + out["px"] + out["p2"] - 1.0) < 1e-12


def test_normalize_always_sums_to_one():
    for ph, pd, pa, dh, da in [
        (0.60, 0.24, 0.16, -0.16, 0.0),
        (0.15, 0.20, 0.65, 0.0, -0.12),
        (0.45, 0.29, 0.26, -0.12, -0.06),
        (0.05, 0.10, 0.85, -0.2, -0.1),
        (0.90, 0.06, 0.04, -0.3, 0.0),
        (0.95, 0.03, 0.02, 0.0, -0.25),
    ]:
        out = ra.apply_rotation_deltas(ph, pd, pa, dh, da)
        assert abs(out["p1"] + out["px"] + out["p2"] - 1.0) < 1e-9
        assert out["p1"] > 0 and out["px"] > 0 and out["p2"] > 0
        assert math.isfinite(out["k1"]) and math.isfinite(out["kx"]) and math.isfinite(out["k2"])


def test_example1_strong_home_rotation():
    # Spec §25
    out = ra.apply_rotation_deltas(0.60, 0.24, 0.16, -0.16, 0.0)
    assert abs(out["p1"] * 100 - 56.11) < 0.05
    assert abs(out["px"] * 100 - 26.34) < 0.05
    assert abs(out["p2"] * 100 - 17.56) < 0.05
    assert abs(out["k1"] - 1.78) < 0.01
    assert abs(out["kx"] - 3.80) < 0.01
    assert abs(out["k2"] - 5.70) < 0.02
    assert out["p1"] < 0.60


def test_example2_strong_away_favourite():
    # Spec §26
    out = ra.apply_rotation_deltas(0.15, 0.20, 0.65, 0.0, -0.12)
    assert abs(out["p1"] * 100 - 16.19) < 0.05
    assert abs(out["px"] * 100 - 21.59) < 0.05
    assert abs(out["p2"] * 100 - 62.22) < 0.05
    assert abs(out["k1"] - 6.18) < 0.02
    assert abs(out["kx"] - 4.63) < 0.02
    assert abs(out["k2"] - 1.61) < 0.02
    assert out["p2"] < 0.65


def test_example3_both_rotate():
    # Spec §27
    out = ra.apply_rotation_deltas(0.45, 0.29, 0.26, -0.12, -0.06)
    assert abs(out["p1"] * 100 - 42.73) < 0.05
    assert abs(out["px"] * 100 - 31.05) < 0.05
    assert abs(out["p2"] * 100 - 26.22) < 0.05
    assert abs(out["k1"] - 2.34) < 0.02
    assert abs(out["kx"] - 3.22) < 0.02
    assert abs(out["k2"] - 3.81) < 0.02


def test_order_independence_both_sides():
    a = ra.apply_rotation_deltas(0.45, 0.29, 0.26, -0.12, -0.06)
    # applying is joint — same deltas always same result
    b = ra.apply_rotation_deltas(0.45, 0.29, 0.26, -0.12, -0.06)
    assert a == b


def test_probability_buckets():
    edges = ra.DEFAULT_PROB_BUCKETS
    assert ra.probability_bucket(0.15, edges) == "0-20"
    assert ra.probability_bucket(0.25, edges) == "20-30"
    assert ra.probability_bucket(0.55, edges) == "50-60"
    assert ra.probability_bucket(0.80, edges) == ">=80"
    assert ra.probability_bucket(0.799, edges) == "70-80"


def test_reliability_levels():
    assert ra.reliability_level(10) == "low"
    assert ra.reliability_level(20) == "medium"
    assert ra.reliability_level(49) == "medium"
    assert ra.reliability_level(50) == "high"


def test_delta_from_buckets_middle_high():
    # Spec §16–18 style synthetic data in one bucket (~0.65)
    obs = []
    for i in range(60):
        obs.append(
            ra.TeamMatchObs(
                match_id=f"n{i}",
                team_id="T",
                opponent_id="O",
                league="L",
                season="S",
                season_index=0,
                home_flag=True,
                rotation_code="none",
                opponent_rotation_code="none",
                p_model=0.65,
                p_close=ra.inv_logit(ra.logit(0.65) - 0.03),  # R ≈ -0.03
                weight=1.0,
            )
        )
    for i in range(40):
        obs.append(
            ra.TeamMatchObs(
                match_id=f"m{i}",
                team_id="T",
                opponent_id="O",
                league="L",
                season="S",
                season_index=0,
                home_flag=True,
                rotation_code="middle",
                opponent_rotation_code="none",
                p_model=0.65,
                p_close=ra.inv_logit(ra.logit(0.65) - 0.09),
                weight=1.0,
            )
        )
    for i in range(40):
        obs.append(
            ra.TeamMatchObs(
                match_id=f"h{i}",
                team_id="T",
                opponent_id="O",
                league="L",
                season="S",
                season_index=0,
                home_flag=True,
                rotation_code="high",
                opponent_rotation_code="none",
                p_model=0.65,
                p_close=ra.inv_logit(ra.logit(0.65) - 0.19),
                weight=1.0,
            )
        )

    mid = ra.estimate_delta_logit(obs, rotation_code="middle", p_model=0.65)
    high = ra.estimate_delta_logit(obs, rotation_code="high", p_model=0.65)
    assert mid["adjustment_available"] is True
    assert high["adjustment_available"] is True
    assert abs(mid["delta_logit"] - (-0.06)) < 0.02
    assert abs(high["delta_logit"] - (-0.16)) < 0.02
    assert mid["n"] == 40
    assert high["reliability"] == "medium"


def test_unstable_positive_rejected():
    obs = []
    for i in range(30):
        obs.append(
            ra.TeamMatchObs(
                match_id=f"n{i}", team_id="T", opponent_id="O", league="L", season="S",
                season_index=0, home_flag=True, rotation_code="none", opponent_rotation_code="none",
                p_model=0.55, p_close=0.55, weight=1.0,
            )
        )
    for i in range(30):
        # rotation with POSITIVE residual relative to baseline → unstable
        obs.append(
            ra.TeamMatchObs(
                match_id=f"h{i}", team_id="T", opponent_id="O", league="L", season="S",
                season_index=0, home_flag=True, rotation_code="high", opponent_rotation_code="none",
                p_model=0.55, p_close=0.62, weight=1.0,
            )
        )
    est = ra.estimate_delta_logit(obs, rotation_code="high", p_model=0.55)
    assert est["adjustment_available"] is False
    assert est["reason"] == "unstable_estimate"


def test_adjust_match_none_none_skipped():
    res = ra.adjust_match_1x2(0.5, 0.25, 0.25, "none", "none", [])
    assert res["skipped"] is True
    assert res["reason"] == "no_rotation"
    assert res["adjusted"] is None


def test_adjust_match_with_synthetic_history():
    obs = []
    for i in range(60):
        obs.append(
            ra.TeamMatchObs(
                match_id=f"n{i}", team_id="T", opponent_id="O", league="L", season="S",
                season_index=0, home_flag=True, rotation_code="none", opponent_rotation_code="none",
                p_model=0.60, p_close=ra.inv_logit(ra.logit(0.60) - 0.02), weight=1.0,
            )
        )
    for i in range(55):
        obs.append(
            ra.TeamMatchObs(
                match_id=f"h{i}", team_id="T", opponent_id="O", league="L", season="S",
                season_index=0, home_flag=True, rotation_code="high", opponent_rotation_code="none",
                p_model=0.60, p_close=ra.inv_logit(ra.logit(0.60) - 0.18), weight=1.0,
            )
        )
    res = ra.adjust_match_1x2(0.60, 0.24, 0.16, "high", "none", obs)
    assert res["adjustment_available"] is True
    assert res["adjusted"] is not None
    assert res["adjusted"]["p1"] < 0.60
    assert res["delta_logit_home"] < 0
    assert abs(res["delta_logit_away"]) < 1e-12


def test_null_legacy_rotation_mapping():
    assert ra.normalize_rotation_code is not None
    from rotation_training import normalize_rotation_code
    assert normalize_rotation_code(None) == "none"
    assert normalize_rotation_code("") == "none"
    assert normalize_rotation_code("HIGH") == "high"


def test_season_weight_auto():
    assert ra.season_weight_auto(0) == 1.0
    assert ra.season_weight_auto(1) == 0.6
    assert ra.season_weight_auto(2) == 0.3


def test_walk_forward_cutoff_rejects_leakage():
    ra.assert_strict_past_cutoff(["2025-09-01", "2025-09-10"], "2025-09-15")
    try:
        ra.assert_strict_past_cutoff(["2025-09-01", "2025-09-15"], "2025-09-15")
        assert False, "expected leakage error"
    except ValueError as e:
        assert "leakage" in str(e)


def test_walk_forward_batches_expanding_window():
    matches = [
        {"match_id": "A", "date": "2025-09-01"},
        {"match_id": "B", "date": "2025-09-15"},
        {"match_id": "C1", "date": "2025-10-01"},
        {"match_id": "C2", "date": "2025-10-01"},
    ]
    steps = ra.walk_forward_batches(matches, min_train=1)
    assert steps[0]["predict_date"] == "2025-09-01"
    assert steps[0]["train_n"] == 0
    assert steps[0]["can_predict"] is False
    assert steps[1]["predict_date"] == "2025-09-15"
    assert steps[1]["train_n"] == 1
    assert steps[1]["can_predict"] is True
    assert steps[2]["predict_date"] == "2025-10-01"
    assert steps[2]["train_n"] == 2
    assert steps[2]["batch_n"] == 2
    # After each step, prior dates only — no same-day leakage into train of that step
    for st in steps:
        ra.assert_strict_past_cutoff(
            [m["date"] for m in matches if m["date"] < st["predict_date"]],
            st["predict_date"],
        )


def test_oos_residual_example_dortmund():
    # Spec example: P_model OOS 52%, closing 48% → R ≈ -0.16
    r = ra.oos_residual_from_probs(0.48, 0.52)
    assert abs(r - (-0.160)) < 0.01


def test_historical_oos_prediction_row_shape():
    row = ra.historical_oos_prediction_row(
        match_id="m1",
        match_date="2025-11-08",
        train_cutoff="2025-11-08",
        p1=0.5,
        px=0.25,
        p2=0.25,
        train_n=120,
    )
    assert row["estimator_note"] == "walkforward_oos"
    assert abs(row["p1"] + row["px"] + row["p2"] - 1.0) < 1e-12
    assert row["train_cutoff"] == "2025-11-08"
    assert row["model_version"] == "legacy_line_walkforward_v1"
