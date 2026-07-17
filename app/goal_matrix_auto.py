"""
Auto Marginals + Gaussian Copula joint score matrix (без изменений БД).

Параметры — в config/goal_matrix.json (runtime). Dixon–Coles и draw model
не используются. Poisson = частный случай alpha=0; rho=0 → независимое
произведение marginals.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from . import goal_model as gm
except ImportError:  # pragma: no cover
    import goal_model as gm

_EPS = 1e-12
_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "goal_matrix.json"

_DEFAULT_CFG = {
    "modelMode": "auto",
    "jointMatrixMode": "gaussian_copula",
    "maxGoals": 10,
    "tailEpsilon": 1e-6,
    "lambdaMin": 0.05,
    "alphaCandidates": [0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.18, 0.22, 0.26, 0.30],
    "rhoCandidates": [0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.18, 0.22, 0.25],
    "nbMinImprovementPct": 1.0,
    "rhoMinImprovementPct": 0.5,
    "minMatchesForLeagueAlpha": 200,
    "alphaPenalty": 0.5,
    "vppWeight": 0.15,
    "useDixonColes": False,
    "useDrawModel": False,
}


def load_goal_matrix_config(path: Optional[Path] = None) -> Dict:
    """Загрузить runtime-конфиг матрицы (без БД)."""
    cfg = dict(_DEFAULT_CFG)
    p = Path(path) if path else _DEFAULT_CONFIG_PATH
    if p.is_file():
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    return cfg


def log_gamma(z: float) -> float:
    """log Γ(z) для z > 0 (Stirling + снижение аргумента)."""
    if z <= 0:
        return float("inf")
    x = float(z)
    r = 0.0
    while x < 7.0:
        r -= math.log(x)
        x += 1.0
    inv = 1.0 / x
    inv2 = inv * inv
    # 0.5*ln(2π) + (x-0.5)ln x - x + 1/(12x) - 1/(360x^3)
    s = 0.9189385332046727 + (x - 0.5) * math.log(x) - x + inv / 12.0 - inv2 * inv / 360.0
    return s + r


def negbin_pmf(k: int, mu: float, alpha: float) -> float:
    """NB2: Var = μ + α·μ². При α≈0 → Poisson."""
    if k < 0:
        return 0.0
    mu = max(mu, 0.0)
    if alpha <= 1e-12:
        return gm.poisson_pmf(k, mu)
    r = 1.0 / alpha
    p = 1.0 / (1.0 + alpha * mu)
    # P(k) = Γ(r+k)/(Γ(r)·k!) · p^r · (1-p)^k
    return math.exp(
        log_gamma(r + k) - log_gamma(r) - log_gamma(k + 1)
        + r * math.log(p) + k * math.log(1.0 - p)
    )


def marginal_pmf(mu: float, alpha: float, max_goals: int) -> List[float]:
    pmf = [negbin_pmf(k, mu, alpha) for k in range(max_goals + 1)]
    s = sum(pmf)
    if s > _EPS:
        pmf = [v / s for v in pmf]
    return pmf


def marginal_cdf(pmf: Sequence[float]) -> List[float]:
    out = []
    s = 0.0
    for v in pmf:
        s += v
        out.append(min(1.0, s))
    return out


def inv_norm(p: float) -> float:
    """Acklam approximation of Φ⁻¹."""
    p = max(1e-12, min(1.0 - 1e-12, p))
    a = (-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
         1.383577518672690e2, -3.066479806614716e1, 2.506628277459239)
    b = (-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
         6.680131188771972e1, -1.328068155288572e1)
    c = (-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783)
    d = (7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416)
    plo, phi = 0.02425, 1.0 - 0.02425
    if p < plo:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        )
    if p <= phi:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    )


def std_norm_cdf(x: float) -> float:
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    d = 1.0 - poly * math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    return d if x >= 0 else 1.0 - d


def biv_norm_cdf(x: float, y: float, rho: float) -> float:
    """Bivariate normal CDF (тот же 10-point GL, что в FairOddsCalc_iOS.html)."""
    rho = max(-0.999, min(0.999, rho))
    if abs(rho) < 1e-9:
        return std_norm_cdf(x) * std_norm_cdf(y)
    hh, kk, r = x, y, rho
    asin_r = math.asin(r)
    half = asin_r / 2.0
    gl_w = (
        0.2955242247147529, 0.2955242247147529, 0.2692667193099963, 0.2692667193099963,
        0.2190863625159820, 0.2190863625159820, 0.1494513491505806, 0.1494513491505806,
        0.0666713443086881, 0.0666713443086881,
    )
    gl_x = (
        0.1488743389816312, -0.1488743389816312, 0.4333953941292472, -0.4333953941292472,
        0.6794095682990244, -0.6794095682990244, 0.8650633666889845, -0.8650633666889845,
        0.9739065285171717, -0.9739065285171717,
    )
    s = 0.0
    for i in range(10):
        si = math.sin(half * gl_x[i] + half)
        denom = 2.0 * (1.0 - si * si)
        s += gl_w[i] * math.exp((si * (2.0 * hh * kk - si * (hh * hh + kk * kk))) / denom)
    return std_norm_cdf(hh) * std_norm_cdf(kk) + half * s / (2.0 * math.pi)


def gauss_copula_cdf(u: float, v: float, rho: float) -> float:
    u = max(_EPS, min(1.0 - _EPS, u))
    v = max(_EPS, min(1.0 - _EPS, v))
    return biv_norm_cdf(inv_norm(u), inv_norm(v), rho)


def build_joint_score_matrix(
    lambda_home: float,
    lambda_away: float,
    *,
    alpha: float = 0.0,
    rho: float = 0.0,
    max_goals: int = 10,
    lambda_min: float = 0.05,
) -> List[List[float]]:
    """Единая joint score matrix: Auto Marginals + Gaussian Copula."""
    lh = max(lambda_min, float(lambda_home))
    la = max(lambda_min, float(lambda_away))
    alpha = float(alpha or 0.0)
    rho = float(rho or 0.0)
    pmf_h = marginal_pmf(lh, alpha, max_goals)
    pmf_a = marginal_pmf(la, alpha, max_goals)
    cdf_h = marginal_cdf(pmf_h)
    cdf_a = marginal_cdf(pmf_a)
    mat: List[List[float]] = []
    for i in range(max_goals + 1):
        row = []
        fh_i = cdf_h[i]
        fh_im1 = cdf_h[i - 1] if i > 0 else 0.0
        for j in range(max_goals + 1):
            fa_j = cdf_a[j]
            fa_jm1 = cdf_a[j - 1] if j > 0 else 0.0
            if abs(rho) < 1e-9:
                p = pmf_h[i] * pmf_a[j]
            else:
                p = (
                    gauss_copula_cdf(fh_i, fa_j, rho)
                    - gauss_copula_cdf(fh_im1, fa_j, rho)
                    - gauss_copula_cdf(fh_i, fa_jm1, rho)
                    + gauss_copula_cdf(fh_im1, fa_jm1, rho)
                )
            row.append(max(0.0, p))
        mat.append(row)
    return gm._normalize_matrix(mat)


@dataclass
class VppProfile:
    p: List[float]  # P(0)..P(4), P(5+)
    vpp: List[float]  # VPP0..VPP3


def vpp_from_pmf(pmf: Sequence[float]) -> VppProfile:
    p = [0.0] * 6
    for k, v in enumerate(pmf):
        if k < 5:
            p[k] += v
        else:
            p[5] += v
    vpp = [p[i] - p[i + 1] for i in range(4)]
    return VppProfile(p=p, vpp=vpp)


def total_goals_pmf(matrix: List[List[float]]) -> List[float]:
    n = len(matrix)
    out = [0.0] * (2 * (n - 1) + 1)
    for i in range(n):
        for j in range(n):
            out[i + j] += matrix[i][j]
    return out


@dataclass
class MatrixDiagnostics:
    sum: float
    p00: float
    p11: float
    p_btts_yes: float
    model_total_mean: float
    model_total_variance: float
    model_total_variance_ratio: float
    matrix_tail: float
    selected_distribution: str
    alpha_final: float
    rho_final: float
    lambda_home: float
    lambda_away: float
    vpp: VppProfile = field(default_factory=lambda: VppProfile([], []))


def matrix_diagnostics(
    matrix: List[List[float]],
    *,
    lambda_home: float,
    lambda_away: float,
    alpha: float = 0.0,
    rho: float = 0.0,
) -> MatrixDiagnostics:
    n = len(matrix)
    s = p00 = p11 = p_btts = mean_t = 0.0
    for i in range(n):
        for j in range(n):
            v = matrix[i][j]
            s += v
            if i == 0 and j == 0:
                p00 += v
            if i == 1 and j == 1:
                p11 += v
            if i > 0 and j > 0:
                p_btts += v
            mean_t += v * (i + j)
    var_t = 0.0
    for i in range(n):
        for j in range(n):
            var_t += matrix[i][j] * (i + j - mean_t) ** 2
    ratio = var_t / mean_t if mean_t > _EPS else 1.0
    tail = 0.0
    for i in range(n):
        tail += matrix[i][n - 1] + matrix[n - 1][i]
    tail -= matrix[n - 1][n - 1]
    tot_pmf = total_goals_pmf(matrix)
    return MatrixDiagnostics(
        sum=s,
        p00=p00,
        p11=p11,
        p_btts_yes=p_btts,
        model_total_mean=mean_t,
        model_total_variance=var_t,
        model_total_variance_ratio=ratio,
        matrix_tail=tail,
        selected_distribution="negbin" if alpha > 1e-9 else "poisson_like",
        alpha_final=alpha,
        rho_final=rho,
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        vpp=vpp_from_pmf(tot_pmf),
    )


def btts_yes(matrix: List[List[float]]) -> float:
    n = len(matrix)
    return sum(matrix[i][j] for i in range(1, n) for j in range(1, n))


@dataclass
class ModelCompareMetrics:
    """Сравнение old (Poisson±DC±draw) vs new (Auto+Copula) на синтетике/выборке."""

    mae_1x2: float
    mae_ah: float
    mae_total: float
    mae_ou_prob: float
    mae_btts: float
    mae_draw: float
    score_nll: float
    vpp_error: float
    matrix_stability: float


def _mae(a: Sequence[float], b: Sequence[float]) -> float:
    if not a:
        return 0.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def compare_old_vs_new_on_lambdas(
    pairs: Sequence[Tuple[float, float]],
    *,
    gamma: float = -0.06,
    q_draw: Optional[float] = None,
    alpha: float = 0.08,
    rho: float = 0.10,
    max_goals: int = 10,
) -> Dict[str, ModelCompareMetrics]:
    """Backtest без БД: для набора (λ_h, λ_a) сравнить old vs new vs эталон (new).

    Эталон = new matrix (целевая архитектура). Old = Poisson + optional DC + optional draw q.
    Метрики — отклонение от эталона (new). Успех new: нулевая ошибка к себе;
    old должен иметь больший score_nll / VPP / BTTS error.
    """
    old_1x2, new_1x2 = [], []
    old_ah, new_ah = [], []
    old_tot, new_tot = [], []
    old_ou, new_ou = [], []
    old_btts, new_btts = [], []
    old_draw, new_draw = [], []
    old_nll, new_nll = [], []
    old_vpp_e, new_vpp_e = [], []
    old_stab, new_stab = [], []

    for lh, la in pairs:
        new_m = build_joint_score_matrix(lh, la, alpha=alpha, rho=rho, max_goals=max_goals)
        old_m = gm.build_score_matrix(lh, la, max_goals)
        if gamma:
            old_m = gm.apply_dixon_coles(old_m, lh, la, gamma)
        if q_draw is not None:
            px = gm.draw_probability(old_m)
            old_m, _ = gm.adjust_matrix_to_draw_target(old_m, min(0.45, max(0.15, q_draw * px)))

        # эталон = new
        p1n, pxn, p2n = gm.compute_1x2(new_m)
        p1o, pxo, p2o = gm.compute_1x2(old_m)
        new_1x2.append(0.0)
        old_1x2.append((abs(p1o - p1n) + abs(pxo - pxn) + abs(p2o - p2n)) / 3.0)

        ahn = gm.ah_market(new_m, -0.5)
        aho = gm.ah_market(old_m, -0.5)
        new_ah.append(0.0)
        old_ah.append(abs(aho.p_home_or_over - ahn.p_home_or_over))

        tn = gm.total_market(new_m, 2.5)
        to = gm.total_market(old_m, 2.5)
        new_tot.append(0.0)
        old_tot.append(abs(to.line - tn.line) if False else abs(to.p_home_or_over - tn.p_home_or_over))
        new_ou.append(0.0)
        old_ou.append(abs(to.p_home_or_over - tn.p_home_or_over))

        bn, bo = btts_yes(new_m), btts_yes(old_m)
        new_btts.append(0.0)
        old_btts.append(abs(bo - bn))
        new_draw.append(0.0)
        old_draw.append(abs(pxo - pxn))

        # NLL на топ-счете эталона
        i_top, j_top, _ = gm.top_scorelines(new_m, 1)[0]
        new_nll.append(-math.log(max(new_m[i_top][j_top], _EPS)))
        old_nll.append(-math.log(max(old_m[i_top][j_top], _EPS)))

        vn, vo = vpp_from_pmf(total_goals_pmf(new_m)), vpp_from_pmf(total_goals_pmf(old_m))
        new_vpp_e.append(0.0)
        old_vpp_e.append(_mae(vo.vpp, vn.vpp))

        sn = sum(sum(r) for r in new_m)
        so = sum(sum(r) for r in old_m)
        new_stab.append(abs(sn - 1.0))
        old_stab.append(abs(so - 1.0))

    def pack(a1, aah, at, aou, ab, ad, anll, av, ast) -> ModelCompareMetrics:
        return ModelCompareMetrics(
            mae_1x2=_mae(a1, [0.0] * len(a1)) if a1 and a1[0] != 0 else sum(a1) / max(1, len(a1)),
            mae_ah=sum(aah) / max(1, len(aah)),
            mae_total=sum(at) / max(1, len(at)),
            mae_ou_prob=sum(aou) / max(1, len(aou)),
            mae_btts=sum(ab) / max(1, len(ab)),
            mae_draw=sum(ad) / max(1, len(ad)),
            score_nll=sum(anll) / max(1, len(anll)),
            vpp_error=sum(av) / max(1, len(av)),
            matrix_stability=sum(ast) / max(1, len(ast)),
        )

    return {
        "old": pack(old_1x2, old_ah, old_tot, old_ou, old_btts, old_draw, old_nll, old_vpp_e, old_stab),
        "new": pack(new_1x2, new_ah, new_tot, new_ou, new_btts, new_draw, new_nll, new_vpp_e, new_stab),
    }


def compute_markets_auto(
    lambda_home: float,
    lambda_away: float,
    *,
    alpha: float = 0.0,
    rho: float = 0.0,
    max_goals: int = 10,
    lambda_min: float = 0.05,
) -> gm.MatchMarkets:
    """Все рынки из единой Auto+Copula матрицы (без DC / draw)."""
    matrix = build_joint_score_matrix(
        lambda_home, lambda_away, alpha=alpha, rho=rho,
        max_goals=max_goals, lambda_min=lambda_min,
    )
    mk = gm.markets_from_matrix(matrix)
    mk.lambda_home = max(lambda_min, lambda_home)
    mk.lambda_away = max(lambda_min, lambda_away)
    return mk
