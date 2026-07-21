"""Tests for Auto 1X2 quality gate (acceptance criteria)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import auto_1x2_quality as q  # noqa: E402


def test_bias_score():
    assert abs(q.systematic_bias_score({"biasP1": 0.01, "biasPX": -0.02, "biasP2": 0.005}) - 0.035) < 1e-12


def test_accept_when_1x2_improves_and_ah_ou_ok():
    before = {
        "mae1x2": 0.050,
        "biasP1": 0.02,
        "biasPX": -0.03,
        "biasP2": 0.01,
        "maeAhLine": 0.10,
        "maeAhOdds": 0.08,
        "maeOuLine": 0.12,
        "maeOuOdds": 0.09,
    }
    after = {
        "mae1x2": 0.040,
        "biasP1": 0.01,
        "biasPX": -0.015,
        "biasP2": 0.005,
        "maeAhLine": 0.102,  # +2% ok under 5%
        "maeAhOdds": 0.081,
        "maeOuLine": 0.12,
        "maeOuOdds": 0.09,
    }
    gate = q.accept_auto_1x2_upgrade(before, after)
    assert gate["accepted"] is True
    assert gate["improved1x2"] is True
    assert gate["biasOk"] is True


def test_reject_when_1x2_not_improved():
    before = {"mae1x2": 0.04, "biasP1": 0.01, "biasPX": -0.01, "biasP2": 0.0,
              "maeAhLine": 0.1, "maeAhOdds": 0.1, "maeOuLine": 0.1, "maeOuOdds": 0.1}
    after = {"mae1x2": 0.041, "biasP1": 0.005, "biasPX": -0.005, "biasP2": 0.0,
             "maeAhLine": 0.1, "maeAhOdds": 0.1, "maeOuLine": 0.1, "maeOuOdds": 0.1}
    gate = q.accept_auto_1x2_upgrade(before, after)
    assert gate["accepted"] is False
    assert "1x2_mae_not_improved" in gate["reasons"]


def test_reject_when_ah_degrades_too_much():
    before = {"mae1x2": 0.05, "biasP1": 0.02, "biasPX": -0.02, "biasP2": 0.0,
              "maeAhLine": 0.10, "maeAhOdds": 0.10, "maeOuLine": 0.10, "maeOuOdds": 0.10}
    after = {"mae1x2": 0.04, "biasP1": 0.01, "biasPX": -0.01, "biasP2": 0.0,
             "maeAhLine": 0.12, "maeAhOdds": 0.10, "maeOuLine": 0.10, "maeOuOdds": 0.10}  # +20%
    gate = q.accept_auto_1x2_upgrade(before, after, {"ahOuDegradeMaxPct": 5})
    assert gate["accepted"] is False
    assert "ah_degraded_beyond_threshold" in gate["reasons"]


def test_reject_when_bias_worsens():
    before = {"mae1x2": 0.05, "biasP1": 0.01, "biasPX": -0.01, "biasP2": 0.0,
              "maeAhLine": 0.1, "maeAhOdds": 0.1, "maeOuLine": 0.1, "maeOuOdds": 0.1}
    after = {"mae1x2": 0.04, "biasP1": 0.03, "biasPX": -0.03, "biasP2": 0.02,
             "maeAhLine": 0.1, "maeAhOdds": 0.1, "maeOuLine": 0.1, "maeOuOdds": 0.1}
    gate = q.accept_auto_1x2_upgrade(before, after)
    assert gate["accepted"] is False
    assert "systematic_bias_not_reduced" in gate["reasons"]


def test_cfg_defaults_enabled():
    cfg = q.auto_1x2_calib_from_cfg({})
    assert cfg["enabled"] is True
    assert cfg["recalibrateSdWithMatrix"] is True
    assert abs(cfg["w1x2"] - 1.0) < 1e-9
