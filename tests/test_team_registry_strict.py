"""Справочник команд — строгая валидация."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import history_store as hs  # noqa: E402
import team_registry as tg  # noqa: E402


def test_validate_rejects_unknown_team(tmp_path):
    reg = tmp_path / "registry.json"
    tg.add_team("epl", "Arsenal", path=reg)
    try:
        tg.validate_teams_registered("epl", ["Arsenal", "Unknown FC"], path=reg)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Unknown FC" in str(exc)
        assert "не знает" in str(exc)


def test_import_requires_registry(tmp_path, monkeypatch=None):
    reg = tmp_path / "registry.json"
    if monkeypatch is not None:
        monkeypatch.setattr(tg, "registry_path", lambda: reg)
    else:
        orig = tg.registry_path
        tg.registry_path = lambda: reg  # type: ignore[method-assign]
    try:
        tg.add_team("epl", "Arsenal", path=reg)
        tg.add_team("epl", "Chelsea", path=reg)
        matches = [hs.HistoricalMatch("Arsenal", "Chelsea", 1.9, 3.5, 4.0)]
        hs.import_season_matches("epl", "2099-00", matches)
        try:
            hs.import_season_matches(
                "epl",
                "2099-01",
                [hs.HistoricalMatch("Arsenal", "Tottenham", 2.0, 3.2, 3.5)],
            )
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "Tottenham" in str(exc)
    finally:
        if monkeypatch is None:
            tg.registry_path = orig  # type: ignore[method-assign]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        test_validate_rejects_unknown_team(base)
    with tempfile.TemporaryDirectory() as td:
        test_import_requires_registry(Path(td))
    print("OK")
