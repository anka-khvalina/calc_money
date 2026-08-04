"""Simple metrics for Attack/Defence experiment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from .model import Prediction


@dataclass(frozen=True)
class Metrics:
    n: int
    mae_D: float
    mae_S: float
    mae_lambda_home: float
    mae_lambda_away: float
    bias_D: float
    bias_S: float


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def compute_metrics(preds: Sequence[Prediction]) -> Metrics:
    d_err: List[float] = []
    s_err: List[float] = []
    d_signed: List[float] = []
    s_signed: List[float] = []
    lh_err: List[float] = []
    la_err: List[float] = []
    for p in preds:
        act_d = p.match.home_goals - p.match.away_goals
        act_s = p.match.home_goals + p.match.away_goals
        d_err.append(abs(p.D - act_d))
        s_err.append(abs(p.S - act_s))
        d_signed.append(p.D - act_d)
        s_signed.append(p.S - act_s)
        lh_err.append(abs(p.lambda_home - p.match.home_goals))
        la_err.append(abs(p.lambda_away - p.match.away_goals))
    return Metrics(
        n=len(preds),
        mae_D=_mean(d_err),
        mae_S=_mean(s_err),
        mae_lambda_home=_mean(lh_err),
        mae_lambda_away=_mean(la_err),
        bias_D=_mean(d_signed),
        bias_S=_mean(s_signed),
    )
