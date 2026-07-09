#!/usr/bin/env python3
"""Проставить derby_weight=0 (не дерби) всем матчам в Supabase."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from supabase_history import reset_all_derby_flags  # noqa: E402
from supabase_teams import SupabaseError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сбросить флаг дерби: derby_weight=0 для всех матчей в таблице matches.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только посчитать, сколько строк ещё не derby_weight=0",
    )
    args = parser.parse_args()
    try:
        n = reset_all_derby_flags(dry_run=args.dry_run)
    except SupabaseError as exc:
        print(f"Ошибка Supabase: {exc}", file=sys.stderr)
        if exc.body:
            print(exc.body, file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"Будет обновлено матчей: {n}")
    elif n == 0:
        print("Все матчи уже derby_weight=0 (не дерби).")
    else:
        print(f"Готово: derby_weight=0 проставлено для {n} матч(ей).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
