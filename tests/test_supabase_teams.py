"""Supabase REST-клиент справочника команд."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import supabase_teams as sb  # noqa: E402


def _mock_urlopen(response_body: bytes, *, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = response_body
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    if status >= 400:
        import urllib.error

        err = urllib.error.HTTPError(
            url="http://test",
            code=status,
            msg="error",
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=response_body)),
        )
        raise err
    return resp


def test_fetch_leagues_parses_rows():
    payload = json.dumps(
        [
            {"id": "uuid-1", "name": "Premier League"},
            {"id": "uuid-2", "name": "La Liga"},
        ]
    ).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
        leagues = sb.fetch_leagues()
    assert len(leagues) == 2
    assert leagues[0] == sb.League(id="uuid-1", name="Premier League")


def test_fetch_teams_filters_by_league():
    payload = json.dumps(
        [
            {"id": 1, "leagues_id": "uuid-1", "name_team": "Arsenal"},
            {"id": 2, "leagues_id": "uuid-1", "name_team": "Chelsea"},
        ]
    ).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)) as opener:
        teams = sb.fetch_teams("uuid-1")
    req = opener.call_args[0][0]
    url = getattr(req, "full_url", None) or req.get_full_url()
    assert "leagues_id=eq.uuid-1" in url
    assert teams[0].name_team == "Arsenal"
    assert teams[0].id_str == "1"


def test_create_team_posts_without_client_id():
    get_payload = json.dumps([]).encode()
    post_payload = json.dumps(
        [{"id": 99, "leagues_id": "uuid-1", "name_team": "New Team"}]
    ).encode()

    def side_effect(req, timeout=30.0):
        if req.method == "GET":
            return _mock_urlopen(get_payload)
        assert req.method == "POST"
        body = json.loads(req.data.decode())
        assert body == {"leagues_id": "uuid-1", "name_team": "New Team"}
        assert "id" not in body
        assert req.headers.get("Prefer") == "return=representation"
        return _mock_urlopen(post_payload)

    with patch("urllib.request.urlopen", side_effect=side_effect):
        team = sb.create_team("uuid-1", "New Team")
    assert team.id == 99
    assert team.name_team == "New Team"


def test_create_team_rejects_duplicate_name():
    existing = json.dumps(
        [{"id": 1, "leagues_id": "uuid-1", "name_team": "Arsenal"}]
    ).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(existing)):
        try:
            sb.create_team("uuid-1", "arsenal")
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "уже есть" in str(exc)


def test_create_team_validation():
    try:
        sb.create_team("", "Team")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Выберите лигу" in str(exc)
    try:
        sb.create_team("uuid-1", "   ")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Введите название" in str(exc)
