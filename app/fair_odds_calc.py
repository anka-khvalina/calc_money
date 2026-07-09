"""
Калькулятор средней A по линиям 1X2.

Фронт повторяет исходное приложение:
  колонки "Коэф дома" / "Коэф гости" / "A",
  поля "Результат", "A:", "X :", "Средняя А",
  кнопки "Считаем А лиги" и "Считать".

Добавлено:
  - поле выбора метода усреднения "Средней A";
  - фильтр аномальных выбросов по IQR (метод Тьюки x1.5) ПЕРЕД усреднением.

Мера строки:           A = (Коэф гости - 1) / (Коэф дома - 1)
Фильтр аномалий:       значение A считается аномальным, если выходит за
                       интервал [Q1 - 1.5*IQR ; Q3 + 1.5*IQR].
Итоговый коэффициент:  среднее (по умолчанию геометрическое) по значениям,
                       НЕ признанным аномальными.
"""

import csv
import io
import math
from pathlib import Path


# ---------------------------------------------------------------------------
# Вычислительное ядро (без GUI, импортируемо для тестов)
# ---------------------------------------------------------------------------

ARITHMETIC = "Среднеарифметическое"
GEOMETRIC = "Геометрическое"
TRIMMED_LINEAR = "Линейное усечённое 5%"
TRIMMED_GEOMETRIC = "Усечённо-геометрическое 5%"

METHODS = [ARITHMETIC, GEOMETRIC, TRIMMED_LINEAR, TRIMMED_GEOMETRIC]

TRIM_FRACTION = 0.05
IQR_K = 1.5


def row_a(k_home: float, k_away: float) -> float:
    """A для одной строки: (K_гости - 1) / (K_дома - 1)."""
    denom = k_home - 1.0
    if denom <= 0:
        raise ValueError(f"Коэф дома должен быть > 1 (получено {k_home})")
    if k_away - 1.0 <= 0:
        raise ValueError(f"Коэф гости должен быть > 1 (получено {k_away})")
    return (k_away - 1.0) / denom


def compute_a(home, away):
    """Список A по парам коэффициентов."""
    if len(home) != len(away):
        raise ValueError(
            f"Число коэффициентов не совпадает: дома {len(home)}, гости {len(away)}"
        )
    return [row_a(h, a) for h, a in zip(home, away)]


def percentile(sorted_values, p):
    """Перцентиль с линейной интерполяцией (как в C#-реализации).

    position = (n - 1) * p; интерполяция между соседними отсортированными
    значениями.
    """
    n = len(sorted_values)
    if n == 0:
        raise ValueError("Список значений пуст")
    position = (n - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(lower)]
    weight = position - lower
    return sorted_values[int(lower)] * (1 - weight) + sorted_values[int(upper)] * weight


def iqr_bounds(values, k=IQR_K):
    """Границы Тьюки [Q1 - k*IQR ; Q3 + k*IQR] и сами Q1, Q3, IQR."""
    s = sorted(values)
    q1 = percentile(s, 0.25)
    q3 = percentile(s, 0.75)
    iqr = q3 - q1
    lower = q1 - k * iqr
    upper = q3 + k * iqr
    return {"q1": q1, "q3": q3, "iqr": iqr, "lower": lower, "upper": upper}


def detect_outliers(values, k=IQR_K):
    """Список флагов is_outlier для каждого значения A.

    Аномалия: A < lower или A > upper.
    """
    b = iqr_bounds(values, k)
    flags = [(v < b["lower"]) or (v > b["upper"]) for v in values]
    return flags, b


def filter_valid(values, k=IQR_K):
    """Значения без аномалий (по IQR)."""
    flags, _ = detect_outliers(values, k)
    return [v for v, out in zip(values, flags) if not out]


def _trim(sorted_values, fraction):
    n = int(len(sorted_values) * fraction)
    if n == 0:
        return list(sorted_values)
    return sorted_values[n:len(sorted_values) - n]


def average(values, method=ARITHMETIC):
    """Среднее значений выбранным методом."""
    if not values:
        raise ValueError("Нет данных для расчёта")
    n = len(values)

    if method == ARITHMETIC:
        return sum(values) / n

    if method == GEOMETRIC:
        return math.exp(sum(math.log(v) for v in values) / n)

    if method == TRIMMED_LINEAR:
        core = _trim(sorted(values), TRIM_FRACTION)
        return sum(core) / len(core)

    if method == TRIMMED_GEOMETRIC:
        logs = sorted(math.log(v) for v in values)
        core = _trim(logs, TRIM_FRACTION)
        return math.exp(sum(core) / len(core))

    raise ValueError(f"Неизвестный метод: {method}")


def median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        raise ValueError("Нет данных для расчёта")
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


# ---------------------------------------------------------------------------
# Форматирование чисел (десятичная запятая, как в исходном приложении)
# ---------------------------------------------------------------------------

def fmt(value, digits=None):
    if digits is None:
        text = f"{value:.15g}"
    else:
        text = f"{value:.{digits}f}"
    return text.replace(".", ",")


def parse_column(text):
    """Числа из многострочного поля (по одному в строке), запятая или точка."""
    values = []
    for raw in text.splitlines():
        token = raw.strip().replace(",", ".")
        if not token:
            continue
        values.append(float(token))
    return values


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


def _parse_matches_text(text: str):
    """Парсинг матчей из текстового поля.

    Формат строки (csv/semicolon):
      home_team,away_team,odds_1,odds_x,odds_2
    """
    import team_ranking as tr

    sample = text.strip()
    if not sample:
        raise ValueError("Поле матчей пустое")

    # Пробуем угадать разделитель: ; или ,
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(sample), delimiter=delim)

    matches = []
    rows = [r for r in reader if r and any(cell.strip() for cell in r)]
    if not rows:
        raise ValueError("Не найдено валидных матчей")

    # Если первая строка похожа на заголовок (odds_* не числа) — пропускаем.
    start_idx = 0
    if len(rows[0]) >= 5:
        try:
            float(rows[0][2].strip().replace(",", "."))
            float(rows[0][3].strip().replace(",", "."))
            float(rows[0][4].strip().replace(",", "."))
        except Exception:
            start_idx = 1

    for row_idx, row in enumerate(rows[start_idx:], start=1 + start_idx):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) < 5:
            raise ValueError(
                f"Строка {row_idx}: нужно 5 полей (home, away, odds1, oddsX, odds2)"
            )
        home = row[0].strip()
        away = row[1].strip()
        if not home or not away:
            raise ValueError(f"Строка {row_idx}: пустое имя команды")
        try:
            odds_1 = float(row[2].strip().replace(",", "."))
            odds_x = float(row[3].strip().replace(",", "."))
            odds_2 = float(row[4].strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"Строка {row_idx}: коэффициенты должны быть числами") from exc

        matches.append(
            tr.MatchOdds(
                home_team=home,
                away_team=away,
                odds_1=odds_1,
                odds_x=odds_x,
                odds_2=odds_2,
            )
        )

    if not matches:
        raise ValueError("Не найдено валидных матчей")
    return matches


def build_app():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    import team_ranking as tr

    def _examples_dir() -> Path:
        try:
            from runtime_paths import examples_dir as _ed
        except ImportError:  # pragma: no cover
            return Path(__file__).resolve().parents[1] / "docs" / "examples"
        return _ed()

    root = tk.Tk()
    root.title("Калькулятор A + Рейтинг + История")
    root.geometry("1220x720")
    root.minsize(1020, 620)

    # ---- Clipboard UX: Ctrl+V / Shift+Insert + контекстное меню ----
    editable_widget = {"w": None}

    def _widget_state(widget):
        try:
            return str(widget.cget("state")).lower()
        except Exception:
            return "normal"

    def _is_editable(widget):
        # Text / Entry / ttk.Entry / ttk.Combobox (если не readonly/disabled)
        cls = widget.winfo_class().lower()
        if "text" in cls or "entry" in cls or "combobox" in cls:
            return _widget_state(widget) not in {"disabled", "readonly"}
        return False

    def _do_virtual(event_name):
        w = editable_widget["w"]
        if w is None:
            return
        try:
            w.event_generate(event_name)
        except Exception:
            pass

    def _remember_focus(event):
        editable_widget["w"] = event.widget

    def _paste_shortcut(event):
        editable_widget["w"] = event.widget
        if _is_editable(event.widget):
            event.widget.event_generate("<<Paste>>")
            return "break"
        return None

    def _copy_shortcut(event):
        editable_widget["w"] = event.widget
        try:
            event.widget.event_generate("<<Copy>>")
            return "break"
        except Exception:
            return None

    def _cut_shortcut(event):
        editable_widget["w"] = event.widget
        if _is_editable(event.widget):
            event.widget.event_generate("<<Cut>>")
            return "break"
        return None

    def _select_all_shortcut(event):
        editable_widget["w"] = event.widget
        w = event.widget
        try:
            cls = w.winfo_class().lower()
            if "text" in cls:
                w.tag_add("sel", "1.0", "end-1c")
            elif "entry" in cls or "combobox" in cls:
                w.selection_range(0, "end")
            return "break"
        except Exception:
            return None

    menu = tk.Menu(root, tearoff=0)
    menu.add_command(label="Вырезать", command=lambda: _do_virtual("<<Cut>>"))
    menu.add_command(label="Копировать", command=lambda: _do_virtual("<<Copy>>"))
    menu.add_command(label="Вставить", command=lambda: _do_virtual("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Выделить всё", command=lambda: _do_virtual("<<SelectAll>>"))

    def _show_context_menu(event):
        editable_widget["w"] = event.widget
        if not _is_editable(event.widget) and "entry" not in event.widget.winfo_class().lower():
            # Для не редактируемых полей оставляем только копирование.
            menu.entryconfig("Вырезать", state="disabled")
            menu.entryconfig("Вставить", state="disabled")
        else:
            menu.entryconfig("Вырезать", state="normal")
            menu.entryconfig("Вставить", state="normal")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    root.bind_all("<FocusIn>", _remember_focus, add="+")
    root.bind_all("<Control-v>", _paste_shortcut, add="+")
    root.bind_all("<Control-V>", _paste_shortcut, add="+")
    root.bind_all("<Shift-Insert>", _paste_shortcut, add="+")
    root.bind_all("<Control-c>", _copy_shortcut, add="+")
    root.bind_all("<Control-C>", _copy_shortcut, add="+")
    root.bind_all("<Control-x>", _cut_shortcut, add="+")
    root.bind_all("<Control-X>", _cut_shortcut, add="+")
    root.bind_all("<Control-a>", _select_all_shortcut, add="+")
    root.bind_all("<Control-A>", _select_all_shortcut, add="+")
    root.bind_all("<Button-3>", _show_context_menu, add="+")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    # ======================================================================
    # TAB 1: калькулятор A
    # ======================================================================
    tab_a = ttk.Frame(notebook, padding=10)
    notebook.add(tab_a, text="Калькулятор A")

    left = ttk.Frame(tab_a)
    left.pack(side="left", fill="both", expand=True)

    headers = ["Коэф дома", "Коэф гости", "A"]
    for col, title in enumerate(headers):
        ttk.Label(left, text=title).grid(row=0, column=col, padx=6, sticky="w")

    text_home = tk.Text(left, width=14, height=24, relief="solid", borderwidth=1)
    text_away = tk.Text(left, width=14, height=24, relief="solid", borderwidth=1)
    text_a = tk.Text(
        left, width=16, height=24, relief="solid", borderwidth=1, background="#f4f4f4"
    )
    text_a.tag_configure("outlier", foreground="#c0392b")
    text_a.configure(state="disabled")

    text_home.grid(row=1, column=0, padx=6, pady=4, sticky="nsew")
    text_away.grid(row=1, column=1, padx=6, pady=4, sticky="nsew")
    text_a.grid(row=1, column=2, padx=6, pady=4, sticky="nsew")
    left.rowconfigure(1, weight=1)
    for c in range(3):
        left.columnconfigure(c, weight=1)

    text_home.insert("1.0", "2.02\n1.88\n2.42\n2.07\n")
    text_away.insert("1.0", "2.21\n2.11\n2.79\n2.36\n")

    right = ttk.Frame(tab_a, padding=(16, 0, 0, 0))
    right.pack(side="left", fill="y")

    def labeled_entry(parent, label):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(8, 0))
        var = tk.StringVar()
        ttk.Entry(parent, textvariable=var, width=32).pack(anchor="w")
        return var

    var_result = labeled_entry(right, "Результат:")
    var_a = labeled_entry(right, "A:")
    var_x = labeled_entry(right, "X :")
    var_mean = labeled_entry(right, "Средняя А")

    ttk.Label(right, text="Метод расчёта").pack(anchor="w", pady=(12, 0))
    method_var = tk.StringVar(value=GEOMETRIC)
    ttk.Combobox(
        right, textvariable=method_var, values=METHODS, state="readonly", width=30
    ).pack(anchor="w")

    drop_outliers = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        right, text="Исключать аномалии (IQR x1.5)", variable=drop_outliers
    ).pack(anchor="w", pady=(8, 0))

    info_var = tk.StringVar(value="")
    ttk.Label(right, textvariable=info_var, justify="left", foreground="#555").pack(
        anchor="w", pady=(8, 0)
    )

    def read_columns():
        home = parse_column(text_home.get("1.0", "end"))
        away = parse_column(text_away.get("1.0", "end"))
        return home, away

    def fill_a_column(a_values, flags):
        text_a.configure(state="normal")
        text_a.delete("1.0", "end")
        for v, out in zip(a_values, flags):
            line = fmt(round(v, 2), 3)
            if out:
                line += "  ✕"
                text_a.insert("end", line + "\n", "outlier")
            else:
                text_a.insert("end", line + "\n")
        text_a.configure(state="disabled")

    def analyze():
        home, away = read_columns()
        a_values = compute_a(home, away)
        if drop_outliers.get() and len(a_values) >= 4:
            flags, b = detect_outliers(a_values)
        else:
            flags = [False] * len(a_values)
            b = None
        fill_a_column(a_values, flags)
        valid = [v for v, out in zip(a_values, flags) if not out]
        if not valid:
            raise ValueError("Все значения помечены как аномалии — нет данных")
        return valid, flags, b

    def show_results(valid, flags, b, league=False):
        n_out = sum(flags)
        mean_sel = average(valid, method_var.get())
        var_mean.set(fmt(mean_sel))
        var_result.set(
            fmt(average(valid, GEOMETRIC), 3) if not league else fmt(average(valid, GEOMETRIC))
        )
        var_a.set(fmt(average(valid, ARITHMETIC)))
        var_x.set(fmt(median(valid)))
        lines = [f"строк: {len(flags)}   валидных: {len(valid)}   аномалий: {n_out}"]
        if b is not None:
            lines.append(
                f"Q1={fmt(b['q1'], 3)}  Q3={fmt(b['q3'], 3)}  IQR={fmt(b['iqr'], 3)}"
            )
            lines.append(f"границы: [{fmt(b['lower'], 3)} ; {fmt(b['upper'], 3)}]")
        lines.append(
            f"геом={fmt(average(valid, GEOMETRIC), 4)}  "
            f"ариф={fmt(average(valid, ARITHMETIC), 4)}  "
            f"медиана={fmt(median(valid), 4)}"
        )
        info_var.set("\n".join(lines))

    def calc():
        try:
            valid, flags, b = analyze()
            show_results(valid, flags, b, league=False)
        except (ValueError, ZeroDivisionError) as exc:
            messagebox.showerror("Ошибка", str(exc))

    def calc_league():
        try:
            valid, flags, b = analyze()
            show_results(valid, flags, b, league=True)
        except (ValueError, ZeroDivisionError) as exc:
            messagebox.showerror("Ошибка", str(exc))

    ttk.Button(right, text="Считаем А лиги", command=calc_league).pack(
        anchor="w", pady=(16, 4), ipadx=20, ipady=4
    )
    ttk.Button(right, text="Считать", command=calc).pack(
        anchor="w", pady=4, ipadx=20, ipady=4
    )

    # ======================================================================
    # TAB 2: рейтинг команд
    # ======================================================================
    import history_store as hs
    import team_registry as tg
    import supabase_teams as sb
    import supabase_history as sbh
    import userbet_odds as ubo

    tab_rank = ttk.Frame(notebook, padding=10)
    notebook.add(tab_rank, text="Рейтинг команд")

    rank_left = ttk.Frame(tab_rank)
    rank_left.pack(side="left", fill="both", expand=True)
    rank_right = ttk.Frame(tab_rank, padding=(12, 0, 0, 0))
    rank_right.pack(side="left", fill="both")

    ttk.Label(
        rank_left,
        text=(
            "Матчи (по строке): home_team,away_team,odds_1,odds_x,odds_2\n"
            "Команды должны быть в справочнике выбранной лиги (с id)."
        ),
    ).pack(anchor="w")

    text_matches = tk.Text(rank_left, width=82, height=30, relief="solid", borderwidth=1)
    text_matches.pack(fill="both", expand=True, pady=(6, 0))

    sample_lines = []
    sample_path = _examples_dir() / "season_odds_la_liga_2024_25.csv"
    if sample_path.exists():
        try:
            with sample_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sample_lines.append(
                        f"{row['home_team']},{row['away_team']},{row['p1']},{row['x']},{row['p2']}"
                    )
        except Exception:
            sample_lines = []
    if not sample_lines:
        sample_lines = [
            "Barcelona,Real Madrid,1.72,4.00,4.80",
            "Real Madrid,Atletico Madrid,2.10,3.50,3.40",
            "Atletico Madrid,Valencia,1.70,3.80,5.00",
        ]
    text_matches.insert("1.0", "\n".join(sample_lines) + "\n")

    controls = ttk.Frame(rank_right)
    controls.pack(fill="x", anchor="n")

    ttk.Label(controls, text="Параметры расчёта").pack(anchor="w")

    rank_league_labels = [title for title, _key in hs.format_league_options()]
    rank_league_keys = {title: key for title, key in hs.format_league_options()}
    rank_league_var = tk.StringVar(value=rank_league_labels[0])
    ttk.Label(controls, text="Лига (справочник):").pack(anchor="w", pady=(8, 0))
    ttk.Combobox(
        controls,
        textvariable=rank_league_var,
        values=rank_league_labels,
        state="readonly",
        width=28,
    ).pack(anchor="w")

    def _rank_league_key():
        title = rank_league_var.get().strip()
        key = rank_league_keys.get(title)
        if not key:
            key = hs.normalize_league(title)
        return key

    top_var = tk.StringVar(value="0")
    ttk.Label(controls, text="TOP N (0 = все):").pack(anchor="w", pady=(8, 0))
    ttk.Entry(controls, textvariable=top_var, width=10).pack(anchor="w")

    robust_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        controls,
        text="Робастная оценка (Huber + вес)",
        variable=robust_var,
    ).pack(anchor="w", pady=(8, 0))

    btns = ttk.Frame(controls)
    btns.pack(anchor="w", pady=(8, 0))

    stats_var = tk.StringVar(value="Результат пока не рассчитан")
    ttk.Label(controls, textvariable=stats_var, justify="left").pack(anchor="w", pady=(10, 6))

    ttk.Label(rank_right, text="Рейтинг команд").pack(anchor="w")
    tree = ttk.Treeview(
        rank_right,
        columns=("rank", "id", "team", "rating", "coef"),
        show="headings",
        height=20,
    )
    tree.heading("rank", text="№")
    tree.heading("id", text="ID")
    tree.heading("team", text="Команда")
    tree.heading("rating", text="Рейтинг")
    tree.heading("coef", text="Коэф. силы")
    tree.column("rank", width=44, anchor="e")
    tree.column("id", width=72, anchor="w")
    tree.column("team", width=160, anchor="w")
    tree.column("rating", width=100, anchor="e")
    tree.column("coef", width=100, anchor="e")
    tree.pack(fill="both", expand=True)

    latest_result = {"value": None, "team_ids": {}}

    def fill_tree(result, top_n, team_ids=None):
        team_ids = team_ids or {}
        for item in tree.get_children():
            tree.delete(item)
        rows = result.teams if top_n <= 0 else result.teams[:top_n]
        for i, r in enumerate(rows, start=1):
            tid = team_ids.get(r.team, "")
            tree.insert(
                "",
                "end",
                values=(
                    i,
                    tid,
                    r.team,
                    f"{r.rating:.3f}".replace(".", ","),
                    f"{r.strength_coef:.4f}".replace(".", ","),
                ),
            )

    def run_ranking(matches, top_n):
        league_key = _rank_league_key()
        team_ids = tg.validate_matches_teams(league_key, matches)
        result = tr.build_ranking(matches, robust=robust_var.get())
        latest_result["value"] = result
        latest_result["team_ids"] = team_ids
        fill_tree(result, top_n, team_ids)
        lines = [
            f"Матчей: {result.matches_count}",
            f"Команд: {len(result.teams)}",
            f"H: {str(round(result.home_advantage, 3)).replace('.', ',')}  "
            f"(x{str(round(result.home_advantage_coef, 4)).replace('.', ',')})",
        ]
        if result.method == "robust":
            shift = result.home_advantage - result.home_advantage_ols
            lines.append(
                f"H (OLS): {str(round(result.home_advantage_ols, 3)).replace('.', ',')}  "
                f"(сдвиг {('%+.1f' % shift).replace('.', ',')})"
            )
            lines.append(f"Задавлено матчей: {len(result.downweighted)}")
        lines.append(f"RMSE: {str(round(result.rmse, 3)).replace('.', ',')}")
        stats_var.set("\n".join(lines))

    def parse_top():
        raw = top_var.get().strip()
        if not raw:
            return 0
        try:
            val = int(raw)
        except ValueError as exc:
            raise ValueError("TOP N должен быть целым числом") from exc
        if val < 0:
            raise ValueError("TOP N не может быть отрицательным")
        return val

    def calc_from_text():
        try:
            matches = _parse_matches_text(text_matches.get("1.0", "end"))
            run_ranking(matches, parse_top())
        except Exception as exc:
            messagebox.showerror("Ошибка расчёта рейтинга", str(exc))

    def load_csv():
        path = filedialog.askopenfilename(
            title="Выберите CSV матчей",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            matches = tr.load_matches_csv(Path(path))
            lines = [
                f"{m.home_team},{m.away_team},{m.odds_1},{m.odds_x},{m.odds_2}"
                for m in matches
            ]
            text_matches.delete("1.0", "end")
            text_matches.insert("1.0", "\n".join(lines) + "\n")
            run_ranking(matches, parse_top())
        except Exception as exc:
            messagebox.showerror("Ошибка загрузки CSV", str(exc))

    def save_rating_csv():
        result = latest_result["value"]
        if result is None:
            messagebox.showwarning("Нет данных", "Сначала рассчитайте рейтинг.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить рейтинг CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile="rating.csv",
        )
        if not path:
            return
        try:
            tr.save_ranking_csv(Path(path), result)
        except Exception as exc:
            messagebox.showerror("Ошибка сохранения", str(exc))
            return
        messagebox.showinfo("Готово", f"Рейтинг сохранён:\n{path}")

    ttk.Button(btns, text="Рассчитать рейтинг", command=calc_from_text).grid(
        row=0, column=0, padx=(0, 6)
    )
    ttk.Button(btns, text="Загрузить CSV", command=load_csv).grid(row=0, column=1, padx=(0, 6))
    ttk.Button(btns, text="Сохранить рейтинг CSV", command=save_rating_csv).grid(row=0, column=2)

    # ======================================================================
    # TAB 3: Справочник команд
    # ======================================================================

    tab_teams = ttk.Frame(notebook, padding=10)
    notebook.add(tab_teams, text="Справочник команд")

    ttk.Label(
        tab_teams,
        text="Справочник команд. Логотип: выберите лигу → команду (ID) → файл PNG/JPEG.",
        foreground="#555",
        justify="left",
    ).pack(anchor="w", pady=(0, 8))

    teams_top = ttk.Frame(tab_teams)
    teams_top.pack(fill="x", anchor="n")

    teams_league_var = tk.StringVar(value="")
    teams_league_ids: dict[str, str] = {}
    teams_team_names: dict[str, str] = {}
    teams_cache: list[sb.Team] = []
    teams_leagues_loaded = False

    ttk.Label(teams_top, text="Лига:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
    teams_league_combo = ttk.Combobox(
        teams_top,
        textvariable=teams_league_var,
        values=[],
        state="disabled",
        width=28,
    )
    teams_league_combo.grid(row=0, column=1, sticky="w", pady=4)

    teams_logo_bar = ttk.LabelFrame(tab_teams, text="Логотип команды", padding=8)
    teams_logo_bar.pack(fill="x", pady=(10, 0))
    teams_logo_team_var = tk.StringVar(value="")
    teams_logo_path_var = tk.StringVar(value="")
    teams_logo_team_labels: dict[str, str] = {}
    teams_logo_team_ids: dict[str, str] = {}

    ttk.Label(teams_logo_bar, text="Команда (ID):").grid(
        row=0, column=0, sticky="w", padx=(0, 8), pady=4
    )
    teams_logo_team_combo = ttk.Combobox(
        teams_logo_bar,
        textvariable=teams_logo_team_var,
        state="readonly",
        width=42,
    )
    teams_logo_team_combo.grid(row=0, column=1, columnspan=2, sticky="w", pady=4)

    teams_add_bar = ttk.Frame(tab_teams)
    teams_add_bar.pack(fill="x", pady=(10, 0))
    teams_name_var = tk.StringVar(value="")
    teams_status_var = tk.StringVar(value="")
    ttk.Label(teams_add_bar, text="Новая команда:").grid(row=0, column=0, sticky="w", padx=(0, 8))
    ttk.Entry(teams_add_bar, textvariable=teams_name_var, width=32).grid(row=0, column=1, sticky="w")
    teams_add_btn = ttk.Button(teams_add_bar, text="Добавить")
    teams_add_btn.grid(row=0, column=2, sticky="w", padx=(12, 0))

    teams_tree = ttk.Treeview(
        tab_teams,
        columns=("num", "name"),
        show="tree headings",
        height=16,
    )
    teams_tree.heading("#0", text="")
    teams_tree.heading("num", text="№")
    teams_tree.heading("name", text="Команда")
    teams_tree.column("#0", width=36, stretch=False, anchor="center")
    teams_tree.column("num", width=44, anchor="e")
    teams_tree.column("name", width=320, anchor="w")
    teams_tree.pack(fill="both", expand=True, pady=(12, 0))

    _teams_logo_photos: dict[str, tk.PhotoImage] = {}

    def _load_team_logo_photo(team_id: str, cache: dict[str, tk.PhotoImage], size: int = 24):
        if not team_id or not tg.has_logo(team_id):
            return None
        if team_id in cache:
            return cache[team_id]
        try:
            img = tk.PhotoImage(file=str(tg.logo_path(team_id)), master=root)
            w, h = img.width(), img.height()
            if w > size or h > size:
                factor = max(w // size, h // size, 1)
                img = img.subsample(factor, factor)
            cache[team_id] = img
            return img
        except tk.TclError:
            return None

    def refresh_logo_team_options(select_team_id: str | None = None):
        teams_logo_team_labels.clear()
        teams_logo_team_ids.clear()
        labels: list[str] = []
        for ent in teams_cache:
            label = f"{ent.id} — {ent.name_team}"
            labels.append(label)
            teams_logo_team_labels[label] = ent.id_str
            teams_logo_team_ids[ent.id_str] = label
        teams_logo_team_combo["values"] = labels
        if select_team_id and select_team_id in teams_logo_team_ids:
            teams_logo_team_var.set(teams_logo_team_ids[select_team_id])
        elif labels:
            if teams_logo_team_var.get() not in labels:
                teams_logo_team_var.set(labels[0])
        else:
            teams_logo_team_var.set("")

    def _selected_logo_team_id() -> str:
        label = teams_logo_team_var.get().strip()
        return teams_logo_team_labels.get(label, "")

    def browse_team_logo_file():
        path = filedialog.askopenfilename(
            title="Логотип команды (PNG или JPEG)",
            filetypes=[
                ("PNG / JPEG", "*.png *.jpg *.jpeg"),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
            ],
        )
        if path:
            teams_logo_path_var.set(path)

    def upload_team_logo_by_id():
        league_name = teams_league_var.get().strip()
        team_id = _selected_logo_team_id()
        if not team_id:
            messagebox.showwarning(
                "Справочник",
                f"Выберите команду лиги {league_name or '—'}.",
            )
            return
        team_name = teams_team_names.get(team_id, team_id)
        path = teams_logo_path_var.get().strip()
        if not path:
            messagebox.showwarning("Справочник", "Выберите файл логотипа (PNG или JPEG).")
            return
        try:
            tg.set_team_logo(team_id, path, require_registry=False)
        except ValueError as exc:
            messagebox.showerror("Справочник", str(exc))
            return
        teams_logo_path_var.set("")
        show_teams_for_league()
        messagebox.showinfo("Справочник", f"Логотип сохранён для {team_name} ({team_id}).")

    def remove_team_logo_by_id():
        league_name = teams_league_var.get().strip()
        team_id = _selected_logo_team_id()
        if not team_id:
            messagebox.showwarning(
                "Справочник",
                f"Выберите команду лиги {league_name or '—'}.",
            )
            return
        team_name = teams_team_names.get(team_id, team_id)
        if not tg.has_logo(team_id):
            messagebox.showinfo("Справочник", f"У команды {team_name} нет логотипа.")
            return
        tg.remove_team_logo(team_id)
        show_teams_for_league()
        messagebox.showinfo("Справочник", f"Логотип удалён для {team_name}.")

    ttk.Label(teams_logo_bar, text="Файл:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Entry(teams_logo_bar, textvariable=teams_logo_path_var, width=48, state="readonly").grid(
        row=1, column=1, columnspan=2, sticky="we", pady=4
    )
    ttk.Button(teams_logo_bar, text="Обзор…", command=browse_team_logo_file).grid(
        row=1, column=3, sticky="w", padx=(8, 0), pady=4
    )
    ttk.Button(teams_logo_bar, text="Загрузить логотип", command=upload_team_logo_by_id).grid(
        row=2, column=1, sticky="w", pady=(8, 0)
    )
    ttk.Button(teams_logo_bar, text="Удалить логотип", command=remove_team_logo_by_id).grid(
        row=2, column=2, sticky="w", padx=(8, 0), pady=(8, 0)
    )
    teams_logo_bar.columnconfigure(1, weight=1)

    def on_teams_tree_select(_event=None):
        sel = teams_tree.selection()
        if sel:
            refresh_logo_team_options(select_team_id=sel[0])

    teams_tree.bind("<<TreeviewSelect>>", on_teams_tree_select)

    def _teams_league_id() -> str:
        name = teams_league_var.get().strip()
        return teams_league_ids.get(name, "")

    def clear_teams_tree():
        for item in teams_tree.get_children():
            teams_tree.delete(item)

    def show_teams_for_league():
        clear_teams_tree()
        _teams_logo_photos.clear()
        league_id = _teams_league_id()
        league_name = teams_league_var.get().strip()
        if not league_id:
            teams_status_var.set("")
            refresh_logo_team_options()
            return
        teams_status_var.set("Загрузка команд…")
        try:
            entries = sb.fetch_teams(league_id)
        except sb.SupabaseError:
            messagebox.showerror("Справочник", "Не удалось загрузить команды выбранной лиги")
            teams_status_var.set(f"{league_name} — ошибка загрузки")
            return
        teams_cache.clear()
        teams_cache.extend(entries)
        teams_team_names.clear()
        for i, ent in enumerate(entries, start=1):
            teams_team_names[ent.id_str] = ent.name_team
            display = f"{ent.name_team} (id:{ent.id})"
            img = _load_team_logo_photo(ent.id_str, _teams_logo_photos)
            kw: dict = {"values": (i, display)}
            if img:
                kw["image"] = img
            teams_tree.insert("", "end", iid=ent.id_str, **kw)
        teams_status_var.set(
            f"{league_name} — {len(entries)} команд"
            if entries
            else f"{league_name} — справочник пуст"
        )
        refresh_logo_team_options()

    def load_teams_leagues():
        nonlocal teams_leagues_loaded
        teams_status_var.set("Загрузка лиг…")
        teams_league_combo.config(state="disabled")
        try:
            leagues = sb.fetch_leagues()
        except sb.SupabaseError:
            messagebox.showerror("Справочник", "Не удалось загрузить список лиг")
            teams_status_var.set("")
            return
        teams_league_ids.clear()
        names: list[str] = []
        for lg in leagues:
            names.append(lg.name)
            teams_league_ids[lg.name] = lg.id
        teams_league_combo["values"] = names
        if not names:
            teams_status_var.set("Список лиг пуст")
            return
        teams_league_combo.config(state="readonly")
        if teams_league_var.get() not in names:
            teams_league_var.set(names[0])
        teams_leagues_loaded = True
        show_teams_for_league()

    def add_team_entry():
        league_id = _teams_league_id()
        if not league_id:
            messagebox.showwarning("Справочник", "Выберите лигу")
            return
        name = teams_name_var.get().strip()
        if not name:
            messagebox.showwarning("Справочник", "Введите название команды")
            return
        teams_add_btn.config(state="disabled")
        try:
            ent = sb.create_team(league_id, name)
        except ValueError as exc:
            messagebox.showerror("Справочник", str(exc))
            return
        finally:
            teams_add_btn.config(state="normal")
        teams_name_var.set("")
        show_teams_for_league()
        refresh_logo_team_options(select_team_id=ent.id_str)
        messagebox.showinfo("Справочник", f"Добавлено: {ent.name_team}")

    teams_league_combo.bind(
        "<<ComboboxSelected>>",
        lambda _e: show_teams_for_league(),
    )
    teams_add_btn.config(command=add_team_entry)
    ttk.Label(tab_teams, textvariable=teams_status_var, foreground="#555", justify="left").pack(
        anchor="w", pady=(10, 0)
    )

    def on_teams_tab_selected(_event=None):
        if notebook.tab(notebook.select(), "text") == "Справочник команд":
            if not teams_leagues_loaded:
                load_teams_leagues()

    notebook.bind("<<NotebookTabChanged>>", on_teams_tab_selected)

    teams_show_on_start = load_teams_leagues

    # ======================================================================
    # TAB 4: Счет кэф (метод Shin)
    # ======================================================================
    import match_shin_calc as msc

    tab_shin = ttk.Frame(notebook, padding=10)
    notebook.add(tab_shin, text="Счет кэф")

    shin_form = ttk.Frame(tab_shin)
    shin_form.pack(fill="x", anchor="n")

    shin_team1_var = tk.StringVar(value="")
    shin_team2_var = tk.StringVar(value="")
    shin_league_var = tk.StringVar(value=hs.format_league_options()[0][0])
    shin_season_var = tk.StringVar(value="2025-26")

    def shin_labeled_entry(parent, row, label, var, width=32):
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=4
        )
        ttk.Entry(parent, textvariable=var, width=width).grid(
            row=row, column=1, sticky="w", pady=4
        )

    shin_league_labels = [title for title, _key in hs.format_league_options()]
    shin_league_keys = {title: key for title, key in hs.format_league_options()}

    def _shin_league_key():
        title = shin_league_var.get().strip()
        key = shin_league_keys.get(title)
        if not key:
            key = hs.normalize_league(title)
        return key

    def refresh_shin_team_options(_event=None):
        league_key = _shin_league_key()
        opts = tg.format_team_options(league_key)
        shin_team1_combo["values"] = opts
        shin_team2_combo["values"] = opts
        if opts:
            if not shin_team1_var.get() or shin_team1_var.get() not in opts:
                shin_team1_var.set(opts[0])
            if not shin_team2_var.get() or shin_team2_var.get() not in opts:
                shin_team2_var.set(opts[1] if len(opts) > 1 else opts[0])
        else:
            shin_team1_var.set("")
            shin_team2_var.set("")

    ttk.Label(shin_form, text="Команда 1:").grid(
        row=0, column=0, sticky="w", padx=(0, 8), pady=4
    )
    shin_team1_combo = ttk.Combobox(
        shin_form,
        textvariable=shin_team1_var,
        state="readonly",
        width=36,
    )
    shin_team1_combo.grid(row=0, column=1, sticky="w", pady=4)
    ttk.Label(shin_form, text="Команда 2:").grid(
        row=1, column=0, sticky="w", padx=(0, 8), pady=4
    )
    shin_team2_combo = ttk.Combobox(
        shin_form,
        textvariable=shin_team2_var,
        state="readonly",
        width=36,
    )
    shin_team2_combo.grid(row=1, column=1, sticky="w", pady=4)
    ttk.Label(shin_form, text="Лига:").grid(
        row=2, column=0, sticky="w", padx=(0, 8), pady=4
    )
    shin_league_combo = ttk.Combobox(
        shin_form,
        textvariable=shin_league_var,
        values=shin_league_labels,
        state="readonly",
        width=26,
    )
    shin_league_combo.grid(row=2, column=1, sticky="w", pady=4)
    shin_league_combo.bind("<<ComboboxSelected>>", refresh_shin_team_options)
    shin_labeled_entry(shin_form, 3, "Сезон:", shin_season_var, 12)
    refresh_shin_team_options()

    shin_out = ttk.LabelFrame(tab_shin, text="Результат (метод Shin)", padding=10)

    shin_title_frame = ttk.Frame(shin_out)
    shin_title_frame.pack(anchor="w", fill="x", pady=(0, 8))

    shin_logo1_lbl = ttk.Label(shin_title_frame)
    shin_logo1_lbl.grid(row=0, column=0, padx=(0, 4))
    shin_title_team1_lbl = ttk.Label(shin_title_frame, font=("", 12, "bold"))
    shin_title_team1_lbl.grid(row=0, column=1, padx=(0, 6))
    ttk.Label(shin_title_frame, text="—", font=("", 12, "bold")).grid(row=0, column=2, padx=(0, 6))
    shin_logo2_lbl = ttk.Label(shin_title_frame)
    shin_logo2_lbl.grid(row=0, column=3, padx=(0, 4))
    shin_title_team2_lbl = ttk.Label(shin_title_frame, font=("", 12, "bold"))
    shin_title_team2_lbl.grid(row=0, column=4, padx=(0, 6))
    shin_title_odds_lbl = ttk.Label(shin_title_frame, font=("", 11), foreground="#1f3ea6")
    shin_title_odds_lbl.grid(row=0, column=5, sticky="w")

    _shin_logo_photos: dict[str, tk.PhotoImage] = {}

    def _set_shin_logo_label(lbl: ttk.Label, team_id: str):
        img = _load_team_logo_photo(team_id, _shin_logo_photos, size=28)
        if img:
            lbl.configure(image=img, text="")
        else:
            lbl.configure(image="", text="")

    shin_odds_frame = ttk.Frame(shin_out)
    shin_odds_frame.pack(fill="x", pady=(0, 10))

    shin_odds_tree = ttk.Treeview(
        shin_odds_frame,
        columns=("outcome", "k", "p"),
        show="tree headings",
        height=3,
    )
    shin_odds_tree.heading("#0", text="")
    shin_odds_tree.heading("outcome", text="Исход")
    shin_odds_tree.heading("k", text="k (коэфф.)")
    shin_odds_tree.heading("p", text="p, %")
    shin_odds_tree.column("#0", width=32, stretch=False)
    shin_odds_tree.column("outcome", width=200, anchor="w")
    shin_odds_tree.column("k", width=100, anchor="e")
    shin_odds_tree.column("p", width=100, anchor="e")
    shin_odds_tree.pack(side="left", fill="x", expand=True)

    shin_meta_frame = ttk.Frame(shin_out)
    shin_meta_frame.pack(fill="x")

    shin_meta_tree = ttk.Treeview(
        shin_meta_frame,
        columns=("param", "value"),
        show="headings",
        height=6,
    )
    shin_meta_tree.heading("param", text="Параметр")
    shin_meta_tree.heading("value", text="Значение")
    shin_meta_tree.column("param", width=200, anchor="w")
    shin_meta_tree.column("value", width=280, anchor="w")
    shin_meta_tree.pack(side="left", fill="x", expand=True)

    ttk.Label(shin_out, text="Подробный расчёт").pack(anchor="w", pady=(12, 4))
    shin_details = tk.Text(
        shin_out, width=95, height=18, relief="solid", borderwidth=1, font=("Consolas", 10)
    )
    shin_details.pack(fill="both", expand=True, pady=(0, 4))
    shin_details.configure(state="disabled")

    def _clear_shin_trees():
        for tree in (shin_odds_tree, shin_meta_tree):
            for item in tree.get_children():
                tree.delete(item)

    def _fill_shin_result(res: msc.ShinMatchResult):
        _clear_shin_trees()
        _shin_logo_photos.clear()
        shin_title_team1_lbl.configure(text=res.team1)
        shin_title_team2_lbl.configure(text=res.team2)
        shin_title_odds_lbl.configure(text=f"k1 / kx / k2: {res.format_odds(2)}")
        _set_shin_logo_label(shin_logo1_lbl, res.team1_id)
        _set_shin_logo_label(shin_logo2_lbl, res.team2_id)

        img1 = _load_team_logo_photo(res.team1_id, _shin_logo_photos)
        row1: dict = {
            "values": (f"P1 ({res.team1})", fmt(res.k1, 2), fmt(res.p1 * 100, 2) + " %"),
        }
        if img1:
            row1["image"] = img1
        shin_odds_tree.insert("", "end", **row1)
        shin_odds_tree.insert(
            "",
            "end",
            values=("X (ничья)", fmt(res.kx, 2), fmt(res.px * 100, 2) + " %"),
        )
        img2 = _load_team_logo_photo(res.team2_id, _shin_logo_photos)
        row2: dict = {
            "values": (f"P2 ({res.team2})", fmt(res.k2, 2), fmt(res.p2 * 100, 2) + " %"),
        }
        if img2:
            row2["image"] = img2
        shin_odds_tree.insert("", "end", **row2)
        meta_rows = [
            ("D (целевой матч, для X)", fmt(res.d_market, 1)),
            ("H (домашнее преимущество)", fmt(res.h_used, 1) if res.h_used is not None else "—"),
            ("Источник данных", res.source_label_ru),
            ("Сезон расчёта", res.season_used),
            ("Матчей в расчёте", str(res.matches_used)),
            ("Метод", res.method),
        ]
        if res.d_chain is not None:
            meta_rows.insert(1, ("D (цепь через соперника)", fmt(res.d_chain, 1)))
        if res.common_opponent:
            meta_rows.insert(3, ("Общий соперник", res.common_opponent))
        if res.team1_id:
            meta_rows.append(("ID команды 1", res.team1_id))
        if res.team2_id:
            meta_rows.append(("ID команды 2", res.team2_id))
        if res.team1_new or res.team2_new:
            meta_rows.append(
                (
                    "Новые команды",
                    ", ".join(
                        n
                        for n, flag in ((res.team1, res.team1_new), (res.team2, res.team2_new))
                        if flag
                    ),
                )
            )
        for param, value in meta_rows:
            shin_meta_tree.insert("", "end", values=(param, value))
        shin_details.configure(state="normal")
        shin_details.delete("1.0", "end")
        if res.details:
            shin_details.insert("1.0", res.details)
        else:
            shin_details.insert("1.0", "Подробный лог недоступен.")
        shin_details.configure(state="disabled")

    def calc_shin_match():
        try:
            league_key = _shin_league_key()
            t1_ref = shin_team1_var.get().strip()
            t2_ref = shin_team2_var.get().strip()
            if not t1_ref or not t2_ref:
                raise msc.ShinCalculationError(
                    "Выберите команды из справочника (вкладка «Справочник команд»)."
                )
            res = msc.calculate_shin_match(
                t1_ref,
                t2_ref,
                league_key,
                shin_season_var.get().strip(),
            )
            _fill_shin_result(res)
        except msc.ShinCalculationError as exc:
            messagebox.showerror("Счет кэф", str(exc))
        except Exception as exc:
            messagebox.showerror("Счет кэф", str(exc))

    shin_actions = ttk.Frame(tab_shin)
    shin_actions.pack(fill="x", pady=(8, 0))
    ttk.Button(shin_actions, text="Рассчитать", command=calc_shin_match).pack(
        anchor="w", ipadx=16, ipady=4
    )
    ttk.Label(
        tab_shin,
        text=(
            "Метод Shin: P1/P2 — формула Shin; ничья — draw-модель px(d).\n"
            "Команды выбираются по id из справочника. Новые команды без матчей в сезоне: R=0.\n"
            "Приоритет: общий соперник → матчи лиги (≥3) → предыдущий сезон."
        ),
        foreground="#555",
        justify="left",
    ).pack(anchor="w", pady=(8, 0))
    shin_out.pack(fill="both", expand=True, pady=(12, 0))

    # ======================================================================
    # TAB 5: История (просмотр из Supabase)
    # ======================================================================

    tab_hist = ttk.Frame(notebook, padding=10)
    notebook.add(tab_hist, text="История")

    ttk.Label(
        tab_hist,
        text="Просмотр и редактирование матчей из Supabase. Двойной клик по ячейке — изменить коэффициент; «Сохранить» — PATCH в matches.",
        justify="left",
    ).pack(anchor="w")

    hist_status_var = tk.StringVar(value="")
    ttk.Label(tab_hist, textvariable=hist_status_var, foreground="#555").pack(
        anchor="w", pady=(6, 0)
    )

    hist_seasons_frame = ttk.LabelFrame(tab_hist, text="Сохранённые сезоны", padding=6)
    hist_seasons_frame.pack(fill="x", pady=(10, 0))

    hist_tree = ttk.Treeview(
        hist_seasons_frame,
        columns=("league", "season", "matches", "imported"),
        show="headings",
        height=8,
    )
    hist_tree.heading("league", text="Лига")
    hist_tree.heading("season", text="Сезон")
    hist_tree.heading("matches", text="Матчей")
    hist_tree.heading("imported", text="Импорт")
    hist_tree.column("league", width=180, anchor="w")
    hist_tree.column("season", width=80, anchor="w")
    hist_tree.column("matches", width=72, anchor="e")
    hist_tree.column("imported", width=140, anchor="w")
    hist_tree.pack(fill="x", expand=False)

    hist_matches_frame = ttk.LabelFrame(tab_hist, text="Матчи выбранного сезона", padding=6)
    hist_matches_frame.pack(fill="both", expand=True, pady=(10, 0))

    hist_matches_tree = ttk.Treeview(
        hist_matches_frame,
        columns=(
            "date",
            "home",
            "away",
            "ah1",
            "ah",
            "ah2",
            "over",
            "tot",
            "under",
            "o1",
            "ox",
            "o2",
            "neutral",
            "home_rot",
            "away_rot",
            "weights",
            "fetch",
            "action",
            "status",
        ),
        show="headings",
        height=14,
    )
    for col, title, w, anchor in [
        ("date", "Дата", 92, "w"),
        ("home", "Дома", 132, "w"),
        ("away", "Гости", 132, "w"),
        ("ah1", "AH1", 56, "e"),
        ("ah", "AH", 56, "e"),
        ("ah2", "AH2", 56, "e"),
        ("over", "O", 56, "e"),
        ("tot", "Тот", 56, "e"),
        ("under", "U", 56, "e"),
        ("o1", "1", 56, "e"),
        ("ox", "X", 56, "e"),
        ("o2", "2", 56, "e"),
        ("neutral", "Нейтр", 60, "center"),
        ("home_rot", "Рот. хоз", 108, "w"),
        ("away_rot", "Рот. гост", 108, "w"),
        ("weights", "Веса", 52, "center"),
        ("fetch", "Данные", 56, "center"),
        ("action", "Действие", 88, "center"),
        ("status", "", 132, "w"),
    ]:
        hist_matches_tree.heading(col, text=title)
        hist_matches_tree.column(col, width=w, anchor=anchor)
    hist_matches_tree.tag_configure("dirty", background="#f0fdf4")
    hist_matches_tree.pack(fill="both", expand=True)

    hist_fetch_bar = ttk.LabelFrame(hist_matches_frame, text="Получить данные", padding=8)
    hist_fetch_match_lbl = ttk.Label(hist_fetch_bar, text="")
    hist_fetch_match_lbl.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
    ttk.Label(hist_fetch_bar, text="id матча с сайта неизвестного мужика:").grid(
        row=1, column=0, sticky="w", padx=(0, 8)
    )
    hist_fetch_var = tk.StringVar()
    hist_fetch_entry = ttk.Entry(hist_fetch_bar, textvariable=hist_fetch_var, width=28)
    hist_fetch_entry.grid(row=1, column=1, sticky="w")
    hist_fetch_err = tk.StringVar()
    ttk.Label(hist_fetch_bar, textvariable=hist_fetch_err, foreground="#b42318").grid(
        row=2, column=0, columnspan=3, sticky="w", pady=(6, 0)
    )

    def _hist_hide_fetch_bar():
        nonlocal hist_fetch_mid
        hist_fetch_mid = None
        hist_fetch_bar.pack_forget()
        for item in hist_matches_tree.get_children():
            _hist_refresh_row(int(item))

    def _hist_show_fetch_bar(mid: int):
        nonlocal hist_fetch_mid
        m = hist_match_by_id.get(mid)
        if m is None:
            return
        if hist_fetch_mid == mid:
            _hist_hide_fetch_bar()
            return
        hist_fetch_mid = mid
        hist_fetch_match_lbl.config(text=f"{sbh.format_ui_date(m.match_date)}  {m.home_team} — {m.away_team}")
        hist_fetch_var.set(hist_external_ids.get(mid, ""))
        hist_fetch_err.set("")
        hist_fetch_bar.pack(fill="x", pady=(6, 0))
        for item in hist_matches_tree.get_children():
            _hist_refresh_row(int(item))

    def _hist_do_fetch_odds():
        if hist_fetch_mid is None:
            return
        mid = hist_fetch_mid
        ext_id = hist_fetch_var.get().strip()
        if not ext_id:
            hist_fetch_err.set("Введите id матча с сайта неизвестного мужика")
            return
        hist_external_ids[mid] = ext_id
        hist_fetch_btn.config(state="disabled", text="Получение...")
        hist_fetch_err.set("")
        hist_matches_frame.update_idletasks()
        try:
            odds = ubo.fetch_odds(ext_id)
        except ubo.UserbetError as exc:
            hist_fetch_err.set(str(exc))
        else:
            edits = hist_edited.setdefault(mid, {})
            edits.update(ubo.odds_to_ui_edits(odds))
            hist_row_status.pop(mid, None)
            hist_manual_edit.discard(mid)
            _hist_refresh_row(mid)
            if not _hist_persist_row(mid, allow_empty=True):
                hist_manual_edit.add(mid)
                _hist_refresh_row(mid)
        finally:
            hist_fetch_btn.config(state="normal", text="Получить данные")

    hist_fetch_btn = ttk.Button(hist_fetch_bar, text="Получить данные", command=_hist_do_fetch_odds)
    hist_fetch_btn.grid(row=1, column=2, sticky="w", padx=(8, 0))
    hist_fetch_bar.pack_forget()

    hist_match_by_id: dict[int, sbh.MatchFull] = {}
    hist_edited: dict[int, dict[str, str]] = {}
    hist_row_status: dict[int, str] = {}
    hist_row_saving: set[int] = set()
    hist_manual_edit: set[int] = set()
    hist_fetch_mid: int | None = None
    hist_external_ids: dict[int, str] = {}
    hist_rotation_levels: list[dict] = []
    _hist_edit_entry: tk.Entry | None = None
    _hist_edit_ctx: tuple[int, str] | None = None

    def _hist_rotation_labels() -> list[str]:
        return [lev["name_ru"] for lev in hist_rotation_levels]

    def _hist_rotation_label_from_code(code: str) -> str:
        norm = sbh.normalize_rotation_code(code)
        for lev in hist_rotation_levels:
            if lev["code"] == norm:
                return lev["name_ru"]
        return sbh.rotation_label(norm)

    def _hist_rotation_code_from_label(label: str) -> str:
        text = str(label or "").strip()
        for lev in hist_rotation_levels:
            if lev["name_ru"] == text:
                return lev["code"]
        return sbh.normalize_rotation_code(text)

    def _hist_rotation_cell(mid: int, ui_col: str) -> str:
        m = hist_match_by_id[mid]
        edits = hist_edited.get(mid, {})
        if ui_col in edits:
            try:
                code = sbh.parse_rotation_input(edits[ui_col])
            except ValueError:
                return str(edits[ui_col])
            return _hist_rotation_label_from_code(code)
        if ui_col == "home_rot":
            return sbh.rotation_display_home(m)
        return sbh.rotation_display_away(m)

    def _hist_mid(iid: str) -> int:
        return int(iid)

    def _hist_dirty_fields(mid: int) -> Set[str]:
        orig = hist_match_by_id.get(mid)
        if orig is None:
            return set()
        edits = hist_edited.get(mid, {})
        dirty: Set[str] = set()
        for ui_col in sbh.EDITABLE_UI_COLS:
            text = edits.get(ui_col, sbh.edit_display_value(orig, ui_col))
            field = sbh.UI_COL_TO_FIELD[ui_col]
            try:
                new_val = sbh.parse_field_input(ui_col, text)
            except ValueError:
                dirty.add(field)
                continue
            old_val = sbh.field_value_from_match(orig, field)
            if not sbh._values_equal(field, old_val, new_val):
                dirty.add(field)
        return dirty

    def _hist_is_manual_dirty(mid: int) -> bool:
        return mid in hist_manual_edit and bool(_hist_dirty_fields(mid))

    def _hist_row_values(mid: int) -> tuple:
        m = hist_match_by_id[mid]
        edits = hist_edited.get(mid, {})
        dirty = _hist_is_manual_dirty(mid)
        saving = mid in hist_row_saving

        def cell(ui_col: str) -> str:
            if ui_col in edits:
                return edits[ui_col]
            return sbh.edit_display_value(m, ui_col)

        action = ""
        if saving:
            action = "Сохранение..."
        elif dirty:
            action = "Сохранить"

        fetch_mark = "▲" if hist_fetch_mid == mid else "▼"
        return (
            sbh.format_ui_date(m.match_date),
            m.home_team,
            m.away_team,
            cell("ah1"),
            cell("ah"),
            cell("ah2"),
            cell("over"),
            cell("tot"),
            cell("under"),
            cell("o1"),
            cell("ox"),
            cell("o2"),
            cell("neutral"),
            _hist_rotation_cell(mid, "home_rot"),
            _hist_rotation_cell(mid, "away_rot"),
            "⚙",
            fetch_mark,
            action,
            hist_row_status.get(mid, ""),
        )

    def _hist_refresh_row(mid: int):
        iid = str(mid)
        if not hist_matches_tree.exists(iid):
            return
        tags = ("dirty",) if _hist_is_manual_dirty(mid) else ()
        hist_matches_tree.item(iid, values=_hist_row_values(mid), tags=tags)

    def _hist_destroy_edit_entry():
        nonlocal _hist_edit_entry, _hist_edit_ctx
        if _hist_edit_entry is not None:
            _hist_edit_entry.destroy()
            _hist_edit_entry = None
            _hist_edit_ctx = None

    def _hist_begin_edit(mid: int, ui_col: str):
        _hist_destroy_edit_entry()
        if mid in hist_row_saving:
            return
        m = hist_match_by_id.get(mid)
        if m is None:
            return
        bbox = hist_matches_tree.bbox(str(mid), ui_col)
        if not bbox:
            return
        x, y, w, h = bbox
        initial = hist_edited.get(mid, {}).get(ui_col, sbh.edit_display_value(m, ui_col))
        entry = tk.Entry(hist_matches_frame, width=max(4, w // 8))
        entry.insert(0, initial)
        entry.place(in_=hist_matches_tree, x=x, y=y, width=w, height=h)
        entry.focus_set()
        entry.select_range(0, "end")

        def commit(_event=None):
            nonlocal _hist_edit_entry, _hist_edit_ctx
            if _hist_edit_entry is None:
                return
            val = entry.get()
            edits = hist_edited.setdefault(mid, {})
            edits[ui_col] = val
            hist_manual_edit.add(mid)
            hist_row_status.pop(mid, None)
            _hist_destroy_edit_entry()
            _hist_refresh_row(mid)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        _hist_edit_entry = entry
        _hist_edit_ctx = (mid, ui_col)

    def _hist_persist_row(mid: int, *, allow_empty: bool = False) -> bool:
        if mid in hist_row_saving:
            return False
        orig = hist_match_by_id.get(mid)
        if orig is None:
            return False
        edits = hist_edited.get(mid, {})
        try:
            payload = sbh.build_dirty_patch(orig, edits)
        except ValueError as exc:
            hist_row_status[mid] = str(exc)
            _hist_refresh_row(mid)
            return False
        if not payload:
            if not allow_empty:
                hist_edited.pop(mid, None)
                hist_row_status.pop(mid, None)
                hist_manual_edit.discard(mid)
                _hist_refresh_row(mid)
                return True
            hist_edited.pop(mid, None)
            hist_row_status[mid] = "Сохранено"
            hist_manual_edit.discard(mid)
            _hist_refresh_row(mid)
            return True
        hist_row_saving.add(mid)
        hist_row_status[mid] = ""
        _hist_refresh_row(mid)
        try:
            updated = sbh.patch_match(mid, payload, original=orig)
        except sb.SupabaseError:
            hist_row_status[mid] = "Не удалось сохранить коэффициенты в БД"
            return False
        except ValueError as exc:
            hist_row_status[mid] = str(exc)
            return False
        else:
            hist_match_by_id[mid] = updated
            for i, ent in enumerate(hist_matches_cache):
                if ent.match_id == mid:
                    hist_matches_cache[i] = updated
                    break
            hist_edited.pop(mid, None)
            hist_row_status[mid] = "Сохранено"
            hist_manual_edit.discard(mid)
            return True
        finally:
            hist_row_saving.discard(mid)
            _hist_refresh_row(mid)

    def _hist_save_row(mid: int):
        if mid not in hist_manual_edit:
            return
        _hist_persist_row(mid)

    def _hist_show_weights_dialog(mid: int):
        _hist_destroy_edit_entry()
        if mid in hist_row_saving:
            return
        m = hist_match_by_id.get(mid)
        if m is None:
            return
        dlg = tk.Toplevel(hist_matches_frame)
        dlg.title("Веса матча")
        dlg.transient(hist_matches_frame.winfo_toplevel())
        dlg.grab_set()
        edits = hist_edited.setdefault(mid, {})
        vars_by_col: dict[str, tk.StringVar] = {}
        for row_i, (ui_col, label) in enumerate(
            (
                ("active", "Активен"),
                ("derby", "Дерби"),
                ("match_w", "Вес матча"),
                ("neutr_w", "Нейтр. вес"),
                ("home_rot", "Ротация хозяев"),
                ("away_rot", "Ротация гостей"),
                ("source", "Источник"),
                ("motivation", "Мотивация"),
            )
        ):
            ttk.Label(dlg, text=f"{label}:").grid(row=row_i, column=0, sticky="w", padx=8, pady=6)
            if ui_col in ("home_rot", "away_rot"):
                code = edits.get(ui_col, sbh.edit_display_value(m, ui_col))
                try:
                    code = sbh.parse_rotation_input(str(code))
                except ValueError:
                    code = sbh.ROTATION_DEFAULT_CODE
                var = tk.StringVar(value=_hist_rotation_label_from_code(code))
                vars_by_col[ui_col] = var
                ttk.Combobox(
                    dlg,
                    textvariable=var,
                    values=_hist_rotation_labels(),
                    state="readonly",
                    width=22,
                ).grid(row=row_i, column=1, sticky="w", padx=8, pady=6)
            elif ui_col == "source":
                initial = edits.get(ui_col, sbh.edit_display_value(m, ui_col))
                var = tk.StringVar(value=initial)
                vars_by_col[ui_col] = var
                ttk.Entry(dlg, textvariable=var, width=22).grid(
                    row=row_i, column=1, sticky="w", padx=8, pady=6
                )
            elif ui_col in ("motivation", "active"):
                initial = edits.get(ui_col, sbh.edit_display_value(m, ui_col))
                var = tk.StringVar(value=initial)
                vars_by_col[ui_col] = var
                ttk.Combobox(
                    dlg,
                    textvariable=var,
                    values=("нет", "да"),
                    state="readonly",
                    width=22,
                ).grid(row=row_i, column=1, sticky="w", padx=8, pady=6)
            else:
                initial = edits.get(ui_col, sbh.edit_display_value(m, ui_col))
                var = tk.StringVar(value=initial)
                vars_by_col[ui_col] = var
                ttk.Entry(dlg, textvariable=var, width=12).grid(
                    row=row_i, column=1, sticky="w", padx=8, pady=6
                )

        def apply_and_close():
            for ui_col, var in vars_by_col.items():
                if ui_col in ("home_rot", "away_rot"):
                    edits[ui_col] = _hist_rotation_code_from_label(var.get())
                else:
                    edits[ui_col] = var.get()
            hist_manual_edit.add(mid)
            hist_row_status.pop(mid, None)
            dlg.destroy()
            _hist_refresh_row(mid)

        btns = ttk.Frame(dlg)
        btns.grid(row=9, column=0, columnspan=2, pady=(4, 10))
        ttk.Button(btns, text="Готово", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btns, text="Отмена", command=dlg.destroy).pack(side="left", padx=6)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

    def _hist_on_matches_click(event):
        region = hist_matches_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = hist_matches_tree.identify_column(event.x)
        iid = hist_matches_tree.identify_row(event.y)
        if not iid:
            return
        col_id = hist_matches_tree["columns"][int(col.replace("#", "")) - 1]
        mid = _hist_mid(iid)
        if col_id == "weights":
            _hist_show_weights_dialog(mid)
            return
        if col_id == "fetch":
            _hist_show_fetch_bar(mid)
            return
        if col_id == "action" and _hist_is_manual_dirty(mid) and mid not in hist_row_saving:
            _hist_save_row(mid)

    def _hist_on_matches_dblclick(event):
        region = hist_matches_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = hist_matches_tree.identify_column(event.x)
        iid = hist_matches_tree.identify_row(event.y)
        if not iid:
            return
        col_id = hist_matches_tree["columns"][int(col.replace("#", "")) - 1]
        if col_id in sbh.EDITABLE_UI_COLS and col_id not in ("home_rot", "away_rot"):
            _hist_begin_edit(_hist_mid(iid), col_id)

    hist_matches_tree.bind("<Button-1>", _hist_on_matches_click)
    hist_matches_tree.bind("<Double-1>", _hist_on_matches_dblclick)

    hist_seasons_cache: list[sbh.SeasonSummary] = []
    hist_matches_cache: list[sbh.MatchFull] = []
    hist_loaded = False
    hist_selected: dict[str, str | int] = {}

    def clear_hist_tree():
        for item in hist_tree.get_children():
            hist_tree.delete(item)

    def clear_hist_matches_tree():
        nonlocal hist_fetch_mid
        _hist_destroy_edit_entry()
        hist_match_by_id.clear()
        hist_edited.clear()
        hist_row_status.clear()
        hist_row_saving.clear()
        hist_manual_edit.clear()
        hist_fetch_mid = None
        hist_fetch_bar.pack_forget()
        for item in hist_matches_tree.get_children():
            hist_matches_tree.delete(item)

    def fill_hist_matches_tree(matches: list[sbh.MatchFull]):
        clear_hist_matches_tree()
        for m in matches:
            hist_match_by_id[m.match_id] = m
            hist_matches_tree.insert(
                "",
                "end",
                iid=str(m.match_id),
                values=_hist_row_values(m.match_id),
            )

    def selected_season_summary() -> sbh.SeasonSummary | None:
        sel = hist_tree.selection()
        if not sel:
            return None
        row_id = sel[0]
        for ent in hist_seasons_cache:
            if ent.row_id == row_id:
                return ent
        return None

    def load_hist_matches(ent: sbh.SeasonSummary):
        hist_selected.clear()
        hist_selected.update(
            {
                "selected_league_id": ent.league_id,
                "selected_league_name": ent.league_name,
                "selected_season_id": ent.season_id,
                "selected_season_label": ent.season_label,
            }
        )
        clear_hist_matches_tree()
        hist_status_var.set(
            f"{ent.league_name} / {ent.season_label} — загрузка матчей…"
        )
        try:
            matches = sbh.fetch_matches(ent.league_id, ent.season_id)
        except sb.SupabaseError:
            messagebox.showerror("История", "Не удалось загрузить матчи выбранного сезона.")
            hist_status_var.set(
                f"{ent.league_name} / {ent.season_label} — ошибка загрузки матчей"
            )
            return
        hist_matches_cache.clear()
        hist_matches_cache.extend(matches)
        if not matches:
            clear_hist_matches_tree()
            hist_status_var.set(f"{ent.league_name} / {ent.season_label} — Нет данных по сезону.")
            return
        fill_hist_matches_tree(matches)
        hist_status_var.set(
            f"{ent.league_name} / {ent.season_label} — {len(matches)} матчей"
        )

    def refresh_history_tree(*, auto_select_first: bool = True):
        clear_hist_tree()
        hist_seasons_cache.clear()
        hist_status_var.set("Загрузка сезонов…")
        try:
            seasons = sbh.fetch_season_summary()
        except sb.SupabaseError:
            messagebox.showerror("История", "Не удалось загрузить список сезонов.")
            hist_status_var.set("")
            return
        hist_seasons_cache.extend(seasons)
        if not seasons:
            clear_hist_matches_tree()
            hist_status_var.set("Пусто — в базе пока нет матчей.")
            return
        for ent in seasons:
            hist_tree.insert(
                "",
                "end",
                iid=ent.row_id,
                values=(
                    ent.league_name,
                    ent.season_label,
                    ent.matches_count,
                    sbh.format_imported_at(ent.imported_at),
                ),
            )
        if auto_select_first:
            first = seasons[0]
            hist_tree.selection_set(first.row_id)
            hist_tree.see(first.row_id)
            load_hist_matches(first)
        else:
            hist_status_var.set(f"Сезонов: {len(seasons)}")

    def on_hist_select(_event=None):
        ent = selected_season_summary()
        if ent is None:
            return
        load_hist_matches(ent)

    hist_tree.bind("<<TreeviewSelect>>", on_hist_select)

    def load_hist_tab():
        nonlocal hist_loaded
        if hist_loaded:
            return
        hist_loaded = True
        hist_rotation_levels[:] = sbh.fetch_rotation_levels()
        refresh_history_tree(auto_select_first=True)

    def on_hist_tab_selected(_event=None):
        if notebook.tab(notebook.select(), "text") == "История":
            load_hist_tab()

    notebook.bind("<<NotebookTabChanged>>", on_hist_tab_selected, add="+")

    # ======================================================================
    # TAB 6: Линия (голы) — Poisson / Dixon-Coles из closing-линий
    # ======================================================================
    import goal_model as gm
    import goal_model_train as gmt
    import goal_line_history as glh
    from history_store import LEAGUES, league_title

    tab_goal = ttk.Frame(notebook, padding=10)
    notebook.add(tab_goal, text="Линия (голы)")

    goal_state: dict = {"model": None, "raw": None, "league": "epl"}
    goal_history = glh.GoalLineHistory()

    goal_top = ttk.Frame(tab_goal)
    goal_top.pack(fill="x", anchor="n")

    ttk.Label(
        goal_top,
        text=(
            "Голевая модель: closing-линии (AH + тоталы + 1X2) → скрытые S/D → "
            "λ_h/λ_a → матрица счетов → все рынки.\n"
            "Обязательные CSV-колонки: home_team, away_team, closing_ah_home, "
            "closing_total_line, ah_home_odds, ah_away_odds, over_odds, under_odds "
            "(+ home_odds, draw_odds, away_odds для ничьи).\n"
            "Необязательные (по строке матча): date, league, neutral_flag, derby_flag, "
            "quality_flag, value (множитель качества), derby_weight, neutral_weight — "
            "нет столбца → вес 1.0; флаги false; quality_flag пустой."
        ),
        foreground="#555",
        justify="left",
    ).pack(anchor="w", pady=(0, 8))

    goal_path_var = tk.StringVar(value="Файл истории не загружен")
    ttk.Label(goal_top, textvariable=goal_path_var, foreground="#1f3ea6").pack(anchor="w")

    goal_controls = ttk.Frame(tab_goal)
    goal_controls.pack(fill="x", pady=(8, 0))

    goal_home_var = tk.StringVar()
    goal_away_var = tk.StringVar()
    goal_league_var = tk.StringVar(value=LEAGUES["epl"])
    ttk.Label(goal_controls, text="Лига:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
    goal_league_combo = ttk.Combobox(
        goal_controls,
        textvariable=goal_league_var,
        values=list(LEAGUES.values()),
        state="readonly",
        width=24,
    )
    goal_league_combo.grid(row=0, column=1, sticky="w", pady=4)
    ttk.Label(goal_controls, text="Хозяева:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
    goal_home_combo = ttk.Combobox(goal_controls, textvariable=goal_home_var, state="readonly", width=24)
    goal_home_combo.grid(row=1, column=1, sticky="w", pady=4)
    ttk.Label(goal_controls, text="Гости:").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=4)
    goal_away_combo = ttk.Combobox(goal_controls, textvariable=goal_away_var, state="readonly", width=24)
    goal_away_combo.grid(row=2, column=1, sticky="w", pady=4)

    goal_neutral_var = tk.BooleanVar(value=False)
    goal_derby_var = tk.BooleanVar(value=False)
    goal_margin_var = tk.BooleanVar(value=False)
    _cb_neu = ttk.Checkbutton(goal_controls, text="Нейтральное поле", variable=goal_neutral_var)
    _cb_neu.grid(row=1, column=2, sticky="w", padx=(16, 0))
    _cb_der = ttk.Checkbutton(goal_controls, text="Дерби", variable=goal_derby_var)
    _cb_der.grid(row=2, column=2, sticky="w", padx=(16, 0))
    goal_margin_pct = tk.StringVar(value="3")
    _cb_mar = ttk.Checkbutton(goal_controls, text="Маржа, %:", variable=goal_margin_var)
    _cb_mar.grid(row=0, column=2, sticky="w", padx=(16, 0))
    ttk.Entry(goal_controls, textvariable=goal_margin_pct, width=6).grid(row=0, column=3, sticky="w")

    # --- Настройки модели (редактируемые веса и параметры) ---
    goal_cfg_frame = ttk.LabelFrame(
        tab_goal,
        text="Настройки модели",
        padding=8,
    )
    goal_cfg_frame.pack(fill="x", pady=(8, 0))

    _gv: dict = {}

    def _attach_tip(widget, text):
        tip = {"win": None}

        def show(_e=None):
            if tip["win"] or not text:
                return
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            tk.Label(
                win, text=text, justify="left", background="#ffffe0",
                relief="solid", borderwidth=1, wraplength=360, font=("", 9), padx=6, pady=4,
            ).pack()
            tip["win"] = win

        def hide(_e=None):
            if tip["win"]:
                tip["win"].destroy()
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def _cfg_entry(parent, row, col, label, key, default, width=7, hint=""):
        lbl = ttk.Label(parent, text=label)
        lbl.grid(row=row, column=col * 2, sticky="w", padx=(0, 4), pady=2)
        var = tk.StringVar(value=str(default))
        ent = ttk.Entry(parent, textvariable=var, width=width)
        ent.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, 12), pady=2)
        if hint:
            _attach_tip(lbl, hint)
            _attach_tip(ent, hint)
        _gv[key] = var
        return var

    _cfg_entry(goal_cfg_frame, 0, 0, "α форы:", "alpha_ah", 0.25,
               hint="Приглушение матчей с большим перевесом сил при обучении рейтинга. Больше α → крупные форы влияют меньше. 0 = все матчи равнозначны.")
    _cfg_entry(goal_cfg_frame, 0, 1, "α тотала:", "alpha_t", 0.5,
               hint="Приглушение матчей с экстремальным тоталом при обучении голевой модели (атака/оборона). Больше α → такие матчи влияют слабее.")
    _cfg_entry(goal_cfg_frame, 0, 2, "вес ничьи (калибр.):", "draw_loss", 1.5,
               hint="Насколько важна ничья при калибровке матрицы под рынок 1X2. Больше → точнее ничья, но П1/П2 чуть грубее.")
    _cfg_entry(goal_cfg_frame, 1, 0, "prior α:", "prior_alpha", 0.7,
               hint="Доля силы прошлого сезона, переносимая на новый (0.7 = 70%). Действует при prior весе > 0.")
    _cfg_entry(goal_cfg_frame, 1, 1, "prior вес:", "prior_weight", 0.0,
               hint="Сила стягивания рейтингов к прошлому сезону. 0 = выкл. Больше → сильнее держим прошлогоднюю оценку (полезно в начале сезона).")
    _cfg_entry(goal_cfg_frame, 1, 2, "новичок: N слабейших:", "promoted_n", 3,
               hint="Команде без матчей (новичок лиги) рейтинг = среднее N слабейших команд.")
    _cfg_entry(goal_cfg_frame, 2, 0, "ничья q_min:", "q_min", 0.95,
               hint="Насколько можно УМЕНЬШИТЬ ничью из матрицы. 0.95 = максимум −5%.")
    _cfg_entry(goal_cfg_frame, 2, 1, "ничья q_max:", "q_max", 1.05,
               hint="Насколько можно УВЕЛИЧИТЬ ничью из матрицы. 1.05 = максимум +5%.")
    _cfg_entry(goal_cfg_frame, 2, 2, "max |ΔS|:", "s_cal_max_delta", 0.15,
               hint="Лимит сдвига тотала при soft-калибровке S (голы на матч).")
    _cfg_entry(goal_cfg_frame, 2, 3, "default сезон:", "w_season_def", 1.0,
               hint="Множитель для матча, чья date не попала ни в один диапазон таблицы сезонов ниже.")

    goal_s_cal_mode_var = tk.StringVar(value="off")
    ttk.Label(goal_cfg_frame, text="калибр. S:").grid(row=3, column=6, sticky="w", padx=(0, 4))
    s_cal_combo = ttk.Combobox(
        goal_cfg_frame, textvariable=goal_s_cal_mode_var, width=6,
        values=("off", "soft"), state="readonly",
    )
    s_cal_combo.grid(row=3, column=7, sticky="w", padx=(0, 8))
    _attach_tip(s_cal_combo, "off: c=0,d=1 (рабочий режим); soft: штраф+лимит ΔS. free — только в коде (эксп.).")
    _cfg_entry(goal_cfg_frame, 3, 4, "γ max:", "gamma_max", 0.20,
               hint="Dixon–Coles: |γ| не выше (±).")

    goal_use_draw_var = tk.BooleanVar(value=False)
    goal_use_dc_var = tk.BooleanVar(value=True)
    _cb_draw = ttk.Checkbutton(goal_cfg_frame, text="Модель ничьи", variable=goal_use_draw_var)
    _cfg_entry(goal_cfg_frame, 3, 0, "дерби H×:", "derby_h_default", 0.7,
               hint="Если в обучении <3 дерби: H_eff = коэфф. × H_league. 0.7 — default; 0.4 — агрессивное ослабление дома.")
    _cfg_entry(goal_cfg_frame, 3, 1, "λ A:", "reg_lambda_attack", 0.10,
               hint="L2-штраф ΣA² при обучении attack. Сдерживает переоценку атаки от шума тотала.")
    _cfg_entry(goal_cfg_frame, 3, 2, "λ Df:", "reg_lambda_defense", 0.10,
               hint="L2-штраф ΣDf² при обучении defense. Сдерживает переоценку «дырявой» обороны.")
    _cb_draw.grid(row=3, column=3, sticky="w", padx=(0, 12))
    _attach_tip(_cb_draw, "Считать ничью отдельной моделью и ею корректировать диагональ матрицы. Выкл → ничья как есть из Пуассона.")
    _cb_dc = ttk.Checkbutton(goal_cfg_frame, text="Dixon-Coles", variable=goal_use_dc_var)
    _cb_dc.grid(row=3, column=4, sticky="w")
    _attach_tip(_cb_dc, "Поправка вероятностей низких счетов (0:0, 1:0, 0:1, 1:1) — рынок оценивает их иначе, чем чистый Пуассон.")

    _season_lbl = ttk.Label(
        goal_cfg_frame,
        text="Веса сезонов (по строке: дата_от, дата_до, вес):",
    )
    _season_lbl.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
    _attach_tip(_season_lbl, "Свежие матчи важнее старых: задайте больший вес для последних дат. Матч ищет свой диапазон по дате; при пересечении применяется добавленный последним (нижняя строка). Вне диапазонов — «default сезон». Строки с # игнорируются.")
    _attach_tip(_cb_neu, "Прогноз без домашнего преимущества (финал, нейтральное поле).")
    _attach_tip(_cb_der, "Матч-дерби: при прогнозе используется H_eff (поправка δ_derby из обучения), не множитель веса.")
    _attach_tip(_cb_mar, "Вкл → коэффициенты с заданной маржой (как у бука). Выкл → честные (сумма вероятностей 100%).")
    goal_season_text = tk.Text(goal_cfg_frame, width=44, height=4, relief="solid", borderwidth=1)
    goal_season_text.grid(row=5, column=0, columnspan=6, sticky="w", pady=(2, 0))
    goal_season_text.insert("1.0", "# 2026-09-01, 2026-12-31, 1.2\n# 2025-08-01, 2026-06-30, 0.6\n")

    def _parse_season_weights():
        out = []
        from datetime import datetime as _dt
        for line in goal_season_text.get("1.0", "end").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = [p.strip() for p in s.split(",")]
            if len(parts) != 3:
                continue
            try:
                d_from = _dt.strptime(parts[0], "%Y-%m-%d").date()
                d_to = _dt.strptime(parts[1], "%Y-%m-%d").date()
                w = float(parts[2].replace(",", "."))
            except ValueError:
                continue
            out.append(gmt.SeasonWeight(label=parts[0], date_from=d_from, date_to=d_to, base_weight=w))
        return out

    def _goal_cfg():
        def f(key, default):
            try:
                return float(_gv[key].get().replace(",", "."))
            except (ValueError, KeyError):
                return default
        gmax = f("gamma_max", 0.20)
        mode = goal_s_cal_mode_var.get().strip().lower()
        if mode not in ("off", "soft", "free"):
            mode = "soft"
        return gmt.ModelConfig(
            default_season_weight=f("w_season_def", 1.0),
            season_weights=_parse_season_weights(),
            alpha_ah=f("alpha_ah", 0.25),
            alpha_t=f("alpha_t", 0.5),
            draw_loss_weight=f("draw_loss", 1.5),
            prior_alpha=f("prior_alpha", 0.7),
            prior_weight=f("prior_weight", 0.0),
            promoted_reference_n=int(f("promoted_n", 3)),
            draw_diag_multiplier_min=f("q_min", 0.95),
            draw_diag_multiplier_max=f("q_max", 1.05),
            s_calibration_mode=mode,
            s_cal_max_delta=f("s_cal_max_delta", 0.15),
            dc_gamma_min=-gmax,
            dc_gamma_max=gmax,
            derby_h_default_ratio=f("derby_h_default", 0.7),
            reg_lambda_attack=f("reg_lambda_attack", 0.10),
            reg_lambda_defense=f("reg_lambda_defense", 0.10),
            use_draw_model=goal_use_draw_var.get(),
            use_dixon_coles=goal_use_dc_var.get(),
        )

    goal_meta_var = tk.StringVar(value="Загрузите CSV closing-линий и обучите модель.")
    ttk.Label(tab_goal, textvariable=goal_meta_var, justify="left").pack(anchor="w", pady=(8, 4))

    goal_out = tk.Text(tab_goal, width=98, height=16, relief="solid", borderwidth=1,
                       font=("Consolas", 10))
    goal_out.pack(fill="both", expand=True, pady=(4, 0))
    goal_out.configure(state="disabled")

    goal_hist_frame = ttk.LabelFrame(tab_goal, text="История расчётов", padding=6)
    goal_hist_frame.pack(fill="x", pady=(8, 0))

    goal_hist_tree = ttk.Treeview(
        goal_hist_frame,
        columns=("at", "league", "match", "summary"),
        show="headings",
        height=6,
    )
    goal_hist_tree.heading("at", text="Дата и время")
    goal_hist_tree.heading("league", text="Лига")
    goal_hist_tree.heading("match", text="Матч")
    goal_hist_tree.heading("summary", text="Итог")
    goal_hist_tree.column("at", width=120, anchor="w")
    goal_hist_tree.column("league", width=140, anchor="w")
    goal_hist_tree.column("match", width=180, anchor="w")
    goal_hist_tree.column("summary", width=520, anchor="w")
    goal_hist_tree.pack(fill="x")

    goal_hist_btns = ttk.Frame(goal_hist_frame)
    goal_hist_btns.pack(fill="x", pady=(6, 0))

    def _goal_set_text(text: str):
        goal_out.configure(state="normal")
        goal_out.delete("1.0", "end")
        goal_out.insert("1.0", text)
        goal_out.configure(state="disabled")

    def _goal_league_key() -> str:
        title = goal_league_var.get().strip()
        for key, label in LEAGUES.items():
            if label == title:
                return key
        raw = goal_state.get("raw") or []
        return glh.infer_league_key(raw)

    def _goal_refresh_history():
        for item in goal_hist_tree.get_children():
            goal_hist_tree.delete(item)
        for entry in goal_history.entries():
            goal_hist_tree.insert(
                "", "end",
                values=(entry.at_display, entry.league_label, entry.match_label, entry.summary),
            )

    def _goal_clear_history():
        if not goal_history.entries():
            return
        if messagebox.askyesno("История расчётов", "Очистить всю историю расчётов?"):
            goal_history.clear()
            _goal_refresh_history()

    def _goal_team_option(model, tid: str) -> str:
        nm = (model.team_names or {}).get(tid)
        if nm and nm != tid:
            return f"{tid} — {nm}"
        return tid

    def _goal_team_from_option(opt: str) -> str:
        if " — " in opt:
            return opt.split(" — ", 1)[0].strip()
        return opt.strip()

    def _goal_refresh_teams():
        model = goal_state["model"]
        teams = sorted(model.strength.ratings) if model else []
        opts = [_goal_team_option(model, t) for t in teams] if model else []
        goal_home_combo["values"] = opts
        goal_away_combo["values"] = opts
        if len(opts) >= 2:
            goal_home_var.set(opts[0])
            goal_away_var.set(opts[1])

    def _goal_train(raw, path_label):
        cfg = _goal_cfg()
        model, prepared = gmt.train_full_model(raw, cfg)
        goal_state["model"] = model
        goal_state["raw"] = raw
        goal_state["league"] = glh.infer_league_key(raw)
        goal_league_var.set(LEAGUES.get(goal_state["league"], goal_state["league"]))
        goal_path_var.set(path_label)
        return model

    def _goal_d_clamp_note(model):
        dc = model.d_clamp
        if not dc or dc.n_hit == 0:
            return ""
        note = f"D clamp: {dc.n_hit}/{dc.n_total} ({dc.pct:.1f}%)"
        if dc.n_sensitive:
            note += f", чувствит. {dc.n_sensitive}"
        if dc.pct > 2 or dc.n_sensitive > 0:
            note += " — проверьте AH vs OU"
        return note + "\n"

    def _goal_format_meta(model, n_matches: int) -> str:
        c = model.calibration
        st = model.strength
        dr = model.draw
        cal_note = ""
        if model.cal_diag and model.cal_diag.n_1x2 > 0:
            cal_note = f"Калибровка 1X2: {model.cal_diag.summary}\n"
        q_note = ""
        if model.draw_q_diag and model.draw_q_diag.n_eval > 0:
            q_note = f"Draw q: {model.draw_q_diag.summary}\n"
        sd_note = ""
        if model.sd_diag and model.sd_diag.n_eval > 0:
            sd_note = f"S/D→1X2: {model.sd_diag.summary}\n"
        dc_line = (
            f"Dixon-Coles: вкл, γ={c.gamma:.4f}  "
            if model.config.use_dixon_coles
            else "Dixon-Coles: выкл, γ не используется  "
        )
        return (
            _goal_d_clamp_note(model)
            + cal_note
            + q_note
            + sd_note
            + f"Обучено: {n_matches} матчей, команд {len(st.ratings)}\n"
            f"H={st.home_advantage:.3f}  δ_derby={st.derby_home_delta:.3f}  "
            f"n_derby={st.derby_n}  RMSE_D={st.rmse:.3f}\n"
            f"μ={model.goals.mu:.3f}  H_g={model.goals.home_goal_adv:.3f}  "
            f"RMSE_logλ={model.goals.rmse:.3f}\n"
            f"Калибр: a={c.a:.3f} b={c.b:.3f} c={c.c:.3f} d={c.d:.3f}  "
            f"{dc_line}"
            f"Ничья: {dr.source}"
        )

    def goal_train_from_path(path):
        try:
            raw = gmt.load_raw_matches(Path(path))
            if len(raw) < 2:
                raise ValueError("В файле меньше 2 матчей.")
            model = _goal_train(raw, f"Загружено: {path}  ({len(raw)} матчей)")
            goal_meta_var.set(_goal_format_meta(model, len(raw)))
            _goal_refresh_teams()
            lines = ["Рейтинги (сила на нейтрали) / атака / оборона:"]
            names = model.team_names or {}
            for tid, r in sorted(model.strength.ratings.items(), key=lambda kv: kv[1], reverse=True):
                label = f"{tid} — {names[tid]}" if names.get(tid) else tid
                lines.append(
                    f"  {label:<28} r={r:+.3f}   A={model.goals.attack[tid]:+.3f}   "
                    f"Df={model.goals.defense[tid]:+.3f}"
                )
            _goal_set_text("\n".join(lines))
        except Exception as exc:
            messagebox.showerror("Линия (голы)", str(exc))

    def goal_load_clicked():
        path = filedialog.askopenfilename(
            title="CSV closing-линий",
            filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")],
        )
        if path:
            goal_train_from_path(path)

    def goal_recalc_clicked():
        raw = goal_state.get("raw")
        if not raw:
            messagebox.showwarning("Линия (голы)", "Сначала загрузите CSV.")
            return
        try:
            model = _goal_train(raw, f"Пересчитано ({len(raw)} матчей, новые настройки)")
            goal_meta_var.set(_goal_format_meta(model, len(raw)))
            _goal_refresh_teams()
        except Exception as exc:
            messagebox.showerror("Линия (голы)", str(exc))

    def goal_predict_clicked():
        model = goal_state["model"]
        if model is None:
            messagebox.showwarning("Линия (голы)", "Сначала загрузите CSV и обучите модель.")
            return
        home_id = _goal_team_from_option(goal_home_var.get())
        away_id = _goal_team_from_option(goal_away_var.get())
        if not home_id or not away_id or home_id == away_id:
            messagebox.showwarning("Линия (голы)", "Выберите разные команды.")
            return
        try:
            pred = gmt.predict_match(
                model, home_id, away_id,
                neutral=goal_neutral_var.get(), derby=goal_derby_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("Линия (голы)", str(exc))
            return
        use_margin = goal_margin_var.get()
        try:
            margin = float(goal_margin_pct.get().replace(",", ".")) / 100.0
        except ValueError:
            margin = 0.0
        lines, summary = glh.format_prediction_report(
            pred,
            neutral=goal_neutral_var.get(),
            use_margin=use_margin,
            margin=margin,
        )
        _goal_set_text("\n".join(lines))
        goal_history.add(
            league=_goal_league_key(),
            home_team=pred.home_team,
            away_team=pred.away_team,
            summary=summary,
        )
        _goal_refresh_history()

    goal_btns = ttk.Frame(tab_goal)
    goal_btns.pack(fill="x", pady=(8, 0), before=goal_out)
    ttk.Button(goal_btns, text="Загрузить CSV и обучить", command=goal_load_clicked).grid(
        row=0, column=0, padx=(0, 6)
    )
    ttk.Button(goal_btns, text="Пересчитать модель", command=goal_recalc_clicked).grid(
        row=0, column=1, padx=(0, 6)
    )
    ttk.Button(goal_btns, text="Рассчитать линию", command=goal_predict_clicked).grid(
        row=0, column=2
    )
    ttk.Button(goal_hist_btns, text="Очистить историю", command=_goal_clear_history).pack(
        anchor="w"
    )
    _goal_refresh_history()

    return root


def main():
    try:
        from runtime_paths import ensure_user_data
    except ImportError:  # pragma: no cover
        from .runtime_paths import ensure_user_data  # type: ignore
    ensure_user_data()
    build_app().mainloop()


if __name__ == "__main__":
    main()
