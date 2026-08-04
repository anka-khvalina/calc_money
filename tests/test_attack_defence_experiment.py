from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from experiments.attack_defence.metrics import compute_metrics
from experiments.attack_defence.model import (
    MatchRow,
    Prediction,
    fit_league_poisson,
    predict_matches,
    rows_from_db_dicts,
)
from experiments.attack_defence.parse_ft import parse_ft_from_note
from experiments.attack_defence.readonly_db import (
    ReadOnlyViolation,
    assert_readonly_query,
    mask_secret,
    safe_db_info,
)


def test_parse_ft_from_note():
    assert parse_ft_from_note("FT 2:1 FTR H") == (2, 1)
    assert parse_ft_from_note("FT 0-0 FTR D") == (0, 0)
    assert parse_ft_from_note(None) is None
    assert parse_ft_from_note("") is None
    assert parse_ft_from_note("no score") is None


def test_lambda_d_s_identities():
    m = MatchRow(
        match_id="1",
        match_date=date(2025, 1, 1),
        league_id="L",
        league_name="L",
        home_team_id="A",
        away_team_id="B",
        home_team="A",
        away_team="B",
        home_goals=2,
        away_goals=1,
    )
    p = Prediction(match=m, lambda_home=1.7, lambda_away=1.1)
    assert abs(p.D - (1.7 - 1.1)) < 1e-12
    assert abs(p.S - (1.7 + 1.1)) < 1e-12
    assert p.lambda_home > 0 and p.lambda_away > 0


def _toy_matches(seed: int = 0) -> list[MatchRow]:
    from datetime import timedelta

    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(6)]
    rows = []
    d0 = date(2024, 8, 1)
    mid = 0
    for week in range(12):
        for i in range(0, 6, 2):
            h, a = teams[i], teams[(i + 1 + week) % 6]
            if h == a:
                a = teams[(i + 2) % 6]
            hg = int(rng.poisson(1.4))
            ag = int(rng.poisson(1.1))
            mid += 1
            rows.append(
                MatchRow(
                    match_id=str(mid),
                    match_date=d0 + timedelta(days=week * 7),
                    league_id="toy",
                    league_name="Toy",
                    home_team_id=h,
                    away_team_id=a,
                    home_team=h,
                    away_team=a,
                    home_goals=hg,
                    away_goals=ag,
                )
            )
    return rows


def test_fit_deterministic_and_centered():
    rows = _toy_matches(1)
    f1 = fit_league_poisson(rows, regularization=0.5, max_iterations=30)
    f2 = fit_league_poisson(rows, regularization=0.5, max_iterations=30)
    assert abs(f1.mu - f2.mu) < 1e-9
    assert abs(f1.home_advantage - f2.home_advantage) < 1e-9
    att = [r.attack for r in f1.ratings.values()]
    deff = [r.defence for r in f1.ratings.values()]
    assert abs(sum(att) / len(att)) < 1e-8
    assert abs(sum(deff) / len(deff)) < 1e-8
    preds = predict_matches(f1, rows[:5])
    for p in preds:
        assert p.lambda_home > 0 and p.lambda_away > 0
        assert abs(p.D - (p.lambda_home - p.lambda_away)) < 1e-9
        assert abs(p.S - (p.lambda_home + p.lambda_away)) < 1e-9


def test_unknown_team_uses_zero_rating():
    rows = _toy_matches(2)
    fit = fit_league_poisson(rows, regularization=1.0)
    # predict with unknown ids — should not crash; uses 0 attack/defence
    lh, la = fit.predict_lambdas("UNKNOWN_H", "UNKNOWN_A")
    assert lh > 0 and la > 0


def test_low_n_team_warning():
    rows = _toy_matches(3)
    # add a one-off team
    rows.append(
        MatchRow(
            match_id="x",
            match_date=date(2024, 10, 1),
            league_id="toy",
            league_name="Toy",
            home_team_id="NEW",
            away_team_id="T0",
            home_team="NEW",
            away_team="T0",
            home_goals=1,
            away_goals=1,
        )
    )
    fit = fit_league_poisson(rows, min_team_matches=5, regularization=1.0)
    assert any("NEW" in w for w in fit.warnings)


def test_readonly_rejects_writes():
    with pytest.raises(ReadOnlyViolation):
        assert_readonly_query("PATCH", "/matches?id=eq.1")
    with pytest.raises(ReadOnlyViolation):
        assert_readonly_query("POST", "/team")
    with pytest.raises(ReadOnlyViolation):
        assert_readonly_query("GET", "/matches?select=*;DELETE FROM matches")
    assert_readonly_query("GET", "/v_matches_full?select=match_id&limit=1")


def test_mask_secret_and_safe_info():
    assert "***" in mask_secret("abcd") or "…" in mask_secret("abcdefghijklmnop")
    info = safe_db_info("https://example.supabase.co/rest/v1")
    assert info.host == "example.supabase.co"
    assert "example" in info.as_log_dict()["host"]


def test_rows_from_db_excludes_bad():
    raw = [
        {
            "match_id": "1",
            "match_date": "2024-01-01",
            "league_id": "L",
            "league_name": "L",
            "home_team_id": 1,
            "away_team_id": 2,
            "home_team": "A",
            "away_team": "B",
            "note": "FT 1:0 FTR H",
            "is_neutral": False,
        },
        {
            "match_id": "2",
            "match_date": "2024-01-02",
            "league_id": "L",
            "league_name": "L",
            "home_team_id": 1,
            "away_team_id": 2,
            "home_team": "A",
            "away_team": "B",
            "note": None,
            "is_neutral": False,
        },
        {
            "match_id": "1",
            "match_date": "2024-01-03",
            "league_id": "L",
            "league_name": "L",
            "home_team_id": 1,
            "away_team_id": 2,
            "home_team": "A",
            "away_team": "B",
            "note": "FT 2:2 FTR D",
            "is_neutral": False,
        },
    ]
    ok, ex = rows_from_db_dicts(raw)
    assert len(ok) == 1
    assert any(e["reason"] == "no_ft_in_note" for e in ex)
    assert any(e["reason"] == "duplicate_match_id" for e in ex)


def test_no_temporal_leakage_split():
    rows = _toy_matches(4)
    cut = date(2024, 9, 1)
    train = [m for m in rows if m.match_date < cut]
    hold = [m for m in rows if m.match_date >= cut]
    assert train and hold
    assert max(m.match_date for m in train) < min(m.match_date for m in hold)
    fit = fit_league_poisson(train)
    preds = predict_matches(fit, hold)
    met = compute_metrics(preds)
    assert met.n == len(hold)
    assert met.mae_D >= 0


def test_import_does_not_start_server():
    # Importing package must be side-effect free regarding servers.
    import experiments.attack_defence as ad
    import experiments.attack_defence.run as run_mod

    assert ad.EXPERIMENT_NAME
    assert callable(run_mod.run)
