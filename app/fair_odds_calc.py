"""
Калькулятор средней A по линиям 1X2.

Фронт повторяет исходное приложение:
  колонки "Коэф дома" / "Коэф гости" / "A",
  поля "Результат", "A:", "X :", "Средняя А",
  кнопки "Считаем А лиги" и "Считать".

Добавлено поле выбора метода усреднения "Средней A":
  - Среднеарифметическое (как в исходной версии)
  - Геометрическое
  - Линейное усечённое 5%
  - Усечённо-геометрическое 5%

Мера строки: A = (Коэф гости - 1) / (Коэф дома - 1).
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


def _trim(sorted_values, fraction):
    k = int(len(sorted_values) * fraction)
    if k == 0:
        return list(sorted_values)
    return sorted_values[k:len(sorted_values) - k]


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
    root.geometry("900x520")
    root.minsize(820, 480)

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
    text_a = tk.Text(left, width=14, height=22, relief="solid", borderwidth=1,
                     background="#f4f4f4")
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
        entry = ttk.Entry(parent, textvariable=var, width=30)
        entry.pack(anchor="w")
        return var

    var_result = labeled_entry(right, "Результат:")
    var_a = labeled_entry(right, "A:")
    var_x = labeled_entry(right, "X :")
    var_mean = labeled_entry(right, "Средняя А")

    ttk.Label(right, text="Метод расчёта").pack(anchor="w", pady=(12, 0))
    method_var = tk.StringVar(value=ARITHMETIC)
    method_box = ttk.Combobox(right, textvariable=method_var, values=METHODS,
                              state="readonly", width=28)
    method_box.pack(anchor="w")

    # ---- Логика ----
    def read_columns():
        home = parse_column(text_home.get("1.0", "end"))
        away = parse_column(text_away.get("1.0", "end"))
        return home, away

    def fill_a_column(a_values):
        text_a.configure(state="normal")
        text_a.delete("1.0", "end")
        text_a.insert("1.0", "\n".join(fmt(round(v, 2), 3) for v in a_values) + "\n")
        text_a.configure(state="disabled")

    def calc():
        try:
            home, away = read_columns()
            a_values = compute_a(home, away)
            fill_a_column(a_values)
            mean = average(a_values, method_var.get())
            var_mean.set(fmt(mean))
            var_a.set(fmt(mean))
            var_result.set(fmt(mean, 3))
            var_x.set(str(len(a_values)))
        except (ValueError, ZeroDivisionError) as exc:
            messagebox.showerror("Ошибка", str(exc))

    def calc_league():
        """Среднее A по всему введённому пулу (А лиги) выбранным методом."""
        try:
            home, away = read_columns()
            a_values = compute_a(home, away)
            fill_a_column(a_values)
            league = average(a_values, method_var.get())
            var_result.set(fmt(league))
            var_a.set(fmt(league))
        except (ValueError, ZeroDivisionError) as exc:
            messagebox.showerror("Ошибка", str(exc))

    btn_league = ttk.Button(right, text="Считаем А лиги", command=calc_league)
    btn_league.pack(anchor="w", pady=(16, 4), ipadx=20, ipady=4)

    btn_calc = ttk.Button(right, text="Считать", command=calc)
    btn_calc.pack(anchor="w", pady=4, ipadx=20, ipady=4)

    return root


def main():
    build_app().mainloop()


if __name__ == "__main__":
    main()
