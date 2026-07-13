#!/usr/bin/env python3
"""Импорт логотипов команд из каталога в data/teams/logos.

Ожидаемые имена файлов: epl_1.png (id epl:1), la_liga_12.png и т.д.
Также принимаются имена по slug команды, если передан --registry.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import team_registry as tg  # noqa: E402

_ID_FILE_RE = re.compile(r"^([a-z0-9_]+)_(\d+)\.(png|jpg|jpeg|gif|webp)$", re.I)


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт логотипов команд")
    parser.add_argument(
        "source_dir",
        type=Path,
        help="Каталог с PNG/JPG (имена: epl_1.png или по имени команды)",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=tg.registry_path(),
        help="Путь к registry.json",
    )
    args = parser.parse_args()
    src_dir = args.source_dir.expanduser().resolve()
    if not src_dir.is_dir():
        print(f"Каталог не найден: {src_dir}", file=sys.stderr)
        return 1

    logos_dir = args.registry.parent / "logos"
    logos_dir.mkdir(parents=True, exist_ok=True)

    name_to_id = {t.name.lower(): t.id for t in tg.list_teams(path=args.registry)}
    imported = 0
    skipped = 0

    for path in sorted(src_dir.iterdir()):
        if not path.is_file():
            continue
        m = _ID_FILE_RE.match(path.name)
        team_id = None
        if m:
            team_id = f"{m.group(1)}:{m.group(2)}"
        else:
            stem = path.stem.lower().replace("_", " ").replace("-", " ")
            for nm, tid in name_to_id.items():
                if nm == stem or nm.replace("'", "") == stem.replace("'", ""):
                    team_id = tid
                    break
        if not team_id or tg.get_team(team_id, path=args.registry) is None:
            print(f"  пропуск: {path.name} (команда не найдена)")
            skipped += 1
            continue
        try:
            tg.set_team_logo(team_id, path, path=logos_dir)
            ent = tg.get_team(team_id, path=args.registry)
            print(f"  OK: {path.name} → {team_id} ({ent.name if ent else '?'})")
            imported += 1
        except ValueError as exc:
            print(f"  ошибка {path.name}: {exc}", file=sys.stderr)
            skipped += 1

    print(f"Готово: {imported} импортировано, {skipped} пропущено → {logos_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
