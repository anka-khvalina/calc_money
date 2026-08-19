#!/usr/bin/env python3
"""Вернуть derby_weight=0 (не дерби) после ошибочного fix_derby_weight_zero.

В полной модели derby_weight: 1.0 = дерби, 0.0 = не дерби.
Скрипт fix_derby_weight_zero (ветка epl-2627) массово выставил 1.0 всем «не дерби»,
из‑за чего WLS получает коллинеарный derby-столбец → «Система вырождена».
"""
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
        key = str(
            cfg.get("service_role_key") or cfg.get("anon_key") or ""
        ).strip()
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


def count_ones(url: str, key: str) -> int:
    _, _, headers = request(
        "GET",
        url,
        key,
        "/matches?select=id&derby_weight=eq.1&limit=1",
        prefer="count=exact",
    )
    cr = headers.get("Content-Range") or headers.get("content-range") or ""
    if "/" in cr:
        return int(cr.split("/")[-1])
    return 0


def fetch_one_ids(url: str, key: str, *, limit: int = 500) -> list[int]:
    q = urllib.parse.urlencode({
        "select": "id",
        "derby_weight": "eq.1",
        "order": "id.asc",
        "limit": str(limit),
    })
    _, rows, _ = request("GET", url, key, f"/matches?{q}")
    if not isinstance(rows, list):
        return []
    return [int(r["id"]) for r in rows if r.get("id") is not None]


def patch_batch(url: str, key: str, ids: list[int]) -> int:
    if not ids:
        return 0
    id_list = ",".join(str(i) for i in ids)
    _, rows, _ = request(
        "PATCH",
        url,
        key,
        f"/matches?id=in.({id_list})",
        body={"derby_weight": 0.0},
        prefer="return=representation",
    )
    return len(rows) if isinstance(rows, list) else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Только посчитать строки")
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()

    url, key = load_config()
    total = count_ones(url, key)
    print(f"Матчей с derby_weight=1.0: {total}")
    if args.dry_run or total == 0:
        return

    fixed = 0
    while True:
        ids = fetch_one_ids(url, key, limit=args.batch)
        if not ids:
            break
        n = patch_batch(url, key, ids)
        fixed += n
        print(f"  исправлено {fixed}/{total}")
        if n < args.batch:
            break
    print(f"Готово: derby_weight=0.0 для {fixed} матчей (не дерби). Дерби отметьте вручную в Истории.")


if __name__ == "__main__":
    main()
