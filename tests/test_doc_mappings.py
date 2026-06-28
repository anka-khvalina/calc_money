"""Документация и код: согласованность маппингов UI ↔ БД."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from supabase_history import (  # noqa: E402
    HIST_LINE_UI_COLS,
    HIST_WEIGHT_UI_COLS,
    PATCH_WHITELIST,
    UI_COL_TO_FIELD,
)


def test_calculation_md_match_weight_formula():
    text = (ROOT / "docs" / "calculation.md").read_text(encoding="utf-8")
    assert "season_weight × match_weight × neutral_mult" in text
    assert "derby_weight × neutral_weight" not in text
    assert "Дерби не входит в `w_base`" in text or "Дерби не входит в вес" in text


def test_patch_whitelist_matches_ui_map_values():
    mapped = set(UI_COL_TO_FIELD.values())
    assert mapped <= PATCH_WHITELIST
    assert "home_team" not in PATCH_WHITELIST
    assert "away_team" not in PATCH_WHITELIST


def test_hist_line_ui_cols_subset_of_map():
    for col in HIST_LINE_UI_COLS:
        assert col in UI_COL_TO_FIELD


def test_hist_weight_ui_cols_subset_of_map():
    for col in HIST_WEIGHT_UI_COLS:
        assert col in UI_COL_TO_FIELD


def test_ui_col_map_covers_history_editable_columns():
    expected = {
        "ah1",
        "ah",
        "ah2",
        "over",
        "tot",
        "under",
        "o1",
        "ox",
        "o2",
        "derby",
        "match_w",
        "neutr_w",
        "neutral",
    }
    assert expected <= set(UI_COL_TO_FIELD.keys())
