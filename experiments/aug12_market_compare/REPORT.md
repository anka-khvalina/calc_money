# Aug-12 market cards vs current production model

Source: uploaded «кэфы на 12 августа.docx» (book screenshots).

## Method

- **Model:** production FULL (`train_full_model` + `predict_match`), Dynamic D + S-EMA on.
- **Train:** all closing history per league through 2025–26 (not the UI single-season ~337 window).
- **1X2 odds:** fair model probs × **same match overround** as the book → comparable to quoted odds.
- **AH / Tot:** compare **main lines** (margin does not apply to the line itself).
- **BASE** column: same ratings but Dynamic D / S-EMA off (diagnostic).
- Excluded brand-new clubs without usable history (Racing Santander, Deportivo, Málaga, Troyes, Le Mans, …).

## Summary (n=34)

| arm | MAE AH | |ΔAH|≥0.5 | MAE Tot | MAE p1 pp | MAE odds 1 |
|---|---:|---:|---:|---:|---:|
| FULL | 0.243 | 6 | 0.132 | 5.22 | 0.579 |
| BASE | 0.132 | 2 | 0.140 | 3.01 | 0.241 |

## Per match — FULL with margin

| League | Match | AH mkt→mod | Tot mkt→mod | Mkt 1X2 | Model+margin 1X2 | Δodds |
|---|---|---|---|---|---|---|
| La Liga | Alaves–Getafe | -0.25→-0.50 | 1.75→1.75 | 2.37/2.82/3.90 | 2.01/3.09/4.72 | -0.36/+0.27/+0.82 |
| La Liga | Sevilla–Vallecano | -0.25→-0.50 | 2.25→2.25 | 2.34/3.28/3.32 | 1.99/3.37/4.27 | -0.35/+0.09/+0.95 |
| La Liga | Espanol–Levante | -0.25→-0.50 | 2.25→2.50 | 2.14/3.30/3.80 | 2.07/3.61/3.66 | -0.07/+0.31/-0.14 |
| La Liga | Celta–Osasuna | -0.50→-0.50 | 2.25→2.25 | 2.09/3.49/3.71 | 2.08/3.41/3.85 | -0.01/-0.08/+0.14 |
| La Liga | Valencia–Betis | +0.00→-0.25 | 2.50→2.50 | 2.69/3.24/2.71 | 2.30/3.41/3.12 | -0.39/+0.17/+0.41 |
| La Liga | Real Madrid–Sociedad | -1.25→-1.50 | 3.00→3.25 | 1.39/5.11/7.13 | 1.34/5.30/8.34 | -0.05/+0.19/+1.21 |
| La Liga | Barcelona–Ath Bilbao | -1.25→-1.50 | 3.00→3.25 | 1.41/5.00/6.83 | 1.28/5.64/10.40 | -0.13/+0.64/+3.57 |
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
| Serie A | Udinese–Como | +0.50→+0.75 | 2.25→2.50 | 4.50/3.52/1.83 | 5.96/3.75/1.61 | +1.46/+0.23/-0.21 |
| Serie A | Parma–Cagliari | +0.00→+0.50 | 2.00→2.25 | 2.68/2.99/2.92 | 3.80/3.35/2.05 | +1.12/+0.36/-0.87 |
| Serie A | Genoa–Napoli | +0.50→+1.00 | 2.25→2.25 | 4.98/3.24/1.84 | 8.97/3.97/1.45 | +3.99/+0.73/-0.39 |
| Serie A | Torino–Milan | +0.75→+1.00 | 2.50→2.50 | 4.42/3.71/1.79 | 6.90/3.99/1.52 | +2.48/+0.28/-0.27 |
| Serie A | Atalanta–Sassuolo | -1.00→-1.25 | 2.75→3.00 | 1.53/4.38/5.76 | 1.48/4.55/6.26 | -0.05/+0.17/+0.50 |
| Serie A | Bologna–Lazio | -0.25→-0.25 | 2.25→2.25 | 2.15/3.23/3.62 | 2.17/3.29/3.49 | +0.02/+0.06/-0.13 |
| Serie A | Roma–Fiorentina | -1.00→-1.25 | 2.50→2.50 | 1.57/4.10/5.74 | 1.44/4.20/8.13 | -0.13/+0.10/+2.39 |
| Ligue 1 | Marseille–Strasbourg | -0.75→-1.00 | 3.00→3.25 | 1.76/4.06/4.19 | 1.56/4.53/5.22 | -0.20/+0.47/+1.03 |
| Ligue 1 | Lens–Auxerre | -1.00→-1.50 | 2.75→3.00 | 1.60/4.25/5.10 | 1.31/5.28/9.68 | -0.29/+1.03/+4.58 |
| Ligue 1 | Toulouse–Lyon | +0.25→+0.50 | 2.75→2.75 | 3.35/3.64/2.09 | 3.75/3.71/1.94 | +0.40/+0.07/-0.15 |
| Ligue 1 | Nice–Lorient | -0.25→-0.25 | 2.75→2.75 | 2.19/3.51/3.23 | 2.18/3.62/3.16 | -0.01/+0.11/-0.07 |
| Ligue 1 | Angers–Lille | +0.75→+1.00 | 2.50→2.50 | 5.00/3.81/1.69 | 7.10/4.08/1.50 | +2.10/+0.27/-0.19 |
| Ligue 1 | Le Havre–Monaco | +0.50→+0.75 | 3.00→3.00 | 3.51/3.98/1.93 | 4.27/4.11/1.74 | +0.76/+0.13/-0.20 |
| Ligue 1 | Paris SG–Rennes | -1.25→-1.25 | 3.50→3.75 | 1.41/5.34/6.24 | 1.44/5.16/5.87 | +0.03/-0.18/-0.37 |

## Notes

UI «Рассчитать линию» can differ: it trains on the loaded season sample and uses Combined Legacy 1X2 + Auto AH/OU with a fixed league training margin (~3% LL), not match-specific overround on all-history FULL.
