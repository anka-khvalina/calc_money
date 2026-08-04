"""Poisson Attack/Defence model (per-league), console-experiment only.

log λ_home = μ + H + attack_home − defence_away
log λ_away = μ + attack_away − defence_home

Identifiability: mean(attack)=0, mean(defence)=0 after each IRLS step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .parse_ft import parse_ft_from_note


@dataclass(frozen=True)
class MatchRow:
    match_id: str
    match_date: date
    league_id: str
    league_name: str
    home_team_id: str
    away_team_id: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    is_neutral: bool = False


@dataclass
class TeamRating:
    team_id: str
    team_name: str
    n_matches: int
    attack: float
    defence: float


@dataclass
class LeagueFit:
    league_id: str
    league_name: str
    mu: float
    home_advantage: float
    ratings: Dict[str, TeamRating]
    n_train: int
    n_teams: int
    iterations: int
    converged: bool
    loss: float
    train_from: Optional[date]
    train_to: Optional[date]
    warnings: List[str] = field(default_factory=list)

    def predict_lambdas(
        self,
        home_id: str,
        away_id: str,
        *,
        neutral: bool = False,
        lambda_min: float = 0.05,
        lambda_max: float = 8.0,
    ) -> Tuple[float, float]:
        names = {tid: r.team_name for tid, r in self.ratings.items()}
        a_h = self.ratings[home_id].attack if home_id in self.ratings else 0.0
        d_a = self.ratings[away_id].defence if away_id in self.ratings else 0.0
        a_a = self.ratings[away_id].attack if away_id in self.ratings else 0.0
        d_h = self.ratings[home_id].defence if home_id in self.ratings else 0.0
        h = 0.0 if neutral else self.home_advantage
        lh = float(np.exp(self.mu + h + a_h - d_a))
        la = float(np.exp(self.mu + a_a - d_h))
        lh = min(max(lh, lambda_min), lambda_max)
        la = min(max(la, lambda_min), lambda_max)
        return lh, la


@dataclass(frozen=True)
class Prediction:
    match: MatchRow
    lambda_home: float
    lambda_away: float

    @property
    def D(self) -> float:
        return self.lambda_home - self.lambda_away

    @property
    def S(self) -> float:
        return self.lambda_home + self.lambda_away


def rows_from_db_dicts(raw: Sequence[dict]) -> Tuple[List[MatchRow], List[dict]]:
    """Convert DB rows → MatchRow; return (valid, exclusion records)."""
    ok: List[MatchRow] = []
    excluded: List[dict] = []
    seen = set()
    for r in raw:
        mid = str(r.get("match_id") or r.get("id") or "")
        reason = None
        if not mid:
            reason = "missing_match_id"
        elif mid in seen:
            reason = "duplicate_match_id"
        ds = (r.get("match_date") or "")[:10]
        try:
            dt = date.fromisoformat(ds) if ds else None
        except ValueError:
            dt = None
        if dt is None and reason is None:
            reason = "bad_or_missing_date"
        hid = r.get("home_team_id")
        aid = r.get("away_team_id")
        if reason is None and (hid is None or aid is None):
            reason = "missing_team_id"
        if reason is None and str(hid) == str(aid):
            reason = "home_equals_away"
        ft = parse_ft_from_note(r.get("note"))
        if reason is None and ft is None:
            reason = "no_ft_in_note"
        if reason is not None:
            excluded.append({"match_id": mid, "reason": reason, "note": r.get("note")})
            continue
        assert ft is not None and dt is not None
        seen.add(mid)
        ok.append(
            MatchRow(
                match_id=mid,
                match_date=dt,
                league_id=str(r.get("league_id") or ""),
                league_name=str(r.get("league_name") or ""),
                home_team_id=str(hid),
                away_team_id=str(aid),
                home_team=str(r.get("home_team") or hid),
                away_team=str(r.get("away_team") or aid),
                home_goals=int(ft[0]),
                away_goals=int(ft[1]),
                is_neutral=bool(r.get("is_neutral")),
            )
        )
    return ok, excluded


def _team_counts(matches: Sequence[MatchRow]) -> Dict[str, int]:
    c: Dict[str, int] = {}
    for m in matches:
        c[m.home_team_id] = c.get(m.home_team_id, 0) + 1
        c[m.away_team_id] = c.get(m.away_team_id, 0) + 1
    return c


def fit_league_poisson(
    matches: Sequence[MatchRow],
    *,
    regularization: float = 0.5,
    max_iterations: int = 40,
    tolerance: float = 1e-6,
    min_team_matches: int = 5,
    lambda_min: float = 0.05,
    lambda_max: float = 8.0,
) -> LeagueFit:
    """IRLS Poisson GLM with ridge on attack/defence; per-league."""
    if not matches:
        raise ValueError("No training matches")
    league_id = matches[0].league_id
    league_name = matches[0].league_name
    warnings: List[str] = []

    counts = _team_counts(matches)
    # Shrink-eligible teams kept with soft prior (ridge); flag low-n
    teams = sorted(counts.keys())
    names: Dict[str, str] = {}
    for m in matches:
        names[m.home_team_id] = m.home_team
        names[m.away_team_id] = m.away_team
    for tid in teams:
        if counts[tid] < min_team_matches:
            warnings.append(f"team {names.get(tid, tid)} n={counts[tid]} < min={min_team_matches} (ridge shrinkage)")

    idx = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    # params: [mu, H, A_0..A_{T-1}, Def_0..Def_{T-1}]
    # Constraints enforced by centering A and Def each iter (drop last gauge via projection)
    n_obs = 2 * len(matches)
    # Design will be built each IRLS with current weights

    theta = np.zeros(2 + 2 * T, dtype=float)
    # init mu from mean log goals
    ys = np.array([m.home_goals for m in matches] + [m.away_goals for m in matches], dtype=float)
    mean_g = max(float(ys.mean()), 0.2)
    theta[0] = np.log(mean_g)
    theta[1] = 0.15  # mild H prior start

    def unpack(th: np.ndarray) -> Tuple[float, float, np.ndarray, np.ndarray]:
        mu = float(th[0])
        H = float(th[1])
        A = th[2 : 2 + T].copy()
        Df = th[2 + T : 2 + 2 * T].copy()
        A -= A.mean()
        Df -= Df.mean()
        return mu, H, A, Df

    def nll(th: np.ndarray) -> float:
        mu, H, A, Df = unpack(th)
        loss = 0.0
        for m in matches:
            ih, ia = idx[m.home_team_id], idx[m.away_team_id]
            h = 0.0 if m.is_neutral else H
            log_lh = mu + h + A[ih] - Df[ia]
            log_la = mu + A[ia] - Df[ih]
            lh = float(np.clip(np.exp(log_lh), lambda_min, lambda_max))
            la = float(np.clip(np.exp(log_la), lambda_min, lambda_max))
            # Poisson nll up to constant: λ - y log λ
            loss += lh - m.home_goals * np.log(lh + 1e-12)
            loss += la - m.away_goals * np.log(la + 1e-12)
        # ridge
        loss += 0.5 * regularization * (float(np.sum(A * A)) + float(np.sum(Df * Df)) + H * H)
        return float(loss)

    prev_loss = nll(theta)
    converged = False
    it_done = 0
    for it in range(max_iterations):
        it_done = it + 1
        mu, H, A, Df = unpack(theta)
        # Build X (n_obs x p), y, w for IRLS: z = log λ + (y-λ)/λ, W=λ
        p = 2 + 2 * T
        X = np.zeros((n_obs, p), dtype=float)
        z = np.zeros(n_obs, dtype=float)
        w = np.zeros(n_obs, dtype=float)
        row = 0
        for m in matches:
            ih, ia = idx[m.home_team_id], idx[m.away_team_id]
            h_ind = 0.0 if m.is_neutral else 1.0
            # home obs
            log_lh = mu + (H if h_ind else 0.0) + A[ih] - Df[ia]
            lh = float(np.clip(np.exp(log_lh), lambda_min, lambda_max))
            X[row, 0] = 1.0
            X[row, 1] = h_ind
            X[row, 2 + ih] = 1.0
            X[row, 2 + T + ia] = -1.0
            z[row] = log_lh + (m.home_goals - lh) / max(lh, 1e-6)
            w[row] = lh
            row += 1
            # away obs
            log_la = mu + A[ia] - Df[ih]
            la = float(np.clip(np.exp(log_la), lambda_min, lambda_max))
            X[row, 0] = 1.0
            X[row, 1] = 0.0
            X[row, 2 + ia] = 1.0
            X[row, 2 + T + ih] = -1.0
            z[row] = log_la + (m.away_goals - la) / max(la, 1e-6)
            w[row] = la
            row += 1

        # Weighted least squares with ridge: (X'WX + λI) θ = X'Wz
        # Do not ridge μ; ridge H and all A/Def
        sw = np.sqrt(np.maximum(w, 1e-8))
        Xw = X * sw[:, None]
        zw = z * sw
        XtX = Xw.T @ Xw
        Xtz = Xw.T @ zw
        reg = np.zeros(p)
        reg[1:] = regularization  # H + ratings
        # Extra ridge for low-n teams
        for t, tid in enumerate(teams):
            if counts[tid] < min_team_matches:
                reg[2 + t] += regularization * 2.0
                reg[2 + T + t] += regularization * 2.0
        XtX = XtX + np.diag(reg)
        try:
            theta_new = np.linalg.solve(XtX, Xtz)
        except np.linalg.LinAlgError:
            theta_new, *_ = np.linalg.lstsq(XtX, Xtz, rcond=None)
        # project gauge
        mu_n, H_n, A_n, Df_n = unpack(theta_new)
        theta = np.concatenate([[mu_n, H_n], A_n, Df_n])
        loss = nll(theta)
        if abs(prev_loss - loss) < tolerance * max(1.0, abs(prev_loss)):
            converged = True
            prev_loss = loss
            break
        prev_loss = loss

    mu, H, A, Df = unpack(theta)
    ratings = {
        tid: TeamRating(
            team_id=tid,
            team_name=names.get(tid, tid),
            n_matches=counts[tid],
            attack=float(A[i]),
            defence=float(Df[i]),
        )
        for i, tid in enumerate(teams)
    }
    dates = [m.match_date for m in matches]
    return LeagueFit(
        league_id=league_id,
        league_name=league_name,
        mu=mu,
        home_advantage=H,
        ratings=ratings,
        n_train=len(matches),
        n_teams=T,
        iterations=it_done,
        converged=converged,
        loss=prev_loss,
        train_from=min(dates),
        train_to=max(dates),
        warnings=warnings,
    )


def predict_matches(fit: LeagueFit, matches: Sequence[MatchRow]) -> List[Prediction]:
    out: List[Prediction] = []
    for m in matches:
        lh, la = fit.predict_lambdas(m.home_team_id, m.away_team_id, neutral=m.is_neutral)
        out.append(Prediction(match=m, lambda_home=lh, lambda_away=la))
    return out
