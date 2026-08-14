"""Offline: pull userbet open/close odds for fixture ids from History API logs.

Sources (same site as «Получить данные»):
  POST /user/get_current_lineups_odds/     → current/close snapshot + main AH/OU lines
  POST /user/load_lineups_odds_histoty/    → ARCHIVE ODDS (typo in upstream URL)
                                            opening + latest tick as close

AH/OU history is requested on the *closing* main line (balanced O/U / AH).
If the market opened on a different line, open on that line may be missing.

Resume: writes progress after every fixture into open_close.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path("/workspace")
sys.path.insert(0, str(ROOT / "app"))
import userbet_odds as ubo  # noqa: E402

OUT = Path("/opt/cursor/artifacts/userbet_open_close")
REPO = ROOT / "experiments" / "userbet_fixture_ids"
OUT.mkdir(parents=True, exist_ok=True)
REPO.mkdir(parents=True, exist_ok=True)

CURRENT_URL = "https://userbet.info/user/get_current_lineups_odds/"
# upstream typo: histoty
HISTORY_URL = "https://userbet.info/user/load_lineups_odds_histoty/"
BOOK = 70
DEFAULT_IDS = REPO / "fixture_ids.txt"
CSV_NAME = "open_close.csv"

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/html, */*",
    "User-Agent": "Mozilla/5.0 (compatible; FairOddsCalc/open-close)",
}

_lock = threading.Lock()
_last = 0.0
MIN_INTERVAL = 0.35

FIELDS = [
    "id_fixture",
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
]


def throttle() -> None:
    global _last
    with _lock:
        now = time.monotonic()
        wait = MIN_INTERVAL - (now - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()


def post(url: str, data: Dict[str, str], *, timeout: float = 45.0) -> str:
    throttle()
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers=dict(HEADERS))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def _clean(text: str) -> str:
    return unescape(text).replace("\xa0", " ").replace("&nbsp;", " ").strip()


def parse_open_close(html: str) -> Tuple[Optional[Tuple[str, float]], Optional[Tuple[str, float]], str]:
    if "not found odds" in html.lower():
        return None, None, "not_found"
    om = re.search(
        r"OPENING ODDS.*?<td>(.*?)</td>\s*<td>\s*<b>\s*([0-9.]+)",
        html,
        re.S | re.I,
    )
    opening = (_clean(om.group(1)), float(om.group(2))) if om else None
    rows = [
        (_clean(a), float(b))
        for a, b in re.findall(
            r'<td class="dt">(.*?)</td>\s*<td class="ev">\s*([0-9.]+)',
            html,
            re.S,
        )
    ]
    closing = rows[0] if rows else None
    return opening, closing, f"n_arch={len(rows)}"


def fmt_handicap(line: float) -> str:
    if abs(line) < 1e-12:
        return "+0"
    # userbet accepts -1.25 and +0.25
    return f"{line:g}" if line < 0 else f"+{line:g}"


def hist_point(
    fid: str,
    *,
    market: int,
    label: str,
    handicap: str = "",
    total: str = "",
) -> Tuple[Optional[Tuple[str, float]], Optional[Tuple[str, float]], str]:
    html = post(
        HISTORY_URL,
        {
            "id_fixture": fid,
            "starting_at_ux": str(int(time.time()) + 10**9),
            "id_market": str(market),
            "id_bookmaker": str(BOOK),
            "handicap": handicap,
            "total": total,
            "label": label,
        },
    )
    return parse_open_close(html)


def fetch_one(fid: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {k: "" for k in FIELDS}
    out["id_fixture"] = fid
    try:
        raw = post(CURRENT_URL, {"id_fixture": fid})
        rows = json.loads(raw)
        live = ubo.parse_odds_response(rows)
    except Exception as exc:
        out["status"] = "error_live"
        out["error"] = str(exc)[:240]
        return out

    out["live_home_odds"] = live["home_odds"]
    out["live_draw_odds"] = live["draw_odds"]
    out["live_away_odds"] = live["away_odds"]
    out["live_ah_home_odds"] = live["ah_home_odds"]
    out["live_ah_away_odds"] = live["ah_away_odds"]
    out["live_over_odds"] = live["over_odds"]
    out["live_under_odds"] = live["under_odds"]
    out["closing_ah_home"] = live["closing_ah_home"]
    out["closing_total_line"] = live["closing_total_line"]

    # defaults: close from live if hist missing
    out["home_odds_close"] = live["home_odds"]
    out["draw_odds_close"] = live["draw_odds"]
    out["away_odds_close"] = live["away_odds"]
    out["ah_home_odds_close"] = live["ah_home_odds"]
    out["ah_away_odds_close"] = live["ah_away_odds"]
    out["over_odds_close"] = live["over_odds"]
    out["under_odds_close"] = live["under_odds"]

    open_dts: List[str] = []
    close_dts: List[str] = []
    errors: List[str] = []

    def take(label_key_open: str, label_key_close: str, op, cl, tag: str) -> None:
        if op:
            out[label_key_open] = op[1]
            open_dts.append(op[0])
        else:
            errors.append(f"{tag}:no_open")
        if cl:
            out[label_key_close] = cl[1]
            close_dts.append(cl[0])

    # 1X2
    for lab, k_open, k_close in (
        ("1", "home_odds_open", "home_odds_close"),
        ("x", "draw_odds_open", "draw_odds_close"),
        ("2", "away_odds_open", "away_odds_close"),
    ):
        try:
            op, cl, st = hist_point(fid, market=1, label=lab)
            take(k_open, k_close, op, cl, f"1x2:{lab}:{st}")
        except Exception as exc:
            errors.append(f"1x2:{lab}:{exc}")

    # AH on closing main line
    h = fmt_handicap(float(live["closing_ah_home"]))
    for lab, k_open, k_close in (
        ("1", "ah_home_odds_open", "ah_home_odds_close"),
        ("2", "ah_away_odds_open", "ah_away_odds_close"),
    ):
        try:
            op, cl, st = hist_point(fid, market=28, label=lab, handicap=h)
            take(k_open, k_close, op, cl, f"ah:{lab}:{st}")
        except Exception as exc:
            errors.append(f"ah:{lab}:{exc}")

    # OU on closing main line
    tot = f"{float(live['closing_total_line']):g}"
    for lab, k_open, k_close in (
        ("o", "over_odds_open", "over_odds_close"),
        ("u", "under_odds_open", "under_odds_close"),
    ):
        try:
            op, cl, st = hist_point(fid, market=12, label=lab, total=tot)
            take(k_open, k_close, op, cl, f"ou:{lab}:{st}")
        except Exception as exc:
            errors.append(f"ou:{lab}:{exc}")

    if open_dts:
        out["open_dt"] = min(open_dts)
    if close_dts:
        out["close_dt"] = max(close_dts)

    has_1x2_open = out["home_odds_open"] != "" and out["draw_odds_open"] != "" and out["away_odds_open"] != ""
    out["status"] = "ok" if has_1x2_open else "partial"
    out["error"] = "; ".join(errors[:8])
    return out


def load_done(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["id_fixture"]: r for r in csv.DictReader(f)}


def write_all(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def main() -> None:
    global MIN_INTERVAL
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=Path, default=DEFAULT_IDS)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.35)
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

    ordered: List[Dict[str, Any]] = []
    pending: List[str] = []
    for fid in ids:
        if fid in done and done[fid].get("status") in ("ok", "partial") and not args.force:
            ordered.append(done[fid])
        else:
            pending.append(fid)

    print(f"total={len(ids)} done={len(ordered)} pending={len(pending)} sleep={MIN_INTERVAL}", flush=True)

    for i, fid in enumerate(pending, 1):
        t0 = time.time()
        row = fetch_one(fid)
        ordered.append(row)
        # keep stable order by input ids
        by = {r["id_fixture"]: r for r in ordered}
        ordered_sorted = [by[x] for x in ids if x in by]
        write_all(out_csv, ordered_sorted)
        write_all(repo_csv, ordered_sorted)
        ok = sum(1 for r in ordered_sorted if r.get("status") == "ok")
        print(
            f"[{len(ordered_sorted)}/{len(ids)}] {fid} {row['status']} "
            f"o1 {row.get('home_odds_open')}→{row.get('home_odds_close')} "
            f"({time.time()-t0:.1f}s) ok={ok}",
            flush=True,
        )
        if i % 25 == 0:
            print(f"checkpoint {out_csv}", flush=True)

    print(f"wrote {out_csv} and {repo_csv}", flush=True)


if __name__ == "__main__":
    main()
