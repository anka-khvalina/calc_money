"""Auto Marginals + Gaussian Copula: AC без изменений БД."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import goal_model as gm  # noqa: E402
import goal_matrix_auto as gma  # noqa: E402


def test_config_loads_without_db():
    cfg = gma.load_goal_matrix_config()
    assert cfg["modelMode"] == "auto"
    assert cfg["jointMatrixMode"] == "gaussian_copula"
    assert 0 in cfg["alphaCandidates"]
    assert cfg["nbMinImprovementPct"] >= 1
    assert cfg["minMatchesForLeagueAlpha"] == 200
    assert cfg["minMatchesForLeagueRho"] == 100
    assert cfg["minMatchesForLeagueRho"] < cfg["minMatchesForLeagueAlpha"]
    assert "legacy" in cfg and isinstance(cfg["legacy"], dict)
    assert "compare" in cfg


def test_matrix_param_gates_split_thresholds():
    # One extra match must not flip both α and ρ together.
    allow_a, allow_r = gma.matrix_param_gates(199)
    assert allow_a is False
    assert allow_r is True
    allow_a, allow_r = gma.matrix_param_gates(200)
    assert allow_a is True
    assert allow_r is True
    allow_a, allow_r = gma.matrix_param_gates(99)
    assert allow_a is False
    assert allow_r is False
    allow_a, allow_r = gma.matrix_param_gates(100)
    assert allow_a is False
    assert allow_r is True


def test_alpha0_marginals_match_poisson():
    lh = 1.4
    pmf = gma.marginal_pmf(lh, 0.0, 10)
    s = sum(gm.poisson_pmf(k, lh) for k in range(11))
    for k in range(11):
        assert abs(pmf[k] - gm.poisson_pmf(k, lh) / s) < 1e-10


def test_rho0_equals_independent_product():
    lh, la, alpha = 1.4, 1.1, 0.12
    mat = gma.build_joint_score_matrix(lh, la, alpha=alpha, rho=0.0, max_goals=8)
    pmf_h = gma.marginal_pmf(lh, alpha, 8)
    pmf_a = gma.marginal_pmf(la, alpha, 8)
    indep = [[pmf_h[i] * pmf_a[j] for j in range(9)] for i in range(9)]
    total = sum(sum(r) for r in indep)
    for i in range(9):
        for j in range(9):
            assert abs(mat[i][j] - indep[i][j] / total) < 1e-9


def test_matrix_normalized_and_1x2_sums():
    mat = gma.build_joint_score_matrix(1.7, 1.0, alpha=0.06, rho=0.08, max_goals=10)
    s = sum(sum(r) for r in mat)
    assert abs(s - 1.0) < 1e-9
    assert all(v >= -1e-15 for row in mat for v in row)
    p1, px, p2 = gm.compute_1x2(mat)
    assert abs(p1 + px + p2 - 1.0) < 1e-9
    assert abs(px - gm.draw_probability(mat)) < 1e-12


def test_draw_is_diagonal_only():
    mat = gma.build_joint_score_matrix(1.5, 1.2, alpha=0.0, rho=0.1)
    px = gm.compute_1x2(mat)[1]
    assert abs(px - sum(mat[i][i] for i in range(len(mat)))) < 1e-12


def test_markets_from_one_matrix():
    mk = gma.compute_markets_auto(1.6, 1.1, alpha=0.04, rho=0.05)
    assert mk.p1 > 0 and mk.px > 0 and mk.p2 > 0
    assert abs(mk.p1 + mk.px + mk.p2 - 1.0) < 1e-9
    assert mk.main_total.line > 0
    assert mk.main_ah.home_or_over_odds > 1.0
    assert mk.top_scores


def test_matrix_diagnostics_variance_ratio():
    mat = gma.build_joint_score_matrix(1.4, 1.1, alpha=0.0, rho=0.0)
    d = gma.matrix_diagnostics(mat, lambda_home=1.4, lambda_away=1.1, alpha=0.0, rho=0.0)
    assert abs(d.sum - 1.0) < 1e-9
    assert d.selected_distribution == "poisson_like"
    assert abs(d.model_total_variance_ratio - 1.0) < 0.05
    mat_nb = gma.build_joint_score_matrix(1.4, 1.1, alpha=0.2, rho=0.0)
    d2 = gma.matrix_diagnostics(mat_nb, lambda_home=1.4, lambda_away=1.1, alpha=0.2)
    assert d2.selected_distribution == "negbin"
    assert d2.model_total_variance_ratio > d.model_total_variance_ratio


def test_vpp_profile_shape():
    pmf = gma.marginal_pmf(2.5, 0.0, 12)
    v = gma.vpp_from_pmf(pmf)
    assert len(v.p) == 6 and len(v.vpp) == 4
    assert abs(sum(v.p) - 1.0) < 1e-9


def test_backtest_old_vs_new_runs():
    pairs = [(1.5, 1.2), (2.0, 0.9), (1.1, 1.1), (1.8, 1.4)]
    out = gma.compare_old_vs_new_on_lambdas(pairs, gamma=-0.06, q_draw=1.05, alpha=0.08, rho=0.1)
    assert "old" in out and "new" in out
    # new is эталон → нулевые MAE к себе
    assert out["new"].mae_1x2 == 0.0
    assert out["new"].vpp_error == 0.0
    # old обычно отличается от new
    assert out["old"].mae_1x2 >= 0.0
    assert out["old"].matrix_stability < 1e-9


def test_alpha0_joint_matches_classic_poisson_matrix():
    lh, la = 1.35, 1.05
    classic = gm.build_score_matrix(lh, la, max_goals=10)
    joint = gma.build_joint_score_matrix(lh, la, alpha=0.0, rho=0.0, max_goals=10)
    for i in range(11):
        for j in range(11):
            assert abs(classic[i][j] - joint[i][j]) < 1e-9
