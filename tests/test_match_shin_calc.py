"""Расчёт матча по методу Shin."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from devig_shin import shin_devig  # noqa: E402
from match_shin_calc import (  # noqa: E402
    CalculationSource,
    ShinCalculationError,
    calculate_shin_match,
    count_team_matches,
    find_common_opponents,
    previous_season,
)
import history_store as hs  # noqa: E402


def test_shin_devig_sums_to_one():
    p1, px, p2 = shin_devig(2.1, 3.4, 3.6)
    assert abs(p1 + px + p2 - 1.0) < 1e-6
    assert all(p > 0 for p in (p1, px, p2))


def test_epl_arsenal_chelsea():
    res = calculate_shin_match("Arsenal", "Chelsea", "epl", "2025-26")
    assert abs(res.p1 + res.px + res.p2 - 1.0) < 1e-5
    assert res.k1 > 1 and res.kx > 1 and res.k2 > 1
    assert " / " in res.format_odds()
    assert res.season_used == "2025-26"
    assert res.matches_used > 0


def test_previous_season_none_for_first():
    assert previous_season("epl", "2025-26") is None


def test_count_team_matches_unique():
    m = hs.HistoricalMatch("A", "B", 2.0, 3.2, 3.5)
    assert count_team_matches("A", "B", [m]) == 1


if __name__ == "__main__":
    test_shin_devig_sums_to_one()
    test_epl_arsenal_chelsea()
    test_previous_season_none_for_first()
    test_count_team_matches_unique()
    print("OK")
