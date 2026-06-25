"""Парсер коэффициентов userbet.info."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import userbet_odds as ubo  # noqa: E402


EXAMPLE_RESPONSE = [
    {"m": 1, "b": 70, "h": "", "t": "", "l": "1", "v": 1.91},
    {"m": 1, "b": 70, "h": "", "t": "", "l": "x", "v": 4.47},
    {"m": 1, "b": 70, "h": "", "t": "", "l": "2", "v": 3.53},
    {"m": 12, "b": 70, "h": "", "t": "3.25", "l": "o", "v": 1.95},
    {"m": 12, "b": 70, "h": "", "t": "3.25", "l": "u", "v": 1.95},
    {"m": 28, "b": 70, "h": "-0.25", "t": "", "l": "1", "v": 1.70},
    {"m": 28, "b": 70, "h": "-0.25", "t": "", "l": "2", "v": 2.27},
]


def test_parse_example_from_spec():
    odds = ubo.parse_odds_response(EXAMPLE_RESPONSE)
    assert odds["home_odds"] == 1.91
    assert odds["draw_odds"] == 4.47
    assert odds["away_odds"] == 3.53
    assert odds["closing_total_line"] == 3.25
    assert odds["over_odds"] == 1.95
    assert odds["under_odds"] == 1.95
    assert odds["closing_ah_home"] == -0.25
    assert odds["ah_home_odds"] == 1.70
    assert odds["ah_away_odds"] == 2.27


def test_total_picks_min_diff_line():
    rows = [
        {"m": 1, "b": 70, "l": "1", "v": 2.0, "h": "", "t": ""},
        {"m": 1, "b": 70, "l": "x", "v": 3.5, "h": "", "t": ""},
        {"m": 1, "b": 70, "l": "2", "v": 3.0, "h": "", "t": ""},
        {"m": 12, "b": 70, "t": "2.5", "l": "o", "v": 1.46, "h": ""},
        {"m": 12, "b": 70, "t": "2.5", "l": "u", "v": 2.80, "h": ""},
        {"m": 12, "b": 70, "t": "3.25", "l": "o", "v": 1.95, "h": ""},
        {"m": 12, "b": 70, "t": "3.25", "l": "u", "v": 1.95, "h": ""},
        {"m": 28, "b": 70, "h": "-0.5", "l": "1", "v": 1.93, "t": ""},
        {"m": 28, "b": 70, "h": "-0.5", "l": "2", "v": 2.00, "t": ""},
    ]
    odds = ubo.parse_odds_response(rows)
    assert odds["closing_total_line"] == 3.25
    assert odds["closing_ah_home"] == -0.5


def test_normalize_handicap_plus_zero():
    assert ubo.normalize_handicap("+0") == 0.0
    assert ubo.normalize_handicap(" 5-1") is None


def test_filter_prefers_bookmaker_70():
    rows = [
        {"m": 1, "b": 99, "l": "1", "v": 9.99, "h": "", "t": ""},
        {"m": 1, "b": 70, "l": "1", "v": 1.5, "h": "", "t": ""},
        {"m": 1, "b": 70, "l": "x", "v": 4.0, "h": "", "t": ""},
        {"m": 1, "b": 70, "l": "2", "v": 5.0, "h": "", "t": ""},
        {"m": 12, "b": 70, "t": "2.5", "l": "o", "v": 1.9, "h": ""},
        {"m": 12, "b": 70, "t": "2.5", "l": "u", "v": 1.9, "h": ""},
        {"m": 28, "b": 70, "h": "0", "l": "1", "v": 1.8, "t": ""},
        {"m": 28, "b": 70, "h": "0", "l": "2", "v": 1.8, "t": ""},
    ]
    odds = ubo.parse_odds_response(rows)
    assert odds["home_odds"] == 1.5


def test_fetch_odds_requires_external_id():
    try:
        ubo.fetch_odds("")
        assert False, "expected UserbetError"
    except ubo.UserbetError as exc:
        assert "Введите id" in str(exc)


def test_fetch_odds_parses_http_response():
    payload = json.dumps(EXAMPLE_RESPONSE).encode()

    class FakeResp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        odds = ubo.fetch_odds("1630429703")
    assert odds["home_odds"] == 1.91


def test_odds_to_ui_edits():
    edits = ubo.odds_to_ui_edits({"home_odds": 1.91, "closing_ah_home": -0.5})
    assert edits["o1"] == "1,91"
    assert edits["ah"] == "-0,5"
