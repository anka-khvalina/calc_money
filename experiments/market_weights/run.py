"""Orchestrate EXP-041..044 offline (side-branch only)."""

from __future__ import annotations

import csv
import json
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from .data import fetch_all_view_rows, parse_rows, probe_readonly
from .eval import (
    better,
    eval_scheme_league,
    month_starts,
    pool_metrics,
)
from .features import compute_features
from .weights import FEATURE_KEYS, correlation_study, feature_vector, pearson

OUT = Path("/opt/cursor/artifacts/exp041_044")
REPO_REPORT = Path("/workspace/experiments/market_weights/REPORT.md")

# Expanding monthly holdout window (closing-odds coverage)
FROM_MONTH = "2025-12"
TO_MONTH = "2026-02"

# Run order: cheap first, then 042, then 041 weight if correlation ok
SCHEMES_CORE = [
    "BASELINE",
    "EXP043_VOL_DECAY",
    "EXP044_SMALL_D",
    "EXP044_LARGE_D",
    "EXP044_USHAPE",
    "EXP042_CP_0.3",
    "EXP042_CP_0.6",
]


def main() -> int:
    import logging
    logging.disable(logging.WARNING)
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print("EXP-041..044 market information weights (read-only DB, no prod edits)", flush=True)
    info = probe_readonly()
    print("readonly probe:", info, flush=True)

    raw = fetch_all_view_rows()
    rows = parse_rows(raw)
    print(f"full closing motivated rows: {len(rows)}", flush=True)
    leagues = sorted({r.league_name for r in rows})
    print("leagues:", leagues, flush=True)

    feats = compute_features(rows)
    print(f"features computed: {len(feats)}", flush=True)

    # --- EXP-041 correlation study (train cut = first holdout month) ---
    train_to = date(int(FROM_MONTH[:4]), int(FROM_MONTH[5:7]), 1)
    corr = correlation_study(rows, feats, train_to=train_to)
    # also overall with labels before train_to
    (OUT / "exp041_correlation.json").write_text(json.dumps(corr, indent=2), encoding="utf-8")
    print(f"\nEXP-041 correlation (features vs future_reval_max, train < {FROM_MONTH}):", flush=True)
    for c in sorted(corr, key=lambda x: -(abs(x["pearson"]) if x["pearson"] is not None else -1)):
        print(f"  {c['feature']:24} n={c['n']:5} r={c['pearson']}", flush=True)

    # Prefer market-dynamics features; season_stage confounds w_time
    exclude = {"season_stage"}
    best_feat = None
    best_abs = 0.0
    for c in corr:
        if c["feature"] in exclude:
            continue
        if c["pearson"] is None:
            continue
        if abs(c["pearson"]) > best_abs:
            best_abs = abs(c["pearson"])
            best_feat = c["feature"]
    # require mild signal
    use_041 = best_feat is not None and best_abs >= 0.05
    print(f"EXP-041 selected feature: {best_feat} |r|={best_abs:.4f} use_weight={use_041}", flush=True)

    schemes = list(SCHEMES_CORE)
    if use_041:
        schemes.append("EXP041_PRED_INFO")
    else:
        print("Skipping EXP041_PRED_INFO weight eval (weak/no correlation)", flush=True)

    cuts = month_starts(rows, from_month=FROM_MONTH, to_month=TO_MONTH)
    print(f"expanding months: {[c.strftime('%Y-%m') for c in cuts]}", flush=True)

    all_preds = []
    all_month = []
    for scheme in schemes:
        print(f"\n=== SCHEME {scheme} ===", flush=True)
        for lg in leagues:
            preds, months = eval_scheme_league(
                lg, rows, feats, scheme, cuts, exp041_feature=best_feat or "vol_max"
            )
            all_preds.extend(preds)
            all_month.extend(months)

    # save rows
    if all_preds:
        with (OUT / "pred_rows.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(all_preds[0].__dict__.keys()))
            w.writeheader()
            for p in all_preds:
                w.writerow(p.__dict__)
    (OUT / "month_summaries.json").write_text(json.dumps(all_month, indent=2), encoding="utf-8")

    pooled = {s: pool_metrics(all_preds, s) for s in schemes}
    (OUT / "pooled.json").write_text(json.dumps(pooled, indent=2), encoding="utf-8")

    # Verdicts
    base = pooled.get("BASELINE")
    verdicts: List[Dict[str, Any]] = []

    def verdict_for(name: str, scheme: str, reason_ok: str) -> None:
        p = pooled.get(scheme)
        if not base or not p or not p.get("n"):
            verdicts.append({"experiment": name, "verdict": "SKIP", "reason": "no preds"})
            return
        ah = better(p, base, "mae_AH")
        tot = better(p, base, "mae_Tot")
        # incremental gain required on AH or Tot without large regression on the other
        reg_ah = better(base, p, "mae_AH")  # True if base better → scheme regressed
        reg_tot = better(base, p, "mae_Tot")
        if (ah or tot) and not ((reg_ah and not ah) or (reg_tot and not tot) and not (ah or tot)):
            # pass if improves at least one and doesn't clearly hurt both
            hurt_both = (reg_ah is True and ah is not True) and (reg_tot is True and tot is not True)
            if hurt_both:
                v = "FAIL"
                reason = f"no clear incremental gain; AH_better={ah} Tot_better={tot}"
            elif ah or tot:
                # check other side not badly worse (>0.01)
                ah_delta = (p["mae_AH"] - base["mae_AH"]) if p.get("mae_AH") is not None else 0
                tot_delta = (p["mae_Tot"] - base["mae_Tot"]) if p.get("mae_Tot") is not None else 0
                if ah_delta > 0.01 and tot_delta > 0.01:
                    v = "FAIL"
                    reason = f"both metrics worse; dAH={ah_delta:.4f} dTot={tot_delta:.4f}"
                elif (ah and tot_delta <= 0.01) or (tot and ah_delta <= 0.01):
                    v = "PASS"
                    reason = reason_ok + f" | dAH={ah_delta:.4f} dTot={tot_delta:.4f}"
                else:
                    v = "PARTIAL"
                    reason = f"mixed; AH_better={ah} Tot_better={tot} dAH={ah_delta:.4f} dTot={tot_delta:.4f}"
            else:
                v = "NO EFFECT"
                reason = "within tolerance"
        else:
            ah_delta = (p["mae_AH"] - base["mae_AH"]) if p.get("mae_AH") is not None else 0
            tot_delta = (p["mae_Tot"] - base["mae_Tot"]) if p.get("mae_Tot") is not None else 0
            if abs(ah_delta) <= 0.005 and abs(tot_delta) <= 0.005:
                v = "NO EFFECT"
                reason = f"within eps; dAH={ah_delta:.4f} dTot={tot_delta:.4f}"
            else:
                v = "FAIL"
                reason = f"no incremental OOS gain vs baseline; dAH={ah_delta:.4f} dTot={tot_delta:.4f}"
        verdicts.append({
            "experiment": name,
            "scheme": scheme,
            "verdict": v,
            "reason": reason,
            "BASELINE": base,
            "SCHEME": p,
        })

    verdict_for("EXP-043_VOL_DECAY", "EXP043_VOL_DECAY", "team volatility decay beats baseline")
    verdict_for("EXP-044_SMALL_D", "EXP044_SMALL_D", "small-|D| upweight helps")
    verdict_for("EXP-044_LARGE_D", "EXP044_LARGE_D", "large-|D| upweight helps")
    verdict_for("EXP-044_USHAPE", "EXP044_USHAPE", "U-shape |D| weights help")
    verdict_for("EXP-042_CP_0.3", "EXP042_CP_0.3", "change-point pre-break×0.3 helps")
    verdict_for("EXP-042_CP_0.6", "EXP042_CP_0.6", "change-point pre-break×0.6 helps")
    if use_041:
        verdict_for(
            "EXP-041_PRED_INFO",
            "EXP041_PRED_INFO",
            f"predicted info weight via {best_feat} helps",
        )
    else:
        verdicts.append({
            "experiment": "EXP-041_PRED_INFO",
            "verdict": "SKIP_WEAK_CORR",
            "reason": f"best |r|={best_abs:.4f} on {best_feat}; need stronger causal link before weighting",
            "correlation": corr,
        })

    # pick best EXP044 hyp
    e44 = [v for v in verdicts if v["experiment"].startswith("EXP-044")]
    passes = [v for v in e44 if v.get("verdict") == "PASS"]
    if passes:
        best44 = min(passes, key=lambda v: (v["SCHEME"]["mae_AH"] + v["SCHEME"]["mae_Tot"]))
        verdicts.append({
            "experiment": "EXP-044_OVERALL",
            "verdict": "PASS",
            "reason": f"best hyp {best44['scheme']}",
            "winner": best44["scheme"],
        })
    else:
        verdicts.append({
            "experiment": "EXP-044_OVERALL",
            "verdict": "FAIL",
            "reason": "no |D|-bucket scheme beats baseline on incremental closing MAE",
        })

    (OUT / "verdicts.json").write_text(json.dumps(verdicts, indent=2), encoding="utf-8")

    # Markdown report
    lines = [
        "# EXP-041 … 044 — Market information weights (offline)",
        "",
        f"- Baseline: DB `match_weight` × Dynamic D / S-EMA / SFA / SFTC / expanding monthly",
        f"- Holdout months: `{FROM_MONTH}` … `{TO_MONTH}`",
        f"- Rows (full closing, motivated): `{len(rows)}`",
        f"- Opening lines in DB: **absent** → open/close drift features skipped",
        f"- Runtime: {time.time() - t0:.0f}s",
        f"- Read-only DB; production codepaths untouched",
        "",
        "## EXP-041 correlation study",
        "",
        "| Feature | n | Pearson vs future_reval_max |",
        "|---|---:|---:|",
    ]
    for c in sorted(corr, key=lambda x: x["feature"]):
        r = c["pearson"]
        rs = f"{r:.4f}" if r is not None else "—"
        lines.append(f"| {c['feature']} | {c['n']} | {rs} |")
    lines += ["", f"Selected feature for weight buckets: `{best_feat}` (|r|={best_abs:.4f})", ""]

    lines += [
        "## Pooled results (n-weighted over leagues × months)",
        "",
        "| Scheme | n | MAE AH | MAE Tot | n AH≥0.5 | n Tot≥0.5 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in schemes:
        p = pooled.get(s) or {}
        if not p.get("n"):
            continue
        lines.append(
            f"| {s} | {p['n']} | {p['mae_AH']:.4f} | {p['mae_Tot']:.4f} | "
            f"{p.get('n_ah_ge_0_5', 0)} | {p.get('n_tot_ge_0_5', 0)} |"
        )

    lines += ["", "## Verdicts", ""]
    for v in verdicts:
        lines.append(f"### {v['experiment']}: **{v['verdict']}**")
        lines.append(v.get("reason", ""))
        lines.append("")

    lines += [
        "## Method notes",
        "",
        "1. Each scheme = **BASELINE + one multiplier** on `quality_match_weight`.",
        "2. Metrics = closing AH/Total only (FT not used for verdicts).",
        "3. Change-point & volatility use only history before each monthly cut.",
        "4. EXP-041 future revaluation is train label / correlation only; runtime weight uses causal feature buckets.",
        "",
    ]
    text = "\n".join(lines)
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    REPO_REPORT.write_text(text, encoding="utf-8")
    print(text[:2500], flush=True)
    for v in verdicts:
        print(f"VERDICT {v['experiment']}: {v['verdict']} — {v.get('reason')}", flush=True)
    print(f"Wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
