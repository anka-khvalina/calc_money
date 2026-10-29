"""Rotation Adjustment — post-process layer on top of base 1X2 probabilities.

Does NOT change training weights, ratings, or the base line engine.
Uses existing home_rotation_code / away_rotation_code (none|middle|high).

Estimator type for MVP: bucket_v1 (logit residual vs none baseline).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from rotation_training import normalize_rotation_code

ESTIMATOR_TYPE = "bucket_v1"

DEFAULT_PROB_BUCKETS: List[float] = [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
DEFAULT_SEASON_WEIGHTS_AUTO: List[float] = [1.0, 0.6, 0.3]  # current, prev, -2
DEFAULT_RELIABILITY = {"low_lt": 20, "medium_lt": 50}
DEFAULT_MIN_N = 20


def rotation_adjustment_cfg(cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Read rotationAdjustment block from model_config (with defaults)."""
    block: Mapping[str, Any] = {}
    if cfg and isinstance(cfg.get("rotationAdjustment"), Mapping):
        block = cfg["rotationAdjustment"]  # type: ignore[assignment]
    buckets = block.get("probBuckets") if isinstance(block.get("probBuckets"), list) else DEFAULT_PROB_BUCKETS
    season_w = (
        block.get("seasonWeightsAuto")
        if isinstance(block.get("seasonWeightsAuto"), list)
        else DEFAULT_SEASON_WEIGHTS_AUTO
    )
    rel = block.get("reliability") if isinstance(block.get("reliability"), Mapping) else {}
    return {
        "enabled": block.get("enabled", True) is not False,
        "estimatorType": str(block.get("estimatorType") or ESTIMATOR_TYPE),
        "probBuckets": [float(x) for x in buckets],
        "seasonWeightsAuto": [float(x) for x in season_w],
        "minN": int(block.get("minN") if block.get("minN") is not None else DEFAULT_MIN_N),
        "reliability": {
            "low_lt": int(rel.get("low_lt") if rel.get("low_lt") is not None else DEFAULT_RELIABILITY["low_lt"]),
            "medium_lt": int(
                rel.get("medium_lt") if rel.get("medium_lt") is not None else DEFAULT_RELIABILITY["medium_lt"]
            ),
        },
        "preferOpponentNone": block.get("preferOpponentNone", True) is not False,
        "rejectPositiveCentral": block.get("rejectPositiveCentral", True) is not False,
    }


def logit(p: float) -> float:
    p = min(max(float(p), 1e-9), 1.0 - 1e-9)
    return math.log(p / (1.0 - p))


def inv_logit(x: float) -> float:
    # numerically stable sigmoid
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def residual(p_close: float, p_model: float) -> float:
    """R = logit(P_close) - logit(P_model)."""
    return logit(p_close) - logit(p_model)


def probability_bucket(p: float, edges: Optional[Sequence[float]] = None) -> str:
    """Return bucket label for P_model, e.g. '50-60'."""
    edges = list(edges or DEFAULT_PROB_BUCKETS)
    x = float(p)
    prev = 0.0
    for e in edges:
        if x < float(e):
            lo = int(round(prev * 100))
            hi = int(round(float(e) * 100))
            return f"{lo}-{hi}"
        prev = float(e)
    return f">={int(round(prev * 100))}"


def reliability_level(n: float, cfg: Optional[Mapping[str, Any]] = None) -> str:
    c = rotation_adjustment_cfg(cfg if cfg and "rotationAdjustment" in (cfg or {}) else {"rotationAdjustment": cfg or {}})
    # allow passing the already-resolved block
    if cfg and "low_lt" in cfg:
        low_lt = int(cfg["low_lt"])
        medium_lt = int(cfg["medium_lt"])
    else:
        low_lt = int(c["reliability"]["low_lt"])
        medium_lt = int(c["reliability"]["medium_lt"])
    if n < low_lt:
        return "low"
    if n < medium_lt:
        return "medium"
    return "high"


def apply_rotation_deltas(
    p_h: float,
    p_d: float,
    p_a: float,
    delta_h: float = 0.0,
    delta_a: float = 0.0,
) -> Dict[str, Any]:
    """Apply Δlogit to home/away win probs; draw unchanged before renormalization.

    W_H = P_H * exp(Δ_H); W_D = P_D; W_A = P_A * exp(Δ_A); then normalize.
    """
    ph, pd, pa = float(p_h), float(p_d), float(p_a)
    s = ph + pd + pa
    if not math.isfinite(s) or s <= 0:
        raise ValueError("invalid base probabilities")
    ph, pd, pa = ph / s, pd / s, pa / s
    dh = float(delta_h or 0.0)
    da = float(delta_a or 0.0)
    wh = ph * math.exp(dh)
    wd = pd
    wa = pa * math.exp(da)
    z = wh + wd + wa
    if not math.isfinite(z) or z <= 0:
        raise ValueError("invalid normalizer after rotation deltas")
    ph2, pd2, pa2 = wh / z, wd / z, wa / z
    return {
        "p1": ph2,
        "px": pd2,
        "p2": pa2,
        "k1": 1.0 / ph2,
        "kx": 1.0 / pd2,
        "k2": 1.0 / pa2,
        "delta_home": dh,
        "delta_away": da,
    }


def _quantile(sorted_vals: Sequence[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    w = pos - lo
    return float(sorted_vals[lo]) * (1 - w) + float(sorted_vals[hi]) * w


def summarize_effects(effects: Sequence[float], weights: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """Weighted mean of E_i = R_i - R_baseline; also median/Q25/Q75 on unweighted sorted E."""
    xs = [float(x) for x in effects if x is not None and math.isfinite(float(x))]
    if not xs:
        return {
            "n": 0,
            "weighted_n": 0.0,
            "mean": None,
            "median": None,
            "q25": None,
            "q75": None,
        }
    if weights is None:
        ws = [1.0] * len(xs)
    else:
        ws = [float(w) for w in weights]
        if len(ws) != len(xs):
            raise ValueError("weights length mismatch")
    wsum = sum(ws)
    mean = sum(x * w for x, w in zip(xs, ws)) / wsum if wsum > 0 else None
    ordered = sorted(xs)
    return {
        "n": len(xs),
        "weighted_n": float(wsum),
        "mean": mean,
        "median": _quantile(ordered, 0.5),
        "q25": _quantile(ordered, 0.25),
        "q75": _quantile(ordered, 0.75),
    }


@dataclass
class TeamMatchObs:
    """One team-side observation for residual estimation."""

    match_id: Any
    team_id: Any
    opponent_id: Any
    league: Any
    season: Any
    season_index: int  # 0 = current (newest), 1 = previous, …
    home_flag: bool
    rotation_code: str
    opponent_rotation_code: str
    p_model: float  # team win prob from base model
    p_close: float  # fair closing win prob for same team
    weight: float = 1.0
    residual: float = field(init=False)

    def __post_init__(self) -> None:
        self.rotation_code = normalize_rotation_code(self.rotation_code)
        self.opponent_rotation_code = normalize_rotation_code(self.opponent_rotation_code)
        self.residual = residual(self.p_close, self.p_model)


def _mean_residual(obs: Sequence[TeamMatchObs]) -> Optional[float]:
    if not obs:
        return None
    ws = [float(o.weight) for o in obs]
    wsum = sum(ws)
    if wsum <= 0:
        return None
    return sum(o.residual * w for o, w in zip(obs, ws)) / wsum


def estimate_delta_logit(
    observations: Sequence[TeamMatchObs],
    *,
    rotation_code: str,
    p_model: float,
    cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Bucket-based Δlogit = E[R|rot] - E[R|none] for the team's P_model bucket.

    Prefer opponent_rotation=none when preferOpponentNone is set.
    Returns adjustment_available=False on insufficient / unstable (positive) estimate.
    """
    racfg = rotation_adjustment_cfg({"rotationAdjustment": cfg} if cfg and "probBuckets" not in (cfg or {}) else None)
    if cfg and "probBuckets" in cfg:
        racfg = rotation_adjustment_cfg({"rotationAdjustment": cfg})
    rot = normalize_rotation_code(rotation_code)
    if rot == "none":
        return {
            "adjustment_available": True,
            "delta_logit": 0.0,
            "q25": 0.0,
            "q75": 0.0,
            "n": 0,
            "weighted_n": 0.0,
            "reliability": "high",
            "probability_bucket": probability_bucket(p_model, racfg["probBuckets"]),
            "fallback": None,
            "reason": None,
            "estimator_type": racfg["estimatorType"],
            "r_baseline": None,
            "r_rotation": None,
        }

    bucket = probability_bucket(p_model, racfg["probBuckets"])
    edges = racfg["probBuckets"]

    def in_bucket(o: TeamMatchObs) -> bool:
        return probability_bucket(o.p_model, edges) == bucket

    def pool(require_opp_none: bool, any_bucket: bool = False) -> List[TeamMatchObs]:
        out: List[TeamMatchObs] = []
        for o in observations:
            if not any_bucket and not in_bucket(o):
                continue
            if require_opp_none and o.opponent_rotation_code != "none":
                continue
            out.append(o)
        return out

    fallback_notes: List[str] = []
    prefer_opp = bool(racfg["preferOpponentNone"])

    # 1) same bucket, opponent none
    base_pool = pool(require_opp_none=prefer_opp)
    rot_pool = [o for o in base_pool if o.rotation_code == rot]
    none_pool = [o for o in base_pool if o.rotation_code == "none"]

    # 2) same bucket, any opponent
    if len(rot_pool) < racfg["minN"] or len(none_pool) < racfg["minN"]:
        base_pool2 = pool(require_opp_none=False)
        rot_pool2 = [o for o in base_pool2 if o.rotation_code == rot]
        none_pool2 = [o for o in base_pool2 if o.rotation_code == "none"]
        if len(rot_pool2) > len(rot_pool) or len(none_pool2) > len(none_pool):
            fallback_notes.append("expanded opponent filter (any opponent rotation)")
            rot_pool, none_pool = rot_pool2, none_pool2

    # 3) widen bucket (± neighbors) — merge all observations for simplicity if still small
    if len(rot_pool) < racfg["minN"]:
        all_rot = [o for o in observations if o.rotation_code == rot]
        all_none = [o for o in observations if o.rotation_code == "none"]
        if prefer_opp:
            all_rot_f = [o for o in all_rot if o.opponent_rotation_code == "none"]
            all_none_f = [o for o in all_none if o.opponent_rotation_code == "none"]
            if len(all_rot_f) >= racfg["minN"] // 2:
                all_rot, all_none = all_rot_f, all_none_f
        if len(all_rot) > len(rot_pool):
            fallback_notes.append(f"expanded probability buckets (was {bucket})")
            rot_pool, none_pool = all_rot, all_none

    r_base = _mean_residual(none_pool)
    r_rot = _mean_residual(rot_pool)
    if r_base is None or r_rot is None or len(rot_pool) == 0:
        return {
            "adjustment_available": False,
            "delta_logit": None,
            "q25": None,
            "q75": None,
            "n": len(rot_pool),
            "weighted_n": float(sum(o.weight for o in rot_pool)),
            "reliability": reliability_level(len(rot_pool), racfg["reliability"]),
            "probability_bucket": bucket,
            "fallback": "; ".join(fallback_notes) if fallback_notes else None,
            "reason": "insufficient_sample",
            "estimator_type": racfg["estimatorType"],
            "r_baseline": r_base,
            "r_rotation": r_rot,
        }

    effects = [o.residual - r_base for o in rot_pool]
    weights = [o.weight for o in rot_pool]
    stats = summarize_effects(effects, weights)
    delta = stats["mean"]
    q25 = stats["q25"]
    q75 = stats["q75"]

    if racfg["rejectPositiveCentral"] and delta is not None and delta > 0:
        return {
            "adjustment_available": False,
            "delta_logit": delta,
            "q25": q25,
            "q75": q75,
            "n": stats["n"],
            "weighted_n": stats["weighted_n"],
            "reliability": reliability_level(stats["n"], racfg["reliability"]),
            "probability_bucket": bucket,
            "fallback": "; ".join(fallback_notes) if fallback_notes else None,
            "reason": "unstable_estimate",
            "estimator_type": racfg["estimatorType"],
            "r_baseline": r_base,
            "r_rotation": r_rot,
        }

    return {
        "adjustment_available": True,
        "delta_logit": delta,
        "q25": q25,
        "q75": q75,
        "n": stats["n"],
        "weighted_n": stats["weighted_n"],
        "mean": stats["mean"],
        "median": stats["median"],
        "reliability": reliability_level(stats["n"], racfg["reliability"]),
        "probability_bucket": bucket,
        "fallback": "; ".join(fallback_notes) if fallback_notes else None,
        "reason": None,
        "estimator_type": racfg["estimatorType"],
        "r_baseline": r_base,
        "r_rotation": r_rot,
        "seasons_used": sorted({o.season for o in rot_pool}, key=lambda x: str(x)),
    }


def adjust_match_1x2(
    p1: float,
    px: float,
    p2: float,
    home_rotation_code: Any,
    away_rotation_code: Any,
    observations: Sequence[TeamMatchObs],
    cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Full service response for one match given base probs + historical observations."""
    racfg = rotation_adjustment_cfg({"rotationAdjustment": cfg} if cfg and "probBuckets" not in (cfg or {}) else None)
    if cfg and "probBuckets" in cfg:
        racfg = rotation_adjustment_cfg({"rotationAdjustment": cfg})

    home_rot = normalize_rotation_code(home_rotation_code)
    away_rot = normalize_rotation_code(away_rotation_code)

    if home_rot == "none" and away_rot == "none":
        base = apply_rotation_deltas(p1, px, p2, 0.0, 0.0)
        return {
            "adjustment_available": False,
            "skipped": True,
            "reason": "no_rotation",
            "home_rotation_code": home_rot,
            "away_rotation_code": away_rot,
            "delta_logit_home": 0.0,
            "delta_logit_away": 0.0,
            "base": {"p1": float(p1), "px": float(px), "p2": float(p2)},
            "adjusted": None,
            "estimator_type": racfg["estimatorType"],
            **{k: base[k] for k in ()},
        }

    if not racfg["enabled"]:
        return {
            "adjustment_available": False,
            "skipped": True,
            "reason": "disabled",
            "home_rotation_code": home_rot,
            "away_rotation_code": away_rot,
            "delta_logit_home": None,
            "delta_logit_away": None,
            "base": {"p1": float(p1), "px": float(px), "p2": float(p2)},
            "adjusted": None,
            "estimator_type": racfg["estimatorType"],
        }

    est_h = estimate_delta_logit(observations, rotation_code=home_rot, p_model=float(p1), cfg=racfg)
    est_a = estimate_delta_logit(observations, rotation_code=away_rot, p_model=float(p2), cfg=racfg)

    dh = 0.0 if home_rot == "none" else est_h.get("delta_logit")
    da = 0.0 if away_rot == "none" else est_a.get("delta_logit")

    home_ok = home_rot == "none" or bool(est_h.get("adjustment_available"))
    away_ok = away_rot == "none" or bool(est_a.get("adjustment_available"))
    if not home_ok or not away_ok or dh is None or da is None:
        return {
            "adjustment_available": False,
            "skipped": False,
            "reason": (est_h.get("reason") if not home_ok else None)
            or (est_a.get("reason") if not away_ok else None)
            or "insufficient_sample",
            "home_rotation_code": home_rot,
            "away_rotation_code": away_rot,
            "delta_logit_home": dh,
            "delta_logit_away": da,
            "home_estimate": est_h,
            "away_estimate": est_a,
            "base": {"p1": float(p1), "px": float(px), "p2": float(p2)},
            "adjusted": None,
            "estimator_type": racfg["estimatorType"],
        }

    central = apply_rotation_deltas(p1, px, p2, float(dh), float(da))
    # historical range from Q25/Q75 of each side (more negative Q = stronger weaken)
    q25_h = est_h.get("q25") if home_rot != "none" else 0.0
    q75_h = est_h.get("q75") if home_rot != "none" else 0.0
    q25_a = est_a.get("q25") if away_rot != "none" else 0.0
    q75_a = est_a.get("q75") if away_rot != "none" else 0.0
    # stronger weaken uses more negative delta → for range of P1 odds, use q25/q75 combos
    lo = apply_rotation_deltas(
        p1, px, p2,
        float(q25_h if q25_h is not None else dh),
        float(q25_a if q25_a is not None else da),
    )
    hi = apply_rotation_deltas(
        p1, px, p2,
        float(q75_h if q75_h is not None else dh),
        float(q75_a if q75_a is not None else da),
    )

    def odds_range(central_k: float, a_k: float, b_k: float) -> Tuple[float, float]:
        return (min(a_k, b_k, central_k), max(a_k, b_k, central_k))

    return {
        "adjustment_available": True,
        "skipped": False,
        "reason": None,
        "home_rotation_code": home_rot,
        "away_rotation_code": away_rot,
        "delta_logit_home": float(dh),
        "delta_logit_away": float(da),
        "home_estimate": est_h,
        "away_estimate": est_a,
        "base": {"p1": float(p1), "px": float(px), "p2": float(p2)},
        "adjusted": {
            "p1": central["p1"],
            "px": central["px"],
            "p2": central["p2"],
            "k1": central["k1"],
            "kx": central["kx"],
            "k2": central["k2"],
            "k1_range": odds_range(central["k1"], lo["k1"], hi["k1"]),
            "kx_range": odds_range(central["kx"], lo["kx"], hi["kx"]),
            "k2_range": odds_range(central["k2"], lo["k2"], hi["k2"]),
        },
        "estimator_type": racfg["estimatorType"],
    }


def season_weight_auto(season_index: int, weights: Optional[Sequence[float]] = None) -> float:
    """season_index 0 = current (newest)."""
    ws = list(weights or DEFAULT_SEASON_WEIGHTS_AUTO)
    if season_index < 0:
        return 0.0
    if season_index < len(ws):
        return float(ws[season_index])
    return float(ws[-1]) * (0.5 ** (season_index - len(ws) + 1))
