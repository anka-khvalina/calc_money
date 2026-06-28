"""Документация и код: согласованность маппингов UI ↔ БД."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from goal_model_train import ModelConfig  # noqa: E402
from supabase_history import (  # noqa: E402
    HIST_LINE_UI_COLS,
    HIST_WEIGHT_UI_COLS,
    PATCH_WHITELIST,
    UI_COL_TO_FIELD,
)


def _read_doc(name: str) -> str:
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


def test_calculation_md_match_weight_formula():
    text = _read_doc("calculation.md")
    assert "season_weight × match_weight × neutral_mult" in text
    assert "derby_weight × neutral_weight" not in text
    assert "Дерби не входит в `w_base`" in text


def test_docs_no_derby_in_w_base_anywhere():
    for name in ("architecture.md", "glossary.md", "reference.md", "db-er-closing-lines.md"):
        text = _read_doc(name)
        assert "derby_weight × neutral_weight" not in text
        assert "match_weight × derby_weight" not in text


def test_training_doc_baseline_defaults():
    text = _read_doc("training-and-calculation.md")
    assert "baseline" in text
    assert "use_draw_model = false" in text or "выкл" in text
    assert "0.95" in text
    assert "0.90 / 1.10" not in text or "Модель ничьи | вкл" not in text


def test_model_config_matches_docs_draw_defaults():
    cfg = ModelConfig()
    assert cfg.use_draw_model is False
    assert cfg.s_calibration_mode == "off"
    assert cfg.draw_diag_multiplier_min == 0.95
    assert cfg.draw_diag_multiplier_max == 1.05
    assert cfg.draw_residual_multiplier_min == 0.98
    assert cfg.draw_residual_multiplier_max == 1.03


def test_db_er_uses_integer_team_id():
    text = _read_doc("db-er-closing-lines.md")
    assert "int id PK" in text or "integer" in text.lower()
    assert "serie_a:12" not in text or "устарел" in text.lower()


def test_architecture_has_mermaid_sequence():
    text = _read_doc("architecture.md")
    assert "sequenceDiagram" in text
    assert "gmTrain" in text or "goalMatchesToRaw" in text


def test_patch_whitelist_matches_ui_map_values():
    mapped = set(UI_COL_TO_FIELD.values())
    assert mapped <= PATCH_WHITELIST
    assert "home_team" not in PATCH_WHITELIST
    assert "away_team" not in PATCH_WHITELIST
    assert "note" not in PATCH_WHITELIST


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
