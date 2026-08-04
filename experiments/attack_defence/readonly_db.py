"""GET-only Supabase REST client with fail-closed write protection.

Uses existing env/config loaders for credentials; never prints secrets.
PostgREST has no SQL transactions — isolation is method+keyword enforcement.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlparse

from supabase_config import SupabaseConfigError, load_supabase_settings

# Mutating HTTP methods — never allowed.
_FORBIDDEN_METHODS = frozenset({
    "POST", "PUT", "PATCH", "DELETE", "CONNECT", "TRACE",
})

# Keywords that indicate state-changing intent (path/query/body).
_WRITE_PATTERN = re.compile(
    r"(?i)\b("
    r"INSERT|UPDATE|DELETE|MERGE|UPSERT|CREATE|ALTER|DROP|TRUNCATE|"
    r"GRANT|REVOKE|CALL|EXECUTE|COPY\s+.+\s+TO|"
    r"BEGIN|COMMIT|VACUUM|REINDEX"
    r")\b"
)


class ReadOnlyViolation(RuntimeError):
    """Raised when a non-read operation is attempted."""


@dataclass(frozen=True)
class SafeDbInfo:
    """Non-secret connection summary for logs."""
    scheme: str
    host: str
    path: str

    def as_log_dict(self) -> Dict[str, str]:
        return {"scheme": self.scheme, "host": self.host, "path": self.path}


def mask_secret(value: str, *, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "***"
    return value[:keep] + "…" + value[-keep:]


def safe_db_info(rest_url: str) -> SafeDbInfo:
    u = urlparse(rest_url)
    return SafeDbInfo(scheme=u.scheme or "https", host=u.hostname or "(unknown)", path=u.path or "/")


def assert_readonly_query(method: str, path: str, body: Optional[str] = None) -> None:
    m = (method or "").upper().strip()
    if m in _FORBIDDEN_METHODS:
        raise ReadOnlyViolation(f"HTTP method forbidden in experiment: {m}")
    if m != "GET":
        raise ReadOnlyViolation(f"Only GET is allowed; got {m!r}")
    blob = f"{path}\n{body or ''}"
    if _WRITE_PATTERN.search(blob):
        raise ReadOnlyViolation("Query text contains a mutating SQL keyword")


class ReadOnlySupabase:
    """Minimal PostgREST client: GET only, fail-closed."""

    def __init__(self) -> None:
        try:
            self._settings = load_supabase_settings()
        except SupabaseConfigError as exc:
            raise ReadOnlyViolation(f"Cannot load Supabase settings: {exc}") from exc
        self._writes_attempted = 0
        self._writes_completed = 0
        self._gets = 0
        self._readonly_verified = False

    @property
    def writes_attempted(self) -> int:
        return self._writes_attempted

    @property
    def writes_completed(self) -> int:
        return self._writes_completed

    @property
    def readonly_verified(self) -> bool:
        return self._readonly_verified

    def info(self) -> SafeDbInfo:
        return safe_db_info(self._settings.rest_url)

    def verify_readonly(self) -> None:
        """Fail-closed probe: confirm GET works and mutating methods are blocked locally."""
        assert_readonly_query("GET", "/leagues?select=id&limit=1")
        try:
            assert_readonly_query("PATCH", "/matches?id=eq.0")
            raise ReadOnlyViolation("PATCH was not rejected by guard")
        except ReadOnlyViolation:
            pass
        # Live GET probe (no secrets in log)
        rows = self.get_json("/leagues?select=id&limit=1")
        if not isinstance(rows, list):
            raise ReadOnlyViolation("Readonly probe returned unexpected payload")
        self._readonly_verified = True

    def get_json(self, path: str) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        assert_readonly_query("GET", path)
        settings = self._settings
        url = settings.rest_url.rstrip("/") + path
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "apikey": settings.anon_key,
                "Authorization": f"Bearer {settings.anon_key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8")
                self._gets += 1
                return json.loads(raw) if raw else []
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GET failed status={exc.code} path={path.split('?')[0]}") from None

    def fetch_matches_paged(
        self,
        *,
        select: str,
        extra_query: str = "",
        page_size: int = 1000,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        offset = 0
        while True:
            q = f"/v_matches_full?select={urllib.parse.quote(select, safe=',()')}&order=match_date.asc,match_id.asc&limit={page_size}&offset={offset}"
            if extra_query:
                q += "&" + extra_query.lstrip("&")
            batch = self.get_json(q)
            if not isinstance(batch, list) or not batch:
                break
            out.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return out

    def close(self) -> None:
        # Stateless HTTP — nothing to commit; document zero writes.
        pass
