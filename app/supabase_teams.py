"""
Supabase REST-клиент для справочника лиг и команд (вкладка «Справочник»).

MVP: только anon key из config/supabase.json (или env), без service_role.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from supabase_config import SupabaseConfigError, SupabaseSettings, load_supabase_settings


class SupabaseError(RuntimeError):
    def __init__(self, message: str, *, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class League:
    id: str
    name: str


@dataclass(frozen=True)
class Team:
    id: int
    leagues_id: str
    name_team: str

    @property
    def id_str(self) -> str:
        return str(self.id)


_SETTINGS: Optional[SupabaseSettings] = None
_MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


def _settings() -> SupabaseSettings:
    global _SETTINGS
    if _SETTINGS is None:
        try:
            _SETTINGS = load_supabase_settings()
        except SupabaseConfigError as exc:
            raise SupabaseError(str(exc)) from exc
    return _SETTINGS


def reset_settings_cache() -> None:
    """Сброс кэша (для тестов)."""
    global _SETTINGS
    _SETTINGS = None


def _headers(settings: SupabaseSettings, *, prefer: Optional[str] = None) -> Dict[str, str]:
    h = {
        "apikey": settings.anon_key,
        "Authorization": f"Bearer {settings.anon_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _request(
    method: str,
    path: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    prefer: Optional[str] = None,
    timeout: float = 30.0,
) -> Any:
    settings = _settings()
    method_u = method.upper()
    if prefer is None and method_u in _MUTATING_METHODS:
        prefer = "return=representation"
    url = f"{settings.rest_url}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers=_headers(settings, prefer=prefer),
        method=method_u,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise SupabaseError(
            f"Supabase HTTP {exc.code}",
            status=exc.code,
            body=err_body,
        ) from exc
    except urllib.error.URLError as exc:
        raise SupabaseError(f"Сеть недоступна: {exc.reason}") from exc


def fetch_leagues() -> List[League]:
    rows = _request("GET", "/leagues?select=id,name&order=name.asc")
    if not isinstance(rows, list):
        raise SupabaseError("Некорректный ответ leagues")
    out: List[League] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lid = str(row.get("id", "")).strip()
        name = str(row.get("name", "")).strip()
        if lid and name:
            out.append(League(id=lid, name=name))
    return out


def fetch_teams(league_id: str) -> List[Team]:
    lid = str(league_id).strip()
    if not lid:
        return []
    q = urllib.parse.urlencode(
        {
            "select": "id,leagues_id,name_team",
            "leagues_id": f"eq.{lid}",
            "order": "id.asc",
        }
    )
    rows = _request("GET", f"/team?{q}")
    if not isinstance(rows, list):
        raise SupabaseError("Некорректный ответ team")
    out: List[Team] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            tid = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        name = str(row.get("name_team", "")).strip()
        lg = str(row.get("leagues_id", lid)).strip()
        if name:
            out.append(Team(id=tid, leagues_id=lg, name_team=name))
    return out


def create_team(league_id: str, name_team: str) -> Team:
    lid = str(league_id).strip()
    name = str(name_team).strip()
    if not lid:
        raise ValueError("Выберите лигу")
    if not name:
        raise ValueError("Введите название команды")
    existing = fetch_teams(lid)
    if any(t.name_team.lower() == name.lower() for t in existing):
        raise ValueError("Такая команда уже есть в этой лиге")
    try:
        rows = _request(
            "POST",
            "/team?select=id,leagues_id,name_team",
            body={"leagues_id": lid, "name_team": name},
        )
    except SupabaseError as exc:
        if exc.status in (409, 422) or "duplicate" in (exc.body or "").lower():
            raise ValueError("Такая команда уже есть в этой лиге") from exc
        raise ValueError("Не удалось добавить команду") from exc
    if isinstance(rows, list) and rows:
        row = rows[0]
    elif isinstance(rows, dict):
        row = rows
    else:
        raise ValueError("Не удалось добавить команду")
    return Team(
        id=int(row["id"]),
        leagues_id=str(row.get("leagues_id", lid)),
        name_team=str(row.get("name_team", name)),
    )
