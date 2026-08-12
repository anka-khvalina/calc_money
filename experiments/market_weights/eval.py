"""Expanding monthly evaluation vs closing AH/Total."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import goal_model_train as gmt

from .data import Row, to_raw_match
from .features import MatchFeatures
from . import weights as W

ROOT = Path("/workspace")
CFG_PATH = ROOT / "web" / "model_config.json"


@dataclass
class PredRow:
    match_id: str
    league: str
    month: str
    scheme: str
    ah_mkt: float
    tot_mkt: float
    ah_pred: float
    tot_pred: float


def load_baseline_config(season_weights: List[gmt.SeasonWeight]) -> gmt.ModelConfig:
    doc = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    cfg = gmt.ModelConfig(season_weights=season_weights, default_season_weight=1.0)
    cfg = gmt.apply_d_correction_config_from_mapping(cfg, doc)
    cfg = gmt.apply_sfa_config_from_mapping(cfg, doc)
    cfg = gmt.apply_sftc_config_from_mapping(cfg, doc)
    cfg = gmt.apply_rating_config_from_mapping(cfg, doc)
    # Avoid Ligue1 derby collinearity & keep eval comparable
    cfg = replace(cfg, rating_mode="standard_wls", rating_by_league={}, derby_min_matches=10**9)
    se = doc.get("dynamic_s_ema") or {}
    if se:
        cfg = replace(
            cfg,
            s_momentum_enabled=bool(se.get("enabled", False)),
            s_momentum_alpha=float(se.get("alpha", 0.3)),
            s_momentum_k=float(se.get("k", 1.0)),
            s_momentum_min_matches=int(se.get("min_team_matches", 1) or 1),
            s_momentum_reset_on_new_season=bool(se.get("reset_on_new_season", True)),
        )
    mom = doc.get("momentum") or {}
    if mom:
        cfg = replace(
            cfg,
            momentum_enabled=bool(mom.get("enabled", True)),
            momentum_alpha=float(mom.get("alpha", 0.3)),
            momentum_k=float(mom.get("k", 0.8)),
            momentum_max_ema=float(mom.get("maxEma", mom.get("max_ema", 0.5))),
            momentum_min_matches=int(mom.get("minMatches", mom.get("min_matches", 1)) or 1),
        )
    return cfg


def season_weights_for(raw: Sequence[gmt.RawMatch]) -> List[gmt.SeasonWeight]:
    labels = sorted({m.season_label for m in raw if m.season_label}, reverse=True)
    defaults = [1.0, 0.7, 0.5]
    out: List[gmt.SeasonWeight] = []
    for i, lab in enumerate(labels):
        dates = [m.date for m in raw if m.season_label == lab and m.date]
        if not dates:
            continue
        w = defaults[i] if i < len(defaults) else 0.5
        out.append(gmt.SeasonWeight(label=str(lab), date_from=min(dates), date_to=max(dates), base_weight=w))
    return out


def month_starts(rows: Sequence[Row], *, from_month: str, to_month: str) -> List[date]:
    """Inclusive month keys YYYY-MM → cuts at day 1 of each month in range."""
    months = sorted({r.match_date.strftime("%Y-%m") for r in rows})
    months = [m for m in months if from_month <= m <= to_month]
    cuts = []
    for m in months:
        y, mo = map(int, m.split("-"))
        cuts.append(date(y, mo, 1))
    return cuts


def metrics(preds: Sequence[PredRow]) -> Dict[str, Any]:
    if not preds:
        return {"n": 0}
    ah = [abs(p.ah_pred - p.ah_mkt) for p in preds]
    tot = [abs(p.tot_pred - p.tot_mkt) for p in preds]
    bah = [p.ah_pred - p.ah_mkt for p in preds]
    btot = [p.tot_pred - p.tot_mkt for p in preds]

    def avg(xs):
        return sum(xs) / len(xs)

    return {
        "n": len(preds),
        "mae_AH": avg(ah),
        "mae_Tot": avg(tot),
        "bias_AH": avg(bah),
        "bias_Tot": avg(btot),
        "n_ah_ge_0_5": sum(1 for e in ah if e >= 0.5),
        "n_ah_ge_0_75": sum(1 for e in ah if e >= 0.75),
        "n_tot_ge_0_5": sum(1 for e in tot if e >= 0.5),
    }


MultFn = Callable[[Row, Mapping[str, MatchFeatures]], float]


def resolve_mults(
    scheme: str,
    rows: Sequence[Row],
    feats: Mapping[str, MatchFeatures],
    train_cut: date,
    *,
    exp041_feature: str = "vol_max",
    exp042_mult: float = 0.3,
) -> Dict[str, float]:
    if scheme == "BASELINE":
        return {r.match_id: 1.0 for r in rows if r.match_date < train_cut}
    if scheme.startswith("EXP044"):
        fn = W.SCHEMES[scheme]
        assert fn is not None
        return {r.match_id: fn(r, feats) for r in rows if r.match_date < train_cut}
    if scheme == "EXP043_VOL_DECAY":
        return W.make_exp043_assigner(rows, feats, train_cut)
    if scheme.startswith("EXP042_CP_"):
        # EXP042_CP_0.0 / 0.3 / 0.6
        mult = float(scheme.split("_")[-1])
        return W.make_exp042_assigner(rows, train_cut, pre_break_mult=mult)
    if scheme == "EXP041_PRED_INFO":
        return W.make_exp041_assigner(rows, feats, train_cut, feature=exp041_feature)
    raise KeyError(scheme)


def eval_scheme_league(
    league: str,
    rows_all: Sequence[Row],
    feats: Mapping[str, MatchFeatures],
    scheme: str,
    cuts: Sequence[date],
    *,
    exp041_feature: str = "vol_max",
) -> Tuple[List[PredRow], List[Dict[str, Any]]]:
    rows = [r for r in rows_all if r.league_name == league]
    preds: List[PredRow] = []
    month_summaries: List[Dict[str, Any]] = []

    for cut in cuts:
        train_rows = [r for r in rows if r.match_date < cut]
        # holdout: this calendar month
        y, m = cut.year, cut.month
        if m == 12:
            cut_end = date(y + 1, 1, 1)
        else:
            cut_end = date(y, m + 1, 1)
        hold = [r for r in rows if cut <= r.match_date < cut_end]
        if len(train_rows) < 80 or len(hold) < 8:
            continue

        mults = resolve_mults(scheme, rows, feats, cut, exp041_feature=exp041_feature)
        raw = [to_raw_match(r, quality_mult=mults.get(r.match_id, 1.0)) for r in train_rows]
        sw = season_weights_for(raw)
        cfg = load_baseline_config(sw)

        t0 = time.time()
        try:
            model, _ = gmt.train_full_model(raw, cfg)
        except ValueError as e:
            month_summaries.append({
                "league": league, "month": cut.strftime("%Y-%m"), "scheme": scheme,
                "skipped": True, "reason": str(e)[:120],
            })
            continue
        train_s = time.time() - t0

        month_preds: List[PredRow] = []
        for r in hold:
            hid = gmt.team_key(r.home_team_id, r.home_team)
            aid = gmt.team_key(r.away_team_id, r.away_team)
            try:
                pred = gmt.predict_match(
                    model, hid, aid,
                    neutral=r.is_neutral,
                    derby=gmt._derby_flag_from_weight(r.derby_weight),
                    match_date=r.match_date,
                    home_odds=r.home_odds,
                    away_odds=r.away_odds,
                    league=r.league_name,
                    season=r.season_label,
                )
            except (ValueError, ZeroDivisionError, KeyError):
                continue
            mk = pred.markets
            month_preds.append(
                PredRow(
                    match_id=r.match_id,
                    league=league,
                    month=cut.strftime("%Y-%m"),
                    scheme=scheme,
                    ah_mkt=r.closing_ah_home,
                    tot_mkt=r.closing_total_line,
                    ah_pred=float(mk.main_ah.line),
                    tot_pred=float(mk.main_total.line),
                )
            )
        preds.extend(month_preds)
        sm = metrics(month_preds)
        sm.update({
            "league": league,
            "month": cut.strftime("%Y-%m"),
            "scheme": scheme,
            "train_n": len(train_rows),
            "train_s": round(train_s, 1),
        })
        month_summaries.append(sm)
        print(
            f"  {league} {cut.strftime('%Y-%m')} {scheme}: n={sm.get('n',0)} "
            f"mae_AH={sm.get('mae_AH')} mae_Tot={sm.get('mae_Tot')} ({train_s:.1f}s)",
            flush=True,
        )
    return preds, month_summaries


def pool_metrics(preds: Sequence[PredRow], scheme: str) -> Dict[str, Any]:
    xs = [p for p in preds if p.scheme == scheme]
    m = metrics(xs)
    m["scheme"] = scheme
    m["leagues"] = len({p.league for p in xs})
    return m


def better(a: Optional[Dict], b: Optional[Dict], key: str, eps: float = 0.005) -> Optional[bool]:
    """True if a better (lower) than b on key."""
    if not a or not b or a.get(key) is None or b.get(key) is None:
        return None
    if a[key] < b[key] - eps:
        return True
    if b[key] < a[key] - eps:
        return False
    return None
