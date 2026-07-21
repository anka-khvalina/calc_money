"""Auto Pipeline 1X2 quality metrics and acceptance gate.

Pure helpers (no Legacy / DC / draw-model). Used by unit tests and docs;
the live calibration runs in the web Auto train path (gmTrain).
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


DEFAULT_AUTO_1X2_CALIB: Dict[str, Any] = {
    "enabled": True,
    "recalibrateSdWithMatrix": True,
    "refineAlphaRho": True,
    "w1x2": 1.0,
    "wAh": 0.35,
    "wOu": 0.25,
    "drawLoss": 2.0,
    "ahOuDegradeMaxPct": 5.0,
    "require1x2Improve": True,
    "requireBiasReduce": True,
}


def auto_1x2_calib_from_cfg(cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Read auto1x2Calib from model_config (top-level or auto.*)."""
    out = dict(DEFAULT_AUTO_1X2_CALIB)
    if not cfg:
        return out
    block = None
    if isinstance(cfg.get("auto1x2Calib"), Mapping):
        block = cfg["auto1x2Calib"]
    else:
        auto = cfg.get("auto")
        if isinstance(auto, Mapping) and isinstance(auto.get("auto1x2Calib"), Mapping):
            block = auto["auto1x2Calib"]
        elif isinstance(auto, Mapping) and isinstance(auto.get("oneXtwoCalib"), Mapping):
            block = auto["oneXtwoCalib"]
    if not block:
        return out
    for key, default in DEFAULT_AUTO_1X2_CALIB.items():
        if key not in block or block[key] is None:
            continue
        val = block[key]
        if isinstance(default, bool):
            out[key] = bool(val)
        elif isinstance(default, (int, float)):
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                pass
        else:
            out[key] = val
    return out


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None  # noqa: PLR0124


def systematic_bias_score(metrics: Mapping[str, Any]) -> Optional[float]:
    """Sum of |mean err| for P1, PX, P2 — lower is better."""
    b1 = _f(metrics.get("biasP1"))
    bx = _f(metrics.get("biasPX"))
    b2 = _f(metrics.get("biasP2"))
    if b1 is None or bx is None or b2 is None:
        return None
    return abs(b1) + abs(bx) + abs(b2)


def accept_auto_1x2_upgrade(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Gate: 1X2 must improve; AH/OU may degrade at most ahOuDegradeMaxPct.

    Metrics keys (optional floats):
      mae1x2, rmse1x2, biasP1, biasPX, biasP2,
      maeAhLine, maeAhOdds, maeOuLine, maeOuOdds
    """
    opts = dict(DEFAULT_AUTO_1X2_CALIB)
    if cfg:
        opts.update({k: v for k, v in dict(cfg).items() if v is not None})

    reasons = []
    degrade_max = float(opts.get("ahOuDegradeMaxPct") or 5.0) / 100.0

    mae_b = _f(before.get("mae1x2"))
    mae_a = _f(after.get("mae1x2"))
    improved_1x2 = False
    if mae_b is not None and mae_a is not None:
        if opts.get("require1x2Improve", True):
            if mae_a < mae_b - 1e-12:
                improved_1x2 = True
            else:
                reasons.append("1x2_mae_not_improved")
        else:
            improved_1x2 = mae_a <= mae_b + 1e-12
    else:
        reasons.append("missing_1x2_mae")

    bias_ok = True
    if opts.get("requireBiasReduce", True):
        sb = systematic_bias_score(before)
        sa = systematic_bias_score(after)
        if sb is None or sa is None:
            reasons.append("missing_bias")
            bias_ok = False
        elif sa > sb + 1e-12:
            reasons.append("systematic_bias_not_reduced")
            bias_ok = False

    def market_ok(key: str) -> bool:
        vb = _f(before.get(key))
        va = _f(after.get(key))
        if vb is None or va is None:
            return True  # skip if market missing in sample
        if vb <= 1e-12:
            return va <= vb + 1e-9
        return va <= vb * (1.0 + degrade_max) + 1e-12

    ah_ok = market_ok("maeAhLine") and market_ok("maeAhOdds")
    ou_ok = market_ok("maeOuLine") and market_ok("maeOuOdds")
    if not ah_ok:
        reasons.append("ah_degraded_beyond_threshold")
    if not ou_ok:
        reasons.append("ou_degraded_beyond_threshold")

    accepted = improved_1x2 and bias_ok and ah_ok and ou_ok and not (
        "missing_1x2_mae" in reasons or "missing_bias" in reasons
    )
    return {
        "accepted": accepted,
        "improved1x2": improved_1x2,
        "biasOk": bias_ok,
        "ahOk": ah_ok,
        "ouOk": ou_ok,
        "reasons": reasons,
        "ahOuDegradeMaxPct": degrade_max * 100.0,
        "biasBefore": systematic_bias_score(before),
        "biasAfter": systematic_bias_score(after),
        "mae1x2Before": mae_b,
        "mae1x2After": mae_a,
    }
