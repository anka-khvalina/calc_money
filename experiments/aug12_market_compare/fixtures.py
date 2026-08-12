"""Market cards from uploaded «кэфы на 12 августа.docx» (screenshots).

Same 34-fixture universe as prior early-2026/27 openers (no brand-new clubs
like Racing Santander / Deportivo / Málaga / Troyes / Le Mans).
Odds refreshed from the Aug-12 book screenshots.
"""

from __future__ import annotations

from typing import Any, Dict, List

# home_id / away_id = Supabase team ids used in closing history.
FIXTURES: List[Dict[str, Any]] = [
    # --- La Liga ---
    dict(league="La Liga", home="Alaves", away="Getafe", home_id="21", away_id="29",
         o1=2.370, ox=2.820, o2=3.900, ah=-0.25, tot=1.75),
    dict(league="La Liga", home="Sevilla", away="Vallecano", home_id="36", away_id="39",
         o1=2.340, ox=3.280, o2=3.320, ah=-0.25, tot=2.25),
    dict(league="La Liga", home="Espanol", away="Levante", home_id="28", away_id="31",
         o1=2.140, ox=3.300, o2=3.800, ah=-0.25, tot=2.25),
    dict(league="La Liga", home="Celta", away="Osasuna", home_id="26", away_id="33",
         o1=2.090, ox=3.490, o2=3.710, ah=-0.5, tot=2.25),
    dict(league="La Liga", home="Valencia", away="Betis", home_id="38", away_id="25",
         o1=2.690, ox=3.240, o2=2.710, ah=0.0, tot=2.5),
    dict(league="La Liga", home="Real Madrid", away="Sociedad", home_id="35", away_id="37",
         o1=1.386, ox=5.110, o2=7.130, ah=-1.25, tot=3.0),
    dict(league="La Liga", home="Barcelona", away="Ath Bilbao", home_id="24", away_id="22",
         o1=1.406, ox=5.000, o2=6.830, ah=-1.25, tot=3.0),
    # --- Premier League ---
    dict(league="Premier League", home="Nott'm Forest", away="Leeds", home_id="16", away_id="11",
         o1=2.260, ox=3.360, o2=3.230, ah=-0.25, tot=2.5),
    dict(league="Premier League", home="Everton", away="Crystal Palace", home_id="9", away_id="8",
         o1=2.150, ox=3.410, o2=3.420, ah=-0.25, tot=2.5),
    dict(league="Premier League", home="Brentford", away="Tottenham", home_id="4", away_id="18",
         o1=2.400, ox=3.670, o2=2.770, ah=0.0, tot=2.75),
    dict(league="Premier League", home="Man City", away="Bournemouth", home_id="13", away_id="3",
         o1=1.446, ox=5.050, o2=5.970, ah=-1.25, tot=3.25),
    dict(league="Premier League", home="Brighton", away="Aston Villa", home_id="5", away_id="2",
         o1=2.260, ox=3.620, o2=3.020, ah=-0.25, tot=3.0),
    dict(league="Premier League", home="Newcastle", away="Liverpool", home_id="15", away_id="12",
         o1=3.420, ox=3.900, o2=1.990, ah=0.5, tot=3.25),
    dict(league="Premier League", home="Fulham", away="Chelsea", home_id="10", away_id="7",
         o1=3.310, ox=3.800, o2=2.060, ah=0.25, tot=3.0),
    # --- Bundesliga ---
    dict(league="Bundesliga", home="Bayern Munich", away="Stuttgart", home_id="42", away_id="55",
         o1=1.284, ox=6.410, o2=7.770, ah=-1.75, tot=4.0),
    dict(league="Bundesliga", home="RB Leipzig", away="M'gladbach", home_id="53", away_id="51",
         o1=1.531, ox=4.640, o2=5.320, ah=-1.0, tot=3.25),
    dict(league="Bundesliga", home="FC Koln", away="Hoffenheim", home_id="45", away_id="49",
         o1=2.980, ox=3.670, o2=2.270, ah=0.25, tot=3.0),
    dict(league="Bundesliga", home="Union Berlin", away="Ein Frankfurt", home_id="56", away_id="44",
         o1=2.470, ox=3.510, o2=2.780, ah=0.0, tot=2.75),
    dict(league="Bundesliga", home="Dortmund", away="Hamburg", home_id="43", away_id="47",
         o1=1.327, ox=5.500, o2=8.190, ah=-1.5, tot=3.0),
    dict(league="Bundesliga", home="Freiburg", away="Werder Bremen", home_id="46", away_id="57",
         o1=2.010, ox=3.630, o2=3.610, ah=-0.5, tot=2.75),
    # --- Serie A ---
    dict(league="Serie A", home="Udinese", away="Como", home_id="77", away_id="62",
         o1=4.500, ox=3.520, o2=1.826, ah=0.5, tot=2.25),
    dict(league="Serie A", home="Parma", away="Cagliari", home_id="72", away_id="61",
         o1=2.680, ox=2.990, o2=2.920, ah=0.0, tot=2.0),
    dict(league="Serie A", home="Genoa", away="Napoli", home_id="65", away_id="71",
         o1=4.980, ox=3.240, o2=1.840, ah=0.5, tot=2.25),
    dict(league="Serie A", home="Torino", away="Milan", home_id="76", away_id="70",
         o1=4.420, ox=3.710, o2=1.793, ah=0.75, tot=2.5),
    dict(league="Serie A", home="Atalanta", away="Sassuolo", home_id="59", away_id="75",
         o1=1.529, ox=4.380, o2=5.760, ah=-1.0, tot=2.75),
    dict(league="Serie A", home="Bologna", away="Lazio", home_id="60", away_id="68",
         o1=2.150, ox=3.230, o2=3.620, ah=-0.25, tot=2.25),
    dict(league="Serie A", home="Roma", away="Fiorentina", home_id="74", away_id="64",
         o1=1.568, ox=4.100, o2=5.740, ah=-1.0, tot=2.5),
    # --- Ligue 1 ---
    dict(league="Ligue 1", home="Marseille", away="Strasbourg", home_id="80", away_id="94",
         o1=1.757, ox=4.060, o2=4.190, ah=-0.75, tot=3.0),
    dict(league="Ligue 1", home="Lens", away="Auxerre", home_id="81", away_id="91",
         o1=1.602, ox=4.250, o2=5.100, ah=-1.0, tot=2.75),
    dict(league="Ligue 1", home="Toulouse", away="Lyon", home_id="86", away_id="82",
         o1=3.350, ox=3.640, o2=2.090, ah=0.25, tot=2.75),
    dict(league="Ligue 1", home="Nice", away="Lorient", home_id="85", away_id="92",
         o1=2.190, ox=3.510, o2=3.230, ah=-0.25, tot=2.75),
    dict(league="Ligue 1", home="Angers", away="Lille", home_id="89", away_id="88",
         o1=5.000, ox=3.810, o2=1.689, ah=0.75, tot=2.5),
    dict(league="Ligue 1", home="Le Havre", away="Monaco", home_id="84", away_id="83",
         o1=3.510, ox=3.980, o2=1.934, ah=0.5, tot=3.0),
    dict(league="Ligue 1", home="Paris SG", away="Rennes", home_id="96", away_id="79",
         o1=1.409, ox=5.340, o2=6.240, ah=-1.25, tot=3.5),
]
