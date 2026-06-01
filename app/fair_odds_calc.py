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

import math


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

def build_app():
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("Калькулятор средней A")
    root.geometry("960x560")
    root.minsize(880, 520)

    main = ttk.Frame(root, padding=10)
    main.pack(fill="both", expand=True)

    # ---- Левая часть: три колонки ----
    left = ttk.Frame(main)
    left.pack(side="left", fill="both", expand=True)

    headers = ["Коэф дома", "Коэф гости", "A"]
    for col, title in enumerate(headers):
        ttk.Label(left, text=title).grid(row=0, column=col, padx=6, sticky="w")

    text_home = tk.Text(left, width=14, height=22, relief="solid", borderwidth=1)
    text_away = tk.Text(left, width=14, height=22, relief="solid", borderwidth=1)
    text_a = tk.Text(left, width=16, height=22, relief="solid", borderwidth=1,
                     background="#f4f4f4")
    text_a.tag_configure("outlier", foreground="#c0392b")
    text_a.configure(state="disabled")

    text_home.grid(row=1, column=0, padx=6, pady=4, sticky="nsew")
    text_away.grid(row=1, column=1, padx=6, pady=4, sticky="nsew")
    text_a.grid(row=1, column=2, padx=6, pady=4, sticky="nsew")
    left.rowconfigure(1, weight=1)
    for c in range(3):
        left.columnconfigure(c, weight=1)

    # Пример из исходного скрина
    text_home.insert("1.0", "2.02\n1.88\n2.42\n2.07\n")
    text_away.insert("1.0", "2.21\n2.11\n2.79\n2.36\n")

    # ---- Правая часть: поля и кнопки ----
    right = ttk.Frame(main, padding=(16, 0, 0, 0))
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
    ttk.Combobox(right, textvariable=method_var, values=METHODS,
                 state="readonly", width=30).pack(anchor="w")

    drop_outliers = tk.BooleanVar(value=True)
    ttk.Checkbutton(right, text="Исключать аномалии (IQR x1.5)",
                    variable=drop_outliers).pack(anchor="w", pady=(8, 0))

    info_var = tk.StringVar(value="")
    info = ttk.Label(right, textvariable=info_var, justify="left",
                     foreground="#555")
    info.pack(anchor="w", pady=(8, 0))

    # ---- Логика ----
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
        """Считает A, выбросы и итоговые средние по валидным значениям."""
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
        return a_values, valid, flags, b

    def show_results(valid, flags, b, league=False):
        n_out = sum(flags)
        mean_sel = average(valid, method_var.get())
        var_mean.set(fmt(mean_sel))
        var_result.set(fmt(average(valid, GEOMETRIC), 3) if not league
                       else fmt(average(valid, GEOMETRIC)))
        var_a.set(fmt(average(valid, ARITHMETIC)))
        var_x.set(fmt(median(valid)))
        lines = [f"строк: {len(flags)}   валидных: {len(valid)}   аномалий: {n_out}"]
        if b is not None:
            lines.append(f"Q1={fmt(b['q1'], 3)}  Q3={fmt(b['q3'], 3)}  "
                         f"IQR={fmt(b['iqr'], 3)}")
            lines.append(f"границы: [{fmt(b['lower'], 3)} ; {fmt(b['upper'], 3)}]")
        lines.append(f"геом={fmt(average(valid, GEOMETRIC), 4)}  "
                     f"ариф={fmt(average(valid, ARITHMETIC), 4)}  "
                     f"медиана={fmt(median(valid), 4)}")
        info_var.set("\n".join(lines))

    def calc():
        try:
            _, valid, flags, b = analyze()
            show_results(valid, flags, b, league=False)
        except (ValueError, ZeroDivisionError) as exc:
            messagebox.showerror("Ошибка", str(exc))

    def calc_league():
        try:
            _, valid, flags, b = analyze()
            show_results(valid, flags, b, league=True)
        except (ValueError, ZeroDivisionError) as exc:
            messagebox.showerror("Ошибка", str(exc))

    ttk.Button(right, text="Считаем А лиги", command=calc_league).pack(
        anchor="w", pady=(16, 4), ipadx=20, ipady=4)
    ttk.Button(right, text="Считать", command=calc).pack(
        anchor="w", pady=4, ipadx=20, ipady=4)

    return root


def main():
    build_app().mainloop()


if __name__ == "__main__":
    main()
