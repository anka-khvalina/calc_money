"""BT-01 H2H history view-model (FT-04 / КП)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import h2h_history as h2h  # noqa: E402


def _m(date, home, away, o1, ox, o2, **extra):
    row = {
        "match_id": extra.get("match_id", hash((date, home, away)) % 100000),
        "match_date": date,
        "home_team": home,
        "away_team": away,
        "home_odds": o1,
        "draw_odds": ox,
        "away_odds": o2,
    }
    row.update(extra)
    return row


def test_kp01_full_history():
    prev = [
        _m("2025-10-19", "Levante", "Betis", 3.22, 3.18, 2.29,
           closing_total_line=2.5, over_odds=1.88, under_odds=2.02),
        _m("2026-03-07", "Betis", "Levante", 1.80, 3.62, 4.90,
           closing_total_line=2.0, over_odds=1.90, under_odds=2.01),
    ]
    curr = [_m("2026-08-24", "Levante", "Betis", 2.88, 3.26, 2.51)]
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=prev,
        curr_matches=curr,
    )
    assert v["warnings"] == []
    assert len(v["seasons"]) == 2
    assert v["seasons"][0]["seasonLabel"] == "2025-26"
    assert len(v["seasons"][0]["matches"]) == 2
    assert v["seasons"][0]["matches"][0]["matchDate"] == "2025-10-19"
    assert v["seasons"][0]["matches"][0]["totalLine"] == 2.5
    assert v["seasons"][0]["matches"][0]["overOdds"] == 1.88
    assert v["seasons"][1]["matches"][0]["homeOdds"] == 2.88
    assert v["seasons"][1]["matches"][0]["totalLine"] is None


def test_kp02_home_away_roles_preserved():
    prev = [_m("2026-03-07", "Betis", "Levante", 1.80, 3.62, 4.90)]
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=prev,
        curr_matches=[],
    )
    row = v["seasons"][0]["matches"][0]
    assert row["homeTeam"] == "Betis"
    assert row["awayTeam"] == "Levante"
    assert row["homeOdds"] == 1.80
    assert row["awayOdds"] == 4.90


def test_kp03_one_prev_match_anomaly():
    prev = [_m("2025-10-19", "Levante", "Betis", 3.22, 3.18, 2.29)]
    curr = [_m("2026-08-24", "Levante", "Betis", 2.88, 3.26, 2.51)]
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=prev,
        curr_matches=curr,
    )
    assert h2h.MSG_PREV_ONE in v["warnings"]
    assert len(v["seasons"][0]["matches"]) == 1
    assert len(v["seasons"][1]["matches"]) == 1


def test_kp04_no_prev_but_curr():
    curr = [_m("2026-08-24", "Levante", "Betis", 2.88, 3.26, 2.51)]
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=[],
        curr_matches=curr,
    )
    assert h2h.MSG_PREV_NONE in v["warnings"]
    assert h2h.MSG_BOTH_NONE not in v["warnings"]
    assert len(v["seasons"]) == 1
    assert v["seasons"][0]["seasonLabel"] == "2026-27"


def test_kp05_both_missing():
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=[],
        curr_matches=[],
    )
    assert v["warnings"] == [h2h.MSG_BOTH_NONE]
    assert v["seasons"] == []


def test_kp06_no_curr_yet_no_warning():
    prev = [
        _m("2025-10-19", "Levante", "Betis", 3.22, 3.18, 2.29),
        _m("2026-03-07", "Betis", "Levante", 1.80, 3.62, 4.90),
    ]
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=prev,
        curr_matches=[],
    )
    assert v["warnings"] == []
    assert len(v["seasons"]) == 1
    assert v["seasons"][0]["seasonLabel"] == "2025-26"


def test_kp09_incomplete_excluded():
    prev = [
        _m("2025-10-19", "Levante", "Betis", 3.22, 3.18, 2.29),
        _m("2026-03-07", "Betis", "Levante", None, 3.62, 4.90),
    ]
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=prev,
        curr_matches=[],
    )
    assert len(v["seasons"][0]["matches"]) == 1
    assert any("Отсутствует часть данных по матчу Betis — Levante" in w for w in v["warnings"])
    assert h2h.MSG_PREV_ONE not in v["warnings"]  # КП-10: two archive rows


def test_kp10_two_prev_one_incomplete_no_ft041():
    prev = [
        _m("2025-10-19", "Levante", "Betis", 3.22, 3.18, 2.29),
        _m("2026-03-07", "Betis", "Levante", 1.80, None, 4.90),
    ]
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=prev,
        curr_matches=[],
    )
    assert h2h.MSG_PREV_ONE not in v["warnings"]
    assert len(v["seasons"][0]["matches"]) == 1


def test_kp11_multiple_incomplete_warnings():
    prev = [
        _m("2025-10-19", "Levante", "Betis", None, 3.18, 2.29),
        _m("2026-03-07", "Betis", "Levante", 1.80, 3.62, None),
    ]
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=prev,
        curr_matches=[],
    )
    assert v["seasons"] == []
    assert len([w for w in v["warnings"] if w.startswith("Отсутствует часть данных")]) == 2


def test_previous_season_from_list():
    seasons = [
        {"season_id": 22, "season_label": "2026-27"},
        {"season_id": 2, "season_label": "2025-26"},
        {"season_id": 1, "season_label": "2024-25"},
    ]
    prev = h2h.previous_season_from_list(seasons, 22)
    assert prev["season_label"] == "2025-26"
    assert h2h.previous_season_from_list(seasons, 1) is None


def test_current_season_blank_future_ignored():
    """Upcoming current-season fixture with null odds is not FT-04.5."""
    prev = [
        _m("2025-09-14", "Levante", "Betis", 3.72, 3.62, 2.07),
        _m("2026-05-23", "Betis", "Levante", 2.44, 3.46, 3.04),
    ]
    curr = [
        _m("2026-08-29", "Levante", "Betis", 3.30, 3.42, 2.31),
        _m("2027-03-14", "Betis", "Levante", None, None, None),
    ]
    v = h2h.build_h2h_view(
        prev_season_label="2025-26",
        curr_season_label="2026-27",
        prev_matches=prev,
        curr_matches=curr,
    )
    assert v["warnings"] == []
    assert len(v["seasons"]) == 2
    assert len(v["seasons"][1]["matches"]) == 1
    assert v["seasons"][1]["matches"][0]["matchDate"] == "2026-08-29"


def test_html_wiring_smoke():
    html = (ROOT / "web" / "FairOddsCalc_iOS.html").read_text(encoding="utf-8")
    assert "h2hBuildView" in html
    assert "h2hRenderBlock" in html
    assert "Очные встречи" in html
    assert "closing_total_line" in html
    assert "Т${" in html or "h2h-tot" in html
    assert h2h.MSG_PREV_ONE in html or "только на 1 матч прошлого сезона" in html
