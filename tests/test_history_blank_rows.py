"""Пустые строки при импорте истории."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from history_store import (  # noqa: E402
    _filter_blank_rows,
    _is_blank_row,
    clean_history_text,
    parse_history_text_with_stats,
)


def test_is_blank_row():
    assert _is_blank_row(",,,,,,,,,,,")
    assert _is_blank_row('""",""",""",""",""",""",""",""",""",""",""","""')
    assert not _is_blank_row('"2026-05-24","Arsenal","2.1","3.4","3.5","Chelsea","","","1","0","H",""')


def test_filter_and_clean():
    raw = (
        "Match Date,Team Home,1 Odds\n"
        '"2026-05-24","Arsenal","2.1"\n'
        ",,,,\n"
        '""",""","""\n'
    )
    cleaned, n = clean_history_text(raw)
    assert n == 2
    assert ",,," not in cleaned.splitlines()[-1]
    rows, stats = parse_history_text_with_stats(raw)
    assert stats["blank_rows_removed"] == 2
    assert len(rows) == 1
    assert rows[0]["team_home"] == "Arsenal"


def test_filter_blank_rows_keeps_header():
    lines = ["h1,h2", "a,b", ",,"]
    kept, n = _filter_blank_rows(lines)
    assert n == 1
    assert kept == ["h1,h2", "a,b"]
