# Aug-12 market cards vs production model (season_202526)

Source: uploaded «кэфы на 12 августа.docx» (book screenshots).

## Method

- **Model:** production FULL (`train_full_model` + `predict_match`), Dynamic D + S-EMA on.
- **Train:** only season **2025-26** per league (UI-like window).
- **1X2 odds:** fair model probs × **same match overround** as the book.
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
| FULL (2025–26 only) | 0.243 | 6 | 0.154 | 5.23 | 0.641 |
| BASE (2025–26 only) | 0.103 | 2 | 0.110 | 2.25 | 0.223 |
| FULL (all history, prior) | 0.243 | 6 | 0.132 | 5.22 | 0.579 |
| BASE (all history, prior) | 0.132 | 2 | 0.140 | 3.01 | 0.241 |

Single-season BASE is closer to the book (AH MAE 0.10 vs 0.13). FULL AH MAE unchanged — early-season Dynamic D still drives most big AH misses. Alaves–Getafe 1X2 with margin ≈ **2.10 / 2.98 / 4.54** (UI ~2.12 / 2.94 / 4.58).

## Per match — FULL with margin

| League | Match | AH mkt→mod | Tot mkt→mod | Mkt 1X2 | Model+margin 1X2 | Δodds |
|---|---|---|---|---|---|---|
| La Liga | Alaves–Getafe | -0.25→-0.50 | 1.75→1.75 | 2.37/2.82/3.90 | 2.10/2.98/4.54 | -0.27/+0.16/+0.64 |
| La Liga | Sevilla–Vallecano | -0.25→-0.50 | 2.25→2.25 | 2.34/3.28/3.32 | 1.98/3.32/4.40 | -0.36/+0.04/+1.08 |
| La Liga | Espanol–Levante | -0.25→-0.25 | 2.25→2.50 | 2.14/3.30/3.80 | 2.11/3.56/3.59 | -0.03/+0.26/-0.21 |
| La Liga | Celta–Osasuna | -0.50→-0.25 | 2.25→2.25 | 2.09/3.49/3.71 | 2.10/3.36/3.82 | +0.01/-0.13/+0.11 |
| La Liga | Valencia–Betis | +0.00→-0.25 | 2.50→2.50 | 2.69/3.24/2.71 | 2.38/3.38/3.00 | -0.31/+0.14/+0.29 |
| La Liga | Real Madrid–Sociedad | -1.25→-1.50 | 3.00→3.50 | 1.39/5.11/7.13 | 1.35/5.41/7.68 | -0.04/+0.30/+0.55 |
| La Liga | Barcelona–Ath Bilbao | -1.25→-1.50 | 3.00→3.25 | 1.41/5.00/6.83 | 1.30/5.51/9.53 | -0.11/+0.51/+2.70 |
| Premier League | Nott'm Forest–Leeds | -0.25→+0.25 | 2.50→2.50 | 2.26/3.36/3.23 | 3.21/3.43/2.24 | +0.95/+0.07/-0.99 |
| Premier League | Everton–Crystal Palace | -0.25→-0.75 | 2.50→2.50 | 2.15/3.41/3.42 | 1.75/3.68/4.79 | -0.40/+0.27/+1.37 |
| Premier League | Brentford–Tottenham | +0.00→-0.25 | 2.75→2.75 | 2.40/3.67/2.77 | 2.28/3.64/2.97 | -0.12/-0.03/+0.20 |
| Premier League | Man City–Bournemouth | -1.25→-1.25 | 3.25→3.50 | 1.45/5.05/5.97 | 1.44/5.03/6.12 | -0.01/-0.02/+0.15 |
| Premier League | Brighton–Aston Villa | -0.25→-0.50 | 3.00→2.75 | 2.26/3.62/3.02 | 1.83/3.76/4.18 | -0.43/+0.14/+1.16 |
| Premier League | Newcastle–Liverpool | +0.50→+0.25 | 3.25→3.00 | 3.42/3.90/1.99 | 2.76/3.84/2.33 | -0.66/-0.06/+0.34 |
| Premier League | Fulham–Chelsea | +0.25→+0.00 | 3.00→2.75 | 3.31/3.80/2.06 | 2.79/3.63/2.40 | -0.52/-0.17/+0.34 |
| Bundesliga | Bayern Munich–Stuttgart | -1.75→-1.50 | 4.00→4.25 | 1.28/6.41/7.77 | 1.40/5.56/5.87 | +0.12/-0.85/-1.90 |
| Bundesliga | RB Leipzig–M'gladbach | -1.00→-1.50 | 3.25→3.50 | 1.53/4.64/5.32 | 1.34/5.42/7.82 | -0.19/+0.78/+2.50 |
| Bundesliga | FC Koln–Hoffenheim | +0.25→+0.50 | 3.00→3.25 | 2.98/3.67/2.27 | 3.85/4.11/1.83 | +0.87/+0.44/-0.44 |
| Bundesliga | Union Berlin–Ein Frankfurt | +0.00→+0.25 | 2.75→3.00 | 2.47/3.51/2.78 | 3.34/3.82/2.05 | +0.87/+0.31/-0.73 |
| Bundesliga | Dortmund–Hamburg | -1.50→-1.50 | 3.00→3.25 | 1.33/5.50/8.19 | 1.29/5.62/9.40 | -0.03/+0.12/+1.21 |
| Bundesliga | Freiburg–Werder Bremen | -0.50→-0.50 | 2.75→2.75 | 2.01/3.63/3.61 | 1.89/3.76/3.93 | -0.12/+0.13/+0.32 |
| Serie A | Udinese–Como | +0.50→+1.00 | 2.25→2.50 | 4.50/3.52/1.83 | 7.49/4.00/1.49 | +2.99/+0.48/-0.33 |
| Serie A | Parma–Cagliari | +0.00→+0.25 | 2.00→2.25 | 2.68/2.99/2.92 | 3.70/3.14/2.17 | +1.02/+0.15/-0.75 |
| Serie A | Genoa–Napoli | +0.50→+1.00 | 2.25→2.25 | 4.98/3.24/1.84 | 9.72/3.90/1.44 | +4.74/+0.66/-0.40 |
| Serie A | Torino–Milan | +0.75→+1.00 | 2.50→2.50 | 4.42/3.71/1.79 | 6.80/3.97/1.53 | +2.38/+0.26/-0.27 |
| Serie A | Atalanta–Sassuolo | -1.00→-1.25 | 2.75→3.00 | 1.53/4.38/5.76 | 1.48/4.48/6.38 | -0.05/+0.10/+0.62 |
| Serie A | Bologna–Lazio | -0.25→-0.25 | 2.25→2.50 | 2.15/3.23/3.62 | 2.22/3.26/3.41 | +0.07/+0.03/-0.21 |
| Serie A | Roma–Fiorentina | -1.00→-1.25 | 2.50→2.50 | 1.57/4.10/5.74 | 1.44/4.21/8.16 | -0.13/+0.11/+2.42 |
| Ligue 1 | Marseille–Strasbourg | -0.75→-1.00 | 3.00→3.25 | 1.76/4.06/4.19 | 1.58/4.42/5.09 | -0.17/+0.36/+0.90 |
| Ligue 1 | Lens–Auxerre | -1.00→-1.50 | 2.75→3.00 | 1.60/4.25/5.10 | 1.31/5.23/9.92 | -0.29/+0.98/+4.82 |
| Ligue 1 | Toulouse–Lyon | +0.25→+0.50 | 2.75→2.50 | 3.35/3.64/2.09 | 3.77/3.61/1.96 | +0.42/-0.03/-0.13 |
| Ligue 1 | Nice–Lorient | -0.25→-0.25 | 2.75→2.75 | 2.19/3.51/3.23 | 2.21/3.55/3.16 | +0.02/+0.04/-0.07 |
| Ligue 1 | Angers–Lille | +0.75→+1.00 | 2.50→2.50 | 5.00/3.81/1.69 | 7.02/4.03/1.51 | +2.02/+0.22/-0.18 |
| Ligue 1 | Le Havre–Monaco | +0.50→+0.75 | 3.00→3.00 | 3.51/3.98/1.93 | 4.53/4.12/1.70 | +1.02/+0.14/-0.24 |
| Ligue 1 | Paris SG–Rennes | -1.25→-1.25 | 3.50→3.75 | 1.41/5.34/6.24 | 1.44/5.23/5.89 | +0.03/-0.11/-0.35 |
