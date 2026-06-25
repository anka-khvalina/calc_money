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

    teams_league_labels = [title for title, _key in hs.format_league_options()]
    teams_league_keys = {title: key for title, key in hs.format_league_options()}
    teams_league_var = tk.StringVar(value=teams_league_labels[0])

    ttk.Label(teams_top, text="Лига:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
    teams_league_combo = ttk.Combobox(
        teams_top,
        textvariable=teams_league_var,
        values=teams_league_labels,
        state="readonly",
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
        league_key = _teams_league_key()
        entries = tg.list_teams(league_key)
        teams_logo_team_labels.clear()
        teams_logo_team_ids.clear()
        labels: list[str] = []
        for ent in entries:
            seq = ent.id.split(":", 1)[-1]
            label = f"{seq} — {ent.name}"
            labels.append(label)
            teams_logo_team_labels[label] = ent.id
            teams_logo_team_ids[ent.id] = label
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
        league_key = _teams_league_key()
        team_id = _selected_logo_team_id()
        if not team_id:
            messagebox.showwarning(
                "Справочник",
                f"Выберите команду лиги {hs.league_title(league_key)}.",
            )
            return
        ent = tg.get_team(team_id)
        if ent is None:
            messagebox.showerror("Справочник", f"Команда с id {team_id!r} не найдена в справочнике.")
            return
        path = teams_logo_path_var.get().strip()
        if not path:
            messagebox.showwarning("Справочник", "Выберите файл логотипа (PNG или JPEG).")
            return
        try:
            tg.set_team_logo(team_id, path)
        except ValueError as exc:
            messagebox.showerror("Справочник", str(exc))
            return
        teams_logo_path_var.set("")
        show_teams_for_league()
        messagebox.showinfo("Справочник", f"Логотип сохранён для {ent.name} ({team_id}).")

    def remove_team_logo_by_id():
        league_key = _teams_league_key()
        team_id = _selected_logo_team_id()
        if not team_id:
            messagebox.showwarning(
                "Справочник",
                f"Выберите команду лиги {hs.league_title(league_key)}.",
            )
            return
        ent = tg.get_team(team_id)
        if ent is None:
            messagebox.showerror("Справочник", f"Команда с id {team_id!r} не найдена в справочнике.")
            return
        if not tg.has_logo(team_id):
            messagebox.showinfo("Справочник", f"У команды {ent.name} нет логотипа.")
            return
        tg.remove_team_logo(team_id)
        show_teams_for_league()
        messagebox.showinfo("Справочник", f"Логотип удалён для {ent.name}.")

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

    def _teams_league_key():
        title = teams_league_var.get().strip()
        key = teams_league_keys.get(title)
        if not key:
            key = hs.normalize_league(title)
        return key

    def clear_teams_tree():
        for item in teams_tree.get_children():
            teams_tree.delete(item)

    def show_teams_for_league():
        clear_teams_tree()
        _teams_logo_photos.clear()
        league_key = _teams_league_key()
        entries = tg.list_teams(league_key)
        for i, ent in enumerate(entries, start=1):
            img = _load_team_logo_photo(ent.id, _teams_logo_photos)
            kw: dict = {"values": (i, ent.name)}
            if img:
                kw["image"] = img
            teams_tree.insert("", "end", iid=ent.id, **kw)
        teams_status_var.set(
            f"{hs.league_title(league_key)} — {len(entries)} команд"
            if entries
            else f"{hs.league_title(league_key)} — справочник пуст"
        )
        refresh_logo_team_options()

    def search_teams():
        show_teams_for_league()

    def add_team_entry():
        league_key = _teams_league_key()
        name = teams_name_var.get().strip()
        if not name:
            messagebox.showwarning("Справочник", "Введите название команды.")
            return
        try:
            ent = tg.add_team(league_key, name)
        except ValueError as exc:
            messagebox.showerror("Справочник", str(exc))
            return
        teams_name_var.set("")
        search_teams()
        messagebox.showinfo("Справочник", f"Добавлено: {ent.name}")

    teams_league_combo.bind(
        "<<ComboboxSelected>>",
        lambda _e: (show_teams_for_league(), refresh_logo_team_options()),
    )
    ttk.Button(teams_add_bar, text="Добавить", command=add_team_entry).grid(
        row=0, column=2, sticky="w", padx=(12, 0)
    )
    ttk.Label(tab_teams, textvariable=teams_status_var, foreground="#555", justify="left").pack(
        anchor="w", pady=(10, 0)
    )

    teams_show_on_start = show_teams_for_league

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
    teams_show_on_start()

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
    # TAB 5: история сезонов
    # ======================================================================

    tab_hist = ttk.Frame(notebook, padding=10)
    notebook.add(tab_hist, text="История сезонов")

    hist_left = ttk.Frame(tab_hist)
    hist_left.pack(side="left", fill="both", expand=True)
    hist_right = ttk.Frame(tab_hist, padding=(12, 0, 0, 0))
    hist_right.pack(side="left", fill="both", expand=True)

    ttk.Label(
        hist_left,
        text=(
            "Импорт истории прошлого сезона (формат Excel-таблицы):\n"
            "Match Date, Team Home, 1 Odds, X Odds, 2 Odds, Away Team, …\n"
            "Все команды должны быть заранее в справочнике выбранной лиги."
        ),
        justify="left",
    ).pack(anchor="w")

    import_bar = ttk.Frame(hist_left)
    import_bar.pack(fill="x", pady=(8, 4))

    league_labels = [title for title, _key in hs.format_league_options()]
    league_keys = {title: key for title, key in hs.format_league_options()}

    ttk.Label(import_bar, text="Лига:").grid(row=0, column=0, sticky="w", padx=(0, 6))
    hist_league_var = tk.StringVar(value=league_labels[0])
    ttk.Combobox(
        import_bar,
        textvariable=hist_league_var,
        values=league_labels,
        state="readonly",
        width=28,
    ).grid(row=0, column=1, sticky="w", padx=(0, 12))

    ttk.Label(import_bar, text="Сезон:").grid(row=0, column=2, sticky="w", padx=(0, 6))
    hist_season_var = tk.StringVar(value="2024-25")
    ttk.Entry(import_bar, textvariable=hist_season_var, width=12).grid(row=0, column=3, sticky="w")

    hist_btns = ttk.Frame(hist_left)
    hist_btns.pack(anchor="w", pady=(8, 0))

    text_history = tk.Text(hist_left, width=90, height=22, relief="solid", borderwidth=1)
    text_history.pack(fill="both", expand=True, pady=(6, 0))

    sample_hist = _examples_dir() / "history_epl_2025_26.csv"
    if sample_hist.exists():
        try:
            text_history.insert("1.0", sample_hist.read_text(encoding="utf-8-sig"))
        except Exception:
            pass

    hist_path_var = tk.StringVar(value=f"Хранилище: {hs.data_root()}")
    ttk.Label(hist_left, textvariable=hist_path_var, foreground="#555").pack(anchor="w", pady=(6, 0))

    ttk.Label(hist_right, text="Сохранённые сезоны").pack(anchor="w")
    hist_tree = ttk.Treeview(
        hist_right,
        columns=("league", "season", "matches", "imported"),
        show="headings",
        height=12,
    )
    hist_tree.heading("league", text="Лига")
    hist_tree.heading("season", text="Сезон")
    hist_tree.heading("matches", text="Матчей")
    hist_tree.heading("imported", text="Импорт")
    hist_tree.column("league", width=160, anchor="w")
    hist_tree.column("season", width=72, anchor="w")
    hist_tree.column("matches", width=64, anchor="e")
    hist_tree.column("imported", width=150, anchor="w")
    hist_tree.pack(fill="both", expand=True, pady=(6, 0))

    ttk.Label(hist_right, text="Матчи сезона").pack(anchor="w", pady=(8, 0))
    hist_matches_tree = ttk.Treeview(
        hist_right,
        columns=("date", "home", "o1", "ox", "o2", "away", "score", "res"),
        show="headings",
        height=10,
    )
    for col, title, w, anchor in [
        ("date", "Дата", 88, "w"),
        ("home", "Дома", 120, "w"),
        ("o1", "1", 44, "e"),
        ("ox", "X", 44, "e"),
        ("o2", "2", 44, "e"),
        ("away", "Гости", 120, "w"),
        ("score", "Счёт", 52, "e"),
        ("res", "Исх.", 36, "center"),
    ]:
        hist_matches_tree.heading(col, text=title)
        hist_matches_tree.column(col, width=w, anchor=anchor)
    hist_matches_tree.pack(fill="both", expand=True, pady=(4, 0))

    hist_stats_var = tk.StringVar(value="Выберите сезон или нажмите «Просмотр истории».")
    ttk.Label(hist_right, textvariable=hist_stats_var, justify="left").pack(
        anchor="w", pady=(10, 0)
    )

    def refresh_history_tree():
        for item in hist_tree.get_children():
            hist_tree.delete(item)
        for info in hs.list_all_seasons():
            imported = info.imported_at.replace("T", " ")[:19] if info.imported_at else ""
            hist_tree.insert(
                "",
                "end",
                iid=f"{info.league}|{info.season}",
                values=(info.league_title, info.season, info.matches, imported),
            )
        hist_path_var.set(f"Хранилище: {hs.data_root()}")

    def selected_season_info():
        sel = hist_tree.selection()
        if not sel:
            return None
        league_key, season = sel[0].split("|", 1)
        return league_key, season

    def show_league_metrics(league_key: str):
        try:
            est = hs.home_advantage_prior(league_key)
            dm = hs.calibrate_draw_model(league_key)
        except Exception as exc:
            hist_stats_var.set(f"Ошибка расчёта: {exc}")
            return
        lines = [
            f"Лига: {hs.league_title(league_key)} ({league_key})",
            f"Сезонов в хранилище: {len(hs.list_seasons(league_key))}",
        ]
        if est.from_default:
            lines.append(f"H_prior (default): {est.h_prior:.2f}")
        else:
            lines.append(f"H_prior (история): {est.h_prior:.2f}")
            for season, h, w in est.seasons_used[:5]:
                lines.append(f"  {season}: H={h:.1f}, вес={w:.3f}")
            if len(est.seasons_used) > 5:
                lines.append(f"  … ещё {len(est.seasons_used) - 5} сезон(ов)")
        lines.append(f"H итог: {est.h_final:.2f}  (x{10 ** (est.h_final / 400):.4f}); {est.confidence}")
        lines.append(
            f"draw px(d) = clamp({dm.a:.4f} + ({dm.b:.6f})·|d|); "
            f"n={dm.n}, источник={dm.source}"
        )
        hist_stats_var.set("\n".join(lines))

    def show_league_metrics_for_combo():
        title = hist_league_var.get().strip()
        key = league_keys.get(title)
        if not key:
            try:
                key = hs.normalize_league(title)
            except ValueError as exc:
                messagebox.showerror("Лига", str(exc))
                return
        show_league_metrics(key)

    def _league_key_from_combo():
        title = hist_league_var.get().strip()
        key = league_keys.get(title)
        if not key:
            key = hs.normalize_league(title)
        return key

    def fill_hist_matches_tree(matches):
        for item in hist_matches_tree.get_children():
            hist_matches_tree.delete(item)
        for m in matches:
            score = ""
            if m.home_goals is not None and m.away_goals is not None:
                score = f"{m.home_goals}:{m.away_goals}"
            hist_matches_tree.insert(
                "",
                "end",
                values=(
                    m.date,
                    m.home_team,
                    f"{m.odds_1:g}",
                    f"{m.odds_x:g}",
                    f"{m.odds_2:g}",
                    m.away_team,
                    score,
                    m.derived_result(),
                ),
            )

    def view_history_season(league_key=None, season=None, select_tree=True):
        if league_key is None:
            league_key = _league_key_from_combo()
        if season is None:
            season = hist_season_var.get().strip()
        if not season:
            raise ValueError("Укажите сезон, напр. 2024-25")
        matches = hs.load_season(league_key, season)
        text_history.delete("1.0", "end")
        text_history.insert("1.0", hs.view_season_text(league_key, season))
        fill_hist_matches_tree(matches)
        est = hs.home_advantage_prior(league_key)
        dm = hs.calibrate_draw_model(league_key)
        lines = [
            f"{hs.league_title(league_key)} / {season}",
            f"Матчей: {len(matches)}",
            f"Файл: {hs.season_path(league_key, season)}",
            "",
            f"H_prior: {est.h_prior:.2f}  →  H={est.h_final:.2f} ({est.confidence})",
            f"draw: px(d)=clamp({dm.a:.4f}+({dm.b:.6f})·|d|), n={dm.n}",
        ]
        hist_stats_var.set("\n".join(lines))
        if select_tree:
            iid = f"{league_key}|{hs._safe_season(season)}"
            if hist_tree.exists(iid):
                hist_tree.selection_set(iid)
                hist_tree.see(iid)

    def on_hist_select(_event=None):
        info = selected_season_info()
        if not info:
            return
        league_key, season = info
        try:
            view_history_season(league_key, season, select_tree=False)
        except Exception as exc:
            hist_stats_var.set(str(exc))

    hist_tree.bind("<<TreeviewSelect>>", on_hist_select)

    def do_import_history(from_file=None):
        league_title_sel = hist_league_var.get().strip()
        season = hist_season_var.get().strip()
        if not league_title_sel:
            messagebox.showwarning("Импорт", "Выберите лигу.")
            return
        if not season:
            messagebox.showwarning("Импорт", "Укажите сезон, напр. 2024-25.")
            return
        league_key = league_keys.get(league_title_sel)
        if not league_key:
            try:
                league_key = hs.normalize_league(league_title_sel)
            except ValueError as exc:
                messagebox.showerror("Импорт", str(exc))
                return
        try:
            raw = (
                Path(from_file).read_text(encoding="utf-8-sig")
                if from_file is not None
                else text_history.get("1.0", "end")
            )
            cleaned, blank_removed = hs.clean_history_text(raw)
            if not cleaned.strip():
                raise ValueError("Нет данных: файл/текст пуст или только пустые строки")
            text_history.delete("1.0", "end")
            text_history.insert("1.0", cleaned)
            matches, blank_removed = hs.parse_history_text_with_stats(cleaned)
            if hs.season_exists(league_key, season):
                prev = len(hs.load_season(league_key, season))
                ok = messagebox.askyesno(
                    "Сезон уже есть",
                    f"{hs.league_title(league_key)} / {season} уже сохранён "
                    f"({prev} матчей).\n\nПерезаписать новыми данными ({len(matches)} матчей)?",
                    icon="warning",
                )
                if not ok:
                    return
            res = hs.import_season_matches(league_key, season, matches)
        except Exception as exc:
            messagebox.showerror("Ошибка импорта", str(exc))
            return
        refresh_history_tree()
        refresh_shin_team_options()
        iid = f"{res.league}|{res.season}"
        if hist_tree.exists(iid):
            hist_tree.selection_set(iid)
            hist_tree.see(iid)
        try:
            view_history_season(res.league, res.season, select_tree=False)
        except Exception:
            on_hist_select()
        messagebox.showinfo(
            "Импорт выполнен",
            f"{hs.league_title(res.league)} / {res.season}\n"
            f"Матчей: {res.matches}\n"
            + (
                f"(перезаписано, было {res.previous_matches})\n"
                if res.replaced
                else "(новый сезон)\n"
            )
            + (f"Удалено пустых строк: {blank_removed}\n" if blank_removed else "")
            + f"{res.path}",
        )

    def load_history_csv():
        path = filedialog.askopenfilename(
            title="Выберите CSV истории сезона",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8-sig")
            text_history.delete("1.0", "end")
            text_history.insert("1.0", content)
            do_import_history(from_file=None)
        except Exception as exc:
            messagebox.showerror("Ошибка загрузки CSV", str(exc))

    def view_history_clicked():
        try:
            view_history_season()
        except Exception as exc:
            messagebox.showerror("Просмотр истории", str(exc))

    ttk.Button(hist_btns, text="Загрузить CSV…", command=load_history_csv).grid(
        row=0, column=0, padx=(0, 6)
    )
    ttk.Button(
        hist_btns,
        text="Сохранить в хранилище",
        command=lambda: do_import_history(None),
    ).grid(row=0, column=1, padx=(0, 6))
    ttk.Button(hist_btns, text="Просмотр истории", command=view_history_clicked).grid(
        row=0, column=2, padx=(0, 6)
    )
    ttk.Button(hist_btns, text="Обновить список", command=refresh_history_tree).grid(
        row=0, column=3, padx=(0, 6)
    )
    ttk.Button(
        hist_btns,
        text="H / draw по лиге",
        command=show_league_metrics_for_combo,
    ).grid(row=0, column=4)

    refresh_history_tree()

    # ======================================================================
    # TAB 6: Линия (голы) — Poisson / Dixon-Coles из closing-линий
    # ======================================================================
    import goal_model as gm
    import goal_model_train as gmt

    tab_goal = ttk.Frame(notebook, padding=10)
    notebook.add(tab_goal, text="Линия (голы)")

    goal_state: dict = {"model": None, "raw": None}

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
    ttk.Label(goal_controls, text="Хозяева:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
    goal_home_combo = ttk.Combobox(goal_controls, textvariable=goal_home_var, state="readonly", width=24)
    goal_home_combo.grid(row=0, column=1, sticky="w", pady=4)
    ttk.Label(goal_controls, text="Гости:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
    goal_away_combo = ttk.Combobox(goal_controls, textvariable=goal_away_var, state="readonly", width=24)
    goal_away_combo.grid(row=1, column=1, sticky="w", pady=4)

    goal_neutral_var = tk.BooleanVar(value=False)
    goal_derby_var = tk.BooleanVar(value=False)
    goal_margin_var = tk.BooleanVar(value=False)
    _cb_neu = ttk.Checkbutton(goal_controls, text="Нейтральное поле", variable=goal_neutral_var)
    _cb_neu.grid(row=0, column=2, sticky="w", padx=(16, 0))
    _cb_der = ttk.Checkbutton(goal_controls, text="Дерби", variable=goal_derby_var)
    _cb_der.grid(row=1, column=2, sticky="w", padx=(16, 0))
    goal_margin_pct = tk.StringVar(value="3")
    _cb_mar = ttk.Checkbutton(goal_controls, text="Маржа, %:", variable=goal_margin_var)
    _cb_mar.grid(row=0, column=3, sticky="w", padx=(16, 0))
    ttk.Entry(goal_controls, textvariable=goal_margin_pct, width=6).grid(row=0, column=4, sticky="w")

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
    _cfg_entry(goal_cfg_frame, 2, 0, "ничья q_min:", "q_min", 0.85,
               hint="Насколько можно УМЕНЬШИТЬ ничью из матрицы. 0.85 = максимум −15%. Защита от перекоса модели ничьи.")
    _cfg_entry(goal_cfg_frame, 2, 1, "ничья q_max:", "q_max", 1.15,
               hint="Насколько можно УВЕЛИЧИТЬ ничью из матрицы. 1.15 = максимум +15%.")
    _cfg_entry(goal_cfg_frame, 2, 2, "default сезон:", "w_season_def", 1.0,
               hint="Множитель для матча, чья date не попала ни в один диапазон таблицы сезонов ниже (или столбца date нет).")

    goal_use_draw_var = tk.BooleanVar(value=True)
    goal_use_dc_var = tk.BooleanVar(value=True)
    _cb_draw = ttk.Checkbutton(goal_cfg_frame, text="Модель ничьи", variable=goal_use_draw_var)
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
    _attach_tip(_cb_der, "Матч-дерби: на прогноз влияет через множитель домашнего преимущества.")
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
        return gmt.ModelConfig(
            default_season_weight=f("w_season_def", 1.0),
            season_weights=_parse_season_weights(),
            alpha_ah=f("alpha_ah", 0.25),
            alpha_t=f("alpha_t", 0.5),
            draw_loss_weight=f("draw_loss", 1.5),
            prior_alpha=f("prior_alpha", 0.7),
            prior_weight=f("prior_weight", 0.0),
            promoted_reference_n=int(f("promoted_n", 3)),
            draw_diag_multiplier_min=f("q_min", 0.85),
            draw_diag_multiplier_max=f("q_max", 1.15),
            use_draw_model=goal_use_draw_var.get(),
            use_dixon_coles=goal_use_dc_var.get(),
        )

    goal_meta_var = tk.StringVar(value="Загрузите CSV closing-линий и обучите модель.")
    ttk.Label(tab_goal, textvariable=goal_meta_var, justify="left").pack(anchor="w", pady=(8, 4))

    goal_out = tk.Text(tab_goal, width=98, height=22, relief="solid", borderwidth=1,
                       font=("Consolas", 10))
    goal_out.pack(fill="both", expand=True, pady=(4, 0))
    goal_out.configure(state="disabled")

    def _goal_set_text(text: str):
        goal_out.configure(state="normal")
        goal_out.delete("1.0", "end")
        goal_out.insert("1.0", text)
        goal_out.configure(state="disabled")

    def _goal_refresh_teams():
        model = goal_state["model"]
        teams = sorted(model.strength.ratings) if model else []
        goal_home_combo["values"] = teams
        goal_away_combo["values"] = teams
        if len(teams) >= 2:
            goal_home_var.set(teams[0])
            goal_away_var.set(teams[1])

    def _goal_train(raw, path_label):
        cfg = _goal_cfg()
        model, prepared = gmt.train_full_model(raw, cfg)
        goal_state["model"] = model
        goal_state["raw"] = raw
        goal_path_var.set(path_label)
        return model

    def goal_train_from_path(path):
        try:
            raw = gmt.load_raw_matches(Path(path))
            if len(raw) < 2:
                raise ValueError("В файле меньше 2 матчей.")
            model = _goal_train(raw, f"Загружено: {path}  ({len(raw)} матчей)")
            c = model.calibration
            goal_meta_var.set(
                f"Команд: {len(model.strength.ratings)}   H(сила)={model.strength.home_advantage:.3f}   "
                f"RMSE_D={model.strength.rmse:.3f}\n"
                f"μ={model.goals.mu:.3f}  H_g={model.goals.home_goal_adv:.3f}  "
                f"RMSE_logλ={model.goals.rmse:.3f}\n"
                f"Калибровка: a={c.a:.3f} b={c.b:.3f} c={c.c:.3f} d={c.d:.3f} γ={c.gamma:.4f}"
            )
            _goal_refresh_teams()
            lines = ["Рейтинги (сила на нейтрали) / атака / оборона:"]
            for t, r in sorted(model.strength.ratings.items(), key=lambda kv: kv[1], reverse=True):
                lines.append(
                    f"  {t:<22} r={r:+.3f}   A={model.goals.attack[t]:+.3f}   "
                    f"Df={model.goals.defense[t]:+.3f}"
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
            c = model.calibration
            goal_meta_var.set(
                f"Команд: {len(model.strength.ratings)}   H(сила)={model.strength.home_advantage:.3f}   "
                f"RMSE_D={model.strength.rmse:.3f}\n"
                f"μ={model.goals.mu:.3f}  H_g={model.goals.home_goal_adv:.3f}\n"
                f"Калибровка: a={c.a:.3f} b={c.b:.3f} c={c.c:.3f} d={c.d:.3f} γ={c.gamma:.4f}   "
                f"Ничья: {model.draw.source}"
            )
            _goal_refresh_teams()
        except Exception as exc:
            messagebox.showerror("Линия (голы)", str(exc))

    def goal_predict_clicked():
        model = goal_state["model"]
        if model is None:
            messagebox.showwarning("Линия (голы)", "Сначала загрузите CSV и обучите модель.")
            return
        home = goal_home_var.get().strip()
        away = goal_away_var.get().strip()
        if not home or not away or home == away:
            messagebox.showwarning("Линия (голы)", "Выберите разные команды.")
            return
        try:
            pred = gmt.predict_match(
                model, home, away,
                neutral=goal_neutral_var.get(), derby=goal_derby_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("Линия (голы)", str(exc))
            return
        mk = pred.markets
        use_margin = goal_margin_var.get()
        try:
            margin = float(goal_margin_pct.get().replace(",", ".")) / 100.0
        except ValueError:
            margin = 0.0

        def k1x2():
            if use_margin and margin > 0:
                return gm.apply_margin_1x2(mk.p1, mk.px, mk.p2, margin)
            return mk.k1(), mk.kx(), mk.k2()

        def k2way(p_a, p_b, ka, kb):
            if use_margin and margin > 0:
                return gm.apply_margin_two_way(p_a, p_b, margin)
            return ka, kb

        ka1, kax, ka2 = k1x2()
        L = []
        tag = " (с маржой)" if use_margin and margin > 0 else " (честные)"
        L.append(f"=== {home} — {away}{' (нейтраль)' if goal_neutral_var.get() else ''} ===")
        L.append(f"λ_h={pred.lambda_home:.3f}  λ_a={pred.lambda_away:.3f}   "
                 f"D_final={pred.d_final:.3f}  S_final={pred.s_final:.3f}")
        if pred.draw_target is not None and pred.draw_diagnostics is not None:
            dd = pred.draw_diagnostics
            L.append(
                f"Ничья: модель→{pred.draw_target*100:.1f}%  "
                f"(матрица {dd['draw_from_matrix']*100:.1f}% → "
                f"{dd['draw_after_calibration']*100:.1f}%, q={dd['diag_multiplier_used']:.3f})"
            )
        L.append("")
        L.append(f"Коэффициенты{tag}:")
        L.append(f"  1X2:  П1={ka1:.2f}  X={kax:.2f}  П2={ka2:.2f}   "
                 f"(p: {mk.p1*100:.1f}% / {mk.px*100:.1f}% / {mk.p2*100:.1f}%)")
        t = mk.main_total
        ot, ut = k2way(1/t.home_or_over_odds, 1/t.away_or_under_odds,
                       t.home_or_over_odds, t.away_or_under_odds)
        L.append(f"  Тотал {t.line}:  Over {ot:.2f} / Under {ut:.2f}")
        a = mk.main_ah
        ah, aa = k2way(1/a.home_or_over_odds, 1/a.away_or_under_odds,
                       a.home_or_over_odds, a.away_or_under_odds)
        L.append(f"  Фора хозяев {a.line:+}:  {ah:.2f} / гости {aa:.2f}")
        L.append("")
        L.append("Индивидуальные тоталы (честные O/U):")
        for ln, ov, un in mk.team_totals_home:
            L.append(f"  {home} {ln}:  Over {ov:.2f} / Under {un:.2f}")
        for ln, ov, un in mk.team_totals_away:
            L.append(f"  {away} {ln}:  Over {ov:.2f} / Under {un:.2f}")
        L.append("")
        L.append("Тоталы (честные O/U):")
        for ml in mk.totals:
            if 1.0 <= ml.line <= 4.5:
                L.append(f"  {ml.line}:  Over {ml.home_or_over_odds:.2f} / "
                         f"Under {ml.away_or_under_odds:.2f}")
        L.append("")
        L.append("Топ счетов:")
        for i, j, p in mk.top_scores[:8]:
            L.append(f"  {i}:{j}  {p*100:.1f}%")
        _goal_set_text("\n".join(L))

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
