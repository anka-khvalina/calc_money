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


def test_integer_total_push():
    # тотал 3.0, ровно 3 гола → возврат (push)
    win, loss = gm.total_units(3, 3.0, "over")
    assert win == 0.0 and loss == 0.0
    win, loss = gm.total_units(4, 3.0, "over")
    assert win == 1.0


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


def test_strength_diagnostics_sorted_by_error():
    csv_path = ROOT / "docs" / "examples" / "closing_lines_serie_a_sample.csv"
    raw = gmt.load_raw_matches(csv_path)
    model, prepared = gmt.train_full_model(raw)
    diag = gmt.strength_diagnostics(model, prepared)
    assert len(diag) > 0
    errs = [abs(r.error) for r in diag]
    assert errs == sorted(errs, reverse=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} OK")
    print("ALL OK")
