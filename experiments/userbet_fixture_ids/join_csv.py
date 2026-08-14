"""Join fixture_names.csv + open_close.csv → fixtures_open_close.csv."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

REPO = Path(__file__).resolve().parent
ART = Path("/opt/cursor/artifacts/userbet_open_close")

COMBINED_FIELDS = [
    "id_fixture",
    "kickoff",
    "home",
    "away",
    "status",
    "error",
    "open_dt",
    "close_dt",
    "home_odds_open",
    "draw_odds_open",
    "away_odds_open",
    "home_odds_close",
    "draw_odds_close",
    "away_odds_close",
    "closing_ah_home",
    "ah_home_odds_open",
    "ah_away_odds_open",
    "ah_home_odds_close",
    "ah_away_odds_close",
    "closing_total_line",
    "over_odds_open",
    "under_odds_open",
    "over_odds_close",
    "under_odds_close",
    "live_home_odds",
    "live_draw_odds",
    "live_away_odds",
    "live_ah_home_odds",
    "live_ah_away_odds",
    "live_over_odds",
    "live_under_odds",
    "name_status",
    "name_error",
]


def join(names_path: Path, odds_path: Path, out_path: Path) -> int:
    names = {
        row["id_fixture"]: row
        for row in csv.DictReader(names_path.open(newline="", encoding="utf-8"))
    }
    odds_rows = list(csv.DictReader(odds_path.open(newline="", encoding="utf-8")))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COMBINED_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in odds_rows:
            nm = names.get(row["id_fixture"]) or {}
            out = dict(row)
            out["kickoff"] = nm.get("kickoff") or ""
            out["home"] = nm.get("home") or ""
            out["away"] = nm.get("away") or ""
            out["name_status"] = nm.get("status") or ("missing" if not nm else "")
            out["name_error"] = nm.get("error") or ""
            w.writerow(out)
    return len(odds_rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--names", type=Path, default=REPO / "fixture_names.csv")
    ap.add_argument("--odds", type=Path, default=REPO / "open_close.csv")
    ap.add_argument("--out", type=Path, default=REPO / "fixtures_open_close.csv")
    args = ap.parse_args()
    n = join(args.names, args.odds, args.out)
    print(f"wrote {args.out} ({n} rows)")
    if ART.exists():
        art_out = ART / "fixtures_open_close.csv"
        join(args.names, args.odds, art_out)
        print(f"wrote {art_out}")


if __name__ == "__main__":
    main()
