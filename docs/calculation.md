# Формулы расчёта

Только формулы, используемые в текущем коде (`goal_model.py`, `goal_matrix_auto.py`, `goal_model_train.py`, `FairOddsCalc_iOS.html`, `match_shin_calc.py`).

**Web / iOS (вкладка «Линия»):** единая score matrix через Auto Marginals + Gaussian Copula.  
Dixon–Coles и отдельная модель ничьи **не используются**. Параметры α/ρ — в runtime-конфиге `config/goal_matrix.json` / `web/goal_matrix.config.json` (БД не меняем).

---

## 1. De-vig (Shin)

Closing-коэффициенты → честные вероятности (Shin). См. `devig_shin.py`.

---

## 2. Восстановление S и D

Из тотала + Over/Under → `S = λ_h + λ_a`.  
Из форы + AH odds → `D = λ_h − λ_a`.

```text
λ_h = (S + D) / 2
λ_a = (S − D) / 2
λ ≥ lambda_min (default 0.05)
```

---

## 3. Рейтинг силы

```text
D_m = r_home − r_away + H · I_home + δ_derby · I_derby_home + ε
```

WLS + Huber; `Σ r = 0`.

---

## 4. H_eff при прогнозе

```text
D_pred = r_home − r_away + H_eff
```

---

## 5. Attack / Defense

```text
log λ_h = μ + A_home − D_away + H_g · I_home
log λ_a = μ + A_away − D_home
```

Ограничения: `Σ A = 0`, `Σ D = 0`. Defaults: `λ_A = λ_Df = 0.10`, `λ_r = 0.10`.

---

## 6. Калибровка S/D под 1X2

```text
D_final = dA + dB · D_model
S_final = sA + sB · S_model
```

`sCalMode=off` → sA=0, sB=1 (S не калибруется). `dCalMode=soft` ограничивает dB.
Подгонка под Shin 1X2. **Без** Dixon–Coles γ.

В Auto после выбора α/ρ выполняется повторная калибровка S/D на joint matrix (см. `auto1x2Calib`); 1X2 по-прежнему только из матрицы.

---

## 7. Единая score matrix (Auto)

```text
α_final = 0  → Poisson-like marginals
α_final > 0  → Negative Binomial (NB2): Var = μ + α·μ²

P(i,j) = Δ GaussianCopula(F_h, F_a; ρ_final)
P_draw = Σ_i P(i,i)
```

α и ρ калибруются автоматически по лиге (grid search).  
Если `nb_improvement_pct < 1%` или матчей `< minMatchesForLeagueAlpha` → `α_final = 0`.  
ρ имеет отдельный порог `minMatchesForLeagueRho` (default 100): при n ∈ [100, 199] допускается ρ при α=0.  
При `ρ = 0`: `P(i,j) = P_h(i)·P_a(j)`.

Конфиг (пример): см. `config/goal_matrix.example.json`.

### VPP (диагностика формы)

```text
VPP(k) = P(X=k) − P(X=k+1)
```

Профиль P(0)…P(5+) и VPP0…VPP3 используется в калибровке/диагностике, не как рынок.

---

## 8. Рынки из одной матрицы

Из одной `joint_score_matrix`:

- 1X2: `P1 = Σ_{i>j}`, `PX = Σ_i P(i,i)`, `P2 = Σ_{i<j}`
- AH: win/push/lose по `i − j + handicap`
- OU: по `i + j`
- BTTS: `i>0` и `j>0`
- Exact score: `P(i,j)`
- Team totals: суммы по строкам/столбцам

Честный коэффициент: `k = 1 / p`.  
С маржой: `k = 1 / (p · (1 + margin))`.

---

## 9. Legacy (desktop / Python helpers)

Функции `apply_dixon_coles` и `adjust_matrix_to_draw_target` остаются в `goal_model.py` для старых тестов и сравнения old vs new (`goal_matrix_auto.compare_old_vs_new_on_lambdas`).  
Горячий путь web-обучения их **не вызывает**.

---

## 10. Калькулятор A (desktop)

```text
A = (k_away − 1) / (k_home − 1)
```

---

## 11. Shin P1/X/P2 матча (вкладка «Счёт кэф»)

Отдельный Shin-калькулятор по введённым 1X2; не путать с голевой матрицей.
