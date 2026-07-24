"""
Configurable Asian-handicap line weights for WLS strength fit (w_line_AH).

D-training weight policy (Goal Difference / strength WLS only):
  Default production mode is **disabled** — AH line weight is excluded from
  observation weights: w = w_base × w_Huber. Total Goals (S) still uses w_line_T.
  Prediction / pricing is unaffected.

Modes (lineWeight.mode / lineWeightMode / line_weight_mode):
  current  — legacy formula 1/(1+α·|D|^p) clamped to [min,max]
  soft     — piecewise table on |D| (preset or config table); never below current
  disabled — w_line_AH = 1 for every match (AH line weight not applied)

Named presets CURRENT / SOFT / DISABLED are built-in. Extra named presets can be
added under lineWeight.presets in model_config.json without code changes:

  lineWeight:
    mode: soft
    table:            # optional override for the active mode
      0.50: 1.00
      1.00: 0.90
    presets:
      experiment_1:
        table: { 0.5: 1.0, 2.0: 0.85 }

Axis is |D| (diff_goals), same as the historical CURRENT formula.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

MODE_CURRENT = "current"
MODE_SOFT = "soft"
MODE_DISABLED = "disabled"
BUILTIN_MODES = frozenset({MODE_CURRENT, MODE_SOFT, MODE_DISABLED})

# Soft preset on |D| (same axis as CURRENT). Chosen so soft ≥ current at typical points.
DEFAULT_SOFT_TABLE: Tuple[Tuple[float, float], ...] = (
    (0.50, 1.00),
    (0.75, 0.95),
    (1.00, 0.90),
    (1.50, 0.80),
    (2.00, 0.70),
    (3.00, 0.60),
)


def _norm_mode(raw: Any) -> str:
    s = str(raw or MODE_DISABLED).strip().lower()
    return s


def parse_weight_table(raw: Any) -> Optional[List[Tuple[float, float]]]:
    """Parse {abs: weight} mapping or [[abs, weight], ...] into sorted breakpoints."""
    if raw is None:
        return None
    pairs: List[Tuple[float, float]] = []
    if isinstance(raw, Mapping):
        for k, v in raw.items():
            if str(k).strip().lower() in ("table", "mode", "kind"):
                continue
            try:
                pairs.append((float(k), float(v)))
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, Mapping):
                try:
                    a = float(item.get("abs", item.get("absD", item.get("abs_d", item.get("ah")))))
                    w = float(item.get("weight", item.get("w")))
                    pairs.append((a, w))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    pairs.append((float(item[0]), float(item[1])))
                except (TypeError, ValueError):
                    continue
    if not pairs:
        return None
    pairs.sort(key=lambda t: t[0])
    out: List[Tuple[float, float]] = []
    for a, w in pairs:
        if a < 0:
            continue
        ww = max(1e-9, min(1.0, w))
        out.append((a, ww))
    return out or None


def parse_presets(raw: Any) -> Dict[str, List[Tuple[float, float]]]:
    """Parse lineWeight.presets: { name: {table: ...} | {abs: w, ...} }."""
    out: Dict[str, List[Tuple[float, float]]] = {}
    if not isinstance(raw, Mapping):
        return out
    for name, body in raw.items():
        key = str(name).strip().lower()
        if not key:
            continue
        table = None
        if isinstance(body, Mapping):
            if "table" in body or "lineWeightTable" in body or "line_weight_table" in body:
                table = parse_weight_table(
                    body.get("table")
                    or body.get("lineWeightTable")
                    or body.get("line_weight_table")
                )
            else:
                table = parse_weight_table(body)
        else:
            table = parse_weight_table(body)
        if table:
            out[key] = table
    return out


def table_lookup(abs_d: float, table: Sequence[Tuple[float, float]]) -> float:
    """Piecewise-linear weight on |D|; flat outside endpoints."""
    if not table:
        return 1.0
    x = abs(float(abs_d))
    if x <= table[0][0]:
        return float(table[0][1])
    if x >= table[-1][0]:
        return float(table[-1][1])
    for i in range(1, len(table)):
        a0, w0 = table[i - 1]
        a1, w1 = table[i]
        if x <= a1:
            if abs(a1 - a0) < 1e-15:
                return float(w1)
            t = (x - a0) / (a1 - a0)
            return float(w0 + t * (w1 - w0))
    return float(table[-1][1])


def formula_weight(
    abs_d: float,
    *,
    alpha: float,
    p: float,
    min_w: float,
    max_w: float,
) -> float:
    ad = abs(float(abs_d))
    raw = 1.0 / (1.0 + float(alpha) * (ad ** float(p)))
    return float(min(max_w, max(min_w, raw)))


@dataclass
class LineWeightConfig:
    mode: str = MODE_DISABLED
    alpha_ah: float = 0.25
    p_ah: float = 2.0
    min_w: float = 0.15
    max_w: float = 1.0
    table: Optional[List[Tuple[float, float]]] = None  # active override
    presets: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)

    def validated(self) -> "LineWeightConfig":
        mode = _norm_mode(self.mode)
        if self.alpha_ah < 0:
            raise ValueError("lineWeight alpha must be >= 0")
        if self.p_ah <= 0:
            raise ValueError("lineWeight p must be > 0")
        if self.min_w < 0 or self.max_w < 0:
            raise ValueError("lineWeight min/max must be >= 0")
        if self.max_w < self.min_w:
            raise ValueError("lineWeight max must be >= min")
        table = self.table
        if table is not None:
            table = [(float(a), float(w)) for a, w in table]
            table.sort(key=lambda t: t[0])
        presets: Dict[str, List[Tuple[float, float]]] = {}
        for k, v in (self.presets or {}).items():
            kk = str(k).strip().lower()
            if not kk or not v:
                continue
            presets[kk] = sorted([(float(a), float(w)) for a, w in v], key=lambda t: t[0])
        # Unknown named mode must resolve via presets or builtin.
        if mode not in BUILTIN_MODES and mode not in presets and table is None:
            raise ValueError(
                f"lineWeight.mode {mode!r} is unknown; "
                f"use one of {sorted(BUILTIN_MODES)} or define presets[{mode}] / table"
            )
        return LineWeightConfig(
            mode=mode,
            alpha_ah=float(self.alpha_ah),
            p_ah=float(self.p_ah),
            min_w=float(self.min_w),
            max_w=float(self.max_w),
            table=table,
            presets=presets,
        )


def _extract_line_weight_block(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Prefer top-level lineWeight, else train.lineWeight / train shorthand."""
    if "lineWeight" in raw or "line_weight" in raw:
        cand = raw.get("lineWeight") or raw.get("line_weight")
        if isinstance(cand, Mapping):
            return cand
    if "lineWeightMode" in raw or "line_weight_mode" in raw:
        return raw
    tr = raw.get("train")
    if isinstance(tr, Mapping):
        cand = tr.get("lineWeight") or tr.get("line_weight")
        if isinstance(cand, Mapping):
            return cand
        if "lineWeightMode" in tr or "line_weight_mode" in tr or "lineWeightTable" in tr:
            return tr
    return {}


def line_weight_config_from_mapping(raw: Optional[Mapping[str, Any]]) -> LineWeightConfig:
    """Read lineWeight block (top-level or nested under train)."""
    if not raw:
        return LineWeightConfig().validated()
    block = _extract_line_weight_block(raw)
    # Top-level shorthand may sit beside a block
    mode = (
        block.get("mode")
        or block.get("lineWeightMode")
        or block.get("line_weight_mode")
        or raw.get("lineWeightMode")
        or raw.get("line_weight_mode")
        or MODE_DISABLED
    )
    table = parse_weight_table(
        block.get("table")
        or block.get("lineWeightTable")
        or block.get("line_weight_table")
        or raw.get("lineWeightTable")
        or raw.get("line_weight_table")
    )
    presets = parse_presets(block.get("presets") or raw.get("lineWeightPresets"))
    # CURRENT formula params: prefer lineWeight block, fall back to train.alphaAh
    train = raw.get("train") if isinstance(raw.get("train"), Mapping) else {}
    alpha = block.get("alphaAh", block.get("alpha_ah", block.get("alpha")))
    if alpha is None:
        alpha = train.get("alphaAh", train.get("alpha_ah", 0.25))
    p = block.get("pAh", block.get("p_ah", block.get("p", 2.0)))
    min_w = block.get("minWAh", block.get("min_w", block.get("minW", 0.15)))
    max_w = block.get("maxWAh", block.get("max_w", block.get("maxW", 1.0)))
    return LineWeightConfig(
        mode=str(mode),
        alpha_ah=float(alpha),
        p_ah=float(p),
        min_w=float(min_w),
        max_w=float(max_w),
        table=table,
        presets=presets,
    ).validated()


def resolve_table_for_mode(cfg: LineWeightConfig) -> Optional[List[Tuple[float, float]]]:
    """Resolve breakpoint table for soft / named preset modes."""
    c = cfg.validated()
    if c.table:
        return list(c.table)
    if c.mode in c.presets:
        return list(c.presets[c.mode])
    if c.mode == MODE_SOFT:
        return list(DEFAULT_SOFT_TABLE)
    return None


def w_line_ah(abs_d: float, cfg: LineWeightConfig) -> float:
    """Compute w_line_AH for one match given |D| (or |AH| proxy)."""
    c = cfg.validated()
    if c.mode == MODE_DISABLED:
        return 1.0
    current_w = formula_weight(
        abs_d, alpha=c.alpha_ah, p=c.p_ah, min_w=c.min_w, max_w=c.max_w
    )
    if c.mode == MODE_CURRENT:
        return current_w
    table = resolve_table_for_mode(c)
    if table is None:
        # Should not happen after validated(); fall back to current
        return current_w
    table_w = table_lookup(abs_d, table)
    # AC2: SOFT must never undercut CURRENT
    if c.mode == MODE_SOFT:
        return float(max(table_w, current_w))
    return float(table_w)


@dataclass
class LineWeightDiagnostics:
    mode: str
    n_matches: int
    avg_w_line_ah: float
    n_affected: int  # w < 1 - 1e-9
    min_w: float
    max_w_obs: float

    def summary_lines(self) -> List[str]:
        return [
            "Training configuration",
            f"lineWeightMode = {self.mode.upper()}",
            f"Average w_line_AH = {self.avg_w_line_ah:.4f}",
            f"Matches affected = {self.n_affected}",
            f"w_line_AH range = [{self.min_w:.4f}, {self.max_w_obs:.4f}]",
        ]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "n_matches": self.n_matches,
            "avg_w_line_ah": self.avg_w_line_ah,
            "n_affected": self.n_affected,
            "min_w": self.min_w,
            "max_w": self.max_w_obs,
        }


def diagnose_weights(weights: Sequence[float], mode: str) -> LineWeightDiagnostics:
    ws = [float(w) for w in weights if w is not None]
    if not ws:
        return LineWeightDiagnostics(
            mode=mode, n_matches=0, avg_w_line_ah=1.0, n_affected=0, min_w=1.0, max_w_obs=1.0
        )
    n_aff = sum(1 for w in ws if w < 1.0 - 1e-9)
    return LineWeightDiagnostics(
        mode=mode,
        n_matches=len(ws),
        avg_w_line_ah=sum(ws) / len(ws),
        n_affected=n_aff,
        min_w=min(ws),
        max_w_obs=max(ws),
    )


def log_diagnostics(diag: LineWeightDiagnostics) -> None:
    for line in diag.summary_lines():
        logger.info("%s", line)
