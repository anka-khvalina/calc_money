"""Regression: Line / Reports / Compare share one S/D/λ core; D weight policy is disabled."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "FairOddsCalc_iOS.html"


def _src() -> str:
    return HTML.read_text(encoding="utf-8")


def _slice(src: str, start_fn: str, end_fn: str) -> str:
    a = src.index(start_fn)
    b = src.index(end_fn, a)
    return src[a:b]


def test_all_predict_entrypoints_use_gm_core():
    src = _src()
    assert "function gmCoreSdLambdas(" in src

    line = _slice(src, "function gmPredict(", "function gmDClampSensitiveTags(")
    assert "gmCoreSdLambdas(" in line

    legacy = _slice(src, "function irPredictLegacy(", "function irTrainLegacy(")
    assert "gmCoreSdLambdas(" in legacy

    lambdas = _slice(src, "function irLambdasFromModel(", "function irBuildMatrixRecipe(")
    assert "gmCoreSdLambdas(" in lambdas
    # SFTC/SFA only inside shared core — not re-applied ad hoc
    assert "gmApplyStrongFavouriteTotalCorrection" not in lambdas
    assert "gmApplyStrongFavoriteAdjustment" not in lambdas

    # Bundle / at-market report paths must go through irLambdasFromModel or shared core
    at_mkt = _slice(src, "function irPredictAtMarket(", "function irPredictBundle(")
    assert "irLambdasFromModel(" in at_mkt or "gmCoreSdLambdas(" in at_mkt

    bundle = src[src.index("function irPredictBundle(") : src.index("function irPredictBundle(") + 2500]
    assert "irPredictAtMarket(" in bundle or "irLambdasFromModel(" in bundle or "gmCoreSdLambdas(" in bundle


def test_shared_core_order_sfa_then_sftc():
    src = _src()
    core = _slice(src, "function gmCoreSdLambdas(", "function gmPredict(")
    i_sfa = core.index("gmApplyStrongFavoriteAdjustment")
    i_sftc = core.index("gmApplyStrongFavouriteTotalCorrection")
    assert i_sfa < i_sftc


def test_lineweight_disabled_in_both_configs():
    cfg = json.loads((ROOT / "config" / "model_config.json").read_text(encoding="utf-8"))
    web = json.loads((ROOT / "web" / "model_config.json").read_text(encoding="utf-8"))
    assert cfg["lineWeight"]["mode"] == "disabled"
    assert web["lineWeight"]["mode"] == "disabled"
    assert cfg["lineWeight"] == web["lineWeight"]


def test_html_defaults_to_disabled_lineweight():
    src = _src()
    # Fallbacks must not resurrect soft as production default
    assert "|| 'disabled'" in src or '|| "disabled"' in src
    assert "lineWeightMode: lwRoot.mode || T.lineWeightMode || 'disabled'" in src
    # Help documents D policy without AH line weight
    assert "AH Line Weight выключен" in src or "AH line weight не участвует" in src


def test_d_train_weight_policy_python_matches_config():
    import sys

    sys.path.insert(0, str(ROOT / "app"))
    import goal_model_train as gmt
    import line_weights as lw

    raw = json.loads((ROOT / "config" / "model_config.json").read_text(encoding="utf-8"))
    cfg = gmt.apply_line_weight_config_from_mapping(gmt.ModelConfig(), raw)
    assert cfg.line_weight_mode == lw.MODE_DISABLED
    m = gmt.PreparedMatch(
        raw=gmt.RawMatch(date=None, league="T", home_team="A", away_team="B"),
        home_id="a",
        away_id="b",
        home_team="A",
        away_team="B",
        i_home=1,
        diff_goals=2.8,
        w_base=1.0,
    )
    gmt._apply_line_ah_weights([m], cfg)
    assert m.w_line_ah == 1.0
