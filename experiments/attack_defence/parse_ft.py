"""Parse full-time scores from existing DB `note` field (read-only derivation)."""

from __future__ import annotations

import re
from typing import Optional, Tuple

_FT_RE = re.compile(r"FT\s*(\d+)\s*[:\-]\s*(\d+)", re.IGNORECASE)


def parse_ft_from_note(note: Optional[str]) -> Optional[Tuple[int, int]]:
    """Return (home_goals, away_goals) if note contains `FT h:a …`."""
    if not note:
        return None
    m = _FT_RE.search(str(note))
    if not m:
        return None
    hg, ag = int(m.group(1)), int(m.group(2))
    if hg < 0 or ag < 0:
        return None
    return hg, ag
