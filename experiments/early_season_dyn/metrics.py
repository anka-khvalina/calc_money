"""Metrics for early-season dynamics offline eval.

AH / Total primary metrics compare **lines** (no book margin).

1X2 monitor always accounts for market margin in one of two ways:
  - probabilities: Shin-devig market odds → compare to model fair probs
  - odds: apply the **same match overround** to model fair probs
    (multiplicative), then MAE vs quoted market 1/X/2
Never compare raw fair model odds to margined market quotes.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

from devig_shin import shin_devig


def mae(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(abs(x) for x in xs) / len(xs)


def market_overround(o1: float, ox: float, o2: float) -> float:
    return 1.0 / o1 + 1.0 / ox + 1.0 / o2


def model_odds_with_market_margin(
    p1: float, px: float, p2: float, o1: float, ox: float, o2: float
) -> Tuple[float, float, float, float]:
    """Scale fair probs to the match's market overround → decimal odds."""
    over = market_overround(o1, ox, o2)
    return 1.0 / (p1 * over), 1.0 / (px * over), 1.0 / (p2 * over), over


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
    # 1X2 monitor A: Shin fair probs vs model probs
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

    # 1X2 monitor B: margined model odds vs market quotes
    odd_rows = []
    for r in rows:
        if r.get("d1_odds") is not None:
            odd_rows.append(r)
            continue
        o1, ox, o2 = r.get("o1"), r.get("ox"), r.get("o2")
        if o1 is None or ox is None or o2 is None:
            continue
        try:
            o1, ox, o2 = float(o1), float(ox), float(o2)
            b1, bx, b2, _ = model_odds_with_market_margin(
                float(r["p1"]), float(r["px"]), float(r["p2"]), o1, ox, o2
            )
            odd_rows.append({
                **r,
                "d1_odds": b1 - o1,
                "dx_odds": bx - ox,
                "d2_odds": b2 - o2,
                "o1": o1, "ox": ox, "o2": o2,
            })
        except Exception:
            continue
    if odd_rows:
        out["mae_odds_1"] = mae([float(r["d1_odds"]) for r in odd_rows])
        out["mae_odds_X"] = mae([float(r["dx_odds"]) for r in odd_rows])
        out["mae_odds_2"] = mae([float(r["d2_odds"]) for r in odd_rows])
        out["bias_odds_1"] = sum(float(r["d1_odds"]) for r in odd_rows) / len(odd_rows)
        out["bias_odds_X"] = sum(float(r["dx_odds"]) for r in odd_rows) / len(odd_rows)
        out["bias_odds_2"] = sum(float(r["d2_odds"]) for r in odd_rows) / len(odd_rows)
        fav = []
        for r in odd_rows:
            if float(r["o1"]) <= float(r["o2"]):
                fav.append(abs(float(r["d1_odds"])))
            else:
                fav.append(abs(float(r["d2_odds"])))
        out["mae_odds_fav"] = mae(fav)
        out["n_odds"] = len(odd_rows)
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
