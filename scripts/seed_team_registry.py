#!/usr/bin/env python3
"""Загрузить стартовый справочник команд по лигам."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from team_registry import DEFAULT_REGISTRY_PATH, save_registry  # noqa: E402

LEAGUE_TEAMS: dict[str, list[str]] = {
    "bundesliga": [
        "Augsburg",
        "Bayern Munich",
        "Dortmund",
        "Ein Frankfurt",
        "FC Koln",
        "Freiburg",
        "Hamburg",
        "Heidenheim",
        "Hoffenheim",
        "Leverkusen",
        "M'gladbach",
        "Mainz",
        "RB Leipzig",
        "St Pauli",
        "Stuttgart",
        "Union Berlin",
        "Werder Bremen",
        "Wolfsburg",
    ],
    "epl": [
        "Arsenal",
        "Aston Villa",
        "Bournemouth",
        "Brentford",
        "Brighton",
        "Burnley",
        "Chelsea",
        "Crystal Palace",
        "Everton",
        "Fulham",
        "Leeds",
        "Liverpool",
        "Man City",
        "Man United",
        "Newcastle",
        "Nott'm Forest",
        "Sunderland",
        "Tottenham",
        "West Ham",
        "Wolves",
    ],
    "la_liga": [
        "Alaves",
        "Ath Bilbao",
        "Ath Madrid",
        "Barcelona",
        "Betis",
        "Celta",
        "Elche",
        "Espanol",
        "Getafe",
        "Girona",
        "Levante",
        "Mallorca",
        "Osasuna",
        "Oviedo",
        "Real Madrid",
        "Sevilla",
        "Sociedad",
        "Valencia",
        "Vallecano",
        "Villarreal",
    ],
    "ligue_1": [
        "Angers",
        "Auxerre",
        "Brest",
        "Le Havre",
        "Lens",
        "Lille",
        "Lorient",
        "Lyon",
        "Marseille",
        "Metz",
        "Monaco",
        "Nantes",
        "Nice",
        "Paris FC",
        "Paris SG",
        "Rennes",
        "Strasbourg",
        "Toulouse",
    ],
    "serie_a": [
        "Atalanta",
        "Bologna",
        "Cagliari",
        "Como",
        "Cremonese",
        "Fiorentina",
        "Genoa",
        "Inter",
        "Juventus",
        "Lazio",
        "Lecce",
        "Milan",
        "Napoli",
        "Parma",
        "Pisa",
        "Roma",
        "Sassuolo",
        "Torino",
        "Udinese",
        "Verona",
    ],
}


def build_registry() -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    teams = []
    next_seq: dict[str, int] = {}
    for league, names in LEAGUE_TEAMS.items():
        next_seq[league] = len(names) + 1
        for i, name in enumerate(names, start=1):
            teams.append(
                {
                    "id": f"{league}:{i}",
                    "league": league,
                    "name": name,
                    "created_at": now,
                    "updated_at": now,
                }
            )
    return {"version": 1, "next_seq": next_seq, "teams": teams}


def main() -> None:
    data = build_registry()
    path = save_registry(data, DEFAULT_REGISTRY_PATH)
    total = len(data["teams"])
    print(f"Saved {total} teams → {path}")
    for league, names in LEAGUE_TEAMS.items():
        print(f"  {league}: {len(names)}")


if __name__ == "__main__":
    main()
