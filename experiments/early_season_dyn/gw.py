"""Gameweek labels and season-start evaluation universe."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Sequence, Tuple

from experiments.market_weights.data import Row


# Seasons with at least one prior season of closing history in DB.
EVAL_SEASONS: List[Tuple[str, str]] = [
    ("La Liga", "2024-25"),
    ("La Liga", "2025-26"),
    ("Serie A", "2024-25"),
    ("Serie A", "2025-26"),
    ("Ligue 1", "2024-25"),
    ("Ligue 1", "2025-26"),
]


@dataclass(frozen=True)
class TaggedRow:
    row: Row
    gw: int  # 1-based team-match GW (max of home/away match index in season)


def assign_gameweeks(season_rows: Sequence[Row]) -> List[TaggedRow]:
    """GW = max(home_team_match_n, away_team_match_n) within the season."""
    rs = sorted(season_rows, key=lambda r: (r.match_date, r.match_id))
    played: Dict[str, int] = defaultdict(int)
    out: List[TaggedRow] = []
    for r in rs:
        played[r.home_team_id] += 1
        played[r.away_team_id] += 1
        gw = max(played[r.home_team_id], played[r.away_team_id])
        out.append(TaggedRow(row=r, gw=gw))
    return out


def gw_bucket(gw: int) -> str:
    if gw <= 0:
        return "other"
    if gw <= 4:
        return f"GW{gw}"
    if gw <= 8:
        return "GW5-8"
    return "GW9+"


def policy_scale(arm: str, gw: int) -> float:
    """
    User policy:
      BASE: scale 1 always
      A/B/C: dampen on GW1–4, full current after GW4
    Diagnostic arms use fixed scale in every bucket (see DIAG_ARMS).
    """
    if arm == "BASE":
        return 1.0
    if gw >= 5:
        return 1.0
    if arm == "A":
        return 0.0
    if arm == "B":
        return 0.25
    if arm == "C":
        return 0.50
    raise KeyError(arm)


POLICY_ARMS = ("BASE", "A", "B", "C")
# Fixed-scale diagnostic to find when dynamic becomes useful again.
DIAG_SCALES = (
    ("scale_1.00", 1.0),
    ("scale_0.50", 0.50),
    ("scale_0.25", 0.25),
    ("scale_0.00", 0.0),
)


def season_sort_key(label: str) -> Tuple[int, str]:
    # "2024-25" → 2024
    try:
        return (int(str(label)[:4]), str(label))
    except ValueError:
        return (0, str(label))


def prior_and_hold(
    all_rows: Sequence[Row],
    league: str,
    season: str,
    gw: int,
) -> Tuple[List[Row], List[TaggedRow]]:
    """Train = all earlier seasons in league + current season matches with GW < gw.
    Hold = current season matches with this exact GW (for GW1–4) .
    """
    tagged = assign_gameweeks([r for r in all_rows if r.league_name == league and r.season_label == season])
    hold = [t for t in tagged if t.gw == gw]
    prior_seasons = [
        r for r in all_rows
        if r.league_name == league
        and r.season_label
        and season_sort_key(r.season_label) < season_sort_key(season)
    ]
    earlier_gw = [t.row for t in tagged if t.gw < gw]
    train = list(prior_seasons) + earlier_gw
    return train, hold


def prior_and_hold_bucket(
    all_rows: Sequence[Row],
    league: str,
    season: str,
    *,
    gw_from: int,
    gw_to: int,
) -> Tuple[List[Row], List[TaggedRow]]:
    """Train before gw_from; hold gw_from..gw_to inclusive."""
    tagged = assign_gameweeks([r for r in all_rows if r.league_name == league and r.season_label == season])
    hold = [t for t in tagged if gw_from <= t.gw <= gw_to]
    prior_seasons = [
        r for r in all_rows
        if r.league_name == league
        and r.season_label
        and season_sort_key(r.season_label) < season_sort_key(season)
    ]
    earlier_gw = [t.row for t in tagged if t.gw < gw_from]
    train = list(prior_seasons) + earlier_gw
    return train, hold
