"""Local expected-total (S) correction for the Strong Favourite segment.

After S_model (and S-calibration) is computed, optionally add a constant
lift to S before λ / Poisson / markets. Does not modify D, dBigFav, or SFA.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


DEFAULT_STRONG_FAVOURITE_THRESHOLD = 1.30
DEFAULT_TOTAL_CORRECTION = 0.10


@dataclass(frozen=True)
class StrongFavouriteTotalConfig:
    """Feature flag + segment threshold + OU total lift."""

    enabled: bool = True
    strong_favourite_threshold: float = DEFAULT_STRONG_FAVOURITE_THRESHOLD
    total_correction: float = DEFAULT_TOTAL_CORRECTION


@dataclass(frozen=True)
class StrongFavouriteTotalDiagnostics:
    enabled: bool
    applied: bool
    in_strong_favourite_segment: bool
    favorite_odds: Optional[float]
    threshold: float
    total_correction: float
    s_before: float
    s_after: float


def is_strong_favourite_segment(
    favorite_odds: Optional[float],
    threshold: float,
) -> bool:
    """True when market (or implied) favorite odds are in the Strong Favourite segment."""
    if favorite_odds is None:
        return False
    try:
        fo = float(favorite_odds)
    except (TypeError, ValueError):
        return False
    if not (fo > 1.0):
        return False
    return fo <= float(threshold) + 1e-15


def apply_strong_favourite_total_correction(
    s_model: float,
    favorite_odds: Optional[float],
    cfg: StrongFavouriteTotalConfig,
) -> Tuple[float, StrongFavouriteTotalDiagnostics]:
    """Return (S_final, diagnostics).

    When the feature flag is off, or the match is outside the Strong Favourite
    segment, S_final == s_model.
    """
    s0 = float(s_model)
    thr = float(cfg.strong_favourite_threshold)
    corr = float(cfg.total_correction)
    in_seg = is_strong_favourite_segment(favorite_odds, thr)
    apply = bool(cfg.enabled) and in_seg and corr != 0.0
    s1 = s0 + corr if apply else s0
    if s1 < 0.05:
        s1 = 0.05
    fav_out: Optional[float] = None
    if favorite_odds is not None:
        try:
            fv = float(favorite_odds)
            if math.isfinite(fv):
                fav_out = fv
        except (TypeError, ValueError):
            fav_out = None
    return s1, StrongFavouriteTotalDiagnostics(
        enabled=bool(cfg.enabled),
        applied=apply,
        in_strong_favourite_segment=in_seg,
        favorite_odds=fav_out,
        threshold=thr,
        total_correction=corr if apply else 0.0,
        s_before=s0,
        s_after=float(s1),
    )


def config_from_mapping(raw: Optional[Mapping[str, Any]]) -> StrongFavouriteTotalConfig:
    """Parse strongFavouriteTotalCorrection from model_config.json."""
    if not raw:
        return StrongFavouriteTotalConfig()
    block: Any = raw.get("strongFavouriteTotalCorrection")
    if block is None:
        block = raw.get("strong_favourite_total_correction")
    if block is None:
        return StrongFavouriteTotalConfig()
    # Allow top-level boolean flag: false restores legacy (no correction).
    if isinstance(block, bool):
        return StrongFavouriteTotalConfig(enabled=block)
    if not isinstance(block, Mapping):
        return StrongFavouriteTotalConfig()
    enabled = block.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("0", "false", "off", "no")
    thr = block.get("strongFavouriteThreshold", block.get("strong_favourite_threshold"))
    corr = block.get("totalCorrection", block.get("total_correction"))
    return StrongFavouriteTotalConfig(
        enabled=bool(enabled),
        strong_favourite_threshold=float(
            thr if thr is not None else DEFAULT_STRONG_FAVOURITE_THRESHOLD
        ),
        total_correction=float(
            corr if corr is not None else DEFAULT_TOTAL_CORRECTION
        ),
    )
