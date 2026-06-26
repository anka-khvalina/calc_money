"""Голевая модель: матрица, рынки, восстановление S/D, обучение и прогноз."""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import goal_model as gm  # noqa: E402
import goal_model_train as gmt  # noqa: E402
import goal_line_history as glh  # noqa: E402


def test_poisson_and_matrix_normalized():
    assert abs(gm.poisson_pmf(0, 1.0) - math.exp(-1.0)) < 1e-12
    m = gm.build_score_matrix(1.4, 1.1, max_goals=10)
    total = sum(sum(r) for r in m)
    assert abs(total - 1.0) < 1e-9


def test_1x2_sums_to_one():
    m = gm.build_score_matrix(1.7, 1.0)
    p1, px, p2 = gm.compute_1x2(m)
    assert abs(p1 + px + p2 - 1.0) < 1e-9
    assert p1 > p2  # хозяева сильнее


def test_ah_quarter_line_halves():
    # -0.75 = среднее(-0.5, -1.0): победа в 1 гол → half_win
    win, loss = gm.ah_home_units(1, 0, -0.75)
    assert abs(win - 0.5) < 1e-9 and abs(loss - 0.0) < 1e-9
    # победа в 2 гола → полный выигрыш
    win, loss = gm.ah_home_units(2, 0, -0.75)
    assert abs(win - 1.0) < 1e-9
    # ничья → полный проигрыш
    win, loss = gm.ah_home_units(1, 1, -0.75)
    assert abs(loss - 1.0) < 1e-9


def test_ah_quarter_line_minus_025_and_plus_075():
    # -0.25 = ½·AH(0) + ½·AH(-0.5): ничья → half_loss
    win, loss = gm.ah_home_units(0, 0, -0.25)
    assert abs(win - 0.0) < 1e-9 and abs(loss - 0.5) < 1e-9
    win, loss = gm.ah_home_units(1, 0, -0.25)
    assert abs(win - 1.0) < 1e-9
    # +0.75 = ½·AH(+0.5) + ½·AH(+1.0): ничья → full win (обе половины в плюсе)
    win, loss = gm.ah_home_units(0, 0, 0.75)
    assert abs(win - 1.0) < 1e-9 and abs(loss - 0.0) < 1e-9
    # поражение в 1 гол → half_loss (+0.5 loss, +1.0 push)
    win, loss = gm.ah_home_units(0, 1, 0.75)
    assert abs(win - 0.0) < 1e-9 and abs(loss - 0.5) < 1e-9


def test_ah_integer_line_push():
    # AH -1.0, счёт 2:1 → margin = 0 → push
    win, loss = gm.ah_home_units(2, 1, -1.0)
    assert win == 0.0 and loss == 0.0


def test_integer_total_push():
    # тотал 3.0, ровно 3 гола → возврат (push)
    win, loss = gm.total_units(3, 3.0, "over")
    assert win == 0.0 and loss == 0.0
    win, loss = gm.total_units(4, 3.0, "over")
    assert win == 1.0


def test_total_quarter_line_halves():
    # 2.25 = среднее(2.0, 2.5): ровно 2 гола → half_loss на Over
    win, loss = gm.total_units(2, 2.25, "over")
    assert abs(win - 0.0) < 1e-9 and abs(loss - 0.5) < 1e-9
    # 3 гола → полный выигрыш Over
    win, loss = gm.total_units(3, 2.25, "over")
    assert abs(win - 1.0) < 1e-9 and abs(loss - 0.0) < 1e-9


def _p_ge(s: float, g_min: int, max_g: int = 30) -> float:
    return sum(gm.poisson_pmf(g, s) for g in range(g_min, max_g))


def _p_le(s: float, g_max: int, max_g: int = 30) -> float:
    return sum(gm.poisson_pmf(g, s) for g in range(0, min(g_max, max_g) + 1))


def test_fair_price_over_line_25():
    s = 2.7
    expected = _p_ge(s, 3)
    assert abs(gm.fair_price_model_over(s, 2.5) - expected) < 1e-9


def test_fair_price_over_line_20():
    s = 2.7
    w = _p_ge(s, 3)
    l = _p_le(s, 1)
    expected = w / (w + l)
    assert abs(gm.fair_price_model_over(s, 2.0) - expected) < 1e-9


def test_fair_price_over_line_225():
    s = 2.7
    w = _p_ge(s, 3)
    l = _p_le(s, 1) + 0.5 * gm.poisson_pmf(2, s)
    expected = w / (w + l)
    assert abs(gm.fair_price_model_over(s, 2.25) - expected) < 1e-9


def _matrix_probs(lh: float, la: float, mx: int = 10):
    return gm.build_score_matrix(lh, la, mx)


def test_fair_price_ah_home_line_0():
    lh, la, mx = 1.5, 1.2, 10
    m = _matrix_probs(lh, la, mx)
    w = sum(m[i][j] for i in range(mx + 1) for j in range(mx + 1) if i > j)
    l = sum(m[i][j] for i in range(mx + 1) for j in range(mx + 1) if i < j)
    expected = w / (w + l)
    assert abs(gm.fair_price_model_ah_home(lh, la, 0.0, max_goals=mx) - expected) < 1e-9


def test_fair_price_ah_home_line_minus_05():
    lh, la, mx = 1.5, 1.2, 10
    m = _matrix_probs(lh, la, mx)
    expected = sum(m[i][j] for i in range(mx + 1) for j in range(mx + 1) if i >= j + 1)
    assert abs(gm.fair_price_model_ah_home(lh, la, -0.5, max_goals=mx) - expected) < 1e-9


def test_fair_price_ah_home_line_minus_025():
    lh, la, mx = 1.5, 1.2, 10
    m = _matrix_probs(lh, la, mx)
    w = l = 0.0
    for i in range(mx + 1):
        for j in range(mx + 1):
            p, du = m[i][j], i - j
            if du >= 1:
                w += p
            elif du == 0:
                l += 0.5 * p
            else:
                l += p
    expected = w / (w + l)
    assert abs(gm.fair_price_model_ah_home(lh, la, -0.25, max_goals=mx) - expected) < 1e-9


def test_fair_price_ah_home_line_plus_075():
    lh, la, mx = 1.5, 1.2, 10
    m = _matrix_probs(lh, la, mx)
    w = l = 0.0
    for i in range(mx + 1):
        for j in range(mx + 1):
            p, du = m[i][j], i - j
            if du >= 1:
                w += p
            elif du == 0:
                w += p
            elif du == -1:
                l += 0.5 * p
            else:
                l += p
    expected = w / (w + l)
    assert abs(gm.fair_price_model_ah_home(lh, la, 0.75, max_goals=mx) - expected) < 1e-9


def test_inter_roma_worked_example():
    # Из спецификации: Over 2.5 @1.91/1.99, AH -0.75 @1.93/1.97
    p_over, _ = gm.devig_two_way(1.91, 1.99)
    assert abs(p_over - 0.510) < 0.002
    s = gm.infer_total_sum(2.5, p_over)
    assert abs(s - 2.716) < 0.02
    p_home, _ = gm.devig_two_way(1.93, 1.97)
    assert abs(p_home - 0.505) < 0.002
    d = gm.infer_goal_diff(-0.75, p_home, s)
    assert abs(d - 0.817) < 0.03
    lh = (s + d) / 2
    la = (s - d) / 2
    assert abs(lh - 1.767) < 0.03
    assert abs(la - 0.949) < 0.03


def test_ah_fair_odds_reproduce_input():
    # Восстановленный D при подстановке обратно даёт исходную вероятность.
    s, line, p_home = 2.716, -0.75, 0.505
    d = gm.infer_goal_diff(line, p_home, s)
    matrix = gm.build_score_matrix((s + d) / 2, (s - d) / 2)
    m = gm.ah_market(matrix, line)
    assert abs(m.p_home_or_over - p_home) < 0.005


def test_find_main_lines_balanced():
    m = gm.build_score_matrix(1.6, 1.1)
    main_t = gm.find_main_total(m)
    main_a = gm.find_main_ah(m)
    assert abs(main_t.home_or_over_odds - main_t.away_or_under_odds) < 0.15
    assert abs(main_a.home_or_over_odds - main_a.away_or_under_odds) < 0.15


def test_train_and_predict_on_sample():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    assert len(raw) >= 20
    model, prepared = gmt.train_full_model(raw)
    # каждый матч получил восстановленные λ
    assert all(p.lambda_home and p.lambda_away for p in prepared)
    # сильнейшая команда выше слабейшей
    ratings = model.strength.ratings
    assert ratings["Inter"] > ratings["Empoli"]
    # домашнее преимущество положительное
    assert model.strength.home_advantage > 0
    assert model.goals.home_goal_adv > 0

    pred = gmt.predict_match(model, "Inter", "Empoli")
    mk = pred.markets
    assert abs(mk.p1 + mk.px + mk.p2 - 1.0) < 1e-9
    assert mk.p1 > mk.p2  # фаворит дома
    assert pred.lambda_home > pred.lambda_away
    # нейтральное поле снижает перевес хозяев
    pred_n = gmt.predict_match(model, "Inter", "Empoli", neutral=True)
    assert pred_n.markets.p1 < mk.p1


def test_margin_increases_overround():
    p1, px, p2 = 0.5, 0.3, 0.2
    k1, kx, k2 = gm.apply_margin_1x2(p1, px, p2, 0.05)
    overround = 1 / k1 + 1 / kx + 1 / k2
    assert abs(overround - 1.05) < 1e-9


def test_total_extremeness_weight_assigned():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    cfg = gmt.ModelConfig()
    prepared = gmt.prepare_matches(raw, cfg)
    gmt.devig_and_infer(prepared, cfg)
    for m in prepared:
        assert cfg.min_w_line_t <= m.w_line_t <= cfg.max_w_line_t
    # экстремальные тоталы получают меньший вес, чем средние
    ws = sorted(prepared, key=lambda m: m.w_line_t)
    assert ws[0].w_line_t <= ws[-1].w_line_t


def test_walk_forward_runs():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    metrics = gmt.walk_forward_validate(raw, min_train=12)
    assert metrics.n_eval > 0
    # на самосогласованных данных ошибки малы
    assert metrics.mae_ah < 0.5
    assert metrics.mae_p1 < 0.05


def test_apply_goal_diff_clamp_trim():
    info = gm.apply_goal_diff_clamp(2.5, 2.0, eps=0.05)
    assert info.hit
    assert abs(info.value - 1.95) < 1e-9
    assert abs(info.trim - 0.55) < 1e-9
    assert info.at_hi


def test_d_clamp_diagnostics_fallback():
    raw = gmt.RawMatch(
        date=None, league="test",
        home_team="A", away_team="B",
        closing_ah_home=-2.5, closing_total_line=2.0,
        over_odds=1.9, under_odds=1.9,
    )
    cfg = gmt.ModelConfig()
    prepared = gmt.prepare_matches([raw], cfg)
    gmt.devig_and_infer(prepared, cfg)
    diag = gmt.d_clamp_diagnostics(prepared)
    assert diag.n_hit == 1
    assert diag.pct == 100.0
    assert abs(diag.rows[0].trim) > 0.35
    assert diag.rows[0].source == "fallback_ah"


def test_d_clamp_diagnostics_saturated_infer():
    s = 2.0
    p_home, _ = gm.devig_two_way(1.05, 12.0)
    d = gm.infer_goal_diff(-2.0, p_home, s)
    hi = s - 0.05
    assert abs(d - hi) < 0.02
    raw = gmt.RawMatch(
        date=None, league="test",
        home_team="H", away_team="A",
        closing_ah_home=-2.0, closing_total_line=s,
        ah_home_odds=1.05, ah_away_odds=12.0,
        over_odds=1.9, under_odds=1.9,
    )
    prepared = gmt.prepare_matches([raw], cfg := gmt.ModelConfig())
    gmt.devig_and_infer(prepared, cfg)
    m = prepared[0]
    assert m.d_clamp_hit
    assert m.d_clamp_at_hi
    diag = gmt.d_clamp_diagnostics(prepared, cfg)
    assert diag.n_hit == 1
    assert diag.rows[0].source == "infer"


def test_d_clamp_sensitive_tags():
    from datetime import date as dt
    cfg = gmt.ModelConfig(d_clamp_low_team_matches=3, d_clamp_early_fraction=0.5)
    raw = [
        gmt.RawMatch(dt(2025, 8, 1), "test", "A", "B", closing_ah_home=-2.0,
                     closing_total_line=2.0, ah_home_odds=1.05, ah_away_odds=12.0,
                     over_odds=1.9, under_odds=1.9, neutral_flag=True),
        gmt.RawMatch(dt(2025, 12, 1), "test", "C", "D", closing_ah_home=0.0,
                     closing_total_line=2.5, ah_home_odds=1.9, ah_away_odds=1.9,
                     over_odds=1.9, under_odds=1.9, derby_flag=True),
    ]
    prepared = gmt.prepare_matches(raw, cfg)
    gmt.devig_and_infer(prepared, cfg)
    # force clamp on second match for test
    prepared[1].diff_goals_raw = 5.0
    info = gm.apply_goal_diff_clamp(5.0, prepared[1].sum_goals, cfg.lambda_epsilon)
    prepared[1].diff_goals = info.value
    prepared[1].d_clamp_hit = info.hit
    prepared[1].d_clamp_trim = info.trim
    prepared[1].d_clamp_at_hi = info.at_hi
    diag = gmt.d_clamp_diagnostics(prepared, cfg)
    by_pair = {(r.home_team, r.away_team): r for r in diag.rows}
    assert "нейтраль" in by_pair[("A", "B")].tags
    assert "начало сезона" in by_pair[("A", "B")].tags
    assert "дерби" in by_pair[("C", "D")].tags
    assert diag.n_sensitive >= 2


def test_strength_diagnostics_sorted_by_error():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    model, prepared = gmt.train_full_model(raw)
    diag = gmt.strength_diagnostics(model, prepared)
    assert len(diag) > 0
    errs = [abs(r.error) for r in diag]
    assert errs == sorted(errs, reverse=True)


def test_adjust_matrix_to_draw_target():
    m = gm.build_score_matrix(1.6, 1.1)
    base_draw = gm.draw_probability(m)
    target = base_draw * 1.10  # +10%, в пределах клампа
    out, diag = gm.adjust_matrix_to_draw_target(m, target, q_min=0.85, q_max=1.15)
    # сумма матрицы = 1, диагональ = целевой ничье
    assert abs(sum(sum(r) for r in out) - 1.0) < 1e-9
    assert abs(gm.draw_probability(out) - target) < 1e-6
    assert abs(diag["diag_multiplier_used"] - 1.10) < 1e-6


def test_draw_target_clamped():
    m = gm.build_score_matrix(1.6, 1.1)
    base = gm.draw_probability(m)
    # абсурдная цель — должна обрезаться q_max
    out, diag = gm.adjust_matrix_to_draw_target(m, 0.95, q_min=0.85, q_max=1.15)
    assert abs(diag["diag_multiplier_used"] - 1.15) < 1e-9
    assert abs(gm.draw_probability(out) - base * 1.15) < 1e-6


def test_draw_model_fit_and_prediction_matches_target():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    model, prepared = gmt.train_full_model(raw)
    dm = model.draw
    assert dm.n > 0
    # базовая ничья при равных командах в разумных пределах
    assert 0.10 <= dm.target_px(0.0, 2.6) <= 0.45
    # прогноз: итоговая ничья = целевой (в пределах клампа), 1X2 = 1
    pred = gmt.predict_match(model, "Inter", "Empoli")
    assert pred.draw_target is not None
    assert abs(pred.markets.p1 + pred.markets.px + pred.markets.p2 - 1.0) < 1e-9
    q = pred.draw_diagnostics["diag_multiplier_used"]
    if model.config.draw_diag_multiplier_min < q < model.config.draw_diag_multiplier_max:
        assert abs(pred.markets.px - pred.draw_target) < 1e-3


def test_draw_diagnostics_present():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    model, prepared = gmt.train_full_model(raw)
    diag = gmt.draw_diagnostics(model, prepared)
    assert len(diag) > 0
    assert all(r.p_draw_shin > 0 for r in diag)


def test_calibration_gamma_zero_when_dc_off():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    cfg = gmt.ModelConfig(use_dixon_coles=False)
    model, _ = gmt.train_full_model(raw, cfg)
    assert model.calibration.gamma == 0.0


def test_draw_q_clamp_detected_in_adjust():
    m = gm.build_score_matrix(1.6, 1.1)
    base = gm.draw_probability(m)
    _, diag = gm.adjust_matrix_to_draw_target(m, base * 1.20, q_min=0.90, q_max=1.10)
    assert diag["diag_multiplier_raw"] > 1.10
    assert abs(diag["diag_multiplier_used"] - 1.10) < 1e-9
    assert gmt._is_q_clamped(diag["diag_multiplier_raw"], 0.90, 1.10)


def test_draw_q_diagnostics_on_trained_model():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    model, prepared = gmt.train_full_model(gmt.load_raw_matches(csv_path))
    assert model.draw_q_diag is not None
    assert model.draw_q_diag.n_eval > 0


def test_sd_1x2_diagnostics_on_trained_model():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    model, prepared = gmt.train_full_model(gmt.load_raw_matches(csv_path))
    assert model.sd_diag is not None
    assert model.sd_diag.n_eval > 0
    assert len(model.sd_diag.rows) == model.sd_diag.n_eval
    assert model.sd_diag.by_s
    assert model.sd_diag.by_d
    for row in model.sd_diag.rows:
        assert row.err_x_model == row.px_model - row.px_market
        assert row.delta_s_cal == row.s_cal - row.s_model


def test_sd_1x2_market_matrix_covers_matches():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    model, prepared = gmt.train_full_model(gmt.load_raw_matches(csv_path))
    diag = model.sd_diag
    assert diag is not None
    # Вариант A: рынок S/D → матрица должен давать конечные вероятности
    assert all(0 <= r.px_market_sd <= 1 for r in diag.rows)
    assert all(abs(r.p1_market_sd + r.px_market_sd + r.p2_market_sd - 1.0) < 1e-6 for r in diag.rows)


def test_calibration_stability_unstable_example():
    cal = gmt.Calibration(a=0.0, b=0.45, c=-0.9, d=1.8, n_1x2=50)
    diag = gmt.assess_calibration_stability(cal)
    assert not diag.stable
    assert any("b=" in s for s in diag.deviations)
    assert any("c=" in s for s in diag.deviations)
    assert any("d=" in s for s in diag.deviations)


def test_calibration_stability_identity():
    cal = gmt.Calibration(n_1x2=80)
    diag = gmt.assess_calibration_stability(cal)
    assert diag.stable


def test_calibration_stability_on_trained_model():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    model, _ = gmt.train_full_model(gmt.load_raw_matches(csv_path))
    assert model.cal_diag is not None
    assert model.calibration.n_1x2 > 0


def test_reg_lambda_stabilizes_coefficients():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    loose, _ = gmt.train_full_model(raw, gmt.ModelConfig(
        reg_lambda=0.0, reg_lambda_attack=0.0, reg_lambda_defense=0.0,
    ))
    tight, _ = gmt.train_full_model(raw, gmt.ModelConfig(
        reg_lambda=1.0, reg_lambda_attack=1.0, reg_lambda_defense=1.0,
    ))
    sum_r_loose = sum(abs(v) for v in loose.strength.ratings.values())
    sum_r_tight = sum(abs(v) for v in tight.strength.ratings.values())
    sum_a_loose = sum(abs(v) for v in loose.goals.attack.values())
    sum_a_tight = sum(abs(v) for v in tight.goals.attack.values())
    sum_d_loose = sum(abs(v) for v in loose.goals.defense.values())
    sum_d_tight = sum(abs(v) for v in tight.goals.defense.values())
    assert sum_r_tight < sum_r_loose
    assert sum_a_tight < sum_a_loose
    assert sum_d_tight < sum_d_loose


def test_prior_shrinkage_pulls_ratings():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    base, _ = gmt.train_full_model(raw)
    cfg = gmt.ModelConfig(prior_weight=3.0, prior_alpha=0.5)
    shrunk, _ = gmt.train_full_model(raw, cfg, prior=base)
    # сильнейшая команда стягивается к 0.5×prior → модуль рейтинга уменьшается
    assert abs(shrunk.strength.ratings["Inter"]) < abs(base.strength.ratings["Inter"])


def test_is_derby_match_from_db_flag():
    from goal_model_train import DERBY_FLAG_YES, DERBY_FLAG_NO, RawMatch, _is_derby_match

    base = dict(
        date=None, league="", home_team="A", away_team="B",
        closing_ah_home=-0.5, closing_total_line=2.5,
        ah_home_odds=1.9, ah_away_odds=1.9, over_odds=1.9, under_odds=1.9,
        home_odds=2.0, draw_odds=3.5, away_odds=3.5,
    )
    assert not _is_derby_match(RawMatch(**base))
    assert not _is_derby_match(RawMatch(**base, derby_match_weight=DERBY_FLAG_NO))
    assert not _is_derby_match(RawMatch(**base, derby_match_weight=0.7))
    assert _is_derby_match(RawMatch(**base, derby_flag=True))
    assert _is_derby_match(RawMatch(**base, derby_match_weight=DERBY_FLAG_YES))
    csv = """home_team,away_team,closing_ah_home,closing_total_line,ah_home_odds,ah_away_odds,over_odds,under_odds,home_odds,draw_odds,away_odds,derby_flag
Inter,Roma,-0.5,2.5,1.9,1.9,1.9,1.9,2.0,3.5,3.5,true
"""
    raw = gmt.parse_raw_matches(csv)
    cfg = gmt.ModelConfig(default_season_weight=1.0)
    assert gmt.base_weight(raw[0], cfg) == 1.0


def test_effective_home_advantage_derby_fallback_default():
    st = gmt.StrengthModel(ratings={}, home_advantage=0.30, derby_n=0)
    cfg = gmt.ModelConfig()
    assert abs(gmt.effective_home_advantage(st, cfg, neutral=False, derby=True) - 0.21) < 1e-9


def test_effective_home_advantage_derby_shrinkage():
    st = gmt.StrengthModel(
        ratings={},
        home_advantage=0.30,
        derby_home_delta=-0.18,
        derby_n=8,
        derby_shrink_w=8 / 38,
    )
    cfg = gmt.ModelConfig(derby_h_default_ratio=0.4)
    assert gmt.effective_home_advantage(st, cfg, neutral=True, derby=True) == 0.0
    assert abs(gmt.effective_home_advantage(st, cfg, neutral=False, derby=False) - 0.30) < 1e-9
    h_derby = gmt.effective_home_advantage(st, cfg, neutral=False, derby=True)
    expected = (8 / 38) * 0.12 + (1 - 8 / 38) * 0.30
    assert abs(h_derby - expected) < 1e-6


def test_per_match_weights_from_csv():
    csv = """home_team,away_team,closing_ah_home,closing_total_line,ah_home_odds,ah_away_odds,over_odds,under_odds,home_odds,draw_odds,away_odds,quality_flag,value,derby_flag,derby_weight
Inter,Empoli,-2.0,3.25,2.01,1.93,1.92,2.03,1.2,7.48,13.88,low_motivation,0.5,true,0.7
Roma,Lazio,-0.5,3.0,2.05,1.9,2.03,1.91,2.0,4.15,3.34,,,
"""
    raw = gmt.parse_raw_matches(csv)
    assert len(raw) == 2
    m0, m1 = raw
    assert m0.quality_flag == "low_motivation"
    assert m0.quality_match_weight == 0.5
    assert m0.derby_match_weight == 0.7
    assert m1.quality_flag is None
    assert m1.quality_match_weight is None
    cfg = gmt.ModelConfig(default_season_weight=1.0)
    assert gmt.base_weight(m0, cfg) == 0.5
    assert gmt.base_weight(m1, cfg) == 1.0


def test_goal_line_history_summary_format():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    model, _ = gmt.train_full_model(raw)
    pred = gmt.predict_match(model, "Inter", "Empoli")
    lines, summary = glh.format_prediction_report(pred)
    assert any("1X2:" in line for line in lines)
    assert "1X2" in summary and "Тотал" in summary and "Фора" in summary


def test_goal_line_history_store():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "hist.json"
        store = glh.GoalLineHistory(path=path)
        store.add(league="serie_a", home_team="Inter", away_team="Empoli", summary="1X2 1.20/7.00/13.00")
        assert len(store.entries()) == 1
        assert store.entries()[0].match_label == "Inter — Empoli"
        store2 = glh.GoalLineHistory(path=path)
        assert len(store2.entries()) == 1
        store2.clear()
        assert store2.entries() == []


def test_infer_league_key_from_raw():
    csv = (
        "home_team,away_team,league,closing_ah_home,closing_total_line,"
        "ah_home_odds,ah_away_odds,over_odds,under_odds\n"
        "A,B,serie_a,-0.5,2.5,1.9,1.9,1.9,1.9\n"
        "C,D,serie_a,-0.5,2.5,1.9,1.9,1.9,1.9\n"
        "E,F,epl,-0.5,2.5,1.9,1.9,1.9,1.9\n"
    )
    raw = gmt.parse_raw_matches(csv)
    assert glh.infer_league_key(raw) == "serie_a"


def test_promoted_team_fallback():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    model, _ = gmt.train_full_model(raw)
    pred = gmt.predict_match(model, "Inter", "NewcomerFC")
    assert abs(pred.markets.p1 + pred.markets.px + pred.markets.p2 - 1.0) < 1e-9
    # новичок слабый → хозяева фавориты
    assert pred.markets.p1 > pred.markets.p2
    # запрет неизвестных команд → ошибка
    cfg = gmt.ModelConfig(allow_unknown_teams=False)
    strict, _ = gmt.train_full_model(raw, cfg)
    raised = False
    try:
        gmt.predict_match(strict, "Inter", "NewcomerFC")
    except ValueError:
        raised = True
    assert raised


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} OK")
    print("ALL OK")
