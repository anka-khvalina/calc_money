"""Unit tests for ideal prediction helpers and API (no live Supabase required)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import ideal_predictions as ip  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import history_api  # noqa: E402


LEAGUE = "0e928134-ae08-48fd-8d2c-3539a994b054"


def _upcoming_match(**overrides):
    m = {
        "match_id": 18542,
        "league_id": LEAGUE,
        "league_name": "LaLiga",
        "season_id": 22,
        "season_label": "2026-27",
        "home_team_id": 10,
        "away_team_id": 20,
        "home_team": "Levante",
        "away_team": "Betis",
        "match_date": "2026-12-20",
        "home_odds": None,
        "draw_odds": None,
        "away_odds": None,
        "closing_ah_home": None,
        "ah_home_odds": None,
        "ah_away_odds": None,
        "closing_total_line": None,
        "over_odds": None,
        "under_odds": None,
    }
    m.update(overrides)
    return m


def _played_match(**overrides):
    return _upcoming_match(
        home_odds=2.05,
        draw_odds=3.45,
        away_odds=3.80,
        closing_ah_home=-0.25,
        ah_home_odds=1.86,
        ah_away_odds=2.04,
        closing_total_line=2.5,
        over_odds=1.95,
        under_odds=1.95,
        **overrides,
    )


PRED = {
    "markets": [
        {
            "market": "1X2",
            "line": None,
            "outcomes": [
                {"outcome": "HOME", "odds": 2.18, "probability": 0.4587},
                {"outcome": "DRAW", "odds": 3.40, "probability": 0.2941},
                {"outcome": "AWAY", "odds": 3.55, "probability": 0.2817},
            ],
        },
        {
            "market": "ASIAN_HANDICAP",
            "line": -0.25,
            "outcomes": [
                {"outcome": "HOME", "odds": 1.94},
                {"outcome": "AWAY", "odds": 1.96},
            ],
        },
        {
            "market": "TOTAL",
            "line": 2.5,
            "outcomes": [
                {"outcome": "OVER", "odds": 1.88},
                {"outcome": "UNDER", "odds": 2.02},
            ],
        },
    ]
}
SETTINGS = {
    "margin": 0.045,
    "withMargin": True,
    "derby": False,
    "neutralField": False,
}


# ----- pure helpers -----


def test_has_closing_odds_1x2():
    assert ip.has_closing_odds({"home_odds": 2.0, "draw_odds": 3.2, "away_odds": 3.5}) is True
    assert ip.has_closing_odds({"home_odds": None, "draw_odds": None, "away_odds": None}) is False
    assert ip.has_closing_odds({"home_odds": 1.0, "draw_odds": 3.0, "away_odds": 3.0}) is False


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
    assert ip.has_closing_odds(_upcoming_match()) is False


def test_closing_markets_structure():
    out = ip.closing_markets_from_match(_played_match())
    assert out is not None
    names = [x["market"] for x in out["markets"]]
    assert names == ["1X2", "ASIAN_HANDICAP", "TOTAL"]
    ah = next(x for x in out["markets"] if x["market"] == "ASIAN_HANDICAP")
    assert ah["line"] == -0.25
    tot = next(x for x in out["markets"] if x["market"] == "TOTAL")
    assert tot["line"] == 2.5
    assert ip.closing_markets_from_match(_upcoming_match()) is None


def test_closing_markets_skips_incomplete_ah():
    m = _upcoming_match(closing_ah_home=-0.25, ah_home_odds=1.9, ah_away_odds=None)
    assert ip.closing_markets_from_match(m) is None


def test_assert_saveable_played():
    with pytest.raises(ip.IdealPredictionError) as ei:
        ip.assert_saveable(_played_match(season_label="2026-27"))
    assert ei.value.code == "MATCH_ALREADY_PLAYED"
    assert ei.value.status == 409
    assert "2026-27" in ei.value.message


def test_assert_saveable_invalid_season():
    with patch.object(ip, "current_season_for_league", return_value=(22, "2026-27")):
        with pytest.raises(ip.IdealPredictionError) as ei:
            ip.assert_saveable(_upcoming_match(season_id=7, season_label="2024-25"))
    assert ei.value.code == "INVALID_SEASON"
    assert ei.value.status == 422


def test_assert_saveable_ok_current_season():
    with patch.object(ip, "current_season_for_league", return_value=(22, "2026-27")):
        ip.assert_saveable(_upcoming_match())  # no raise


# ----- create / update / detail / find (mocked REST) -----


def test_create_ideal_success():
    posted = {}

    def fake_request(method, path, body=None, **_kw):
        if method == "GET" and path.startswith("/v_matches_full"):
            return [_upcoming_match()]
        if method == "GET" and path.startswith("/ideal_predictions"):
            return []
        if method == "GET" and path.startswith("/v_season_summary"):
            return [{"season_id": 22, "season_label": "2026-27", "matches_count": 10}]
        if method == "POST" and path.startswith("/ideal_predictions"):
            posted["body"] = body
            return [{**body, "id": 314, "saved_at": "2026-11-05T08:15:34Z"}]
        raise AssertionError(f"unexpected {method} {path}")

    with patch.object(ip, "_request", side_effect=fake_request):
        out = ip.create_ideal(18542, PRED, SETTINGS)
    assert out["status"] == "CREATED"
    assert out["matchId"] == 18542
    assert out["id"] == 314
    assert posted["body"]["match_id"] == 18542
    assert posted["body"]["prediction_data"] == PRED
    assert posted["body"]["calculation_settings"] == SETTINGS
    assert posted["body"]["home_team"] == "Levante"
    assert isinstance(posted["body"]["match_id"], int)


def test_create_ideal_already_exists():
    def fake_request(method, path, body=None, **_kw):
        if method == "GET" and path.startswith("/v_matches_full"):
            return [_upcoming_match()]
        if method == "GET" and path.startswith("/v_season_summary"):
            return [{"season_id": 22, "season_label": "2026-27", "matches_count": 10}]
        if method == "GET" and path.startswith("/ideal_predictions"):
            return [{"id": 1, "match_id": 18542}]
        raise AssertionError(f"unexpected {method} {path}")

    with patch.object(ip, "_request", side_effect=fake_request):
        with pytest.raises(ip.IdealPredictionError) as ei:
            ip.create_ideal(18542, PRED, SETTINGS)
    assert ei.value.code == "IDEAL_PREDICTION_ALREADY_EXISTS"
    assert ei.value.status == 409


def test_create_ideal_match_not_found():
    with patch.object(ip, "_request", return_value=[]):
        with pytest.raises(ip.IdealPredictionError) as ei:
            ip.create_ideal(99999, PRED, SETTINGS)
    assert ei.value.code == "MATCH_NOT_FOUND"
    assert ei.value.status == 404


def test_create_ideal_played_blocked():
    def fake_request(method, path, body=None, **_kw):
        if method == "GET" and path.startswith("/v_matches_full"):
            return [_played_match()]
        raise AssertionError(path)

    with patch.object(ip, "_request", side_effect=fake_request):
        with pytest.raises(ip.IdealPredictionError) as ei:
            ip.create_ideal(18542, PRED, SETTINGS)
    assert ei.value.code == "MATCH_ALREADY_PLAYED"


def test_update_ideal_replaces_snapshot_atomically():
    patched = {}

    def fake_request(method, path, body=None, **_kw):
        if method == "GET" and path.startswith("/v_matches_full"):
            return [_upcoming_match()]
        if method == "GET" and path.startswith("/v_season_summary"):
            return [{"season_id": 22, "season_label": "2026-27", "matches_count": 5}]
        if method == "GET" and path.startswith("/ideal_predictions"):
            return [
                {
                    "id": 314,
                    "match_id": 18542,
                    "prediction_data": {"markets": []},
                    "calculation_settings": {"margin": 0.01},
                    "home_team": "Levante",
                    "away_team": "Betis",
                    "league_name": "LaLiga",
                    "season_label": "2026-27",
                    "match_date": "2026-12-20",
                }
            ]
        if method == "PATCH" and path.startswith("/ideal_predictions"):
            patched["body"] = body
            return [{"id": 314, "match_id": 18542, "saved_at": "2026-11-06T09:41:21Z", **body}]
        raise AssertionError(f"unexpected {method} {path}")

    new_pred = {"markets": [{"market": "1X2", "outcomes": []}]}
    new_set = {"margin": 0.05, "derby": True}
    with patch.object(ip, "_request", side_effect=fake_request):
        out = ip.update_ideal(18542, new_pred, new_set)
    assert out["status"] == "UPDATED"
    assert out["matchId"] == 18542
    assert patched["body"]["prediction_data"] == new_pred
    assert patched["body"]["calculation_settings"] == new_set
    assert "prediction_data" in patched["body"] and "calculation_settings" in patched["body"]


def test_update_ideal_not_found():
    def fake_request(method, path, body=None, **_kw):
        if method == "GET" and path.startswith("/v_matches_full"):
            return [_upcoming_match()]
        if method == "GET" and path.startswith("/v_season_summary"):
            return [{"season_id": 22, "season_label": "2026-27", "matches_count": 5}]
        if method == "GET" and path.startswith("/ideal_predictions"):
            return []
        raise AssertionError(path)

    with patch.object(ip, "_request", side_effect=fake_request):
        with pytest.raises(ip.IdealPredictionError) as ei:
            ip.update_ideal(18542, PRED, SETTINGS)
    assert ei.value.code == "IDEAL_PREDICTION_NOT_FOUND"
    assert ei.value.status == 404


def test_get_ideal_detail_upcoming_closing_null():
    def fake_request(method, path, body=None, **_kw):
        if method == "GET" and path.startswith("/ideal_predictions"):
            return [
                {
                    "id": 314,
                    "match_id": 18542,
                    "league_name": "LaLiga",
                    "season_label": "2026-27",
                    "home_team": "Levante",
                    "away_team": "Betis",
                    "match_date": "2026-12-20",
                    "saved_at": "2026-12-15T12:32:11Z",
                    "prediction_data": PRED,
                    "calculation_settings": SETTINGS,
                }
            ]
        if method == "GET" and path.startswith("/v_matches_full"):
            return [_upcoming_match()]
        raise AssertionError(path)

    with patch.object(ip, "_request", side_effect=fake_request):
        d = ip.get_ideal_detail(18542)
    assert d["status"] == "UPCOMING"
    assert d["closingOdds"] is None
    assert d["predictionData"] == PRED
    assert d["calculationSettings"] == SETTINGS
    assert d["match"]["matchId"] == 18542


def test_get_ideal_detail_played_returns_closing_without_mutating_pred():
    def fake_request(method, path, body=None, **_kw):
        if method == "GET" and path.startswith("/ideal_predictions"):
            return [
                {
                    "id": 314,
                    "match_id": 18542,
                    "league_name": "LaLiga",
                    "season_label": "2026-27",
                    "home_team": "Levante",
                    "away_team": "Betis",
                    "match_date": "2026-12-20",
                    "saved_at": "2026-12-15T12:32:11Z",
                    "prediction_data": PRED,
                    "calculation_settings": SETTINGS,
                }
            ]
        if method == "GET" and path.startswith("/v_matches_full"):
            return [_played_match()]
        raise AssertionError(path)

    with patch.object(ip, "_request", side_effect=fake_request):
        d = ip.get_ideal_detail(18542)
    assert d["status"] == "PLAYED"
    assert d["predictionData"] == PRED
    assert d["closingOdds"] is not None
    markets = {m["market"]: m for m in d["closingOdds"]["markets"]}
    assert markets["1X2"]["outcomes"][0]["odds"] == 2.05
    assert markets["ASIAN_HANDICAP"]["line"] == -0.25
    assert markets["TOTAL"]["line"] == 2.5


def test_list_ideals_status_filter():
    calls = {"n": 0}

    def fake_request(method, path, body=None, **_kw):
        if method == "GET" and path.startswith("/ideal_predictions"):
            return [
                {
                    "id": 1,
                    "match_id": 100,
                    "league_name": "LaLiga",
                    "season_label": "2026-27",
                    "home_team": "A",
                    "away_team": "B",
                    "match_date": "2026-12-01",
                    "saved_at": "2026-11-01T00:00:00Z",
                },
                {
                    "id": 2,
                    "match_id": 101,
                    "league_name": "LaLiga",
                    "season_label": "2026-27",
                    "home_team": "C",
                    "away_team": "D",
                    "match_date": "2026-12-02",
                    "saved_at": "2026-11-02T00:00:00Z",
                },
            ]
        if method == "GET" and path.startswith("/v_matches_full"):
            calls["n"] += 1
            if "eq.100" in path:
                return [_upcoming_match(match_id=100)]
            return [_played_match(match_id=101)]
        raise AssertionError(path)

    with patch.object(ip, "_request", side_effect=fake_request):
        upcoming = ip.list_ideals(status="UPCOMING", page=1, page_size=50)
        played = ip.list_ideals(status="PLAYED", page=1, page_size=50)
    assert len(upcoming["items"]) == 1
    assert upcoming["items"][0]["matchId"] == 100
    assert upcoming["items"][0]["hasClosingOdds"] is False
    assert len(played["items"]) == 1
    assert played["items"][0]["matchId"] == 101
    assert played["items"][0]["status"] == "PLAYED"


def test_find_upcoming_match_unique():
    with patch.object(ip, "_request", return_value=[_upcoming_match()]):
        m = ip.find_upcoming_match(
            league_id=LEAGUE, season_id=22, home_team_id=10, away_team_id=20
        )
    assert m["match_id"] == 18542


def test_find_upcoming_match_not_found():
    with patch.object(ip, "_request", return_value=[]):
        with pytest.raises(ip.IdealPredictionError) as ei:
            ip.find_upcoming_match(
                league_id=LEAGUE, season_id=22, home_team_id=10, away_team_id=20
            )
    assert ei.value.code == "MATCH_NOT_FOUND"


def test_find_upcoming_match_ambiguous():
    rows = [
        _upcoming_match(match_id=1, match_date="2026-12-01"),
        _upcoming_match(match_id=2, match_date="2026-12-20"),
    ]
    with patch.object(ip, "_request", return_value=rows):
        with pytest.raises(ip.IdealPredictionError) as ei:
            ip.find_upcoming_match(
                league_id=LEAGUE, season_id=22, home_team_id=10, away_team_id=20
            )
    assert ei.value.code == "AMBIGUOUS_MATCH"


def test_find_upcoming_match_prefers_single_without_closing():
    rows = [
        _played_match(match_id=1, match_date="2026-10-01"),
        _upcoming_match(match_id=2, match_date="2026-12-20"),
    ]
    with patch.object(ip, "_request", return_value=rows):
        m = ip.find_upcoming_match(
            league_id=LEAGUE, season_id=22, home_team_id=10, away_team_id=20
        )
    assert m["match_id"] == 2


def test_current_season_skips_test_and_empty():
    rows = [
        {"season_id": 99, "season_label": "TEST-99", "matches_count": 100},
        {"season_id": 88, "season_label": "2027-28", "matches_count": 0},
        {"season_id": 22, "season_label": "2026-27", "matches_count": 12},
    ]
    with patch.object(ip, "_request", return_value=rows):
        sid, lab = ip.current_season_for_league(LEAGUE)
    assert sid == 22
    assert lab == "2026-27"


# ----- FastAPI routes -----


def test_api_post_create_maps_errors():
    client = TestClient(history_api.app)
    with patch.object(
        ip,
        "create_ideal",
        side_effect=ip.IdealPredictionError(
            "IDEAL_PREDICTION_ALREADY_EXISTS", "exists", 409
        ),
    ):
        r = client.post(
            "/api/ideal-predictions",
            json={"matchId": 18542, "predictionData": PRED, "calculationSettings": SETTINGS},
        )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "IDEAL_PREDICTION_ALREADY_EXISTS"


def test_api_post_create_success():
    client = TestClient(history_api.app)
    with patch.object(
        ip,
        "create_ideal",
        return_value={
            "id": 314,
            "matchId": 18542,
            "status": "CREATED",
            "savedAt": "2026-11-05T08:15:34Z",
        },
    ):
        r = client.post(
            "/api/ideal-predictions",
            json={"matchId": 18542, "predictionData": PRED, "calculationSettings": SETTINGS},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "CREATED"
    assert body["matchId"] == 18542


def test_api_put_update_success():
    client = TestClient(history_api.app)
    with patch.object(
        ip,
        "update_ideal",
        return_value={
            "id": 314,
            "matchId": 18542,
            "status": "UPDATED",
            "savedAt": "2026-11-06T09:41:21Z",
        },
    ):
        r = client.put(
            "/api/ideal-predictions/18542",
            json={"predictionData": PRED, "calculationSettings": SETTINGS},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "UPDATED"


def test_api_get_list_and_detail():
    client = TestClient(history_api.app)
    with patch.object(
        ip,
        "list_ideals",
        return_value={"items": [{"matchId": 18542, "status": "UPCOMING"}], "page": 1, "pageSize": 50, "total": 1},
    ):
        r = client.get("/api/ideal-predictions?league=LaLiga&status=UPCOMING")
    assert r.status_code == 200
    assert r.json()["items"][0]["matchId"] == 18542

    with patch.object(
        ip,
        "get_ideal_detail",
        return_value={
            "id": 314,
            "match": {"matchId": 18542},
            "status": "UPCOMING",
            "predictionData": PRED,
            "calculationSettings": SETTINGS,
            "closingOdds": None,
        },
    ):
        r2 = client.get("/api/ideal-predictions/18542")
    assert r2.status_code == 200
    assert r2.json()["closingOdds"] is None


def test_api_resolve_match_uses_current_season():
    client = TestClient(history_api.app)
    match = _upcoming_match()
    with patch.object(ip, "current_season_for_league", return_value=(22, "2026-27")), patch.object(
        ip, "find_upcoming_match", return_value=match
    ) as find, patch.object(ip, "assert_saveable"), patch.object(
        ip, "get_ideal_by_match", return_value=None
    ):
        r = client.post(
            "/api/ideal-predictions/resolve-match",
            json={
                "leagueId": LEAGUE,
                "homeTeamId": 10,
                "awayTeamId": 20,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["matchId"] == 18542
    assert body["canSave"] is True
    assert body["existingIdealId"] is None
    find.assert_called_once()
    assert find.call_args.kwargs["season_id"] == 22


def test_api_resolve_match_played_blocks_save():
    client = TestClient(history_api.app)
    match = _played_match()
    with patch.object(ip, "current_season_for_league", return_value=(22, "2026-27")), patch.object(
        ip, "find_upcoming_match", return_value=match
    ), patch.object(ip, "get_ideal_by_match", return_value={"id": 9}):
        r = client.post(
            "/api/ideal-predictions/resolve-match",
            json={"leagueId": LEAGUE, "homeTeamId": 10, "awayTeamId": 20},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["canSave"] is False
    assert body["blockCode"] == "MATCH_ALREADY_PLAYED"
    assert body["existingIdealId"] == 9


def test_api_post_rejects_non_positive_match_id():
    client = TestClient(history_api.app)
    r = client.post(
        "/api/ideal-predictions",
        json={"matchId": 0, "predictionData": {}, "calculationSettings": {}},
    )
    assert r.status_code == 422


# ----- UI wiring smoke -----


def test_html_has_ideal_tab_and_snapshot_hooks():
    html = (ROOT / "web" / "FairOddsCalc_iOS.html").read_text(encoding="utf-8")
    assert 'data-tab="ideal"' in html
    assert "Идеальные прогнозы" in html
    assert "lastSuccessfulCalculation" in html
    assert "idealOnSuccessfulCalc" in html
    assert "/api/ideal-predictions/resolve-match" in html
    assert "id=\"idealSavePanel\"" in html or "id='idealSavePanel'" in html
    assert "id=\"tab-ideal\"" in html or "id='tab-ideal'" in html
    assert "UNIQUE" not in html  # DB concern stays in SQL
    sql = (ROOT / "docs" / "sql" / "ideal_predictions.sql").read_text(encoding="utf-8")
    assert "UNIQUE (match_id)" in sql
    assert "match_id               bigint" in sql
    assert "league_id              uuid" in sql
    assert "REFERENCES matches(id)" in sql
