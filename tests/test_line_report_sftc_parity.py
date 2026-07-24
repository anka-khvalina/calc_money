"""Static wiring checks: Line and Reports share S/D/λ (+ SFTC) via gmCoreSdLambdas."""

from __future__ import annotations

from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "web" / "FairOddsCalc_iOS.html"


def _src() -> str:
    return HTML.read_text(encoding="utf-8")


def test_shared_core_applies_sftc():
    src = _src()
    assert "function gmCoreSdLambdas(" in src
    core_start = src.index("function gmCoreSdLambdas(")
    core_end = src.index("function gmPredict(", core_start)
    core = src[core_start:core_end]
    assert "gmApplyStrongFavouriteTotalCorrection" in core
    assert "gmApplyStrongFavoriteAdjustment" in core


def test_line_and_report_paths_call_shared_core():
    src = _src()
    # Line Auto
    pred = src[src.index("function gmPredict(") : src.index("function gmDClampSensitiveTags(")]
    assert "gmCoreSdLambdas(" in pred
    # Reports Legacy (1X2 on Line + Отчет)
    leg = src[src.index("function irPredictLegacy(") : src.index("function irTrainLegacy(")]
    assert "gmCoreSdLambdas(" in leg
    # Component recipe λ (must not skip SFTC)
    lam = src[src.index("function irLambdasFromModel(") : src.index("function irBuildMatrixRecipe(")]
    assert "gmCoreSdLambdas(" in lam
    assert "gmApplyStrongFavouriteTotalCorrection" not in lam  # only via shared core


def test_report_s_model_final_is_post_sftc_sf():
    src = _src()
    # S_model_final must be the S used for markets (sf), not raw S-EMA dynamic alone
    assert "S_model_final: (sf!=null && isFinite(Number(sf))) ? Number(sf) : null" in src
    assert "S_before_sftc:" in src
    assert "sftc_applied:" in src


def test_line_ui_exposes_sftc_note():
    src = _src()
    assert "SFTC (OU Auto)" in src
    assert "sftc: predAuto.sftc" in src
