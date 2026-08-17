"""Fill FairOddsCalc History (EPL 2024-25) from userbet.info archive + odds API.

How userbet IDs work (important):

  Archive / lineups URL:
    https://userbet.info/lineups_fixture/.../21106972/
    → attribute ``fid`` / HTML ``id_fixture`` = 21106972
    → this does **NOT** work with get_current_lineups_odds (returns {})

  Odds API (same as History «Получить данные»):
    POST /user/get_current_lineups_odds/  {id_fixture: <ps_id>}
    → ``ps_id`` from the same archive card / odds_header, e.g. 1593036427
    → site JS itself posts ps_id as id_fixture

  Archive listing:
    GET /user/arhive/?date=YYYY-MM-DD
    (cpid only highlights a chip; HTML dump is all leagues — we filter
     the ``England : Premier League`` tournament block via id_tournament /
     lgname.)

This script:
  1) loads Supabase Premier League / 2024-25 rows (teams+dates already there)
  2) scrapes archive for each distinct match_date
  3) matches home/away (+ aliases) → ps_id
  4) fetch_odds(ps_id) → optional PATCH matches
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))

import userbet_odds as ubo  # noqa: E402
from supabase_history import MatchFull, fetch_matches, patch_match  # noqa: E402

OUT = Path("/opt/cursor/artifacts/epl2425_userbet_fill")
REPO = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

EPL_LEAGUE_ID = "2613ee27-7e8d-4d18-bd5e-e3f525121848"
EPL_SEASON_ID = 3  # 2024-25
EPL_SEASON_LABEL = "2024-25"

ARCHIVE_URL = "https://userbet.info/user/arhive/"
UA = {
    "User-Agent": "Mozilla/5.0 (compatible; FairOddsCalc/epl-fill)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# FairOddsCalc History name → userbet archive name(s)
NAME_ALIASES: Dict[str, Tuple[str, ...]] = {
    "Man United": ("Manchester Utd", "Man Utd", "Manchester United"),
    "Man City": ("Manchester City", "Man. City"),
    "Nott'm Forest": ("Nottingham", "Nottingham Forest", "Nott'm Forest"),
    "Ipswich": ("Ipswich (Eng)", "Ipswich"),
    "Tottenham": ("Tottenham", "Tottenham Hotspur", "Spurs"),
    "Wolves": ("Wolves", "Wolverhampton"),
    "West Ham": ("West Ham", "West Ham United"),
    "Brighton": ("Brighton", "Brighton & Hove"),
    "Newcastle": ("Newcastle", "Newcastle Utd", "Newcastle United"),
    "Leicester": ("Leicester", "Leicester City"),
    "Crystal Palace": ("Crystal Palace",),
    "Aston Villa": ("Aston Villa",),
    "Bournemouth": ("Bournemouth",),
    "Brentford": ("Brentford",),
    "Chelsea": ("Chelsea",),
    "Everton": ("Everton",),
    "Fulham": ("Fulham",),
    "Arsenal": ("Arsenal",),
    "Liverpool": ("Liverpool",),
    "Southampton": ("Southampton",),
}


@dataclass
class ArchiveRow:
    match_date: str
    home: str
    away: str
    fid: str
    ps_id: str
    slug: str
    id_tournament: str = ""


def _norm(name: str) -> str:
    s = unescape(name or "").strip().lower()
    s = s.replace(".", " ").replace("'", "'").replace("`", "'")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*\(eng\)\s*$", "", s)
    return s


def name_keys(our_name: str) -> List[str]:
    keys = [_norm(our_name)]
    for alt in NAME_ALIASES.get(our_name, ()):
        keys.append(_norm(alt))
    # also strip trailing spaces / common suffixes
    return list(dict.fromkeys(keys))


def names_match(our: str, theirs: str) -> bool:
    t = _norm(theirs)
    return t in set(name_keys(our)) or any(t.startswith(k) or k.startswith(t) for k in name_keys(our) if len(k) >= 5)


def fetch_archive_html(day: str, *, cpid: Optional[int] = None) -> str:
    q = {"date": day}
    if cpid is not None:
        q["cpid"] = str(cpid)
    url = ARCHIVE_URL + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers=dict(UA))
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_england_premier_league(html: str, day: str) -> List[ArchiveRow]:
    """Parse England : Premier League blocks (exclude Premier League 2)."""
    rows: List[ArchiveRow] = []
    # Each tournament: <div class="chmpshipline " id_tournament="..."> ... lgname ... matches
    for m in re.finditer(
        r'<div class="chmpshipline\s*"\s*id_tournament="(\d+)">\s*'
        r'<div class="lgname">(?P<head>.*?)</div>(?P<body>.*?)(?=<div class="chmpshipline\s*"|$)',
        html,
        re.S,
    ):
        head = re.sub(r"<[^>]+>", " ", m.group("head"))
        head = re.sub(r"\s+", " ", unescape(head)).strip()
        if "England : Premier League" not in head:
            continue
        if "Premier League 2" in head:
            continue
        tid = m.group(1)
        body = m.group("body")
        for fm in re.finditer(
            r'<div[^>]*class="clearfix"[^>]*fid="(\d+)"[^>]*>(.*?)</div><!--4-->',
            body,
            re.S,
        ):
            block = fm.group(0)
            fid = fm.group(1)
            date_m = re.search(r'mt_date="([^"]+)"', block)
            slug_m = re.search(r'lineups_fixture/([^/]+)/(\d+)/', block)
            names = re.findall(r'<div class="fxlogo"[^>]*></div>\s*([^<]+)', block)
            ps = re.findall(r'ps_id="(\d+)"', block)
            if not ps or len(names) < 2:
                continue
            rows.append(
                ArchiveRow(
                    match_date=(date_m.group(1) if date_m else day),
                    home=unescape(names[0]).strip(),
                    away=unescape(names[1]).strip(),
                    fid=fid,
                    ps_id=ps[0],
                    slug=slug_m.group(1) if slug_m else "",
                    id_tournament=tid,
                )
            )
    # de-dupe by ps_id (HTML sometimes repeats the block)
    seen = set()
    uniq: List[ArchiveRow] = []
    for r in rows:
        if r.ps_id in seen:
            continue
        seen.add(r.ps_id)
        uniq.append(r)
    return uniq


def match_archive_to_supabase(
    sb: Sequence[MatchFull],
    arch: Sequence[ArchiveRow],
) -> Tuple[List[dict], List[MatchFull], List[ArchiveRow]]:
    """Return (joined rows, unmatched supabase, unmatched archive)."""
    used_ps: set = set()
    used_mid: set = set()
    joined: List[dict] = []
    for m in sb:
        day = str(m.match_date)[:10]
        cands = [a for a in arch if a.match_date == day and a.ps_id not in used_ps]
        hit = None
        for a in cands:
            if names_match(m.home_team, a.home) and names_match(m.away_team, a.away):
                hit = a
                break
        if not hit:
            continue
        used_ps.add(hit.ps_id)
        used_mid.add(m.match_id)
        joined.append(
            {
                "match_id": m.match_id,
                "match_date": day,
                "home_team": m.home_team,
                "away_team": m.away_team,
                "ps_id": hit.ps_id,
                "fid": hit.fid,
                "userbet_home": hit.home,
                "userbet_away": hit.away,
                "slug": hit.slug,
                "lineups_url": f"https://userbet.info/lineups_fixture/{hit.slug}/{hit.fid}/"
                if hit.slug
                else "",
            }
        )
    unmatched_sb = [m for m in sb if m.match_id not in used_mid]
    unmatched_ar = [a for a in arch if a.ps_id not in used_ps]
    return joined, unmatched_sb, unmatched_ar


def odds_already_filled(m: MatchFull) -> bool:
    fields = (
        m.home_odds,
        m.draw_odds,
        m.away_odds,
        m.closing_ah_home,
        m.ah_home_odds,
        m.ah_away_odds,
        m.closing_total_line,
        m.over_odds,
        m.under_odds,
    )
    return all(v is not None for v in fields)


def write_csv(path: Path, rows: Sequence[dict], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league-id", default=EPL_LEAGUE_ID)
    ap.add_argument("--season-id", type=int, default=EPL_SEASON_ID)
    ap.add_argument("--from-date", default="", help="YYYY-MM-DD inclusive")
    ap.add_argument("--to-date", default="", help="YYYY-MM-DD inclusive")
    ap.add_argument("--limit", type=int, default=0, help="max matches to fetch odds for")
    ap.add_argument("--write", action="store_true", help="PATCH Supabase matches")
    ap.add_argument("--skip-filled", action="store_true", default=True)
    ap.add_argument("--no-skip-filled", action="store_false", dest="skip_filled")
    ap.add_argument("--cpid", type=int, default=5, help="archive chip (5=Premier League highlight)")
    ap.add_argument("--sleep-archive", type=float, default=0.4)
    args = ap.parse_args()

    print(f"Loading Supabase {args.league_id} season {args.season_id}…")
    matches = fetch_matches(args.league_id, args.season_id)
    if args.from_date:
        matches = [m for m in matches if str(m.match_date)[:10] >= args.from_date]
    if args.to_date:
        matches = [m for m in matches if str(m.match_date)[:10] <= args.to_date]
    print(f"  {len(matches)} matches in window")

    days = sorted({str(m.match_date)[:10] for m in matches})
    print(f"Scraping archive for {len(days)} days (cpid={args.cpid})…")
    all_arch: List[ArchiveRow] = []
    for i, day in enumerate(days):
        try:
            html = fetch_archive_html(day, cpid=args.cpid)
            rows = parse_england_premier_league(html, day)
            print(f"  {day}: {len(rows)} EPL fixtures")
            all_arch.extend(rows)
        except Exception as exc:
            print(f"  {day}: ERROR {exc}")
        if args.sleep_archive:
            time.sleep(args.sleep_archive)

    joined, miss_sb, miss_ar = match_archive_to_supabase(matches, all_arch)
    print(f"Joined {len(joined)} / {len(matches)}; unmatched SB={len(miss_sb)} archive leftover={len(miss_ar)}")
    map_path = OUT / "epl2425_id_map.csv"
    write_csv(
        map_path,
        joined,
        [
            "match_id",
            "match_date",
            "home_team",
            "away_team",
            "ps_id",
            "fid",
            "userbet_home",
            "userbet_away",
            "slug",
            "lineups_url",
        ],
    )
    (REPO / "epl2425_id_map.csv").write_text(map_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote {map_path}")

    if miss_sb:
        miss_path = OUT / "unmatched_supabase.csv"
        write_csv(
            miss_path,
            [
                {
                    "match_id": m.match_id,
                    "match_date": str(m.match_date)[:10],
                    "home_team": m.home_team,
                    "away_team": m.away_team,
                }
                for m in miss_sb
            ],
            ["match_id", "match_date", "home_team", "away_team"],
        )
        print(f"Unmatched supabase → {miss_path}")

    # index MatchFull by id for patch
    by_id = {m.match_id: m for m in matches}
    results = []
    todo = joined
    if args.limit and args.limit > 0:
        todo = todo[: args.limit]

    for i, row in enumerate(todo):
        m = by_id[row["match_id"]]
        if args.skip_filled and odds_already_filled(m):
            results.append({**row, "status": "skipped_filled", "error": ""})
            continue
        try:
            odds = ubo.fetch_odds(row["ps_id"])
            status = "ok"
            err = ""
            if args.write:
                patch_match(m.match_id, odds, original=m)
                status = "written"
            results.append({**row, **{k: odds.get(k) for k in ubo.ODDS_PATCH_FIELDS}, "status": status, "error": err})
            print(f"[{i+1}/{len(todo)}] {row['match_date']} {row['home_team']}–{row['away_team']} ps_id={row['ps_id']} → {status} ({len(odds)} fields)")
        except Exception as exc:
            results.append({**row, "status": "error", "error": str(exc)})
            print(f"[{i+1}/{len(todo)}] ERROR {row['home_team']}–{row['away_team']}: {exc}")

    res_path = OUT / "epl2425_fill_results.csv"
    fields = list(joined[0].keys()) + sorted(ubo.ODDS_PATCH_FIELDS) + ["status", "error"] if joined else ["status"]
    # unique preserve
    seen_f = []
    for f in fields:
        if f not in seen_f:
            seen_f.append(f)
    write_csv(res_path, results, seen_f)
    (REPO / "epl2425_fill_results.csv").write_text(res_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Results → {res_path}")
    summary = {
        "joined": len(joined),
        "attempted": len(todo),
        "ok": sum(1 for r in results if r.get("status") in ("ok", "written")),
        "written": sum(1 for r in results if r.get("status") == "written"),
        "errors": sum(1 for r in results if r.get("status") == "error"),
        "unmatched_supabase": len(miss_sb),
        "write": bool(args.write),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
