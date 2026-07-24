"""Configurable w_line_AH modes: CURRENT / SOFT / DISABLED + table presets."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import goal_model_train as gmt  # noqa: E402
import line_weights as lw  # noqa: E402


def test_modes_current_soft_disabled():
    cur = lw.LineWeightConfig(mode="current").validated()
    soft = lw.LineWeightConfig(mode="soft").validated()
    off = lw.LineWeightConfig(mode="disabled").validated()
    for abs_d in (0.0, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        assert lw.w_line_ah(abs_d, off) == 1.0
        c = lw.w_line_ah(abs_d, cur)
        s = lw.w_line_ah(abs_d, soft)
        assert 0.15 - 1e-12 <= c <= 1.0 + 1e-12
        assert s >= c - 1e-12  # AC2
        expected = min(1.0, max(0.15, 1.0 / (1.0 + 0.25 * abs_d ** 2)))
        assert abs(c - expected) < 1e-12


def test_soft_default_table_points():
    soft = lw.LineWeightConfig(mode="soft").validated()
    # Soft table values (floor vs CURRENT may lift some points)
    assert lw.w_line_ah(0.5, soft) == 1.0
    assert lw.w_line_ah(1.0, soft) >= 0.90 - 1e-12
    assert lw.w_line_ah(2.0, soft) >= 0.70 - 1e-12


def test_custom_table_override():
    cfg = lw.LineWeightConfig(
        mode="soft",
        table=[(0.5, 1.0), (2.0, 0.85), (3.0, 0.75)],
    ).validated()
    # At |D|=2 table says 0.85; CURRENT formula = 0.5 → soft max → 0.85
    assert abs(lw.w_line_ah(2.0, cfg) - 0.85) < 1e-12
    # Custom table below CURRENT is floored by AC2 for soft
    low = lw.LineWeightConfig(
        mode="soft",
        table=[(0.5, 1.0), (2.0, 0.20)],
    ).validated()
    assert lw.w_line_ah(2.0, low) >= lw.w_line_ah(2.0, lw.LineWeightConfig(mode="current"))


def test_named_experiment_preset_without_code_change():
    cfg = lw.line_weight_config_from_mapping(
        {
            "lineWeight": {
                "mode": "experiment_1",
                "presets": {
                    "experiment_1": {"table": {"0.5": 1.0, "2.0": 0.85}},
                },
            }
        }
    )
    assert cfg.mode == "experiment_1"
    assert abs(lw.w_line_ah(2.0, cfg) - 0.85) < 1e-12


def test_apply_line_weight_config_from_mapping_default_disabled():
    cfg = gmt.apply_line_weight_config_from_mapping(gmt.ModelConfig(), {})
    # empty mapping keeps ModelConfig defaults (disabled — D weight policy)
    assert cfg.line_weight_mode == lw.MODE_DISABLED
    cfg2 = gmt.apply_line_weight_config_from_mapping(
        gmt.ModelConfig(),
        {"lineWeight": {"mode": "SOFT"}},
    )
    assert cfg2.line_weight_mode == lw.MODE_SOFT
    lcfg = gmt.resolve_line_weight_config(cfg)
    assert lw.w_line_ah(3.0, lcfg) == 1.0


def test_config_json_default_disabled_d_weight_policy():
    import json

    raw = json.loads((ROOT / "config" / "model_config.json").read_text(encoding="utf-8"))
    lcfg = lw.line_weight_config_from_mapping(raw)
    assert lcfg.mode == lw.MODE_DISABLED
    # table may remain for experiments / soft rollback; disabled ignores it
    assert lw.w_line_ah(2.0, lcfg) == 1.0
    assert lw.w_line_ah(3.0, lcfg) == 1.0


def test_apply_weights_disabled_vs_current():
    raw = gmt.RawMatch(date=None, league="T", home_team="A", away_team="B")
    used = [
        gmt.PreparedMatch(
            raw=raw, home_id="a", away_id="b", home_team="A", away_team="B",
            i_home=1, diff_goals=2.0, w_base=1.0, w_robust=1.0,
        ),
        gmt.PreparedMatch(
            raw=raw, home_id="b", away_id="a", home_team="B", away_team="A",
            i_home=1, diff_goals=0.5, w_base=0.8, w_robust=1.0,
        ),
    ]
    cfg_off = gmt.ModelConfig(line_weight_mode=lw.MODE_DISABLED)
    gmt._apply_line_ah_weights(used, cfg_off)
    assert all(abs(m.w_line_ah - 1.0) < 1e-12 for m in used)
    assert used[0].w_base == 1.0 and used[1].w_base == 0.8  # AC4
    assert all(m.w_robust == 1.0 for m in used)

    cfg_cur = gmt.ModelConfig(line_weight_mode=lw.MODE_CURRENT)
    gmt._apply_line_ah_weights(used, cfg_cur)
    assert used[0].w_line_ah < 1.0
    assert abs(used[0].w_line_ah - (1.0 / (1.0 + 0.25 * 4.0))) < 1e-12
    w_cur = [m.w_line_ah for m in used]

    cfg_soft = gmt.ModelConfig(line_weight_mode=lw.MODE_SOFT)
    gmt._apply_line_ah_weights(used, cfg_soft)
    for i, m in enumerate(used):
        assert m.w_line_ah + 1e-12 >= w_cur[i]


def test_d_strength_weight_excludes_ah_line_when_disabled():
    """AC: D training weight = w_base × w_Huber (no AH line factor)."""
    raw = gmt.RawMatch(date=None, league="T", home_team="A", away_team="B")
    m = gmt.PreparedMatch(
        raw=raw, home_id="a", away_id="b", home_team="A", away_team="B",
        i_home=1, diff_goals=2.5, w_base=0.7, w_robust=0.8, w_line_ah=0.5,
    )
    cfg = gmt.ModelConfig(line_weight_mode=lw.MODE_DISABLED)
    gmt._apply_line_ah_weights([m], cfg)
    assert m.w_line_ah == 1.0  # AH line weight not applied
    m.w_robust = 0.8  # Huber applied later in WLS iterations
    import hierarchical_wls as hwls
    rcfg = hwls.HierarchicalWlsConfig(mode="standard_wls")
    w = gmt._strength_observation_weight(m, cfg, rcfg, include_robust=True)
    assert abs(w - (0.7 * 1.0 * 0.8)) < 1e-12
    # soft/current would shrink |D|=2.5; disabled must not
    cfg_soft = gmt.ModelConfig(line_weight_mode=lw.MODE_SOFT)
    m2 = gmt.PreparedMatch(
        raw=raw, home_id="a", away_id="b", home_team="A", away_team="B",
        i_home=1, diff_goals=2.5, w_base=0.7, w_robust=1.0,
    )
    gmt._apply_line_ah_weights([m2], cfg_soft)
    assert m2.w_line_ah < 1.0


def test_diagnose_summary():
    diag = lw.diagnose_weights([1.0, 0.9, 0.7], "soft")
    lines = "\n".join(diag.summary_lines())
    assert "lineWeightMode = SOFT" in lines
    assert "Matches affected = 2" in lines
    assert math.isclose(diag.avg_w_line_ah, (1.0 + 0.9 + 0.7) / 3.0)


def test_unknown_mode_without_preset_raises():
    try:
        lw.LineWeightConfig(mode="no_such_mode").validated()
        assert False, "expected ValueError"
    except ValueError:
        pass
