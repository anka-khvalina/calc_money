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
            "Например: Barcelona,Real Madrid,1.72,4.00,4.80"
        ),
    ).pack(anchor="w")

    text_matches = tk.Text(rank_left, width=82, height=30, relief="solid", borderwidth=1)
    text_matches.pack(fill="both", expand=True, pady=(6, 0))

    sample_lines = []
    sample_path = Path(__file__).resolve().parents[1] / "docs" / "examples" / "season_odds_la_liga_2024_25.csv"
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

    top_var = tk.StringVar(value="0")
    ttk.Label(controls, text="TOP N (0 = все):").pack(anchor="w", pady=(8, 0))
    ttk.Entry(controls, textvariable=top_var, width=10).pack(anchor="w")

    robust_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        controls,
        text="Робастная оценка (Huber + вес)",
        variable=robust_var,
    ).pack(anchor="w", pady=(8, 0))

    stats_var = tk.StringVar(value="Результат пока не рассчитан")
    ttk.Label(controls, textvariable=stats_var, justify="left").pack(anchor="w", pady=(10, 6))

    ttk.Label(rank_right, text="Рейтинг команд").pack(anchor="w")
    tree = ttk.Treeview(
        rank_right,
        columns=("rank", "team", "rating", "coef"),
        show="headings",
        height=20,
    )
    tree.heading("rank", text="№")
    tree.heading("team", text="Команда")
    tree.heading("rating", text="Рейтинг")
    tree.heading("coef", text="Коэф. силы")
    tree.column("rank", width=44, anchor="e")
    tree.column("team", width=180, anchor="w")
    tree.column("rating", width=100, anchor="e")
    tree.column("coef", width=100, anchor="e")
    tree.pack(fill="both", expand=True)

    latest_result = {"value": None}

    def fill_tree(result, top_n):
        for item in tree.get_children():
            tree.delete(item)
        rows = result.teams if top_n <= 0 else result.teams[:top_n]
        for i, r in enumerate(rows, start=1):
            tree.insert(
                "",
                "end",
                values=(i, r.team, f"{r.rating:.3f}".replace(".", ","), f"{r.strength_coef:.4f}".replace(".", ",")),
            )

    def run_ranking(matches, top_n):
        result = tr.build_ranking(matches, robust=robust_var.get())
        latest_result["value"] = result
        fill_tree(result, top_n)
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

    btns = ttk.Frame(controls)
    btns.pack(anchor="w", pady=(8, 0))
    ttk.Button(btns, text="Рассчитать рейтинг", command=calc_from_text).grid(
        row=0, column=0, padx=(0, 6)
    )
    ttk.Button(btns, text="Загрузить CSV", command=load_csv).grid(row=0, column=1, padx=(0, 6))
    ttk.Button(btns, text="Сохранить рейтинг CSV", command=save_rating_csv).grid(row=0, column=2)

    # ======================================================================
    # TAB 3: Счет кэф (метод Shin)
    # ======================================================================
    import history_store as hs
    import match_shin_calc as msc

    tab_shin = ttk.Frame(notebook, padding=10)
    notebook.add(tab_shin, text="Счет кэф")

    shin_form = ttk.Frame(tab_shin)
    shin_form.pack(fill="x", anchor="n")

    shin_team1_var = tk.StringVar(value="Arsenal")
    shin_team2_var = tk.StringVar(value="Chelsea")
    shin_league_var = tk.StringVar(value=hs.format_league_options()[0][0])
    shin_season_var = tk.StringVar(value="2025-26")

    def shin_labeled(parent, row, label, var, width=32):
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 8), pady=4
        )
        ttk.Entry(parent, textvariable=var, width=width).grid(
            row=row, column=1, sticky="w", pady=4
        )

    shin_league_labels = [title for title, _key in hs.format_league_options()]
    shin_league_keys = {title: key for title, key in hs.format_league_options()}

    shin_labeled(shin_form, 0, "Команда 1:", shin_team1_var, 28)
    shin_labeled(shin_form, 1, "Команда 2:", shin_team2_var, 28)
    ttk.Label(shin_form, text="Лига:").grid(
        row=2, column=0, sticky="w", padx=(0, 8), pady=4
    )
    ttk.Combobox(
        shin_form,
        textvariable=shin_league_var,
        values=shin_league_labels,
        state="readonly",
        width=26,
    ).grid(row=2, column=1, sticky="w", pady=4)
    shin_labeled(shin_form, 3, "Сезон:", shin_season_var, 12)

    shin_out = ttk.LabelFrame(tab_shin, text="Результат (метод Shin)", padding=10)
    shin_out.pack(fill="both", expand=True, pady=(12, 0))

    shin_odds_var = tk.StringVar(value="—")
    shin_d_var = tk.StringVar(value="—")
    shin_p1_var = tk.StringVar(value="—")
    shin_px_var = tk.StringVar(value="—")
    shin_p2_var = tk.StringVar(value="—")
    shin_source_var = tk.StringVar(value="—")
    shin_season_used_var = tk.StringVar(value="—")
    shin_matches_var = tk.StringVar(value="—")
    shin_opp_var = tk.StringVar(value="")

    def shin_row(row, label, var, bold=True):
        ttk.Label(shin_out, text=label, width=28).grid(
            row=row, column=0, sticky="w", pady=2
        )
        font = ("", 11, "bold") if bold else ("", 10)
        ttk.Label(shin_out, textvariable=var, font=font).grid(
            row=row, column=1, sticky="w", pady=2
        )

    shin_row(0, "k1 / kx / k2 (1 / X / 2):", shin_odds_var)
    shin_row(1, "D (разница сил):", shin_d_var)
    shin_row(2, "P1 (вероятность):", shin_p1_var, bold=False)
    shin_row(3, "Draw / X:", shin_px_var, bold=False)
    shin_row(4, "P2 (вероятность):", shin_p2_var, bold=False)
    shin_row(5, "Источник данных:", shin_source_var)
    shin_row(6, "Сезон расчёта:", shin_season_used_var)
    shin_row(7, "Матчей в расчёте:", shin_matches_var)

    ttk.Label(shin_out, textvariable=shin_opp_var, foreground="#555").grid(
        row=8, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )

    def calc_shin_match():
        try:
            league_key = shin_league_keys[shin_league_var.get()]
            res = msc.calculate_shin_match(
                shin_team1_var.get(),
                shin_team2_var.get(),
                league_key,
                shin_season_var.get().strip(),
            )
            shin_odds_var.set(res.format_odds(2))
            d_line = fmt(res.d_market, 1)
            if res.h_used is not None:
                d_line += f"  (H = {fmt(res.h_used, 1)})"
            shin_d_var.set(d_line)
            shin_p1_var.set(fmt(res.p1 * 100, 2) + " %")
            shin_px_var.set(fmt(res.px * 100, 2) + " %")
            shin_p2_var.set(fmt(res.p2 * 100, 2) + " %")
            shin_source_var.set(res.source_label_ru)
            shin_season_used_var.set(res.season_used)
            shin_matches_var.set(str(res.matches_used))
            if res.common_opponent:
                shin_opp_var.set(f"Общий соперник: {res.common_opponent}")
            else:
                shin_opp_var.set("")
        except msc.ShinCalculationError as exc:
            messagebox.showerror("Счет кэф", str(exc))
        except Exception as exc:
            messagebox.showerror("Счет кэф", str(exc))

    ttk.Button(tab_shin, text="Рассчитать", command=calc_shin_match).pack(
        anchor="w", pady=(12, 0), ipadx=16, ipady=4
    )
    ttk.Label(
        tab_shin,
        text=(
            "Метод Shin: P1/P2 — формула Shin; ничья — draw-модель px(d).\n"
            "Приоритет: общий соперник → матчи лиги (≥3) → предыдущий сезон."
        ),
        foreground="#555",
        justify="left",
    ).pack(anchor="w", pady=(8, 0))

    # ======================================================================
    # TAB 4: история сезонов
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
            "Можно вставить из буфера или загрузить CSV-файл."
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

    text_history = tk.Text(hist_left, width=90, height=22, relief="solid", borderwidth=1)
    text_history.pack(fill="both", expand=True, pady=(6, 0))

    sample_hist = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "examples"
        / "history_epl_2025_26.csv"
    )
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

    hist_btns = ttk.Frame(hist_left)
    hist_btns.pack(anchor="w", pady=(8, 0))
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

    return root


def main():
    build_app().mainloop()


if __name__ == "__main__":
    main()
