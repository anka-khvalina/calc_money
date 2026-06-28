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

**Тотал:** `fair_price_model(Over) = W/(W+L)` — азиатский settlement (2.5 → `P(G≥3)`; 2.0 → push; 2.25 → half на 2 голах), не `P(G > line)`.  
**Фора:** `fair_price_model(AH home) = W/(W+L)` через `margin = (i−j)+line`, win/push/half/loss; четвертные −0.25 / +0.75 как среднее половин.

### Конвенция знака AH

`closing_ah_home` — **как у букмекера** (минус у фаворита-хозяина). В `infer_goal_diff` линия **без смены знака**.  
Внутренний `D = λ_h − λ_a` подбирается численно; при отсутствии AH-кэфов: `D ≈ −closing_ah_home`.

Подробнее: [training-and-calculation.md](training-and-calculation.md).

---

## 3. Веса матча

### Базовый вес при обучении

```text
w_base = season_weight × match_weight × neutral_mult

neutral_mult = is_neutral ? neutral_weight : 1.0
```

| Множитель | Где задаётся | Пример |
|-----------|--------------|--------|
| `season_weight` | таблица весов на вкладке «Линия» | прошлый сезон 0.7 |
| `match_weight` | БД / «История» → «Вес» | низкая мотивация 0.5 (legacy CSV: колонка `value`) |
| `neutral_weight` | БД / «История» → ▼ Веса | 0.8, **только** если нейтральное поле = да |
| Дерби | «История» → ▼ Веса | флаг 1/0; `δ_derby` при обучении, `H_eff` при прогнозе |

**Дерби не входит в `w_base`.** В БД `derby_weight` = **1** (дерби) или **0** / пусто (не дерби).  
Флаг участвует в регрессии силы как `δ_derby · I_derby_home` (§4).

`match_weight = 0` — матч не влияет на обучение.  
Метка `low_motivation` в `note` — только заметка; в формулу входит **число** `match_weight`.

### Прогноз: дерби меняет H, не вес

```text
H_eff = 0                              # нейтральное поле
H_eff = H_league                       # обычный матч
H_eff = shrink(H_league + δ_used)      # дерби (если δ оценена)
H_eff = derbyDefaultFactor × H_league  # дерби, мало данных (default 0.7; 0.4 — агрессивно)

D_pred = r_home − r_away + H_eff
```

### Автоматические множители (в коде)

**Этап «сила»** (`D_m = r_h − r_a + H_eff`):

```text
w_line_AH = clamp(1 / (1 + α_AH · |D_m|^p), 0.15, 1)
w_strength = w_base × w_line_AH × w_robust_Huber
```

Большой перевес по форе → матч слабее влияет на рейтинг. Defaults: `α_AH = 0.25`, `p = 2`.

**Этап attack/defense** (log λ):

```text
w_line_T = clamp(1 / (1 + α_T · (S_m − S̄)²), 0.3, 1)
w_attack = w_base × w_line_T × w_robust_Huber
```

Экстремальный тотал → меньший вес. Default: `α_T = 0.5`.

`w_robust_Huber` — итеративный Huber: выбросы по остаткам ослабляются.

**Нейтральное поле vs нейтр. вес:** `is_neutral` меняет формулу (`I_home = 0`, нет H); `neutral_weight` — только множитель в `w_base` при обучении.

---

## 4. Рейтинг силы и домашнее преимущество

Robust WLS (Huber) при обучении:

```text
D_m ≈ r_home − r_away + H_league · I_home + δ_derby · I_derby_home
```

- `I_home = 0` если `is_neutral`
- `I_derby_home = 1` если матч дерби **и** не нейтральное поле
- `δ_derby` оценивается только при **≥ 3** дерби-матчах в выборке
- shrinkage: `δ_used = w · δ_raw`, `w = n_derby / (n_derby + τ)`, default `τ = 30`

Ограничение: `Σ r_team = 0`.

### Прогноз: эффективное H

```text
H_eff = 0                              # нейтральное поле
H_eff = H_league                       # обычный матч
H_eff = shrink(H_league + δ_used)      # дерби (если δ оценена)
H_eff = derbyDefaultFactor × H_league    # дерби, мало данных (default 0.7; 0.4 — агрессивно)
```

```text
D_pred = r_home − r_away + H_eff
```

---

## 5. Attack / Defense

```text
log λ_h = μ + A_home − D_away + H_g · I_home
log λ_a = μ + A_away − D_home
```

Ограничения: `Σ A = 0`, `Σ D = 0`.

**Attack/defense** (robust WLS по log λ):

```text
Loss = Σ w·ε² + λ_A·Σ A² + λ_Df·Σ Df²
```

Defaults: `λ_A = λ_Df = 0.10`. **Сила** `r`: отдельно `λ_r = reg_lambda = 0.10`.

---

## 6. Калибровка и Dixon–Coles

```text
D_final = a + b · D_model
S_final = c + d · S_model
```

Подгонка под 1X2 (вес ничьи `draw_loss_weight`, default 1.5).  
Dixon–Coles: поправка низких счётов параметром `γ`.

**Стабильность калибровки:** `a≈0`, `b≈1`, `c≈0`, `d≈1` (допуски ±0.35 на сдвиги, ±0.30 на масштаб). Иначе — проверить восстановление S/D.

---

## 7. Модель ничьи

```text
logit(P_X) = α + β_D·|D| + β_S·S + β_S2·S² + β_DxS·|D|·S
```

В прогнозе целевая `P_X^target` корректирует диагональ матрицы:

```text
q = clamp(P_X^target / P_X^matrix, q_min, q_max)
```

Defaults: `q_min = 0.90`, `q_max = 1.10` (±10%; 0.85–1.15 — экспериментальный режим).

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
