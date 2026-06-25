"""Supabase REST-клиент вкладки «История»."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import supabase_history as sbh  # noqa: E402
import supabase_teams as sb  # noqa: E402


def setup_function():
    sb.reset_settings_cache()


def _mock_urlopen(response_body: bytes):
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.read.return_value = response_body
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_fetch_season_summary_parses_rows():
    payload = json.dumps(
        [
            {
                "league_id": "uuid-1",
                "league_name": "Premier League",
                "season_id": 4,
                "season_label": "2025-26",
                "matches_count": 380,
                "imported_at": "2026-06-25T16:05:00+00:00",
            }
        ]
    ).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
        rows = sbh.fetch_season_summary()
    assert len(rows) == 1
    assert rows[0].league_name == "Premier League"
    assert rows[0].row_id == "uuid-1|4"
    assert sbh.format_imported_at(rows[0].imported_at) == "2026-06-25 16:05"


def test_fetch_matches_filters_by_league_and_season():
    payload = json.dumps(
        [
            {
                "match_id": 1,
                "match_date": "2025-08-15",
                "league_id": "uuid-1",
                "league_name": "Premier League",
                "season_id": 4,
                "season_label": "2025-26",
                "home_team_id": 12,
                "home_team": "Liverpool",
                "away_team_id": 3,
                "away_team": "Bournemouth",
                "closing_ah_home": None,
                "closing_total_line": None,
                "ah_home_odds": None,
                "ah_away_odds": None,
                "over_odds": None,
                "under_odds": None,
                "home_odds": 1.3,
                "draw_odds": 6.25,
                "away_odds": 9.75,
                "is_neutral": False,
                "match_weight": 1,
                "derby_weight": 1,
                "neutral_weight": 1,
                "note": None,
            }
        ]
    ).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)) as opener:
        matches = sbh.fetch_matches("uuid-1", 4)
    req = opener.call_args[0][0]
    url = getattr(req, "full_url", None) or req.get_full_url()
    assert "league_id=eq.uuid-1" in url
    assert "season_id=eq.4" in url
    assert matches[0].home_team == "Liverpool"
    assert sbh.format_cell(matches[0].home_odds, kind="num") == "1.3"
    assert sbh.format_cell(matches[0].is_neutral, kind="bool") == "нет"


def test_build_dirty_patch_only_changed():
    m = sbh.MatchFull(
        match_id=1,
        match_date="2025-08-15",
        league_id="uuid",
        league_name="PL",
        season_id=4,
        season_label="2025-26",
        home_team_id=1,
        home_team="Arsenal",
        away_team_id=2,
        away_team="Chelsea",
        closing_ah_home=None,
        closing_total_line=2.5,
        ah_home_odds=None,
        ah_away_odds=None,
        over_odds=1.9,
        under_odds=2.0,
        home_odds=1.3,
        draw_odds=6.0,
        away_odds=9.0,
        is_neutral=False,
        match_weight=1.0,
        derby_weight=1.0,
        neutral_weight=1.0,
        note=None,
    )
    payload = sbh.build_dirty_patch(m, {"o1": "1,27", "ox": "6,0"})
    assert payload == {"home_odds": 1.27}


def test_build_dirty_patch_rejects_bad_odds():
    m = sbh.MatchFull(
        match_id=1,
        match_date="2025-08-15",
        league_id="uuid",
        league_name="PL",
        season_id=4,
        season_label="2025-26",
        home_team_id=1,
        home_team="A",
        away_team_id=2,
        away_team="B",
        closing_ah_home=None,
        closing_total_line=None,
        ah_home_odds=None,
        ah_away_odds=None,
        over_odds=None,
        under_odds=None,
        home_odds=1.3,
        draw_odds=6.0,
        away_odds=9.0,
        is_neutral=False,
        match_weight=1.0,
        derby_weight=1.0,
        neutral_weight=1.0,
        note=None,
    )
    try:
        sbh.build_dirty_patch(m, {"o1": "0,95"})
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Проверьте" in str(exc)


def test_build_dirty_patch_match_and_neutral_weights():
    m = sbh.MatchFull(
        match_id=1,
        match_date="2025-08-15",
        league_id="uuid",
        league_name="PL",
        season_id=4,
        season_label="2025-26",
        home_team_id=1,
        home_team="A",
        away_team_id=2,
        away_team="B",
        closing_ah_home=None,
        closing_total_line=None,
        ah_home_odds=None,
        ah_away_odds=None,
        over_odds=None,
        under_odds=None,
        home_odds=1.3,
        draw_odds=6.0,
        away_odds=9.0,
        is_neutral=False,
        match_weight=1.0,
        derby_weight=1.0,
        neutral_weight=1.0,
        note=None,
    )
    payload = sbh.build_dirty_patch(m, {"match_w": "0,8", "neutr_w": "0,7"})
    assert payload == {"match_weight": 0.8, "neutral_weight": 0.7}


def test_patch_match_sends_whitelist_only():
    import json
    from unittest.mock import patch

    captured = {}

    def side_effect(method, path, *, body=None, prefer=None, timeout=30.0):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return [{"match_id": 1, "home_odds": 1.27, "home_team": "A", "away_team": "B", "match_date": "2025-08-15", "league_id": "u", "league_name": "L", "season_id": 4, "season_label": "25-26", "is_neutral": False, "match_weight": 1, "derby_weight": 1, "neutral_weight": 1}]

    with patch("supabase_history._request", side_effect=side_effect):
        sbh.patch_match(1, {"home_odds": 1.27})
    assert captured["method"] == "PATCH"
    assert "id=eq.1" in captured["path"]
    assert captured["body"] == {"home_odds": 1.27}


def test_matches_to_goal_csv():
    m = sbh.MatchFull(
        match_id=1,
        match_date="2025-08-15",
        league_id="uuid-1",
        league_name="Premier League",
        season_id=4,
        season_label="2025-26",
        home_team_id=1,
        home_team="Liverpool",
        away_team_id=2,
        away_team="Bournemouth",
        closing_ah_home=None,
        closing_total_line=2.5,
        ah_home_odds=None,
        ah_away_odds=None,
        over_odds=1.9,
        under_odds=2.0,
        home_odds=1.3,
        draw_odds=6.25,
        away_odds=9.75,
        is_neutral=False,
        match_weight=1.0,
        derby_weight=1.0,
        neutral_weight=1.0,
        note=None,
    )
    csv = sbh.matches_to_goal_csv([m])
    assert csv.startswith("date,league,home_team")
    assert "Liverpool,Bournemouth" in csv
    assert ",2.5," in csv
