# Деплой `userbet-odds` (прокси для iOS web)

Нужен для обхода CORS при «Получить данные» в web-превью.

## Вариант A — Supabase Studio (быстрее всего)

1. Откройте [Edge Functions](https://supabase.com/dashboard/project/vhoeiyymxghjafyollyg/functions)
2. **Deploy a new function** → имя: `userbet-odds`
3. Вставьте код из `supabase/functions/userbet-odds/index.ts`
4. **Deploy function**
5. В настройках функции отключите **Verify JWT** (или оставьте включённым — iOS шлёт anon key)

Проверка:

```bash
curl -X POST 'https://vhoeiyymxghjafyollyg.supabase.co/functions/v1/userbet-odds' \
  -H 'Content-Type: application/json' \
  -d '{"id_fixture":"1611253099"}'
```

Ожидается JSON-массив коэффициентов (не 404).

## Вариант B — CLI

1. Токен: [Account → Access Tokens](https://supabase.com/dashboard/account/tokens)
2. В корне репозитория:

```bash
export SUPABASE_ACCESS_TOKEN=sbp_...
npx supabase functions deploy userbet-odds --project-ref vhoeiyymxghjafyollyg
```

## Вариант C — GitHub Actions

1. GitHub → repo → **Settings → Secrets → Actions**
2. Secret `SUPABASE_ACCESS_TOKEN` = тот же `sbp_...`
3. **Actions → Deploy Supabase Edge Functions → Run workflow**

Или push в ветку с изменениями в `supabase/functions/`.
