# Aug-12 market cards vs production model (season_202526)

Source: uploaded «кэфы на 12 августа.docx» (book screenshots).

## Method

- **Model:** production FULL (`train_full_model` + `predict_match`), Dynamic D + S-EMA on.
- **Dynamic State Aging:** forced **ON**, `H_D = H_S = 60` (research candidate; prod default remains OFF). `match_date=2026-08-12`.
- **Train:** only season **2025-26** per league (UI-like window).
- **1X2 odds:** fair model probs × **same match overround** as the book (маржа рынка).
- **AH / Tot:** compare **main lines**.
- **BASE:** same ratings, Dynamic D / S-EMA off.
- Excluded brand-new clubs (Racing Santander, Deportivo, Málaga, Troyes, Le Mans, …).

### Train sizes

- Bundesliga: **296** matches
- La Liga: **371** matches
- Ligue 1: **303** matches
- Premier League: **367** matches
- Serie A: **370** matches

## Summary (n=34)

| arm | MAE AH | |ΔAH|≥0.5 | MAE Tot | MAE p1 pp | MAE odds 1 |
|---|---:|---:|---:|---:|---:|
| FULL | 0.103 | 0 | 0.118 | 2.51 | 0.317 |
| BASE | 0.103 | 2 | 0.110 | 2.25 | 0.223 |

## 1X2 — рынок vs наши (aging H60 + маржа)

| Лига | Матч | Рынок 1 | X | 2 | Наши+маржа 1 | X | 2 | Δ1 | ΔX | Δ2 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| La Liga | Alaves–Getafe | 2.37 | 2.82 | 3.90 | 2.26 | 2.83 | 4.21 | -0.11 | +0.01 | +0.31 |
| La Liga | Sevilla–Vallecano | 2.34 | 3.28 | 3.32 | 2.20 | 3.22 | 3.72 | -0.14 | -0.06 | +0.40 |
| La Liga | Espanol–Levante | 2.14 | 3.30 | 3.80 | 1.92 | 3.62 | 4.24 | -0.22 | +0.32 | +0.44 |
| La Liga | Celta–Osasuna | 2.09 | 3.49 | 3.71 | 2.07 | 3.37 | 3.93 | -0.02 | -0.12 | +0.22 |
| La Liga | Valencia–Betis | 2.69 | 3.24 | 2.71 | 2.60 | 3.33 | 2.75 | -0.09 | +0.09 | +0.04 |
| La Liga | Real Madrid–Sociedad | 1.39 | 5.11 | 7.13 | 1.33 | 5.42 | 8.32 | -0.06 | +0.31 | +1.19 |
| La Liga | Barcelona–Ath Bilbao | 1.41 | 5.00 | 6.83 | 1.31 | 5.39 | 9.14 | -0.10 | +0.39 | +2.31 |
| Premier League | Nott'm Forest–Leeds | 2.26 | 3.36 | 3.23 | 2.29 | 3.33 | 3.20 | +0.03 | -0.03 | -0.03 |
| Premier League | Everton–Crystal Palace | 2.15 | 3.41 | 3.42 | 2.07 | 3.39 | 3.67 | -0.08 | -0.02 | +0.25 |
| Premier League | Brentford–Tottenham | 2.40 | 3.67 | 2.77 | 2.21 | 3.58 | 3.14 | -0.19 | -0.09 | +0.37 |
| Premier League | Man City–Bournemouth | 1.45 | 5.05 | 5.97 | 1.40 | 5.15 | 6.84 | -0.05 | +0.10 | +0.87 |
| Premier League | Brighton–Aston Villa | 2.26 | 3.62 | 3.02 | 2.08 | 3.63 | 3.42 | -0.18 | +0.01 | +0.40 |
| Premier League | Newcastle–Liverpool | 3.42 | 3.90 | 1.99 | 2.93 | 3.80 | 2.24 | -0.49 | -0.10 | +0.25 |
| Premier League | Fulham–Chelsea | 3.31 | 3.80 | 2.06 | 3.20 | 3.63 | 2.16 | -0.11 | -0.17 | +0.10 |
| Bundesliga | Bayern Munich–Stuttgart | 1.28 | 6.41 | 7.77 | 1.29 | 6.14 | 7.93 | +0.01 | -0.27 | +0.16 |
| Bundesliga | RB Leipzig–M'gladbach | 1.53 | 4.64 | 5.32 | 1.46 | 4.81 | 6.16 | -0.07 | +0.17 | +0.84 |
| Bundesliga | FC Koln–Hoffenheim | 2.98 | 3.67 | 2.27 | 3.25 | 3.86 | 2.08 | +0.27 | +0.19 | -0.19 |
| Bundesliga | Union Berlin–Ein Frankfurt | 2.47 | 3.51 | 2.78 | 2.83 | 3.56 | 2.41 | +0.36 | +0.05 | -0.37 |
| Bundesliga | Dortmund–Hamburg | 1.33 | 5.50 | 8.19 | 1.32 | 5.32 | 8.80 | -0.00 | -0.18 | +0.61 |
| Bundesliga | Freiburg–Werder Bremen | 2.01 | 3.63 | 3.61 | 1.88 | 3.70 | 4.04 | -0.13 | +0.07 | +0.43 |
| Serie A | Udinese–Como | 4.50 | 3.52 | 1.83 | 5.67 | 3.62 | 1.66 | +1.17 | +0.10 | -0.16 |
| Serie A | Parma–Cagliari | 2.68 | 2.99 | 2.92 | 2.92 | 3.01 | 2.66 | +0.24 | +0.02 | -0.26 |
| Serie A | Genoa–Napoli | 4.98 | 3.24 | 1.84 | 6.69 | 3.39 | 1.64 | +1.71 | +0.15 | -0.20 |
| Serie A | Torino–Milan | 4.42 | 3.71 | 1.79 | 6.24 | 3.78 | 1.59 | +1.82 | +0.07 | -0.20 |
| Serie A | Atalanta–Sassuolo | 1.53 | 4.38 | 5.76 | 1.48 | 4.38 | 6.55 | -0.05 | +0.00 | +0.79 |
| Serie A | Bologna–Lazio | 2.15 | 3.23 | 3.62 | 2.16 | 3.21 | 3.61 | +0.01 | -0.02 | -0.01 |
| Serie A | Roma–Fiorentina | 1.57 | 4.10 | 5.74 | 1.57 | 3.79 | 6.38 | +0.01 | -0.31 | +0.64 |
| Ligue 1 | Marseille–Strasbourg | 1.76 | 4.06 | 4.19 | 1.66 | 4.18 | 4.72 | -0.10 | +0.12 | +0.53 |
| Ligue 1 | Lens–Auxerre | 1.60 | 4.25 | 5.10 | 1.38 | 4.76 | 8.29 | -0.22 | +0.51 | +3.19 |
| Ligue 1 | Toulouse–Lyon | 3.35 | 3.64 | 2.09 | 3.40 | 3.43 | 2.15 | +0.05 | -0.21 | +0.06 |
| Ligue 1 | Nice–Lorient | 2.19 | 3.51 | 3.23 | 2.08 | 3.55 | 3.47 | -0.11 | +0.04 | +0.24 |
| Ligue 1 | Angers–Lille | 5.00 | 3.81 | 1.69 | 6.31 | 3.85 | 1.57 | +1.31 | +0.04 | -0.12 |
| Ligue 1 | Le Havre–Monaco | 3.51 | 3.98 | 1.93 | 4.71 | 4.06 | 1.68 | +1.20 | +0.08 | -0.25 |
| Ligue 1 | Paris SG–Rennes | 1.41 | 5.34 | 6.24 | 1.32 | 5.69 | 8.05 | -0.09 | +0.35 | +1.81 |

Δ = наши (fair × overround матча) − рынок. Положительная Δ2 на сильном фаворите = мы даём андердогу более длинный кэф (занижаем вероятность dog).
