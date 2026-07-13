"""
История расчётов вкладки «Линия» и форматирование отчёта прогноза.

Математика прогноза не меняется — только вывод и хранение записей.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from . import goal_model as gm
    from . import goal_model_train as gmt
    from .history_store import league_title, normalize_league
    from .runtime_paths import user_data_dir
except ImportError:  # pragma: no cover
    import goal_model as gm
    import goal_model_train as gmt
    from history_store import league_title, normalize_league
    from runtime_paths import user_data_dir


@dataclass
class GoalLineHistoryEntry:
    at: str
    league: str
    home_team: str
    away_team: str
    summary: str

    @property
    def league_label(self) -> str:
        try:
            return league_title(normalize_league(self.league))
        except ValueError:
            return self.league or "—"

    @property
    def match_label(self) -> str:
        return f"{self.home_team} — {self.away_team}"

    @property
    def at_display(self) -> str:
        try:
            return datetime.fromisoformat(self.at).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return self.at


def infer_league_key(raw: Sequence[gmt.RawMatch]) -> str:
    """Наиболее частая лига в загруженном CSV (или epl по умолчанию)."""
    counts: Dict[str, int] = {}
    for row in raw:
        if not row.league:
            continue
        try:
            key = normalize_league(row.league)
        except ValueError:
            key = row.league.strip()
        if key:
            counts[key] = counts.get(key, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return "epl"


def format_prediction_report(
    pred: gmt.Prediction,
    *,
    neutral: bool = False,
    use_margin: bool = False,
    margin: float = 0.0,
) -> Tuple[List[str], str]:
    """Полный текст отчёта и краткая строка для истории (без изменения расчётов)."""
    mk = pred.markets
    home, away = pred.home_team, pred.away_team

    def k1x2():
        if use_margin and margin > 0:
            return gm.apply_margin_1x2(mk.p1, mk.px, mk.p2, margin)
        return mk.k1(), mk.kx(), mk.k2()

    def k2way(p_a: float, p_b: float, ka: float, kb: float):
        if use_margin and margin > 0:
            return gm.apply_margin_two_way(p_a, p_b, margin)
        return ka, kb

    ka1, kax, ka2 = k1x2()
    lines: List[str] = []
    tag = " (с маржой)" if use_margin and margin > 0 else " (честные)"
    lines.append(f"=== {home} — {away}{' (нейтраль)' if neutral else ''} ===")
    lines.append(
        f"λ_h={pred.lambda_home:.3f}  λ_a={pred.lambda_away:.3f}   "
        f"D_final={pred.d_final:.3f}  S_final={pred.s_final:.3f}"
    )
    if pred.draw_target is not None and pred.draw_diagnostics is not None:
        dd = pred.draw_diagnostics
        lines.append(
            f"Ничья: модель→{pred.draw_target * 100:.1f}%  "
            f"(матрица {dd['draw_from_matrix'] * 100:.1f}% → "
            f"{dd['draw_after_calibration'] * 100:.1f}%, q={dd['diag_multiplier_used']:.3f})"
        )
    lines.append("")
    lines.append(f"Коэффициенты{tag}:")
    lines.append(
        f"  1X2:  П1={ka1:.2f}  X={kax:.2f}  П2={ka2:.2f}   "
        f"(p: {mk.p1 * 100:.1f}% / {mk.px * 100:.1f}% / {mk.p2 * 100:.1f}%)"
    )
    total = mk.main_total
    ot, ut = k2way(
        1 / total.home_or_over_odds,
        1 / total.away_or_under_odds,
        total.home_or_over_odds,
        total.away_or_under_odds,
    )
    lines.append(f"  Тотал {total.line}:  Over {ot:.2f} / Under {ut:.2f}")
    ah = mk.main_ah
    a_home, a_away = k2way(
        1 / ah.home_or_over_odds,
        1 / ah.away_or_under_odds,
        ah.home_or_over_odds,
        ah.away_or_under_odds,
    )
    lines.append(f"  Фора хозяев {ah.line:+}:  {a_home:.2f} / гости {a_away:.2f}")
    lines.append("")
    lines.append("Индивидуальные тоталы (честные O/U):")
    for ln, ov, un in mk.team_totals_home:
        lines.append(f"  {home} {ln}:  Over {ov:.2f} / Under {un:.2f}")
    for ln, ov, un in mk.team_totals_away:
        lines.append(f"  {away} {ln}:  Over {ov:.2f} / Under {un:.2f}")
    lines.append("")
    lines.append("Тоталы (честные O/U):")
    for ml in mk.totals:
        if 1.0 <= ml.line <= 4.5:
            lines.append(
                f"  {ml.line}:  Over {ml.home_or_over_odds:.2f} / "
                f"Under {ml.away_or_under_odds:.2f}"
            )
    lines.append("")
    lines.append("Топ счетов:")
    for i, j, p in mk.top_scores[:8]:
        lines.append(f"  {i}:{j}  {p * 100:.1f}%")

    summary = (
        f"1X2 {ka1:.2f}/{kax:.2f}/{ka2:.2f} · "
        f"Тотал {total.line} {ot:.2f}/{ut:.2f} · "
        f"Фора {ah.line:+g} {a_home:.2f}/{a_away:.2f}"
    )
    return lines, summary


class GoalLineHistory:
    """JSON-хранилище последних расчётов линии."""

    MAX_ENTRIES = 200

    def __init__(self, path: Optional[Path] = None):
        self._path = path or (user_data_dir() / "goal_line_history.json")
        self._entries: List[GoalLineHistoryEntry] = []
        self.load()

    def load(self) -> None:
        self._entries = []
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                self._entries.append(GoalLineHistoryEntry(**item))
            except TypeError:
                continue

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(e) for e in self._entries]
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def entries(self) -> List[GoalLineHistoryEntry]:
        return list(self._entries)

    def add(
        self,
        *,
        league: str,
        home_team: str,
        away_team: str,
        summary: str,
        at: Optional[datetime] = None,
    ) -> GoalLineHistoryEntry:
        entry = GoalLineHistoryEntry(
            at=(at or datetime.now()).isoformat(timespec="minutes"),
            league=league,
            home_team=home_team,
            away_team=away_team,
            summary=summary,
        )
        self._entries.insert(0, entry)
        self._entries = self._entries[: self.MAX_ENTRIES]
        self.save()
        return entry

    def clear(self) -> None:
        self._entries = []
        self.save()
