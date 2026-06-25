# iOS / Safari — как открыть FairOddsCalc

Репозиторий **приватный**. Ссылки `htmlpreview` и `raw.githubusercontent.com` **не работают**.

## GitHub Pages (рекомендуется)

### Шаг 1 — включить Pages (один раз)

1. Откройте: https://github.com/anka-khvalina/calc_money/settings/pages  
2. **Build and deployment → Source:** `Deploy from a branch`  
3. **Branch:** `gh-pages` → папка `/ (root)` → **Save**  
4. Подождите 1–2 минуты.

### Шаг 2 — открыть в Safari

```
https://anka-khvalina.github.io/calc_money/FairOddsCalc_iOS.html
```

Корень (редирект на iOS):

```
https://anka-khvalina.github.io/calc_money/
```

> У private-репо сайт виден **только залогиненным** пользователям с доступом к репозиторию.  
> Для публичной ссылки без логина: Settings → Pages → **Visibility: Public** (нужен план GitHub с публичными Pages для private repo) или сделайте репозиторий Public.

## Локально (всегда работает)

```bash
cd web
python3 -m http.server 8080
```

Safari: http://localhost:8080/FairOddsCalc_iOS.html

Нужен файл `web/supabase.config.json` (anon key) — он лежит в репозитории.

## Деплой

При push в `web/` на ветках `main`, `cursor/supabase-history-17b5` и др. CI обновляет ветку `gh-pages`.
