# Формулы расчётов

Только формулы, используемые в текущем коде (`goal_model.py`, `goal_model_train.py`, `FairOddsCalc_iOS.html`, `match_shin_calc.py`).

---

## 1. Снятие маржи (Shin)

Для рынка с исходами `k_1, …, k_n` подбирается `z`, затем:

```text
p_i = (sqrt(z² + 4(1-z)/k_i) - z) / (2(1-z))
```

- **2 исхода** (Over/Under, AH): `devig_two_way`
- **3 исхода** (1X2): `shin_devig_1x2`

---

## 2. Скрытые параметры матча

Из de-vig вероятностей и линий:

```text
S_m = λ_h + λ_a   — сумма голов (из тотала + Over/Under)
D_m = λ_h − λ_a   — разница (из форы + AH-odds)

λ_h = (S_m + D_m) / 2
λ_a = (S_m − D_m) / 2
```

`S_m` — подбор по линии тотала и `P(Over)`.  
`D_m` — подбор по азиатской форе и `P(AH home)` (учёт четвертных линий и push).

---

## 3. Веса матча

### Базовый вес

```text
w_base = season_weight × match_weight × derby_weight × neutral_weight
```

### На этапе «сила» (D_m = r_h − r_a + H·I_home)

```text
w_line_AH = clamp(1 / (1 + α_AH · |D_m|^p), 0.15, 1)
w_strength = w_base × w_line_AH × w_robust_Huber
```

Defaults: `α_AH = 0.25`, `p = 2`.

### На этапе attack/defense (log λ)

```text
w_line_T = clamp(1 / (1 + α_T · (S_m − S̄)²), 0.3, 1)
w_attack = w_base × w_line_T × w_robust_Huber
```

Default: `α_T = 0.5`.

---

## 4. Рейтинг силы

Robust WLS (Huber):

```text
D_m ≈ r_home − r_away + H · I_home
```

Ограничение: `Σ r_team = 0`.

---

## 5. Attack / Defense

```text
log λ_h = μ + A_home − D_away + H_g · I_home
log λ_a = μ + A_away − D_home
```

Ограничения: `Σ A = 0`, `Σ D = 0`.

---

## 6. Калибровка и Dixon–Coles

```text
D_final = a + b · D_model
S_final = c + d · S_model
```

Подгонка под 1X2 (вес ничьи `draw_loss_weight`, default 1.5).  
Dixon–Coles: поправка низких счётов параметром `γ`.

---

## 7. Модель ничьи

```text
logit(P_X) = α + β_D·|D| + β_S·S + β_S2·S² + β_DxS·|D|·S
```

В прогнозе целевая `P_X^target` корректирует диагональ матрицы:

```text
q = clamp(P_X^target / P_X^matrix, q_min, q_max)
```

Defaults: `q_min = 0.85`, `q_max = 1.15`.

---

## 8. Матрица и рынки

Пуассоновская матрица `P(i,j)` до `max_goals` (default 10).

Из одной матрицы:

- 1X2: `P1 = Σ_{i>j} P(i,j)`, `PX = Σ_i P(i,i)`, `P2 = Σ_{i<j} P(i,j)`
- Тоталы, азиатские форы, индивидуальные тоталы, топ счетов

Честный коэффициент: `k = 1 / p`.  
С маржой: `k = 1 / (p · (1 + margin))`.

---

## 9. Калькулятор A (desktop)

```text
A = (k_away − 1) / (k_home − 1)
```

Усреднение с опциональным IQR-фильтром выбросов (×1.5).

---

## 10. Shin P1/X/P2 матча (вкладка «Счёт кэф»)

Рейтинги команд из истории 1X2 → вероятности исходов целевого матча через метод Shin на тройке коэффициентов.

См. `app/match_shin_calc.py`, тесты `tests/test_match_shin_calc.py`.
