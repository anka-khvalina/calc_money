"""
Supabase REST-клиент для справочника лиг и команд (вкладка «Справочник»).

MVP: только anon key, без service_role.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


SUPABASE_REST_URL = os.environ.get(
    "SUPABASE_REST_URL",
    "https://vhoeiyymxghjafyollyg.supabase.co/rest/v1",
).rstrip("/")

SUPABASE_ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZob2VpeXlteGdoamFmeW9sbHlnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODIzNjgwMzMsImV4cCI6MjA5Nzk0NDAzM30.SKnvWB922f84jZzFdpCHV4X5-Z-jC935xqozJENzlP4",
)


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


def _headers(*, prefer: Optional[str] = None) -> Dict[str, str]:
    h = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
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
    url = f"{SUPABASE_REST_URL}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(prefer=prefer), method=method)
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
            prefer="return=representation",
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
