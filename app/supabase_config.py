"""Загрузка конфигурации Supabase (anon key, REST URL)."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from runtime_paths import app_install_dir


class SupabaseConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupabaseSettings:
    rest_url: str
    anon_key: str


def _config_paths() -> list[Path]:
    root = app_install_dir()
    return [
        root / "config" / "supabase.json",
        Path(__file__).resolve().parent.parent / "config" / "supabase.json",
    ]


def _jwt_role(token: str) -> Optional[str]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
        role = data.get("role")
        return str(role) if role is not None else None
    except (ValueError, json.JSONDecodeError, IndexError):
        return None


def _validate_anon_key(anon_key: str) -> None:
    role = _jwt_role(anon_key)
    if role == "service_role":
        raise SupabaseConfigError(
            "В конфигурации указан service_role key. "
            "В клиентском приложении разрешён только anon key."
        )


def load_supabase_settings() -> SupabaseSettings:
    """Переменные окружения имеют приоритет над config/supabase.json."""
    rest_url = os.environ.get("SUPABASE_REST_URL", "").strip()
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()

    if not rest_url or not anon_key:
        for path in _config_paths():
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SupabaseConfigError(f"Не удалось прочитать {path}") from exc
            if not rest_url:
                rest_url = str(data.get("rest_url", "")).strip()
            if not anon_key:
                anon_key = str(data.get("anon_key", "")).strip()
            break

    if not rest_url:
        raise SupabaseConfigError(
            "Не задан SUPABASE_REST_URL. Укажите в config/supabase.json "
            "или переменной окружения SUPABASE_REST_URL."
        )
    if not anon_key:
        raise SupabaseConfigError(
            "Не задан SUPABASE_ANON_KEY. Укажите в config/supabase.json "
            "или переменной окружения SUPABASE_ANON_KEY."
        )

    _validate_anon_key(anon_key)
    return SupabaseSettings(rest_url=rest_url.rstrip("/"), anon_key=anon_key)
