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
    parse = src[src.index("function sbHistBulkParseMatchWeight(") : src.index("/** Recommended split")]
    assert "return 1" in parse
    assert "n < 0" in parse


def test_bulk_weight_hint_present_with_research_numbers():
    src = _src()
    i_panel = src.index("Массовое управление весом матчей")
    i_hint = src.index('id="histBulkWeightHint"')
    assert i_panel < i_hint
    hint = src[i_hint : i_hint + 4000]
    # default recommended plan 1.0 / 0.8 / 0.6 with 60/120-day bounds
    assert "0–60" in hint and "61–120" in hint
    assert "1.0 / 0.8 / 0.6" in src
    # aggressive OU variant and the guard rails
    assert "1.0 / 0.7 / 0.4" in hint
    assert "не ставьте <code>0</code>" in hint.lower() or "не ставьте <code>0</code>" in hint
    assert "0.4" in hint


def test_weight_period_plan_matches_hint():
    src = _src()
    plan = src[src.index("const SB_WEIGHT_PERIOD_PLAN") : src.index("function sbHistShiftDate(")]
    assert "maxAgeDays: 60, weight: 1.0" in plan
    assert "maxAgeDays: 120, weight: 0.8" in plan
    assert "maxAgeDays: null, weight: 0.6" in plan


def test_weight_suggest_wired_to_season_and_form():
    src = _src()
    assert "function sbHistBuildWeightPeriods(" in src
    assert "function sbHistRenderWeightSuggest(" in src
    assert "function sbHistFillWeightForm(" in src
    # refreshed when a season is selected and when the hint opens
    sel = src[src.index("async function sbSelectHistSeason(") : src.index("function sbHistErrorMessage(")]
    assert "sbHistRenderWeightSuggest()" in sel
    assert "data-hist-weight-fill" in src
    assert "histBulkWeightHint')?.addEventListener('toggle'" in src
