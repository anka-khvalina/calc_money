"""Supabase REST-клиент вкладки «История»."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
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


def test_format_ui_date():
    assert sbh.format_ui_date("2025-08-15") == "15.08.2025"
    assert sbh.format_ui_date("2025-08-15T12:00:00") == "15.08.2025"
    assert sbh.format_ui_date("") == ""
    assert sbh.format_ui_date(None) == ""


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


def test_build_dirty_patch_derby_bool():
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
        derby_weight=0.0,
        neutral_weight=1.0,
        note=None,
    )
    payload = sbh.build_dirty_patch(m, {"derby": "да"})
    assert payload == {"derby_weight": sbh.DERBY_FLAG_YES}
    payload2 = sbh.build_dirty_patch(
        replace(m, derby_weight=sbh.DERBY_FLAG_YES),
        {"derby": "нет"},
    )
    assert payload2 == {"derby_weight": sbh.DERBY_FLAG_NO}


def test_is_derby_default_not_derby():
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
        derby_weight=None,
        neutral_weight=1.0,
        note=None,
    )
    assert not sbh.is_derby_match(m)
    assert not sbh.is_derby_match(replace(m, derby_weight=0))
    assert not sbh.is_derby_match(replace(m, derby_weight=0.7))
    assert sbh.is_derby_match(replace(m, derby_weight=sbh.DERBY_FLAG_YES))


def test_normalized_derby_weight():
    assert sbh.normalized_derby_weight(None) == sbh.DERBY_FLAG_NO
    assert sbh.normalized_derby_weight(0) == sbh.DERBY_FLAG_NO
    assert sbh.normalized_derby_weight(0.7) == sbh.DERBY_FLAG_NO
    assert sbh.normalized_derby_weight(1) == sbh.DERBY_FLAG_YES


def test_reset_all_derby_flags_dry_run():
    payload = json.dumps(
        [
            {"id": 1, "derby_weight": None},
            {"id": 2, "derby_weight": 1},
            {"id": 3, "derby_weight": 0},
        ]
    ).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
        assert sbh.reset_all_derby_flags(dry_run=True) == 2


def test_reset_all_derby_flags_patch():
    get_payload = json.dumps([{"id": 1, "derby_weight": 1}]).encode()

    def fake_urlopen(req, timeout=30.0):
        url = getattr(req, "full_url", None) or req.get_full_url()
        if req.method == "GET":
            return _mock_urlopen(get_payload)
        if req.method == "PATCH":
            assert "derby_weight" in req.data.decode()
            return _mock_urlopen(b"")
        raise AssertionError(f"unexpected {req.method} {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert sbh.reset_all_derby_flags(dry_run=False) == 1


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
    original = sbh.MatchFull(
        match_id=1,
        match_date="2025-08-15",
        league_id="uuid",
        league_name="PL",
        season_id=4,
        season_label="2025-26",
        home_team_id=1,
        home_team="Liverpool",
        away_team_id=2,
        away_team="Bournemouth",
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

    def side_effect(method, path, *, body=None, prefer=None, timeout=30.0):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return [{"match_id": 1, "home_odds": 1.27}]

    with patch("supabase_history._request", side_effect=side_effect):
        updated = sbh.patch_match(1, {"home_odds": 1.27}, original=original)
    assert captured["method"] == "PATCH"
    assert "id=eq.1" in captured["path"]
    assert captured["body"] == {"home_odds": 1.27}
    assert updated.home_team == "Liverpool"
    assert updated.away_team == "Bournemouth"
    assert updated.home_odds == 1.27


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
    assert csv.startswith("date,league,league_id,home_team_id,home_team")
    # id команд сохраняются для ключей модели
    assert "1,Liverpool,2,Bournemouth" in csv
    assert ",2.5," in csv
