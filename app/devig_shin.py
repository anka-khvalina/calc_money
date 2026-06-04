"""
Снятие маржи Shin для линии 1X2 (три исхода).

Вход: десятичные коэффициенты k1, kx, k2 > 1.
Выход: (p1, px, p2), сумма = 1.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple


def shin_devig(
    odds_1: float, odds_x: float, odds_2: float
) -> Tuple[float, float, float]:
    """Честные вероятности по модели Shin."""
    odds = (odds_1, odds_x, odds_2)
    for k in odds:
        if k is None or not math.isfinite(k) or k <= 1.0:
            raise ValueError(f"Коэффициент должен быть > 1, получено: {k}")

    implied = [1.0 / k for k in odds]
    z = _solve_shin_z(implied)
    probs = [_shin_prob(pi, z) for pi in implied]
    total = sum(probs)
    if total <= 0:
        raise ValueError("Shin: нулевая сумма вероятностей")
    return tuple(p / total for p in probs)


def _shin_prob(pi: float, z: float) -> float:
    if z >= 1.0:
        return 0.0
    return (math.sqrt(z * z + 4.0 * (1.0 - z) * pi * pi) - z) / (2.0 * (1.0 - z))


def _solve_shin_z(implied: Sequence[float]) -> float:
    """Найти z ∈ [0, 1) такое, что Σ p_i(z) = 1."""

    def excess(z: float) -> float:
        return sum(_shin_prob(p, z) for p in implied) - 1.0

    lo, hi = 0.0, 0.999999
    if excess(lo) < 0:
        return 0.0
    if excess(hi) > 0:
        return hi

    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if excess(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
