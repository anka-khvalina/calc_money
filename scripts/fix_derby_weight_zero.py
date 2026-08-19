#!/usr/bin/env python3
"""Исправить derby_weight=0 → 1.0 (legacy: импорт календаря писал 0 для «не дерби»)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config() -> tuple[str, str]:
    for rel in ("config/supabase.json", "web/supabase.config.json"):
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        url = str(cfg.get("rest_url", "")).rstrip("/")
        key = str(cfg.get("anon_key") or cfg.get("service_role_key") or "").strip()
        if url and key:
            return url, key
    raise SystemExit("Не найден config/supabase.json с rest_url и ключом")


def request(method: str, url: str, key: str, path: str, *, body=None, prefer: str | None = None):
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None), resp.headers
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        raise SystemExit(f"HTTP {e.code}: {body_text[:500]}") from e


def count_zero(url: str, key: str) -> int:
    _, _, headers = request(
        "GET", url, key,
        "/matches?select=id&derby_weight=eq.0&limit=1",
        prefer="count=exact",
    )
    cr = headers.get("Content-Range") or headers.get("content-range") or ""
    # 0-0/5989
    if "/" in cr:
        return int(cr.split("/")[-1])
    return 0


def patch_batch(url: str, key: str, ids: list[int]) -> int:
    if not ids:
        return 0
    id_list = ",".join(str(i) for i in ids)
    status, rows, _ = request(
        "PATCH", url, key,
        f"/matches?id=in.({id_list})",
        body={"derby_weight": 1.0},
        prefer="return=representation",
    )
    return len(rows) if isinstance(rows, list) else 0


def fetch_zero_ids(url: str, key: str, *, limit: int = 500, offset: int = 0) -> list[int]:
    q = urllib.parse.urlencode({
        "select": "id",
        "derby_weight": "eq.0",
        "order": "id.asc",
        "limit": str(limit),
        "offset": str(offset),
    })
    _, rows, _ = request("GET", url, key, f"/matches?{q}")
    if not isinstance(rows, list):
        return []
    return [int(r["id"]) for r in rows if r.get("id") is not None]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Только посчитать строки")
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()

    url, key = load_config()
    total = count_zero(url, key)
    print(f"Матчей с derby_weight=0: {total}")
    if args.dry_run or total == 0:
        return

    fixed = 0
    offset = 0
    while True:
        ids = fetch_zero_ids(url, key, limit=args.batch, offset=0)
        if not ids:
            break
        n = patch_batch(url, key, ids)
        fixed += n
        print(f"  исправлено {fixed}/{total}")
        if n < args.batch:
            break
        offset += args.batch
    print(f"Готово: derby_weight=1.0 для {fixed} матчей")


if __name__ == "__main__":
    main()
