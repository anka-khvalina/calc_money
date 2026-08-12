"""Metrics for early-season dynamics offline eval."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from devig_shin import shin_devig


def mae(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(abs(x) for x in xs) / len(xs)


def summarize_preds(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    ah = [float(r["ah_pred"]) - float(r["ah_mkt"]) for r in rows]
    tot = [float(r["tot_pred"]) - float(r["tot_mkt"]) for r in rows]
    abs_ah = [abs(x) for x in ah]
    abs_tot = [abs(x) for x in tot]
    out: Dict[str, Any] = {
        "n": len(rows),
        "mae_AH": mae(ah),
        "mae_Tot": mae(tot),
        "n_ah_ge_0_5": sum(1 for e in abs_ah if e >= 0.5 - 1e-12),
        "n_ah_ge_0_75": sum(1 for e in abs_ah if e >= 0.75 - 1e-12),
        "n_tot_ge_0_5": sum(1 for e in abs_tot if e >= 0.5 - 1e-12),
        "bias_AH": sum(ah) / len(ah),
        "bias_Tot": sum(tot) / len(tot),
    }
    # 1X2 monitor (Shin market vs model probs)
    d1, dx, d2 = [], [], []
    for r in rows:
        if r.get("m1") is None:
            continue
        d1.append(float(r["p1"]) - float(r["m1"]))
        dx.append(float(r["px"]) - float(r["mx"]))
        d2.append(float(r["p2"]) - float(r["m2"]))
    if d1:
        out["mae_P1"] = mae(d1)
        out["mae_PX"] = mae(dx)
        out["mae_P2"] = mae(d2)
        out["n_1x2"] = len(d1)
    return out


def market_1x2_probs(home: Optional[float], draw: Optional[float], away: Optional[float]):
    if home is None or draw is None or away is None:
        return None
    if min(home, draw, away) <= 1.0:
        return None
    try:
        return shin_devig(float(home), float(draw), float(away))
    except Exception:
        return None
