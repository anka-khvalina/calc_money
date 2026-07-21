"""Tests for rotation → training_weight."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import goal_model_train as gmt  # noqa: E402
import rotation_training as rt  # noqa: E402


def test_normalize_unknown_to_none():
    assert rt.normalize_rotation_code(None) == "none"
    assert rt.normalize_rotation_code("") == "none"
    assert rt.normalize_rotation_code("  HIGH ") == "high"
    assert rt.normalize_rotation_code("medium") == "none"
    assert rt.normalize_rotation_code("strong") == "none"


def test_training_weight_table():
    assert rt.training_weight_for_rotation("none", "none") == 1.0
    assert rt.training_weight_for_rotation("middle", "none") == 0.7
    assert rt.training_weight_for_rotation("none", "middle") == 0.7
    assert rt.training_weight_for_rotation("middle", "middle") == 0.7
    assert rt.training_weight_for_rotation("high", "none") == 0.0
    assert rt.training_weight_for_rotation("none", "high") == 0.0
    assert rt.training_weight_for_rotation("middle", "high") == 0.0
    assert rt.training_weight_for_rotation("high", "high") == 0.0


def test_custom_middle_weight_from_cfg():
    w = {"none": 1.0, "middle": 0.5, "high": 0.0}
    assert rt.training_weight_for_rotation("middle", "none", w) == 0.5
    assert rt.training_weight_for_rotation("high", "middle", w) == 0.0


def test_rotation_reason_and_status():
    ann = rt.annotate_rotation_training("high", "middle")
    assert ann["training_weight"] == 0.0
    assert ann["training_status"] == "excluded"
    assert ann["rotation_reason"] == "high_home"
    ann2 = rt.annotate_rotation_training("middle", "middle")
    assert ann2["training_status"] == "reduced"
    assert ann2["rotation_reason"] == "middle_both"
    ann3 = rt.annotate_rotation_training("none", "none")
    assert ann3["training_status"] == "full"


def test_summarize_rotation_training():
    rows = [
        {"home_rotation_code": "none", "away_rotation_code": "none"},
        {"home_rotation_code": "middle", "away_rotation_code": "none"},
        {"home_rotation_code": "high", "away_rotation_code": "none"},
    ]
    s = rt.summarize_rotation_training(rows)
    assert s["total"] == 3
    assert s["full"] == 1
    assert s["reduced"] == 1
    assert s["excluded"] == 1
    assert abs(s["effective_sample_size"] - 1.7) < 1e-9


def test_base_weight_includes_rotation():
    m = gmt.RawMatch(
        date=None,
        league="PL",
        home_team="A",
        away_team="B",
        quality_match_weight=1.0,
        home_rotation_code="middle",
        away_rotation_code="none",
    )
    cfg = gmt.ModelConfig()
    assert abs(gmt.base_weight(m, cfg) - 0.7) < 1e-9
    m2 = gmt.RawMatch(
        date=None,
        league="PL",
        home_team="A",
        away_team="B",
        quality_match_weight=1.0,
        home_rotation_code="high",
        away_rotation_code="none",
    )
    assert gmt.base_weight(m2, cfg) == 0.0


def test_config_has_rotation_weights():
    import json
    cfg = json.loads((ROOT / "web" / "model_config.json").read_text(encoding="utf-8"))
    w = cfg["rotationTrainingWeights"]
    assert w["none"] == 1.0
    assert w["middle"] == 0.7
    assert w["high"] == 0.0
    from_cfg = rt.rotation_training_weights_from_cfg(cfg)
    assert from_cfg["middle"] == 0.7
