"""Statistikani grafik (PNG rasm) ko'rinishida chiqarish. Telegram'ga rasm sifatida yuboriladi."""
import io

import matplotlib
matplotlib.use("Agg")  # serverda ekran kerak emas
import matplotlib.pyplot as plt

from formatting import money


def category_bar_chart(categories: dict, currency: str, title: str) -> io.BytesIO:
    """categories: {"Oziq-ovqat": 400000, "Transport": 120000, ...}"""
    if not categories:
        raise ValueError("Grafik uchun ma'lumot yo'q")

    items = list(categories.items())[:10]  # eng ko'p 10 toifa, aks holda grafik tiqilib qoladi
    names = [n for n, _ in items]
    values = [v for _, v in items]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    bars = ax.barh(names[::-1], values[::-1], color="#4C8BF5")
    ax.set_xlabel(f"Summa ({currency})")
    ax.set_title(title)
    for bar, val in zip(bars, values[::-1]):
        ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2, f" {money(val)}",
                 va="center", fontsize=9)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def monthly_trend_chart(monthly_data: dict, currency: str) -> io.BytesIO:
    """monthly_data — db.monthly() natijasi: {"2026-07": {"UZS": {...}}, ...}"""
    months = sorted(monthly_data.keys())
    if not months:
        raise ValueError("Grafik uchun ma'lumot yo'q")

    incomes, expenses = [], []
    for m in months:
        d = monthly_data[m].get(currency, {})
        incomes.append(d.get("daromad", 0))
        expenses.append(d.get("xarajat", 0))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(months, incomes, marker="o", label="Daromad", color="#2ecc71")
    ax.plot(months, expenses, marker="o", label="Xarajat", color="#e74c3c")
    ax.set_ylabel(f"Summa ({currency})")
    ax.set_title("Oylar bo'yicha daromad va xarajat")
    ax.legend()
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf
