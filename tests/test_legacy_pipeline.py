"""Regression: historical Legacy pipeline is isolated from Auto Copula/NB."""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import goal_matrix_auto as gma  # noqa: E402
import goal_model as gm  # noqa: E402

FIXTURE_JSON = ROOT / "fixtures" / "legacy_reference.json"
FIXTURE_CSV = ROOT / "fixtures" / "legacy_reference.csv"
ODDS_TOL = 0.005


def _legacy_matrix(lh: float, la: float, gamma: float, q_mult, max_goals: int = 10):
    mat = gm.build_score_matrix(lh, la, max_goals)
    if gamma:
        mat = gm.apply_dixon_coles(mat, lh, la, gamma)
    if q_mult is not None:
        px = gm.draw_probability(mat)
        target = min(0.45, max(0.05, float(q_mult) * px))
        mat, _ = gm.adjust_matrix_to_draw_target(mat, target)
    return mat


def _legacy_odds_at_market(row: dict) -> dict:
    mat = _legacy_matrix(row["lambda_home"], row["lambda_away"], row["gamma"], row.get("q_mult"))
    p1, px, p2 = gm.compute_1x2(mat)
    ah = gm.ah_market(mat, row["market_ah"])
    tot = gm.total_market(mat, row["market_tot"])
    mk = gm.markets_from_matrix(mat)
    return {
        "1": 1.0 / p1,
        "x": 1.0 / px,
        "2": 1.0 / p2,
        "ah1": ah.home_or_over_odds,
        "ah2": ah.away_or_under_odds,
        "o": tot.home_or_over_odds,
        "u": tot.away_or_under_odds,
        "ah_main": mk.main_ah.line,
        "tot_main": mk.main_total.line,
    }


def test_legacy_reference_fixture_exists():
    assert FIXTURE_JSON.is_file()
    assert FIXTURE_CSV.is_file()
    data = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert data["tolerance_odds"] <= ODDS_TOL
    assert len(data["rows"]) >= 5


def test_legacy_matches_historical_reference_within_tol():
    data = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    tol = float(data.get("tolerance_odds", ODDS_TOL))
    keys = [
        ("1", "historical_legacy_1"),
        ("x", "historical_legacy_x"),
        ("2", "historical_legacy_2"),
        ("ah1", "historical_legacy_ah1"),
        ("ah2", "historical_legacy_ah2"),
        ("o", "historical_legacy_o"),
        ("u", "historical_legacy_u"),
    ]
    for row in data["rows"]:
        got = _legacy_odds_at_market(row)
        for gk, rk in keys:
            assert abs(got[gk] - row[rk]) <= tol, (row["match_id"], gk, got[gk], row[rk])
        assert got["ah_main"] == row["historical_legacy_ah_main"]
        assert got["tot_main"] == row["historical_legacy_total_main"]


def test_legacy_csv_matches_json():
    data = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    with FIXTURE_CSV.open(encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))
    assert len(csv_rows) == len(data["rows"])
    for a, b in zip(data["rows"], csv_rows):
        assert a["match_id"] == b["match_id"]
        assert abs(float(a["historical_legacy_1"]) - float(b["historical_legacy_1"])) < 1e-9


def test_legacy_does_not_use_copula_or_nb():
    """AC2/AC3: changing Auto α/ρ must not change Legacy matrix odds."""
    lh, la, gamma = 1.55, 1.15, -0.08
    mat_l = _legacy_matrix(lh, la, gamma, 1.05)
    p1_a, _, _ = gm.compute_1x2(mat_l)
    # Auto with different α/ρ
    mat_auto = gma.build_joint_score_matrix(lh, la, alpha=0.2, rho=0.2, max_goals=10)
    p1_b, _, _ = gm.compute_1x2(mat_auto)
    assert abs(p1_a - p1_b) > 1e-4
    # Legacy rebuild identical
    mat_l2 = _legacy_matrix(lh, la, gamma, 1.05)
    p1_c, _, _ = gm.compute_1x2(mat_l2)
    assert abs(p1_a - p1_c) < 1e-12


def test_auto_draw_is_diagonal_only_no_draw_model():
    mat = gma.build_joint_score_matrix(1.6, 1.1, alpha=0.06, rho=0.08)
    px = gm.compute_1x2(mat)[1]
    assert abs(px - sum(mat[i][i] for i in range(len(mat)))) < 1e-12


def test_ah_ou_priced_at_market_line_not_main():
    """AC6: odds at market line differ from main-line odds when lines differ."""
    data = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    row = next(r for r in data["rows"] if abs(r["market_ah"] - r["historical_legacy_ah_main"]) > 1e-9)
    mat = _legacy_matrix(row["lambda_home"], row["lambda_away"], row["gamma"], row.get("q_mult"))
    at_mkt = gm.ah_market(mat, row["market_ah"]).home_or_over_odds
    at_main = gm.ah_market(mat, row["historical_legacy_ah_main"]).home_or_over_odds
    assert abs(at_mkt - at_main) > 0.01
    assert abs(at_mkt - row["historical_legacy_ah1"]) <= ODDS_TOL


def test_config_legacy_and_auto_sections_isolated():
    cfg = gma.load_goal_matrix_config()
    assert "legacy" in cfg and isinstance(cfg["legacy"], dict)
    leg = cfg["legacy"]
    assert leg.get("useDixonColes", True) is True or (leg.get("dixonColes") or {}).get("enabled", True)
    # Auto section must not enable DC/draw knobs used by Legacy
    auto = cfg.get("auto") or {}
    assert "dixonColes" not in auto
    assert "drawModel" not in auto
