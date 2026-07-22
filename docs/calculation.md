# Формулы расчёта

Только формулы, используемые в текущем коде (`goal_model.py`, `goal_matrix_auto.py`, `goal_model_train.py`, `FairOddsCalc_iOS.html`, `match_shin_calc.py`).

**Web / iOS (вкладка «Линия»):** две независимые score matrix на одной обучающей выборке:

| Рынок | Модель | Матрица |
|-------|--------|---------|
| 1X2 | Legacy | Poisson + Dixon–Coles (`useDraw=false`, PX = диагональ) |
| AH / OU | Auto | Marginals (α) + Gaussian Copula (ρ) |

Параметры α/ρ — в runtime-конфиге `model_config.json` (БД не меняем). Итоговый 1X2 из Auto пользователю не отдаётся.

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

В Auto после выбора α/ρ выполняется повторная калибровка S/D на joint matrix (см. `auto1x2Calib`); это улучшает Auto-матрицу для AH/OU. **Итоговый 1X2 пользователю берётся только из Legacy.**

---

## 7. Score matrix

### 7a. Legacy (1X2)

```text
λ_h, λ_a → Poisson → Dixon–Coles(γ)
P1 = Σ_{i>j} P(i,j)
PX = Σ_i P(i,i)
P2 = Σ_{i<j} P(i,j)
```

Без Copula, NB и draw-model.

### 7b. Auto (AH / OU)

```text
α_final = 0  → Poisson-like marginals
α_final > 0  → Negative Binomial (NB2): Var = μ + α·μ²

P(i,j) = Δ GaussianCopula(F_h, F_a; ρ_final)
```

Из Auto matrix — main AH/OU линии и кэфы. 1X2 из этой матрицы в UI не показывается.
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

## 8. Рынки из матриц (комбинированный итог)

| Рынок | Источник |
|-------|----------|
| Home / Draw / Away | Legacy matrix |
| AH line + odds | Auto matrix |
| OU line + odds | Auto matrix |

Программа **не** пересчитывает AH/OU на Legacy и **не** отдаёт 1X2 из Auto в пользовательский результат.

Честный коэффициент: `k = 1 / p`.  
С маржой: `k = 1 / (p · (1 + margin))`.

---

## 9. Legacy helpers (Python)

Функции `apply_dixon_coles` и `adjust_matrix_to_draw_target` в `goal_model.py` используются Legacy-пайплайном (web: `irTrainLegacy` / `calculateLegacyModel`).  
Сравнение old vs new на λ: `goal_matrix_auto.compare_old_vs_new_on_lambdas`.
---

## 10. Калькулятор A (desktop)

```text
A = (k_away − 1) / (k_home − 1)
```

---

## 11. Shin P1/X/P2 матча (вкладка «Счёт кэф»)

Отдельный Shin-калькулятор по введённым 1X2; не путать с голевой матрицей.
