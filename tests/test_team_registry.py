"""Справочник команд."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import history_store as hs  # noqa: E402
import team_registry as tg  # noqa: E402
from match_shin_calc import calculate_shin_match  # noqa: E402


def test_add_and_list_teams(tmp_path):
    reg = tmp_path / "registry.json"
    a = tg.add_team("epl", "Arsenal", path=reg)
    b = tg.add_team("epl", "Chelsea", path=reg)
    assert a.id == "epl:1"
    assert b.id == "epl:2"
    teams = tg.list_teams("epl", path=reg)
    assert [t.name for t in teams] == ["Arsenal", "Chelsea"]
    assert tg.get_team("epl:1", path=reg).name == "Arsenal"


def test_resolve_team_id_by_name_and_id(tmp_path):
    reg = tmp_path / "registry.json"
    ent = tg.add_team("epl", "Leeds", path=reg)
    assert tg.resolve_team_id("epl", ent.id, path=reg) == ent.id
    assert tg.resolve_team_id("epl", "Leeds", path=reg) == ent.id
    try:
        tg.resolve_team_id("epl", "Unknown FC", path=reg)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_sync_from_matches(tmp_path):
    reg = tmp_path / "registry.json"
    matches = [
        hs.HistoricalMatch("Arsenal", "Chelsea", 1.9, 3.5, 4.0),
        hs.HistoricalMatch("Leeds", "Everton", 2.4, 3.2, 3.1),
    ]
    added = tg.sync_from_matches(matches, path=reg)
    assert added == 4
    names = {t.name for t in tg.list_teams("epl", path=reg)}
    assert names == {"Arsenal", "Chelsea", "Leeds", "Everton"}


def test_team_logo_save_and_remove(tmp_path):
    reg = tmp_path / "registry.json"
    logos = tmp_path / "logos"
    ent = tg.add_team("epl", "Arsenal", path=reg)
    png = tmp_path / "logo.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    assert not tg.has_logo(ent.id, path=logos)
    saved = tg.set_team_logo(ent.id, png, path=logos)
    assert saved.is_file()
    assert tg.has_logo(ent.id, path=logos)
    assert tg.logo_path(ent.id, path=logos) == logos / "epl_1.png"
    assert tg.remove_team_logo(ent.id, path=logos)
    assert not tg.has_logo(ent.id, path=logos)


def test_new_team_shin_calc(tmp_path, monkeypatch):
    reg = tmp_path / "registry.json"
    monkeypatch.setattr(tg, "registry_path", lambda: reg)
    monkeypatch.setattr(tg, "DEFAULT_REGISTRY_PATH", reg)

    csv_path = ROOT / "docs" / "examples" / "history_epl_2025_26.csv"
    raw = csv_path.read_text(encoding="utf-8-sig")
    matches, _ = hs.parse_history_text_with_stats(raw)
    tg.sync_from_matches(matches, path=reg)
    hs.import_season_matches("epl", "2025-26", matches)

    new_ent = tg.add_team("epl", "Promoted FC", path=reg)
    arsenal_id = tg.resolve_team_id("epl", "Arsenal", path=reg)

    res = calculate_shin_match(arsenal_id, new_ent.id, "epl", "2025-26")
    assert res.team2_new is True
    assert res.team2_id == new_ent.id
    assert abs(res.p1 + res.px + res.p2 - 1.0) < 1e-5
    assert res.p1 > res.p2


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        test_add_and_list_teams(base)
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        test_resolve_team_id_by_name_and_id(base)
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        test_sync_from_matches(base)
    print("unit OK")
