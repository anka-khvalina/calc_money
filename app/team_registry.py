"""Справочник команд по лигам с уникальными id."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from .history_store import LEAGUES, league_title, normalize_league
except ImportError:  # pragma: no cover
    from history_store import LEAGUES, league_title, normalize_league

DEFAULT_LEAGUE = "epl"

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "teams" / "registry.json"
_REGISTRY_ENV = "FAIR_ODDS_TEAMS"
_ID_RE = re.compile(r"^([a-z0-9_]+):(\d+)$")
_LOGO_EXT = ".png"
_ALLOWED_LOGO_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@dataclass(frozen=True)
class TeamEntry:
    id: str
    league: str
    name: str
    created_at: str
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "league": self.league,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TeamEntry":
        return cls(
            id=str(data["id"]),
            league=normalize_league(str(data.get("league", DEFAULT_LEAGUE))),
            name=str(data["name"]).strip(),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


def logo_filename(team_id: str) -> str:
    return str(team_id).strip().replace(":", "_") + _LOGO_EXT


def logos_dir() -> Path:
    if getattr(sys, "frozen", False):
        try:
            from .runtime_paths import teams_logos_dir
        except ImportError:  # pragma: no cover
            try:
                from runtime_paths import teams_logos_dir
            except ImportError:
                return registry_path().parent / "logos"
            return teams_logos_dir()
        return teams_logos_dir()
    return registry_path().parent / "logos"


def logo_path(team_id: str, *, path: Optional[Path] = None) -> Path:
    """Путь к файлу логотипа команды (PNG). path — только для тестов (каталог logos)."""
    base = path if path is not None else logos_dir()
    return base / logo_filename(team_id)


def has_logo(team_id: str, *, path: Optional[Path] = None) -> bool:
    return logo_path(team_id, path=path).is_file()


def _save_logo_png(source: Path, dest: Path) -> None:
    suffix = source.suffix.lower()
    if suffix == ".png":
        shutil.copy2(source, dest)
        return
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        if suffix in {".jpg", ".jpeg", ".webp"}:
            raise ValueError(
                "Для загрузки JPEG/WebP установите Pillow (pip install Pillow) или сохраните логотип в PNG."
            ) from exc
        if suffix == ".gif":
            shutil.copy2(source, dest.with_suffix(".gif"))
            dest.unlink(missing_ok=True)
            raise ValueError("GIF сохраните как PNG или установите Pillow для автоконвертации.")
        raise ValueError(f"Формат {suffix!r} не поддерживается.") from exc
    with Image.open(source) as im:
        im.convert("RGBA").save(dest, format="PNG")


def set_team_logo(
    team_id: str,
    source_path: os.PathLike[str] | str,
    *,
    path: Optional[Path] = None,
) -> Path:
    """Скопировать/конвертировать файл логотипа для команды."""
    tid = str(team_id).strip()
    if get_team(tid, path=path) is None:
        raise ValueError(f"Команда {tid!r} не найдена в справочнике.")
    src = Path(source_path)
    if not src.is_file():
        raise ValueError("Файл логотипа не найден.")
    suffix = src.suffix.lower()
    if suffix not in _ALLOWED_LOGO_SUFFIXES:
        raise ValueError(
            f"Формат {suffix!r} не поддерживается. Используйте PNG, JPG, GIF или WebP."
        )
    dest = logo_path(tid, path=path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _save_logo_png(src, dest)
    return dest


def remove_team_logo(team_id: str, *, path: Optional[Path] = None) -> bool:
    p = logo_path(team_id, path=path)
    if p.is_file():
        p.unlink()
        return True
    return False


def registry_path() -> Path:
    override = os.environ.get(_REGISTRY_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_REGISTRY_PATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_registry() -> Dict[str, Any]:
    return {"version": 1, "next_seq": {}, "teams": []}


def load_registry(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or registry_path()
    if not p.exists():
        return _empty_registry()
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return _empty_registry()
    data.setdefault("version", 1)
    data.setdefault("next_seq", {})
    data.setdefault("teams", [])
    return data


def save_registry(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    p = path or registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return p


def list_teams(league: Optional[str] = None, *, path: Optional[Path] = None) -> List[TeamEntry]:
    data = load_registry(path)
    teams = [TeamEntry.from_dict(t) for t in data.get("teams", [])]
    if league is not None:
        lg = normalize_league(league)
        teams = [t for t in teams if t.league == lg]
    return sorted(teams, key=lambda t: (t.league, t.name.lower(), t.id))


def get_team(team_id: str, *, path: Optional[Path] = None) -> Optional[TeamEntry]:
    tid = str(team_id).strip()
    for t in list_teams(path=path):
        if t.id == tid:
            return t
    return None


def _next_id(data: Dict[str, Any], league: str) -> str:
    lg = normalize_league(league)
    seq_map = data.setdefault("next_seq", {})
    n = int(seq_map.get(lg, 1))
    seq_map[lg] = n + 1
    return f"{lg}:{n}"


def add_team(
    league: str,
    name: str,
    *,
    path: Optional[Path] = None,
    allow_duplicate_name: bool = False,
) -> TeamEntry:
    lg = normalize_league(league)
    nm = str(name).strip()
    if not nm:
        raise ValueError("Название команды не может быть пустым.")
    data = load_registry(path)
    teams_raw = data.setdefault("teams", [])
    if not allow_duplicate_name:
        for t in teams_raw:
            if normalize_league(t.get("league", "")) == lg and str(t.get("name", "")).strip().lower() == nm.lower():
                return TeamEntry.from_dict(t)
    now = _now_iso()
    entry = TeamEntry(id=_next_id(data, lg), league=lg, name=nm, created_at=now, updated_at=now)
    teams_raw.append(entry.to_dict())
    save_registry(data, path)
    return entry


def sync_team_names(
    league: str,
    team_id: str,
    name: str,
    *,
    path: Optional[Path] = None,
) -> TeamEntry:
    """Обновить отображаемое имя команды в справочнике."""
    lg = normalize_league(league)
    tid = str(team_id).strip()
    nm = str(name).strip()
    data = load_registry(path)
    for t in data.get("teams", []):
        if str(t.get("id")) == tid and normalize_league(t.get("league", "")) == lg:
            t["name"] = nm
            t["updated_at"] = _now_iso()
            save_registry(data, path)
            return TeamEntry.from_dict(t)
    raise KeyError(f"Команда {tid!r} не найдена в лиге {lg!r}.")


def sync_from_matches(matches: List[Any], *, path: Optional[Path] = None) -> int:
    """Добавить в справочник команды из матчей истории (по имени, если id ещё нет)."""
    added = 0
    seen: set[Tuple[str, str]] = set()
    for m in matches:
        lg = normalize_league(getattr(m, "league", DEFAULT_LEAGUE))
        for attr in ("home_team", "away_team"):
            nm = str(getattr(m, attr, "")).strip()
            if not nm:
                continue
            key = (lg, nm.lower())
            if key in seen:
                continue
            seen.add(key)
            existing = [
                t
                for t in list_teams(lg, path=path)
                if t.name.lower() == nm.lower()
            ]
            if not existing:
                add_team(lg, nm, path=path, allow_duplicate_name=False)
                added += 1
    return added


def validate_teams_registered(
    league: str,
    names: Iterable[str],
    *,
    path: Optional[Path] = None,
) -> Dict[str, str]:
    """
    Проверить, что все команды зарегистрированы в справочнике.
    Возвращает имя/id → team id. Неизвестные команды → ValueError.
    """
    lg = normalize_league(league)
    out: Dict[str, str] = {}
    unknown: List[str] = []
    for raw in names:
        ref = str(raw).strip()
        if not ref:
            continue
        try:
            tid = resolve_team_id(lg, ref, path=path, required=True)
            out[ref] = tid
            out[team_name_by_id(tid, path=path)] = tid
        except ValueError:
            unknown.append(ref)
    if unknown:
        uniq = sorted({u for u in unknown}, key=str.lower)
        raise ValueError(
            f"Система не знает команд без id в справочнике ({league_title(lg)}): "
            + ", ".join(uniq)
            + ". Добавьте их во вкладке «Справочник команд»."
        )
    return out


def validate_matches_teams(
    league: str,
    matches: Sequence[Any],
    *,
    path: Optional[Path] = None,
) -> Dict[str, str]:
    """Все home/away в матчах должны быть в справочнике лиги."""
    names: List[str] = []
    for m in matches:
        for attr in ("home_team", "away_team", "home", "away"):
            if hasattr(m, attr):
                val = str(getattr(m, attr, "")).strip()
                if val:
                    names.append(val)
    return validate_teams_registered(league, names, path=path)


def resolve_team_id(
    league: str,
    team_ref: str,
    *,
    path: Optional[Path] = None,
    required: bool = True,
) -> Optional[str]:
    """
    team_ref — id (epl:3) или имя команды.
    Если required и команда не найдена — ValueError.
    """
    lg = normalize_league(league)
    ref = str(team_ref).strip()
    if not ref:
        if required:
            raise ValueError("Не указана команда (выберите из справочника).")
        return None
    if _ID_RE.match(ref):
        ent = get_team(ref, path=path)
        if ent is None:
            raise ValueError(f"Команда с id {ref!r} не найдена в справочнике.")
        if ent.league != lg:
            raise ValueError(f"Команда {ref!r} относится к лиге {ent.league!r}, а не {lg!r}.")
        return ent.id
    for t in list_teams(lg, path=path):
        if t.name.lower() == ref.lower():
            return t.id
    if required:
        raise ValueError(
            f"Команда {ref!r} не найдена в справочнике лиги {league_title(lg)}. "
            "Добавьте команду во вкладке «Справочник команд»."
        )
    return None


def team_name_by_id(team_id: str, *, path: Optional[Path] = None) -> str:
    ent = get_team(team_id, path=path)
    return ent.name if ent else str(team_id)


def format_team_options(league: str, *, path: Optional[Path] = None) -> List[str]:
    """Имена команд лиги для выпадающих списков (без id)."""
    return [t.name for t in list_teams(league, path=path)]


def parse_team_option(option: str) -> Tuple[str, str]:
    """Из строки «epl:1 — Arsenal» → (id, name)."""
    s = str(option).strip()
    if " — " in s:
        tid, name = s.split(" — ", 1)
        return tid.strip(), name.strip()
    if _ID_RE.match(s):
        ent = get_team(s)
        return s, ent.name if ent else s
    return s, s


def ensure_teams_for_league(league: str, names: List[str], *, path: Optional[Path] = None) -> Dict[str, str]:
    """Имя → id; только уже зарегистрированные команды (без автодобавления)."""
    return validate_teams_registered(league, names, path=path)


def sync_all_from_history(*, path: Optional[Path] = None) -> int:
    """Синхронизировать справочник со всеми сезонами в хранилище истории."""
    try:
        from . import history_store as _hs
    except ImportError:  # pragma: no cover
        import history_store as _hs
    added = 0
    for info in _hs.list_all_seasons():
        matches = _hs.load_season(info.league, info.season)
        added += sync_from_matches(matches, path=path)
    return added
