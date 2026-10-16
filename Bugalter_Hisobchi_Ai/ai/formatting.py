"""db.py dan kelgan raqamli natijalarni foydalanuvchiga o'qish uchun qulay matnga aylantiradi.
Summalarni AI emas, shu fayl (kod) formatlaydi — shunda raqamlarda adashish bo'lmaydi."""
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

PERIOD_NAMES = {"day": "Bugun", "week": "Oxirgi 7 kun", "month": "Bu oy", "year": "Oxirgi 12 oy"}
MONTH_NAMES = (
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
)


def money(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def format_summary(data: dict) -> str:
    title = PERIOD_NAMES.get(data["davr"], data["davr"])
    lines = [f"📊 <b>{title}</b>\n"]

    if not data["valyutalar"]:
        lines.append("Bu davrda hech qanday yozuv topilmadi.")
        return "\n".join(lines)

    for cur, r in data["valyutalar"].items():
        lines.append(f"💰 Daromad: {money(r['daromad'])} {cur}")
        lines.append(f"💸 Xarajat: {money(r['xarajat'])} {cur}")
        if r["kredit_tolovlari"]:
            lines.append(f"🏦 Kredit to'lovlari: {money(r['kredit_tolovlari'])} {cur}")
        lines.append(f"📈 Qoldiq: {money(r['qolgan'])} {cur}")

        if r["xarajat_kategoriyalari"]:
            lines.append("\nEng ko'p xarajat qilingan toifalar:")
            for cat, amt in list(r["xarajat_kategoriyalari"].items())[:5]:
                lines.append(f"  • {cat}: {money(amt)} {cur}")
        lines.append("")

    return "\n".join(lines).strip()


def format_monthly_comparison(data: dict) -> str:
    def month_label(value: str) -> str:
        year, month = (int(part) for part in value.split("-"))
        return f"{MONTH_NAMES[month - 1].capitalize()} {year}"

    previous = data["oldingi_oy"]
    current = data["joriy_oy"]
    currencies = data["valyutalar"]
    lines = [f"📊 <b>Oylar taqqoslanishi</b>\n{month_label(previous)} → {month_label(current)}\n"]
    previous_data = currencies.get(previous, {})
    current_data = currencies.get(current, {})
    all_currencies = sorted(set(previous_data) | set(current_data))
    if not all_currencies:
        return "\n".join(lines + ["Bu oylar uchun hali moliyaviy yozuvlar topilmadi."])

    for currency in all_currencies:
        before = previous_data.get(currency, {})
        after = current_data.get(currency, {})
        lines.append(f"💰 <b>{currency}</b>")
        for key, label in (("daromad", "Daromad"), ("xarajat", "Xarajat"), ("qolgan", "Qoldiq")):
            old_amount = before.get(key, 0)
            current_amount = after.get(key, 0)
            difference = current_amount - old_amount
            if old_amount:
                change = f"{difference:+,.0f}".replace(",", " ")
                percent = f" ({difference / abs(old_amount) * 100:+.0f}%)"
            elif current_amount:
                change = f"{difference:+,.0f}".replace(",", " ")
                percent = " (yangi)"
            else:
                change, percent = "0", ""
            lines.append(
                f"  {label}: {money(old_amount)} → {money(current_amount)} "
                f"{currency} ({change}{percent})"
            )

        categories = after.get("xarajat_kategoriyalari", {})
        if categories:
            lines.append("  Joriy oyda ko'p sarflangan toifalar:")
            for category, amount in list(categories.items())[:3]:
                lines.append(f"    • {category}: {money(amount)} {currency}")
        lines.append("")
    return "\n".join(lines).strip()


def format_wallets(wallets: list[dict]) -> str:
    if not wallets:
        return "Hamyonlar topilmadi."
    lines = ["👛 <b>Hamyonlaringiz</b>\n"]
    for w in wallets:
        belgisi = " ⭐️" if w["is_default"] else ""
        lines.append(f"<b>{w['name']}</b>{belgisi}")
        for cur, bal in w["balanslar"].items():
            lines.append(f"  {money(bal)} {cur}")
        lines.append("")
    return "\n".join(lines).strip()


def format_recurring(items: list[dict]) -> str:
    if not items:
        return "Takroriy to'lovlar yo'q. /takroriy_qoshish bilan qo'shing."
    lines = ["🔁 <b>Takroriy to'lovlar</b>\n"]
    for r in items:
        belgi = "➕" if r["type"] == "income" else "➖"
        lines.append(
            f"{belgi} <b>{r['title']}</b> — {money(r['amount'])} {r['currency']}, "
            f"har oyning {r['day_of_month']}-kunida (ID: {r['id']})"
        )
    return "\n".join(lines)


def format_goals(goals: list[dict]) -> str:
    if not goals:
        return "Jamg'arma maqsadlari yo'q. /maqsad_qoshish bilan qo'shing."
    lines = ["🎯 <b>Jamg'arma maqsadlari</b>\n"]
    for g in goals:
        foiz = min(100, round(g["saved_amount"] / g["target_amount"] * 100)) if g["target_amount"] else 0
        lines.append(
            f"<b>{g['title']}</b> (ID: {g['id']})\n"
            f"  {money(g['saved_amount'])} / {money(g['target_amount'])} {g['currency']} ({foiz}%)"
        )
        if g.get("target_date"):
            try:
                target = date.fromisoformat(g["target_date"])
            except ValueError:
                lines.append(f"  Maqsad sanasi noto'g'ri formatda: {g['target_date']}")
            else:
                lines.append(f"  Maqsad sanasi: {g['target_date']}")
                remaining = max(0, g["target_amount"] - g["saved_amount"])
                today = datetime.now(ZoneInfo("Asia/Tashkent")).date()
                months = (target.year - today.year) * 12 + target.month - today.month + 1
                if remaining == 0:
                    lines.append("  Maqsad to'liq yig'ilgan. 🎉")
                elif target < today:
                    lines.append(f"  Muddat o'tgan; qolgan summa: {money(remaining)} {g['currency']}.")
                else:
                    monthly_amount = math.ceil(remaining / max(1, months) * 100) / 100
                    lines.append(f"  Oyiga ajratish kerak: {money(monthly_amount)} {g['currency']}.")
        else:
            lines.append("  Oyiga qancha ajratishni hisoblash uchun maqsad sanasini kiriting.")
        lines.append("")
    return "\n".join(lines).strip()


def format_budgets(budgets: list[dict], spent_by_currency: dict) -> str:
    if not budgets:
        return "Byudjet limiti o'rnatilmagan. /byudjet 3000000 kabi yozing."
    lines = ["🎚 <b>Oylik byudjet</b>\n"]
    for b in budgets:
        spent = spent_by_currency.get(b["currency"], 0)
        foiz = min(999, round(spent / b["monthly_limit"] * 100)) if b["monthly_limit"] else 0
        lines.append(f"{money(spent)} / {money(b['monthly_limit'])} {b['currency']} ({foiz}%)")
    return "\n".join(lines)


def format_debts(data: dict) -> str:
    lines = ["🤝 <b>Qarzlar va kredit holati</b>\n"]

    if data["menga_qarzdor"]:
        lines.append("Sizga qarzdorlar:")
        for d in data["menga_qarzdor"]:
            lines.append(f"  • {d['kim']}: {money(d['summa'])} {d['valyuta']}")
    else:
        lines.append("Sizga hech kim qarzdor emas.")

    lines.append("")
    if data["men_qarzdorman"]:
        lines.append("Siz qarzdorsiz:")
        for d in data["men_qarzdorman"]:
            lines.append(f"  • {d['kim']}: {money(d['summa'])} {d['valyuta']}")
    else:
        lines.append("Sizning qarzingiz yo'q.")

    if data["kredit_qoldigi"]:
        lines.append("\nKredit qoldig'i:")
        for cur, amt in data["kredit_qoldigi"].items():
            lines.append(f"  • {money(amt)} {cur}")

    return "\n".join(lines)
