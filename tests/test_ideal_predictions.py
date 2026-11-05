"""Unit tests for ideal prediction helpers (no live Supabase required)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import ideal_predictions as ip  # noqa: E402


def test_has_closing_odds_1x2():
    assert ip.has_closing_odds({"home_odds": 2.0, "draw_odds": 3.2, "away_odds": 3.5}) is True
    assert ip.has_closing_odds({"home_odds": None, "draw_odds": None, "away_odds": None}) is False


def test_has_closing_odds_ah_ou():
    m = {
        "closing_ah_home": -0.25,
        "ah_home_odds": 1.94,
        "ah_away_odds": 1.96,
        "home_odds": None,
    }
    assert ip.has_closing_odds(m) is True
    m2 = {
        "closing_total_line": 2.5,
        "over_odds": 1.88,
        "under_odds": 2.02,
    }
    assert ip.has_closing_odds(m2) is True


def test_closing_markets_structure():
    m = {
        "home_odds": 2.18,
        "draw_odds": 3.40,
        "away_odds": 3.55,
        "closing_ah_home": -0.25,
        "ah_home_odds": 1.94,
        "ah_away_odds": 1.96,
        "closing_total_line": 2.5,
        "over_odds": 1.88,
        "under_odds": 2.02,
    }
    out = ip.closing_markets_from_match(m)
    assert out is not None
    names = [x["market"] for x in out["markets"]]
    assert "1X2" in names and "ASIAN_HANDICAP" in names and "TOTAL" in names
    ah = next(x for x in out["markets"] if x["market"] == "ASIAN_HANDICAP")
    assert ah["line"] == -0.25


def test_assert_saveable_played():
    try:
        ip.assert_saveable(
            {
                "home_odds": 2.0,
                "draw_odds": 3.0,
                "away_odds": 3.5,
                "season_label": "2026-27",
                "league_id": "x",
                "season_id": 1,
            }
        )
        assert False
    except ip.IdealPredictionError as e:
        assert e.code == "MATCH_ALREADY_PLAYED"
        assert e.status == 409
