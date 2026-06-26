"""History API endpoint."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from fastapi.testclient import TestClient  # noqa: E402

import history_api  # noqa: E402
import userbet_odds as ubo  # noqa: E402


def test_health():
    client = TestClient(history_api.app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_fetch_odds_requires_body():
    client = TestClient(history_api.app)
    r = client.post("/api/history/fetch-odds", json={})
    assert r.status_code == 422


def test_fetch_odds_returns_parsed_odds():
    sample = {
        "home_odds": 1.56,
        "draw_odds": 4.78,
        "away_odds": 5.58,
        "closing_total_line": 3.5,
        "over_odds": 2.06,
        "under_odds": 1.85,
        "closing_ah_home": -1.0,
        "ah_home_odds": 1.87,
        "ah_away_odds": 2.06,
    }
    client = TestClient(history_api.app)
    with patch.object(ubo, "fetch_odds", return_value=sample):
        r = client.post("/api/history/fetch-odds", json={"id_fixture": "1611253099"})
    assert r.status_code == 200
    assert r.json()["odds"]["home_odds"] == 1.56


def test_fetch_odds_maps_userbet_error():
    client = TestClient(history_api.app)
    with patch.object(ubo, "fetch_odds", side_effect=ubo.UserbetError("Введите id матча с сайта неизвестного мужика")):
        r = client.post("/api/history/fetch-odds", json={"id_fixture": "x"})
    assert r.status_code == 400
    assert "Введите id" in r.json()["detail"]
