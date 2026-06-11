"""Логит-модель ничьей: px = σ(α + β·X + γ·X²), X = |EffectiveD|."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import history_store as hs  # noqa: E402
from history_store import DrawModel, _default_draw_model, _sigmoid  # noqa: E402
from match_shin_calc import (  # noqa: E402
    CalculationSource,
    VENUE_AWAY,
    VENUE_HOME,
    VENUE_NEUTRAL,
    _calc_league_ranking,
)


def test_default_model_sane():
    dm = _default_draw_model()
    assert dm.source == "default"
    # px(0) ≈ 29 %, падает с ростом X
    assert 0.27 < dm.px(0) < 0.31
    assert dm.px(150) < dm.px(0)
    assert dm.px(300) < dm.px(150)
    assert dm.lo <= dm.px(1000) <= dm.hi


def test_forecast_px_logit_lines():
    dm = DrawModel(alpha=-0.85, beta=-0.002, gamma=0.0, x_max=400, n=100, source="market")
    px, lines = dm.forecast_px(120.0)
    expected = _sigmoid(-0.85 - 0.002 * 120)
    assert abs(px - expected) < 1e-9
    assert any("Логит-модель" in ln for ln in lines)
    assert any("X = |EffectiveD|" in ln for ln in lines)


def test_x_clamped_to_x_max():
    # γ > 0: без клампа квадратика разворачивает px вверх за пределами данных
    dm = DrawModel(alpha=-0.9, beta=-0.004, gamma=0.00001, x_max=300, n=50, source="market")
    assert dm.px(1000) == dm.px(300)


def test_calibrate_returns_market_model_on_history():
    csv_path = ROOT / "docs" / "examples" / "history_epl_2025_26.csv"
    try:
        hs.import_season("epl", "2025-26", csv_path)
    except ValueError:
        pass
    dm = hs.calibrate_draw_model("epl")
    assert dm.source == "market"
    assert dm.n >= 5
    # При X = 0 (равные команды) ничья в разумном диапазоне
    assert 0.10 <= dm.px(0) <= 0.45


def test_venue_changes_probabilities():
    csv_path = ROOT / "docs" / "examples" / "history_epl_2025_26.csv"
    try:
        hs.import_season("epl", "2025-26", csv_path)
    except ValueError:
        pass
    matches = hs.load_season("epl", "2025-26")
    home = _calc_league_ranking(
        "Arsenal", "Leeds", matches, "epl", "2025-26",
        CalculationSource.LEAGUE_MATCHES, venue=VENUE_HOME,
    )
    neutral = _calc_league_ranking(
        "Arsenal", "Leeds", matches, "epl", "2025-26",
        CalculationSource.LEAGUE_MATCHES, venue=VENUE_NEUTRAL,
    )
    away = _calc_league_ranking(
        "Arsenal", "Leeds", matches, "epl", "2025-26",
        CalculationSource.LEAGUE_MATCHES, venue=VENUE_AWAY,
    )
    for res in (home, neutral, away):
        assert abs(res.p1 + res.px + res.p2 - 1.0) < 1e-9
    # Дома p1 выше, чем на нейтрали, и тем более чем в гостях
    assert home.p1 > neutral.p1 > away.p1
    assert home.d_market > neutral.d_market > away.d_market


if __name__ == "__main__":
    test_default_model_sane()
    test_forecast_px_logit_lines()
    test_x_clamped_to_x_max()
    test_calibrate_returns_market_model_on_history()
    test_venue_changes_probabilities()
    print("OK")
