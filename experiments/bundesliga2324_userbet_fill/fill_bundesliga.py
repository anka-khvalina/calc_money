"""Fill FairOddsCalc History (Bundesliga) from userbet.info archive + odds API.

Same pattern as experiments/epl2425_userbet_fill/fill_epl2425.py:

  Archive listing: GET /user/arhive/?date=YYYY-MM-DD&cpid=2
  (cpid=2 highlights Bundesliga; HTML lists all leagues — we parse
   ``Germany : Bundesliga`` and skip ``2. Bundesliga``.)

  Odds API uses archive ``ps_id`` (not URL ``fid``).

Default: season_id=14 (2023-24). Also works for 2024-25 (season_id=1).
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
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))

import userbet_odds as ubo  # noqa: E402
from supabase_history import MatchFull, fetch_matches, patch_match  # noqa: E402

ART_ROOT = Path("/opt/cursor/artifacts")
REPO = Path(__file__).resolve().parent

BL_LEAGUE_ID = "0e928134-ae08-48fd-8d2c-3539a994b054"
BL_SEASON_ID = 14  # 2023-24
SEASON_LABELS = {14: "2023-24", 1: "2024-25", 2: "2025-26", 22: "2026-27"}

ARCHIVE_URL = "https://userbet.info/user/arhive/"
HISTORY_URL = "https://userbet.info/user/load_lineups_odds_histoty/"  # upstream typo
CURRENT_URL = "https://userbet.info/user/get_current_lineups_odds/"
BOOK = 70
UA = {
    "User-Agent": "Mozilla/5.0 (compatible; FairOddsCalc/bundesliga-fill)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
POST_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/html, */*",
    "User-Agent": UA["User-Agent"],
}
_COMMON_TOTALS = [2.5, 2.25, 2.75, 3.0, 2.0, 3.25, 1.5, 3.5, 2.75, 4.0, 1.75]
_COMMON_AH = [0.0, -0.25, 0.25, -0.5, 0.5, -0.75, 0.75, -1.0, 1.0, -1.25, 1.25, -1.5, 1.5, -1.75, 1.75, -2.0, 2.0, -2.25, 2.25]

# FairOddsCalc History name → userbet archive name(s)
NAME_ALIASES: Dict[str, Tuple[str, ...]] = {
    "Augsburg": ("Augsburg",),
    "Bayern Munich": ("Bayern Munich", "Bayern", "Bayern (Ger)", "Bayern Munich (Ger)"),
    "Bochum": ("Bochum", "VfL Bochum"),
    "Darmstadt": ("Darmstadt", "Darmstadt 98"),
    "Dortmund": ("Dortmund", "Dortmund (Ger)", "Borussia Dortmund"),
    "Ein Frankfurt": ("Ein Frankfurt", "Eintracht Frankfurt", "Frankfurt"),
    "FC Koln": ("FC Koln", "Koln", "Cologne", "1. FC Koln"),
    "Freiburg": ("Freiburg", "SC Freiburg"),
    "Heidenheim": ("Heidenheim", "Heidenheim (Ger)"),
    "Hoffenheim": ("Hoffenheim",),
    "Leverkusen": ("Leverkusen", "Bayer Leverkusen", "Bayer Leverkusen (Ger)"),
    "M'gladbach": (
        "M'gladbach",
        "B. Monchengladbach",
        "Borussia Monchengladbach",
        "Monchengladbach",
        "Gladbach",
    ),
    "Mainz": ("Mainz", "Mainz 05"),
    "RB Leipzig": ("RB Leipzig", "Leipzig"),
    "Stuttgart": ("Stuttgart", "VfB Stuttgart"),
    "Union Berlin": ("Union Berlin", "1. FC Union Berlin"),
    "Werder Bremen": ("Werder Bremen", "Bremen"),
    "Wolfsburg": ("Wolfsburg", "VfL Wolfsburg"),
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
    s = re.sub(r"\s*\((eng|ger)\)\s*$", "", s)
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


def parse_germany_bundesliga(html: str, day: str) -> List[ArchiveRow]:
    """Parse Germany : Bundesliga blocks (exclude 2. Bundesliga / 3. Liga)."""
    rows: List[ArchiveRow] = []
    for m in re.finditer(
        r'<div class="chmpshipline\s*"\s*id_tournament="(\d+)">\s*'
        r'<div class="lgname">(?P<head>.*?)</div>(?P<body>.*?)(?=<div class="chmpshipline\s*"|$)',
        html,
        re.S,
    ):
        head = re.sub(r"<[^>]+>", " ", m.group("head"))
        head = re.sub(r"\s+", " ", unescape(head)).strip()
        if "Germany : Bundesliga" not in head:
            continue
        if "2. Bundesliga" in head or "3. Liga" in head:
            continue
        tid = m.group(1)
        body = m.group("body")
        for fm in re.finditer(
            r'<div[^>]*class="clearfix"[^>]*fid="(\d+)"[^>]*>',
            body,
            re.S,
        ):
            # take from this clearfix until next clearfix or end of body chunk
            start = fm.start()
            nxt = re.search(r'<div[^>]*class="clearfix"', body[fm.end() :])
            end = fm.end() + (nxt.start() if nxt else len(body) - fm.end())
            block = body[start:end]
            fid = fm.group(1)
            date_m = re.search(r'mt_date="([^"]+)"', block)
            slug_m = re.search(r'lineups_fixture/([^/]+)/(\d+)/', block)
            names = re.findall(r'<div class="fxlogo"[^>]*></div>\s*([^<]+)', block)
            ps = [p for p in re.findall(r'ps_id="(\d+)"', block) if p and p != "0"]
            if len(names) < 2:
                continue
            rows.append(
                ArchiveRow(
                    match_date=(date_m.group(1) if date_m else day),
                    home=unescape(names[0]).strip(),
                    away=unescape(names[1]).strip(),
                    fid=fid,
                    ps_id=ps[0] if ps else "",
                    slug=slug_m.group(1) if slug_m else "",
                    id_tournament=tid,
                )
            )
    seen = set()
    uniq: List[ArchiveRow] = []
    for r in rows:
        key = r.ps_id or f"{r.fid}:{r.home}:{r.away}"
        if key in seen:
            continue
        seen.add(key)
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


_lock = None
_last_post = 0.0
MIN_POST_INTERVAL = 0.35


def _post_form(url: str, data: Dict[str, str], *, timeout: float = 45.0) -> str:
    global _last_post
    now = time.monotonic()
    wait = MIN_POST_INTERVAL - (now - _last_post)
    if wait > 0:
        time.sleep(wait)
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers=dict(POST_HEADERS))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    finally:
        _last_post = time.monotonic()


def _odds_ok(val: Any) -> bool:
    try:
        return float(val) > 1.0
    except (TypeError, ValueError):
        return False


def _fmt_ah(line: float) -> str:
    if abs(line) < 1e-12:
        return "+0"
    return f"{line:g}" if line < 0 else f"+{line:g}"


def _hist_close(ps_id: str, *, market: int, label: str, handicap: str = "", total: str = "") -> Optional[float]:
    html = _post_form(
        HISTORY_URL,
        {
            "id_fixture": ps_id,
            "starting_at_ux": str(int(time.time()) + 10**9),
            "id_market": str(market),
            "id_bookmaker": str(BOOK),
            "handicap": handicap,
            "total": total,
            "label": label,
        },
    )
    if "not found odds" in html.lower():
        return None
    rows = re.findall(r'<td class="ev">\s*([0-9.]+)', html)
    if not rows:
        return None
    try:
        v = float(rows[0])
    except ValueError:
        return None
    return v if v > 1.0 else None


def _parse_current_loose(ps_id: str) -> Dict[str, float]:
    """Best-effort current snapshot; older seasons often have 1X2=0 or missing OU."""
    raw = _post_form(CURRENT_URL, {"id_fixture": ps_id})
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(rows, list) or not rows:
        return {}
    filtered = ubo.filter_by_bookmaker(rows)
    by_m: Dict[int, List[dict]] = {}
    for row in filtered:
        try:
            by_m.setdefault(int(row["m"]), []).append(dict(row))
        except (KeyError, TypeError, ValueError):
            continue
    out: Dict[str, float] = {}
    # 1X2 is usually m=1
    for m_val, group in by_m.items():
        labels = {str(r.get("l", "")).strip().lower() for r in group}
        if {"1", "x", "2"} <= labels:
            try:
                out.update(ubo._parse_1x2(group))
            except Exception:
                pass
        if {"o", "u"} <= labels:
            try:
                out.update(ubo._parse_total(group))
            except Exception:
                pass
        if {"1", "2"} <= labels and any(str(r.get("h", "")).strip() for r in group):
            try:
                out.update(ubo._parse_ah(group))
            except Exception:
                pass
    return {k: v for k, v in out.items() if k in ubo.ODDS_PATCH_FIELDS}


def fetch_closing_odds(ps_id: str) -> Dict[str, float]:
    """Closing line for History: current snapshot, then archive history for zeros/missing."""
    if not ps_id or ps_id == "0":
        raise RuntimeError("no ps_id")
    out = _parse_current_loose(ps_id)
    # 1X2 from history if current is empty/zero
    if not all(_odds_ok(out.get(k)) for k in ("home_odds", "draw_odds", "away_odds")):
        h1 = _hist_close(ps_id, market=1, label="1")
        hx = _hist_close(ps_id, market=1, label="x")
        h2 = _hist_close(ps_id, market=1, label="2")
        if h1 and hx and h2:
            out["home_odds"] = round(h1, 2)
            out["draw_odds"] = round(hx, 2)
            out["away_odds"] = round(h2, 2)
    # OU: probe common lines if missing
    if not all(_odds_ok(out.get(k)) for k in ("over_odds", "under_odds")) or out.get("closing_total_line") is None:
        best = None
        best_diff = 1e9
        for tot in _COMMON_TOTALS:
            t = f"{tot:g}"
            o = _hist_close(ps_id, market=12, label="o", total=t)
            u = _hist_close(ps_id, market=12, label="u", total=t)
            if o and u:
                diff = abs(o - u)
                if diff < best_diff:
                    best_diff = diff
                    best = (float(tot), round(o, 2), round(u, 2))
        if best:
            out["closing_total_line"] = best[0]
            out["over_odds"] = best[1]
            out["under_odds"] = best[2]
    # AH: probe if missing / invalid prices
    if not all(_odds_ok(out.get(k)) for k in ("ah_home_odds", "ah_away_odds")) or out.get("closing_ah_home") is None:
        best = None
        best_diff = 1e9
        for ah in _COMMON_AH:
            hcap = _fmt_ah(ah)
            a1 = _hist_close(ps_id, market=28, label="1", handicap=hcap)
            a2 = _hist_close(ps_id, market=28, label="2", handicap=hcap)
            if a1 and a2:
                diff = abs(a1 - a2)
                if diff < best_diff:
                    best_diff = diff
                    best = (float(ah), round(a1, 2), round(a2, 2))
        if best:
            out["closing_ah_home"] = best[0]
            out["ah_home_odds"] = best[1]
            out["ah_away_odds"] = best[2]
    needed = (
        "home_odds",
        "draw_odds",
        "away_odds",
        "closing_ah_home",
        "ah_home_odds",
        "ah_away_odds",
        "closing_total_line",
        "over_odds",
        "under_odds",
    )
    missing = [k for k in needed if k not in out or (k in ubo.ODDS_PATCH_FIELDS and k.endswith("odds") and not _odds_ok(out.get(k)))]
    # closing lines can be 0 / negative
    line_ok = out.get("closing_ah_home") is not None and out.get("closing_total_line") is not None
    price_ok = all(_odds_ok(out.get(k)) for k in (
        "home_odds", "draw_odds", "away_odds", "ah_home_odds", "ah_away_odds", "over_odds", "under_odds"
    ))
    if not (line_ok and price_ok):
        raise RuntimeError(f"incomplete closing odds missing={missing} got={ {k: out.get(k) for k in needed} }")
    return {k: out[k] for k in needed}


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
    ap.add_argument("--league-id", default=BL_LEAGUE_ID)
    ap.add_argument("--season-id", type=int, default=BL_SEASON_ID)
    ap.add_argument("--from-date", default="", help="YYYY-MM-DD inclusive")
    ap.add_argument("--to-date", default="", help="YYYY-MM-DD inclusive")
    ap.add_argument("--limit", type=int, default=0, help="max matches to fetch odds for")
    ap.add_argument("--write", action="store_true", help="PATCH Supabase matches")
    ap.add_argument("--skip-filled", action="store_true", default=True)
    ap.add_argument("--no-skip-filled", action="store_false", dest="skip_filled")
    ap.add_argument("--cpid", type=int, default=2, help="archive chip (2=Bundesliga highlight)")
    ap.add_argument("--sleep-archive", type=float, default=0.4)
    ap.add_argument("--prefix", default="", help="artifact filename prefix (default bl{label})")
    args = ap.parse_args()

    label = SEASON_LABELS.get(args.season_id, str(args.season_id))
    prefix = args.prefix or f"bl{label.replace('-', '')}"
    out_dir = ART_ROOT / f"{prefix}_userbet_fill"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading Supabase {args.league_id} season {args.season_id} ({label})…")
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
            rows = parse_germany_bundesliga(html, day)
            print(f"  {day}: {len(rows)} Bundesliga fixtures")
            all_arch.extend(rows)
        except Exception as exc:
            print(f"  {day}: ERROR {exc}")
        if args.sleep_archive:
            time.sleep(args.sleep_archive)

    joined, miss_sb, miss_ar = match_archive_to_supabase(matches, all_arch)
    print(f"Joined {len(joined)} / {len(matches)}; unmatched SB={len(miss_sb)} archive leftover={len(miss_ar)}")
    map_path = out_dir / f"{prefix}_id_map.csv"
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
    (REPO / f"{prefix}_id_map.csv").write_text(map_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote {map_path}")

    if miss_sb:
        miss_path = out_dir / f"{prefix}_unmatched_supabase.csv"
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
        if not row.get("ps_id"):
            results.append({**row, "status": "error", "error": "no ps_id (archive graph id=0)"})
            print(f"[{i+1}/{len(todo)}] ERROR {row['home_team']}–{row['away_team']}: no ps_id")
            continue
        try:
            odds = fetch_closing_odds(row["ps_id"])
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

    res_path = out_dir / f"{prefix}_fill_results.csv"
    fields = list(joined[0].keys()) + sorted(ubo.ODDS_PATCH_FIELDS) + ["status", "error"] if joined else ["status"]
    # unique preserve
    seen_f = []
    for f in fields:
        if f not in seen_f:
            seen_f.append(f)
    write_csv(res_path, results, seen_f)
    (REPO / f"{prefix}_fill_results.csv").write_text(res_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Results → {res_path}")
    summary = {
        "season_id": args.season_id,
        "season_label": label,
        "joined": len(joined),
        "attempted": len(todo),
        "ok": sum(1 for r in results if r.get("status") in ("ok", "written")),
        "written": sum(1 for r in results if r.get("status") == "written"),
        "skipped_filled": sum(1 for r in results if r.get("status") == "skipped_filled"),
        "errors": sum(1 for r in results if r.get("status") == "error"),
        "unmatched_supabase": len(miss_sb),
        "write": bool(args.write),
    }
    (out_dir / f"{prefix}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (REPO / f"{prefix}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
