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
    for row_idx, row in enumerate(reader, start=1):
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
    root.title("Калькулятор A + Рейтинг команд")
    root.geometry("1180x680")
    root.minsize(1020, 620)

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
        result = tr.build_ranking(matches)
        latest_result["value"] = result
        fill_tree(result, top_n)
        stats_var.set(
            f"Матчей: {result.matches_count}\n"
            f"Команд: {len(result.teams)}\n"
            f"H: {str(round(result.home_advantage, 3)).replace('.', ',')}  "
            f"(x{str(round(result.home_advantage_coef, 4)).replace('.', ',')})\n"
            f"RMSE: {str(round(result.rmse, 3)).replace('.', ',')}"
        )

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

    return root


def main():
    build_app().mainloop()


if __name__ == "__main__":
    main()
