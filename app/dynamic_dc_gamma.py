"""Динамический Dixon–Coles γ от |D_model_final|.

Сезонный gamma сохраняется для обучения и как fallback при
dynamic_dc_gamma.enabled=false / невалидной конфигурации.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SEGMENT_DISABLED = "DISABLED_USE_SEASON"
SEGMENT_FALLBACK = "DEFAULT_FALLBACK"

DEFAULT_SEGMENTS: Tuple[Dict[str, Any], ...] = (
    {"max_abs_d": 0.5, "gamma": 0.11},
    {"max_abs_d": 1.0, "gamma": 0.07},
    {"max_abs_d": 1.5, "gamma": 0.02},
    {"max_abs_d": None, "gamma": 0.00},
)


def _fmt_bound(x: float) -> str:
    return f"{float(x):.1f}".replace(".", "_")


@dataclass(frozen=True)
class DcGammaSegment:
    max_abs_d: Optional[float]  # None = open-ended (covers all larger |D|)
    gamma: float


@dataclass(frozen=True)
class DynamicDcGammaConfig:
    enabled: bool = False
    source: str = "D_model_final"
    default_gamma: float = 0.09
    segments: Tuple[DcGammaSegment, ...] = field(default_factory=tuple)
    invalid: bool = False
    invalid_reason: Optional[str] = None

    def validated(self) -> "DynamicDcGammaConfig":
        """Validate; on failure return enabled=False + invalid flags (never raises)."""
        try:
            segs = list(self.segments) if self.segments else [
                DcGammaSegment(
                    None if s["max_abs_d"] is None else float(s["max_abs_d"]),
                    float(s["gamma"]),
                )
                for s in DEFAULT_SEGMENTS
            ]
            if not segs:
                raise ValueError("dynamic_dc_gamma.segments must contain at least one segment")
            bounds: List[Optional[float]] = []
            for i, seg in enumerate(segs):
                g = float(seg.gamma)
                if g < 0:
                    raise ValueError(f"dynamic_dc_gamma segment gamma must be >= 0, got {g}")
                if seg.max_abs_d is not None:
                    m = float(seg.max_abs_d)
                    if m < 0:
                        raise ValueError(f"dynamic_dc_gamma max_abs_d must be >= 0, got {m}")
                    bounds.append(m)
                    segs[i] = DcGammaSegment(m, g)
                else:
                    bounds.append(None)
                    segs[i] = DcGammaSegment(None, g)
            # null only allowed as last
            for i, b in enumerate(bounds):
                if b is None and i != len(bounds) - 1:
                    raise ValueError("dynamic_dc_gamma: only the last segment may have max_abs_d=null")
            finite = [b for b in bounds if b is not None]
            if sorted(finite) != finite:
                raise ValueError("dynamic_dc_gamma segments must be sorted by ascending max_abs_d")
            if len(finite) != len(set(finite)):
                raise ValueError("dynamic_dc_gamma segment max_abs_d values must be unique")
            if bounds[-1] is not None:
                # allow last finite bound as "covers above" by treating as open — but spec wants null
                # Still accept: last finite means gamma for abs_d > last? No — last with max covers ≤ max only.
                # Require open-ended coverage: last must be null OR we treat last as catch-all for >
                pass
            # Ensure open-ended coverage: if last has a finite max, values above it fall back
            src = str(self.source or "D_model_final").strip() or "D_model_final"
            dg = float(self.default_gamma)
            return DynamicDcGammaConfig(
                enabled=bool(self.enabled),
                source=src,
                default_gamma=dg,
                segments=tuple(segs),
                invalid=False,
                invalid_reason=None,
            )
        except Exception as e:  # noqa: BLE001
            reason = str(e)
            logger.error("dynamic_dc_gamma config invalid: %s — using default_gamma fallback", reason)
            return DynamicDcGammaConfig(
                enabled=False,
                source=str(self.source or "D_model_final"),
                default_gamma=float(self.default_gamma) if self.default_gamma == self.default_gamma else 0.09,
                segments=tuple(
                    DcGammaSegment(
                        None if s["max_abs_d"] is None else float(s["max_abs_d"]),
                        float(s["gamma"]),
                    )
                    for s in DEFAULT_SEGMENTS
                ),
                invalid=True,
                invalid_reason=reason,
            )


@dataclass(frozen=True)
class DcGammaResolution:
    gamma_season: float
    dynamic_dc_gamma_enabled: bool
    d_model_final: float
    abs_d_model_final: float
    gamma_segment: str
    gamma_effective: float
    dc_applied: bool
    dc_fallback_used: bool
    dc_fallback_reason: Optional[str]


def segment_label(prev: Optional[float], cur: Optional[float]) -> str:
    if cur is None:
        if prev is None:
            return "ABS_D_ALL"
        return f"ABS_D_GT_{_fmt_bound(prev)}"
    if prev is None:
        return f"ABS_D_LE_{_fmt_bound(cur)}"
    return f"ABS_D_GT_{_fmt_bound(prev)}_LE_{_fmt_bound(cur)}"


def resolve_gamma_effective(
    d_model_final: float,
    *,
    gamma_season: float,
    cfg: DynamicDcGammaConfig,
) -> DcGammaResolution:
    """Выбрать gamma_effective по |D_model_final| (AC-01…AC-09)."""
    abs_d = abs(float(d_model_final))
    season = float(gamma_season) if gamma_season == gamma_season else float(cfg.default_gamma)

    if cfg.invalid:
        g = float(cfg.default_gamma) if cfg.default_gamma == cfg.default_gamma else season
        return DcGammaResolution(
            gamma_season=season,
            dynamic_dc_gamma_enabled=False,
            d_model_final=float(d_model_final),
            abs_d_model_final=abs_d,
            gamma_segment=SEGMENT_FALLBACK,
            gamma_effective=g,
            dc_applied=abs(g) > 1e-15,
            dc_fallback_used=True,
            dc_fallback_reason=cfg.invalid_reason or "invalid_config",
        )

    if not cfg.enabled:
        return DcGammaResolution(
            gamma_season=season,
            dynamic_dc_gamma_enabled=False,
            d_model_final=float(d_model_final),
            abs_d_model_final=abs_d,
            gamma_segment=SEGMENT_DISABLED,
            gamma_effective=season,
            dc_applied=abs(season) > 1e-15,
            dc_fallback_used=False,
            dc_fallback_reason=None,
        )

    segs = cfg.segments or tuple(
        DcGammaSegment(
            None if s["max_abs_d"] is None else float(s["max_abs_d"]),
            float(s["gamma"]),
        )
        for s in DEFAULT_SEGMENTS
    )
    prev: Optional[float] = None
    for seg in segs:
        if seg.max_abs_d is None:
            return DcGammaResolution(
                gamma_season=season,
                dynamic_dc_gamma_enabled=True,
                d_model_final=float(d_model_final),
                abs_d_model_final=abs_d,
                gamma_segment=segment_label(prev, None),
                gamma_effective=float(seg.gamma),
                dc_applied=abs(float(seg.gamma)) > 1e-15,
                dc_fallback_used=False,
                dc_fallback_reason=None,
            )
        if abs_d <= float(seg.max_abs_d) + 1e-15:
            return DcGammaResolution(
                gamma_season=season,
                dynamic_dc_gamma_enabled=True,
                d_model_final=float(d_model_final),
                abs_d_model_final=abs_d,
                gamma_segment=segment_label(prev, float(seg.max_abs_d)),
                gamma_effective=float(seg.gamma),
                dc_applied=abs(float(seg.gamma)) > 1e-15,
                dc_fallback_used=False,
                dc_fallback_reason=None,
            )
        prev = float(seg.max_abs_d)

    # no open-ended segment — fallback
    g = float(cfg.default_gamma) if cfg.default_gamma == cfg.default_gamma else season
    logger.error(
        "dynamic_dc_gamma: no segment for abs_D=%.4f — using default_gamma=%s",
        abs_d, g,
    )
    return DcGammaResolution(
        gamma_season=season,
        dynamic_dc_gamma_enabled=True,
        d_model_final=float(d_model_final),
        abs_d_model_final=abs_d,
        gamma_segment=SEGMENT_FALLBACK,
        gamma_effective=g,
        dc_applied=abs(g) > 1e-15,
        dc_fallback_used=True,
        dc_fallback_reason="segment_not_found",
    )


def _opt_float(v: Any) -> Optional[float]:
    if v is None or v == "" or v == "null":
        return None
    return float(v)


def _parse_segments(raw_segs: Any) -> Tuple[DcGammaSegment, ...]:
    if not isinstance(raw_segs, (list, tuple)) or not raw_segs:
        return tuple(
            DcGammaSegment(
                None if s["max_abs_d"] is None else float(s["max_abs_d"]),
                float(s["gamma"]),
            )
            for s in DEFAULT_SEGMENTS
        )
    out: List[DcGammaSegment] = []
    for item in raw_segs:
        if not isinstance(item, Mapping):
            raise ValueError("dynamic_dc_gamma.segments items must be objects")
        max_d = item.get("max_abs_d", item.get("maxAbsD"))
        g = item.get("gamma")
        if g is None:
            raise ValueError("dynamic_dc_gamma segment missing gamma")
        out.append(DcGammaSegment(_opt_float(max_d), float(g)))
    return tuple(out)


def dynamic_dc_gamma_config_from_mapping(
    raw: Optional[Mapping[str, Any]],
    *,
    league_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> DynamicDcGammaConfig:
    """Читает dynamic_dc_gamma из model_config (с optional byLeague)."""
    base: Dict[str, Any] = {}
    if raw:
        for block in (
            raw.get("dynamic_dc_gamma"),
            raw.get("dynamicDcGamma"),
            (raw.get("legacy") or {}).get("dynamic_dc_gamma") if isinstance(raw.get("legacy"), dict) else None,
            (raw.get("dixonColes") or {}).get("dynamic") if isinstance(raw.get("dixonColes"), dict) else None,
        ):
            if isinstance(block, dict):
                base.update(block)
        by = base.get("byLeague") or base.get("by_league") or {}
        if isinstance(by, dict):
            for key in (league_id, league_name, str(league_id or ""), str(league_name or "")):
                if key and key in by and isinstance(by[key], dict):
                    base = {**base, **by[key]}
                    break
    try:
        segs = _parse_segments(base.get("segments"))
        default_g = base.get("default_gamma", base.get("defaultGamma", 0.09))
        # also allow nested dixon_coles.default_gamma at root
        if raw and "dixon_coles" in raw and isinstance(raw["dixon_coles"], dict):
            default_g = raw["dixon_coles"].get("default_gamma", default_g)
        cfg = DynamicDcGammaConfig(
            enabled=bool(base["enabled"]) if "enabled" in base else False,
            source=str(base.get("source", "D_model_final")),
            default_gamma=float(default_g),
            segments=segs,
        )
        return cfg.validated()
    except Exception as e:  # noqa: BLE001
        logger.error("dynamic_dc_gamma config parse failed: %s", e)
        return DynamicDcGammaConfig(
            enabled=False,
            default_gamma=0.09,
            segments=tuple(
                DcGammaSegment(
                    None if s["max_abs_d"] is None else float(s["max_abs_d"]),
                    float(s["gamma"]),
                )
                for s in DEFAULT_SEGMENTS
            ),
            invalid=True,
            invalid_reason=str(e),
        ).validated()


def config_from_model_fields(
    *,
    enabled: bool,
    source: str = "D_model_final",
    default_gamma: float = 0.09,
    segments: Optional[Sequence[Mapping[str, Any]]] = None,
) -> DynamicDcGammaConfig:
    raw_segs = list(segments) if segments else list(DEFAULT_SEGMENTS)
    try:
        segs = _parse_segments(raw_segs)
        return DynamicDcGammaConfig(
            enabled=bool(enabled),
            source=str(source or "D_model_final"),
            default_gamma=float(default_gamma),
            segments=segs,
        ).validated()
    except Exception as e:  # noqa: BLE001
        logger.error("dynamic_dc_gamma model fields invalid: %s", e)
        return DynamicDcGammaConfig(
            enabled=False,
            default_gamma=float(default_gamma) if default_gamma == default_gamma else 0.09,
            invalid=True,
            invalid_reason=str(e),
        ).validated()
