"""Плавный пол px(d) для фаворитов с большим |D|."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from devig_shin import shin_devig  # noqa: E402
import history_store as hs  # noqa: E402
from history_store import DrawModel, draw_px_lower_bound, forecast_draw_px  # noqa: E402
from match_shin_calc import _calc_league_ranking, CalculationSource, calculate_shin_match  # noqa: E402


def test_draw_px_lower_bound_rises_with_d():
    assert draw_px_lower_bound(50) == 0.06
    assert 0.12 < draw_px_lower_bound(272) < 0.14
    assert draw_px_lower_bound(400) == 0.15


def test_forecast_px_not_six_percent_at_high_d():
    dm = DrawModel(a=0.4255, b=-0.0015, n=55, source="results")
    px, lines = forecast_draw_px(dm, 287.5, s1=3.16, s2=0.316)
    assert px >= 0.12
    assert px <= 0.20
    assert any("px_floor" in ln for ln in lines)


def test_arsenal_leeds_closer_to_market():
    csv_path = ROOT / "docs" / "examples" / "history_epl_2025_26.csv"
    try:
        hs.import_season("epl", "2025-26", csv_path)
    except ValueError:
        pass
    matches = hs.load_season("epl", "2025-26")
    league = _calc_league_ranking(
        "Arsenal", "Leeds", matches, "epl", "2025-26", CalculationSource.LEAGUE_MATCHES
    )
    p1m, pxm, p2m = shin_devig(1.34, 5.50, 9.90)
    assert league.px >= 0.12
    assert abs(league.p1 - p1m) < 0.08
    assert abs(league.p2 - p2m) < 0.08


if __name__ == "__main__":
    test_draw_px_lower_bound_rises_with_d()
    test_forecast_px_not_six_percent_at_high_d()
    test_arsenal_leeds_closer_to_market()
    print("OK")
