"""Console entrypoint for Attack/Defence experiment (dry-run, read-only DB)."""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from typing import Dict, List, Optional, Sequence

from . import EXPERIMENT_NAME, __version__
from .metrics import compute_metrics
from .model import MatchRow, fit_league_poisson, predict_matches, rows_from_db_dicts
from .readonly_db import ReadOnlySupabase, ReadOnlyViolation


SELECT = (
    "match_id,match_date,league_id,league_name,season_id,season_label,"
    "home_team_id,home_team,away_team_id,away_team,"
    "is_neutral,note,active,motivation"
)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m experiments.attack_defence",
        description="Isolated Poisson Attack/Defence experiment (read-only Supabase).",
    )
    p.add_argument("--dry-run", action="store_true", default=True, help="Read-only console run (default).")
    p.add_argument("--no-dry-run", action="store_true", help="Rejected: writes are never allowed.")
    p.add_argument("--date-from", type=_parse_date, default=None, help="Prediction window start (inclusive).")
    p.add_argument("--date-to", type=_parse_date, default=None, help="Prediction window end (inclusive).")
    p.add_argument(
        "--train-date-to",
        type=_parse_date,
        default=None,
        help="Train uses matches with date < this (default: --date-from).",
    )
    p.add_argument("--league", action="append", default=None, help="Filter league name (repeatable).")
    p.add_argument("--min-team-matches", type=int, default=5)
    p.add_argument("--regularization", type=float, default=0.5)
    p.add_argument("--max-iterations", type=int, default=40)
    p.add_argument("--tolerance", type=float, default=1e-6)
    p.add_argument("--prediction-limit", type=int, default=12, help="Max prediction rows printed per league.")
    p.add_argument("--ratings-top", type=int, default=8)
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    return p


def _log(msg: str) -> None:
    print(msg, flush=True)


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    t0 = time.time()

    if args.no_dry_run:
        _log("ERROR: writes are forbidden for this experiment; omit --no-dry-run.")
        return 2

    date_from = args.date_from or date(2025, 3, 1)
    date_to = args.date_to or date(2025, 5, 25)
    train_to = args.train_date_to or date_from
    if date_from > date_to:
        _log("ERROR: date-from > date-to")
        return 2

    _log("=" * 72)
    _log(f"experiment: {EXPERIMENT_NAME}  version: {__version__}")
    _log("dry-run: true")
    _log("refit_mode: fixed_holdout (train date < train_date_to; predict [date_from, date_to])")
    _log(f"train_date_to (exclusive): {train_to}")
    _log(f"prediction_period: {date_from} .. {date_to}")
    _log(f"regularization: {args.regularization}")
    _log(f"min_team_matches: {args.min_team_matches}")
    _log(f"max_iterations: {args.max_iterations}  tolerance: {args.tolerance}")
    _log(f"leagues filter: {args.league or 'ALL'}")
    _log("=" * 72)

    client: Optional[ReadOnlySupabase] = None
    try:
        client = ReadOnlySupabase()
        info = client.info()
        _log(f"db: {info.scheme}://{info.host}{info.path}  (credentials masked)")
        # Never print anon key; show only that it loaded
        _log("secrets: not printed")
        client.verify_readonly()
        _log("read-only verification: PASS (GET-only guard + live probe)")
    except ReadOnlyViolation as exc:
        _log(f"ERROR: read-only verification failed: {exc}")
        return 3
    except Exception as exc:
        _log(f"ERROR: cannot connect (no secrets shown): {type(exc).__name__}: {exc}")
        return 3

    assert client is not None
    try:
        extra = "active=eq.true"
        raw = client.fetch_matches_paged(select=SELECT, extra_query=extra)
        # Drop motivation=false like Line tab
        before_mot = len(raw)
        raw = [r for r in raw if r.get("motivation") is not False and str(r.get("motivation")).lower() != "false"]
        mot_ex = before_mot - len(raw)

        rows, excluded = rows_from_db_dicts(raw)
        if args.league:
            allow = {x.lower() for x in args.league}
            rows = [m for m in rows if m.league_name.lower() in allow]

        reason_counts = Counter(e["reason"] for e in excluded)
        _log("-" * 72)
        _log(f"loaded_rows: {before_mot}")
        _log(f"after_motivation_filter: {before_mot - mot_ex} (excluded motivation=нет: {mot_ex})")
        _log(f"valid_with_FT: {len(rows)}")
        _log(f"excluded_parse: {len(excluded)}  reasons={dict(reason_counts)}")
        if rows:
            _log(f"FT date range: {min(m.match_date for m in rows)} .. {max(m.match_date for m in rows)}")
            _log(f"leagues: {sorted({m.league_name for m in rows})}")
            _log(f"teams: {len({m.home_team_id for m in rows} | {m.away_team_id for m in rows})}")

        by_league: Dict[str, List[MatchRow]] = defaultdict(list)
        for m in rows:
            by_league[m.league_name].append(m)

        all_preds = []
        for league_name in sorted(by_league):
            lg_rows = sorted(by_league[league_name], key=lambda m: (m.match_date, m.match_id))
            train = [m for m in lg_rows if m.match_date < train_to]
            hold = [m for m in lg_rows if date_from <= m.match_date <= date_to]
            _log("-" * 72)
            _log(f"LEAGUE: {league_name}")
            _log(f"  train_n={len(train)}  predict_n={len(hold)}")
            if len(train) < 20:
                _log("  SKIP: insufficient training matches")
                continue
            if not hold:
                _log("  SKIP: no prediction matches in window")
                continue
            # leakage guard
            if any(m.match_date >= train_to for m in train):
                _log("  ERROR: temporal leakage in train")
                return 4

            fit = fit_league_poisson(
                train,
                regularization=args.regularization,
                max_iterations=args.max_iterations,
                tolerance=args.tolerance,
                min_team_matches=args.min_team_matches,
            )
            _log(
                f"  fit: converged={fit.converged} iters={fit.iterations} loss={fit.loss:.3f} "
                f"mu={fit.mu:.4f} H={fit.home_advantage:.4f} teams={fit.n_teams}"
            )
            _log(f"  train_period: {fit.train_from} .. {fit.train_to}")
            if fit.warnings:
                _log(f"  warnings: {len(fit.warnings)} (low-n teams shrunk)")

            # ratings table (top attack / defence)
            rats = list(fit.ratings.values())
            by_att = sorted(rats, key=lambda r: r.attack, reverse=True)[: args.ratings_top]
            by_def = sorted(rats, key=lambda r: r.defence, reverse=True)[: args.ratings_top]
            low_n = sorted(rats, key=lambda r: r.n_matches)[:5]
            _log("  top attack:")
            for r in by_att:
                _log(f"    {r.team_name:<22} A={r.attack:+.3f}  Df={r.defence:+.3f}  n={r.n_matches}")
            _log("  top defence (higher = stronger defence):")
            for r in by_def:
                _log(f"    {r.team_name:<22} Df={r.defence:+.3f}  A={r.attack:+.3f}  n={r.n_matches}")
            _log("  fewest matches:")
            for r in low_n:
                _log(f"    {r.team_name:<22} n={r.n_matches} A={r.attack:+.3f} Df={r.defence:+.3f}")

            preds = predict_matches(fit, hold)
            # ensure identities
            for p in preds:
                assert abs((p.lambda_home - p.lambda_away) - p.D) < 1e-9
                assert abs((p.lambda_home + p.lambda_away) - p.S) < 1e-9
                assert p.lambda_home > 0 and p.lambda_away > 0
            met = compute_metrics(preds)
            _log(
                f"  metrics: n={met.n} MAE_D={met.mae_D:.4f} MAE_S={met.mae_S:.4f} "
                f"MAE_λh={met.mae_lambda_home:.4f} MAE_λa={met.mae_lambda_away:.4f} "
                f"bias_D={met.bias_D:+.4f} bias_S={met.bias_S:+.4f}"
            )
            _log(f"  predictions (first {min(args.prediction_limit, len(preds))}):")
            for p in preds[: args.prediction_limit]:
                m = p.match
                _log(
                    f"    {m.match_date} {m.home_team} vs {m.away_team} | "
                    f"λh={p.lambda_home:.3f} λa={p.lambda_away:.3f} D={p.D:+.3f} S={p.S:.3f} | "
                    f"FT {m.home_goals}:{m.away_goals}"
                )
            all_preds.extend(preds)

        _log("=" * 72)
        if all_preds:
            overall = compute_metrics(all_preds)
            _log(
                f"OVERALL: n={overall.n} MAE_D={overall.mae_D:.4f} MAE_S={overall.mae_S:.4f} "
                f"MAE_λh={overall.mae_lambda_home:.4f} MAE_λa={overall.mae_lambda_away:.4f}"
            )
        else:
            _log("OVERALL: no predictions produced")
        _log(f"read-only verification: {'PASS' if client.readonly_verified else 'FAIL'}")
        _log(f"database writes attempted: {client.writes_attempted}")
        _log(f"database writes completed: {client.writes_completed}")
        _log(f"execution_time_sec: {time.time() - t0:.1f}")
        _log("note: FT goals parsed from existing `note` field (no schema change / no writes)")
        _log("=" * 72)
        return 0
    finally:
        if client is not None:
            client.close()


def main(argv: Optional[Sequence[str]] = None) -> None:
    sys.exit(run(argv))


if __name__ == "__main__":
    main()
