"""Rotation → training_weight for match learning sample.

Codes come from v_matches_full (home_rotation_code / away_rotation_code).
Weight is match-level (max severity of home/away), shared by Legacy and Auto.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, MutableMapping, Optional, Tuple

logger = logging.getLogger(__name__)

ROTATION_CODES = frozenset({"none", "middle", "high"})
DEFAULT_ROTATION_TRAINING_WEIGHTS: Dict[str, float] = {
    "none": 1.0,
    "middle": 0.7,
    "high": 0.0,
}


def normalize_rotation_code(code: Any, *, log_unknown: bool = False) -> str:
    """trim + lowercase; unknown / empty / NULL → none."""
    if code is None:
        return "none"
    raw = str(code).strip().lower()
    if not raw:
        return "none"
    if raw in ROTATION_CODES:
        return raw
    if log_unknown:
        logger.warning("Unknown rotation code %r — using none", code)
    return "none"


def rotation_training_weights_from_cfg(cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, float]:
    """Read rotationTrainingWeights from model_config (top-level or train.*)."""
    out = dict(DEFAULT_ROTATION_TRAINING_WEIGHTS)
    if not cfg:
        return out
    block = None
    if isinstance(cfg.get("rotationTrainingWeights"), Mapping):
        block = cfg["rotationTrainingWeights"]
    else:
        train = cfg.get("train")
        if isinstance(train, Mapping) and isinstance(train.get("rotationTrainingWeights"), Mapping):
            block = train["rotationTrainingWeights"]
        elif isinstance(cfg.get("rotationWeights"), Mapping):
            block = cfg["rotationWeights"]
    if not block:
        return out
    for key in ("none", "middle", "high"):
        if key in block and block[key] is not None:
            try:
                out[key] = float(block[key])
            except (TypeError, ValueError):
                pass
    return out


def training_weight_for_rotation(
    home_code: Any,
    away_code: Any,
    weights: Optional[Mapping[str, float]] = None,
    *,
    log_unknown: bool = False,
) -> float:
    """Match-level weight = min(w_home, w_away) so high→0, middle→0.7, none→1."""
    wmap = dict(DEFAULT_ROTATION_TRAINING_WEIGHTS)
    if weights:
        wmap.update({k: float(v) for k, v in weights.items() if k in ROTATION_CODES})
    home = normalize_rotation_code(home_code, log_unknown=log_unknown)
    away = normalize_rotation_code(away_code, log_unknown=log_unknown)
    return min(float(wmap.get(home, 1.0)), float(wmap.get(away, 1.0)))


def rotation_reason(home_code: Any, away_code: Any) -> str:
    home = normalize_rotation_code(home_code)
    away = normalize_rotation_code(away_code)
    if home == "high" and away == "high":
        return "high_both"
    if home == "high":
        return "high_home"
    if away == "high":
        return "high_away"
    if home == "middle" and away == "middle":
        return "middle_both"
    if home == "middle":
        return "middle_home"
    if away == "middle":
        return "middle_away"
    return "none"


def training_status_for_weight(weight: float) -> str:
    if weight <= 1e-12:
        return "excluded"
    if weight < 1.0 - 1e-12:
        return "reduced"
    return "full"


def annotate_rotation_training(
    home_code: Any,
    away_code: Any,
    weights: Optional[Mapping[str, float]] = None,
    *,
    log_unknown: bool = False,
) -> Dict[str, Any]:
    home = normalize_rotation_code(home_code, log_unknown=log_unknown)
    away = normalize_rotation_code(away_code, log_unknown=log_unknown)
    tw = training_weight_for_rotation(home, away, weights, log_unknown=False)
    return {
        "home_rotation_code": home,
        "away_rotation_code": away,
        "training_weight": tw,
        "training_status": training_status_for_weight(tw),
        "rotation_reason": rotation_reason(home, away),
    }


def summarize_rotation_training(rows: list) -> Dict[str, Any]:
    """rows: iterable of dicts with training_weight and rotation_reason (or codes)."""
    n = len(rows)
    full = reduced = excluded = 0
    effective = 0.0
    reasons: Dict[str, int] = {
        "high_home": 0,
        "high_away": 0,
        "high_both": 0,
        "middle_home": 0,
        "middle_away": 0,
        "middle_both": 0,
        "none": 0,
    }
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        tw = row.get("training_weight")
        if tw is None:
            tw = training_weight_for_rotation(
                row.get("home_rotation_code") or row.get("homeRot"),
                row.get("away_rotation_code") or row.get("awayRot"),
            )
        tw = float(tw)
        effective += tw
        st = training_status_for_weight(tw)
        if st == "full":
            full += 1
        elif st == "reduced":
            reduced += 1
        else:
            excluded += 1
        reason = row.get("rotation_reason") or rotation_reason(
            row.get("home_rotation_code") or row.get("homeRot"),
            row.get("away_rotation_code") or row.get("awayRot"),
        )
        if reason in reasons:
            reasons[reason] += 1
    return {
        "total": n,
        "full": full,
        "reduced": reduced,
        "excluded": excluded,
        "effective_sample_size": effective,
        "reasons": reasons,
    }
