# ТЗ: Legacy-pipeline и лаборатория сравнения моделей

**Статус:** согласовано как постановка на доработку  
**Продукт:** FairOddsCalc (web: вкладка «Сравнение моделей»)  
**Дата:** 2026-07-21  
**Связанный код:** `web/FairOddsCalc_iOS.html`, `web/model_config.json`, `app/goal_model.py`, `app/goal_matrix_auto.py`, `fixtures/legacy_reference.*`

---

## Принцип

Документ состоит из **двух независимых задач**. Их можно планировать, оценивать и принимать по отдельности.

| Задача | Название | Вопрос, на который отвечает |
|--------|----------|-----------------------------|
| **ТЗ-1** | Возврат полноценной Legacy-модели | Есть ли у нас настоящий исторический эталон для сравнения? |
| **ТЗ-2** | Переработка отчёта сравнения | Почему одна модель лучше другой, а не только «кто выиграл по MAE»? |

**Общий принцип отчёта (ТЗ-2):**

> Отчёт должен отвечать не только на вопрос «какая модель лучше?», но и на вопрос **«почему она лучше?»**.

Сейчас вкладка — таблица с числами. Цель — полноценная **лаборатория разработки модели**, где каждое изменение математического компонента проверяется отдельно.

---

# ТЗ-1. Возврат полноценной Legacy-модели

## 1. Цель

Восстановить историческую Legacy-модель в том виде, в котором она использовалась **до** внедрения архитектуры Auto.

Нужна возможность **объективного** сравнения старой и новой моделей на одном наборе матчей.

Переключатель **Legacy / Auto** должен переключать **весь расчётный pipeline**, а не отдельные параметры новой модели.

До сверки с historical reference режим в UI называется:

```text
Legacy (candidate)
```

После прохождения AC по контрольному набору — можно снять пометку `candidate`.

---

## 2. Два независимых pipeline

### 2.1. Legacy

```text
Рыночные коэффициенты
        ↓
Восстановление S и D (исторический алгоритм)
        ↓
Poisson Marginals
        ↓
Independent Probability Matrix
        ↓
Dixon–Coles Correction
        ↓
Draw Model
        ↓
Калибровка Legacy
        ↓
Расчёт рынков
```

### 2.2. Auto

```text
Рыночные коэффициенты
        ↓
Восстановление S и D (алгоритм Auto / актуальный)
        ↓
Auto Marginals (Poisson / Negative Binomial)
        ↓
Gaussian Copula
        ↓
Единая Score Matrix
        ↓
Расчёт рынков
```

Entry points в коде (рекомендуемые имена):

```text
calculateLegacyModel(matchOrTrainSet, legacyConfig)
calculateAutoModel(matchOrTrainSet, autoConfig)
```

Допустима тонкая обёртка `if mode == LEGACY / AUTO`, но **сами pipelines независимы**.

Общими могут остаться только:

- чтение исходных данных;
- округление / форматирование;
- экспорт CSV;
- отображение;
- базовые math-хелперы без бизнес-логики модели.

---

## 3. Запреты изоляции

### 3.1. Legacy не использует компоненты Auto

Запрещено:

- Gaussian Copula / `rho`;
- Auto Marginal Builder;
- Negative Binomial / `alpha` как marginal;
- VPP Auto;
- Auto calibration / Auto S–D calibration;
- любые параметры `autoMarginal` / `copula` из конфига.

### 3.2. Auto не использует компоненты Legacy

Запрещено:

- Dixon–Coles / `gamma` DC;
- Draw Model / draw `q` / draw gamma;
- Legacy calibration (включая legacy-специфичные коэффициенты 1X2/draw).

Ничья в Auto = **только сумма диагонали** матрицы.

---

## 4. Конфигурация

Параметры хранятся **раздельно**. Изменение одной секции не меняет результаты другой.

Минимальная структура:

```json
{
  "legacy": {
    "matrix": {},
    "poisson": {},
    "dixonColes": {},
    "drawModel": {},
    "calibration": {},
    "train": {}
  },
  "auto": {
    "matrix": {},
    "marginals": {},
    "copula": {},
    "train": {}
  }
}
```

Значения — **реальные исторические** для Legacy (не заглушки). Источник: старый отчёт / старый код / `model_config` эпохи до Copula.

Файлы: `web/model_config.json`, `config/model_config.json` (синхронно).

---

## 5. Критерий восстановления Legacy

Legacy считается восстановленной **только** после совпадения с historical reference на контрольном наборе.

### 5.1. Контрольный набор

Immutable fixture, например:

```text
fixtures/legacy_historical_report.csv
```

Поля (минимум):

```text
match_id, date, home, away,
market_1, market_x, market_2,
historical_legacy_1, historical_legacy_x, historical_legacy_2,
historical_legacy_ah, historical_legacy_ah1, historical_legacy_ah2,
historical_legacy_total, historical_legacy_o, historical_legacy_u
```

Источник: **сохранённый старый отдельный Legacy-отчёт** на тех же матчах (не синтетика из текущего кода).

### 5.2. Допуски

```text
коэффициенты: abs(newLegacy − historicalLegacy) ≤ 0.005
AH / Total main line: точное совпадение линии
```

Сравнение:

1. на сырых значениях до округления (если доступны);
2. дополнительно на отображаемых (как в старом отчёте).

Целевой уровень: **100%** в допуске; минимум для merge — **≥ 95%**, с явной пометкой незакрытых кейсов.

### 5.3. Регрессионный тест

Автотест обязан падать, если текущий `calculateLegacyModel` расходится с fixture сверх допуска.

Существующий `fixtures/legacy_reference.*` (синтетика Poisson+DC+draw) **не заменяет** historical report fixture — это smoke на изоляцию pipeline, не proof of history.

---

## 6. Acceptance Criteria — ТЗ-1

| ID | Критерий |
|----|----------|
| L-AC1 | При выборе Legacy выполняется полный legacy-pipeline (S/D → Poisson → independent → DC → draw → legacy cal) |
| L-AC2 | В Legacy не вызываются Copula / NB / Auto α–ρ |
| L-AC3 | В Auto не вызываются DC / Draw Model |
| L-AC4 | Конфиги изолированы: правка `legacy.*` не меняет Auto и наоборот |
| L-AC5 | Совпадение с historical Legacy report в допуске ≤ 0.005 (и точные main lines) |
| L-AC6 | Один набор входных данных в compare: одни матчи, odds, маржа, train window, active/inactive |

---

# ТЗ-2. Переработка отчёта сравнения моделей

## 1. Цель

Текущий отчёт показывает разницу коэффициентов, но **не объясняет**, какая часть модели улучшилась или ухудшилась.

Нужно превратить вкладку в **инструмент анализа модели**.

---

## 2. Режимы формирования отчёта

Переключатель:

```text
○ Legacy (candidate / Legacy)
○ Auto
○ Сравнение
```

| Режим | Поведение |
|-------|-----------|
| Legacy | Только legacy-pipeline |
| Auto | Только auto-pipeline |
| Сравнение | Обе модели за один прогон на одном наборе |

---

## 3. Четыре блока отчёта

### Блок 1. Сравнение 1X2

| Поле | Market | Legacy | Auto | Δ Legacy | Δ Auto |
|------|--------|--------|------|----------|--------|
| 1 | | | | | |
| X | | | | | |
| 2 | | | | | |

Плюс `Winner` — какая модель ближе к рынку (по согласованной метрике MAE/abs на выбранном слое fair/raw).

**Слой эталона (маржа):**

- галка «Маржа» **выкл** → эталон Shin de-vig (fair); модель fair;
- галка **вкл** → эталон raw из БД; модель с заданным % маржи.

Δ всегда в одном слое.

---

### Блок 2. Сравнение Main Line

Это **отдельный** блок. Не коэффициенты — **линии**.

#### AH

| Market AH | Legacy AH | Auto AH | AH_line_delta_L | AH_line_delta_A |

#### Total

| Market Total | Legacy Total | Auto Total | Total_line_delta_L | Total_line_delta_A |

Отвечает на вопрос:

> Какая модель лучше определяет главную линию?

`winner_AH_line` / `winner_OU_line` считаются **только** по line-delta, не по odds.

---

### Блок 3. Коэффициенты на рыночной линии

Самый важный блок для odds-MAE.

Если рынок `AH = -0.75`, **обе** модели возвращают кэфы именно на `-0.75`.  
Если рынок `Total = 2.75`, обе считают Over/Under **2.75**.

Только после этого:

```text
Odds Delta = model_odds_at_market_line − market_odds
```

Запрещено сравнивать `2.00 на -0.75` с `1.98 на -0.50`.

Имена колонок (обязательно явные):

```text
AH1_L_at_market, AH2_L_at_market, AH1_A_at_market, AH2_A_at_market
O_L_at_market, U_L_at_market, O_A_at_market, U_A_at_market
AH1_dL_mkt, …, O_dL_mkt, …
winner_AH, winner_OU   ← только из *_mkt
```

`same_ah` / `same_tot` — справочные флаги совпадения main line с рынком; **не** условие валидности odds-delta (odds уже на market line).

---

### Блок 4. Диагностика модели

Отвечает на вопрос «почему лучше/хуже».

#### Общее

```text
Лига, Сезон, Количество матчей
```

#### Marginals (Auto)

```text
Distribution (Poisson / NB)
alpha
NB improvement %
```

#### Copula (Auto)

```text
rho
```

#### Legacy-специфика

```text
dc_gamma
draw mode / q diagnostics
```

#### Матрица

```text
Matrix Sum
Matrix Tail
```

#### Ничья

```text
PX bias
PX MAE
Доля недооценки PX
```

#### S/D

```text
Средний S_delta
Средний D_delta
Средний |D|_delta
```

#### Основные ошибки

```text
1X2 MAE
AH MAE (@ market line)
Total / OU MAE (@ market total)
BTTS MAE
Score NLL
```

---

## 4. Режим «Сравнение компонентов» (лаборатория)

Отдельный режим/панель. Не только Legacy vs Auto целиком, а **включение/выключение блоков**.

| Компонент | Варианты |
|-----------|----------|
| S/D recovery | Legacy / Auto |
| Marginals | Legacy Poisson / Auto (Pois/NB) |
| Joint Matrix | Independent / Copula |
| Draw | Legacy Draw / Off |
| Calibration | Legacy / Auto |

Примеры комбинаций:

```text
Legacy S/D + Copula + Draw Off
Legacy S/D + Legacy Draw + Copula
Auto S/D + Independent + Draw Off
```

Цель:

> Понять, **что именно** улучшает или ухудшает модель.

Ограничения:

- недопустимые комбинации (если есть) — явно блокировать с текстом причины;
- каждая комбинация логируется в CSV (`component_recipe`);
- результаты не пишутся в БД.

---

## 5. Summary в начале отчёта

```text
Всего матчей: N
```

### Legacy

```text
1X2 MAE, AH MAE (@mkt), OU MAE (@mkt), PX MAE, Score NLL
(+ AH/Total line MAE отдельно)
```

### Auto

то же.

### Победители по блокам

```text
1X2 → …
AH odds @ market → …
OU odds @ market → …
AH line → …
Total line → …
PX → …
Score → …
```

`winner_row` агрегирует только согласованный набор (рекомендация: 1X2 + AH@mkt + OU@mkt), **без** смешивания с line-delta.

---

## 6. Автоматический вывод (narrative)

В конце отчёта (UI + опционально в CSV/markdown) — краткий текст, например:

```text
Auto лучше воспроизводит AH и тоталы.
Legacy лучше воспроизводит вероятность ничьей.
Основная причина различий — отсутствие Draw Model в Auto.
Negative Binomial преимуществ не показал (или показал +X%).
Copula использует rho = …
Рекомендуется исследовать калибровку S/D без возврата Draw Model.
```

Правила генерации — детерминированные шаблоны по порогам MAE/bias (не LLM в runtime).

---

## 7. Acceptance Criteria — ТЗ-2

| ID | Критерий |
|----|----------|
| R-AC1 | Режимы Legacy / Auto / Сравнение работают |
| R-AC2 | Четыре блока отчёта разделены (1X2 / Main Line / Odds@market / Diagnostics) |
| R-AC3 | AH/OU odds сравниваются только на рыночной линии |
| R-AC4 | Main line deltas не смешиваются с odds MAE |
| R-AC5 | Маржа: выкл = fair/de-vig эталон; вкл = raw БД + модель с % |
| R-AC6 | Summary + победители по блокам |
| R-AC7 | Diagnostics отвечает «почему» (PX bias, S/D, α/ρ/γ, matrix) |
| R-AC8 | Режим сравнения компонентов позволяет изолировать вклад блоков |
| R-AC9 | Автовывод формируется по метрикам |

---

# Порядок внедрения (рекомендация)

Задачи независимы по приёмке, но практичный порядок:

1. **ТЗ-1** — historical fixture + доказанное совпадение Legacy (иначе compare врёт эталоном).
2. **ТЗ-2 блоки 1–3** — корректная семантика отчёта (уже частично в коде; довести UI/блоки/summary).
3. **ТЗ-2 блок 4 + narrative** — диагностика «почему».
4. **ТЗ-2 компонент-лаб** — самый мощный, но самый дорогой слой; после стабильных ТЗ-1 и блоков 1–3.

---

# Вне объёма

- Изменения схемы БД / миграции.
- Изменение pipeline вкладки «Линия» / «История» (Auto остаётся боевым, пока отдельно не решено).
- Возврат Draw Model в Auto «по умолчанию» — только через эксперименты в компонент-лабе и явное решение.

---

# Ссылки на текущее состояние кода

| Тема | Где смотреть |
|------|----------------|
| Legacy candidate train/predict | `irTrainLegacy`, `irPredictLegacy`, `calculateLegacyModel` в `web/FairOddsCalc_iOS.html` |
| Auto train/predict | `gmTrain`, `gmPredict`, `calculateAutoModel` |
| Odds @ market / line deltas | `irPredictAtMarket`, колонки `*_at_market`, `*_d*_mkt`, `AH_line_d*` |
| Конфиг | `web/model_config.json` → секции `legacy` / `auto` |
| Smoke fixture (не historical proof) | `fixtures/legacy_reference.*`, `tests/test_legacy_pipeline.py` |

---

# Чеклист приёмки заказчиком

**ТЗ-1**

- [ ] Есть `legacy_historical_report` с реального старого отчёта
- [ ] Текущий Legacy совпадает в допуске ≤ 0.005
- [ ] В логах/диагностике нет Copula/NB на Legacy-прогоне

**ТЗ-2**

- [ ] В CSV/UI явно разделены line-метрики и odds@market
- [ ] `winner_AH` / `winner_OU` считаются только по market-line odds
- [ ] Summary и автовывод объясняют «почему»
- [ ] (Опционально v2 того же ТЗ) работает сравнение компонентов
