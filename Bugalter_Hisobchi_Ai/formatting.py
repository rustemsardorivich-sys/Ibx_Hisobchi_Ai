"""db.py dan kelgan raqamli natijalarni foydalanuvchiga o'qish uchun qulay matnga aylantiradi.
Summalarni AI emas, shu fayl (kod) formatlaydi — shunda raqamlarda adashish bo'lmaydi."""

PERIOD_NAMES = {"day": "Bugun", "week": "Oxirgi 7 kun", "month": "Bu oy", "year": "Oxirgi 12 oy"}


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
