"""Resolve userbet team names / kickoff from id_fixture (ps_id).

Uses:
  GET /user/graph_lineups?ps_id={id}&id_market=1&id_bookmaker=70&label=1

Fixture cell looks like:  ``14.08 22:30 : Atletico Madrid - Granada CF``
"""

from __future__ import annotations

import argparse
import csv
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path("/workspace")
REPO = ROOT / "experiments" / "userbet_fixture_ids"
OUT = Path("/opt/cursor/artifacts/userbet_open_close")
DEFAULT_IDS = REPO / "fixture_ids.txt"
CSV_NAME = "fixture_names.csv"

FIELDS = ["id_fixture", "status", "kickoff", "home", "away", "error"]
_FIXTURE_RE = re.compile(
    r"<b>Fixture</b></td><td>\s*(\d{2}\.\d{2}\s+\d{2}:\d{2})\s*:\s*([^<]+?)\s+-\s+([^<]+?)\s*<",
    re.S,
)

_lock = threading.Lock()
_last = 0.0
MIN_INTERVAL = 0.4


def throttle() -> None:
    global _last
    with _lock:
        now = time.monotonic()
        wait = MIN_INTERVAL - (now - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()


def fetch_name(fid: str, *, timeout: float = 45.0) -> Dict[str, str]:
    throttle()
    url = (
        "https://userbet.info/user/graph_lineups"
        f"?ps_id={fid}&id_market=1&id_bookmaker=70&handicap=&total=&label=1"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; FairOddsCalc/names)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {
            "id_fixture": fid,
            "status": "error",
            "kickoff": "",
            "home": "",
            "away": "",
            "error": str(exc)[:200],
        }
    m = _FIXTURE_RE.search(html)
    if not m:
        err = "parse_fail"
        if "deleted" in html.lower():
            err = "deleted"
        elif "PHP Error" in html:
            err = "php_error"
        return {
            "id_fixture": fid,
            "status": "error",
            "kickoff": "",
            "home": "",
            "away": "",
            "error": err,
        }
    return {
        "id_fixture": fid,
        "status": "ok",
        "kickoff": m.group(1).strip(),
        "home": m.group(2).strip(),
        "away": m.group(3).strip(),
        "error": "",
    }


def load_done(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["id_fixture"]: r for r in csv.DictReader(f)}


def write_all(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    global MIN_INTERVAL
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=Path, default=DEFAULT_IDS)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    MIN_INTERVAL = float(args.sleep)

    ids = [ln.strip() for ln in args.ids.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if args.limit:
        ids = ids[: args.limit]

    out_csv = OUT / CSV_NAME
    repo_csv = REPO / CSV_NAME
    done = {} if args.force else load_done(out_csv)
    if not done and repo_csv.exists():
        done = load_done(repo_csv)

    ordered: List[Dict[str, str]] = []
    pending: List[str] = []
    for fid in ids:
        if fid in done and done[fid].get("status") == "ok" and not args.force:
            ordered.append(done[fid])
        else:
            pending.append(fid)

    print(f"total={len(ids)} done={len(ordered)} pending={len(pending)}", flush=True)
    by = {r["id_fixture"]: r for r in ordered}
    for i, fid in enumerate(pending, 1):
        row = fetch_name(fid)
        by[fid] = row
        ordered_sorted = [by[x] for x in ids if x in by]
        write_all(out_csv, ordered_sorted)
        write_all(repo_csv, ordered_sorted)
        print(
            f"[{len(ordered_sorted)}/{len(ids)}] {fid} {row['status']} "
            f"{row.get('kickoff','')} {row.get('home','')} — {row.get('away','')}",
            flush=True,
        )
        if i % 50 == 0:
            print(f"checkpoint {out_csv}", flush=True)
    print(f"wrote {out_csv}", flush=True)


if __name__ == "__main__":
    main()
