"""Пути к данным: разработка и собранный exe."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def app_install_dir() -> Path:
    """Каталог установки (рядом с exe) или корень репозитория."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_data_dir() -> Path | None:
    """Встроенные данные внутри exe (_MEIPASS/data)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "data"  # type: ignore[attr-defined]
    return app_install_dir() / "data"


def user_data_dir() -> Path:
    return app_install_dir() / "data"


def history_data_root() -> Path:
    return user_data_dir() / "history"


def teams_registry_path() -> Path:
    return user_data_dir() / "teams" / "registry.json"


def examples_dir() -> Path:
    if getattr(sys, "frozen", False):
        p = Path(sys._MEIPASS) / "docs" / "examples"  # type: ignore[attr-defined]
        if p.exists():
            return p
    return Path(__file__).resolve().parent.parent / "docs" / "examples"


def ensure_user_data() -> None:
    """При первом запуске exe скопировать seed-данные рядом с программой."""
    if not getattr(sys, "frozen", False):
        return
    bundled = bundled_data_dir()
    if bundled is None or not bundled.exists():
        return
    target = user_data_dir()
    for name in ("teams", "history"):
        src = bundled / name
        dst = target / name
        if not src.exists():
            continue
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
