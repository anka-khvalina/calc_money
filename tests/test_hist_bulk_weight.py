"""Static wiring: History bulk match_weight panel under bulk active."""

from __future__ import annotations

from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "web" / "FairOddsCalc_iOS.html"


def _src() -> str:
    return HTML.read_text(encoding="utf-8")


def test_bulk_weight_panel_under_bulk_active():
    src = _src()
    i_active = src.index("Массовое управление активностью матчей")
    i_weight = src.index("Массовое управление весом матчей")
    assert i_active < i_weight
    assert 'id="histBulkWeightDateFrom"' in src
    assert 'id="histBulkWeightDateTo"' in src
    assert 'id="histBulkMatchWeight"' in src
    assert 'id="histBulkWeightApply"' in src
    # default weight = 1
    assert 'id="histBulkMatchWeight" min="0" step="0.1" value="1"' in src


def test_bulk_weight_patches_match_weight_field():
    src = _src()
    assert "async function sbHistBulkPatchMatchWeight(" in src
    assert "async function sbHistBulkWeightApply(" in src
    fn = src[src.index("async function sbHistBulkPatchMatchWeight(") : src.index("function sbHistBulkSetMsg(")]
    assert "match_weight" in fn
    assert "method: 'PATCH'" in fn
    assert "bindClick('histBulkWeightApply'" in src


def test_bulk_weight_parse_default_and_validation_in_js():
    src = _src()
    parse = src[src.index("function sbHistBulkParseMatchWeight(") : src.index("async function sbHistBulkApply(")]
    assert "return 1" in parse
    assert "n < 0" in parse
