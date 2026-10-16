"""Foydalanuvchi ma'lumotlarini CSV, Excel, PDF yoki JSON (zaxira nusxa) ko'rinishida eksport qilish."""
import csv
import io
import json

from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from formatting import money

HEADERS = ["Sana", "Turi", "Summa", "Valyuta", "Toifa", "Kontragent", "Izoh", "Hamyon"]

TYPE_NAMES = {
    "expense": "Xarajat", "income": "Daromad",
    "debt_given": "Qarz berildi", "debt_received": "Qarz qaytdi",
    "debt_borrowed": "Qarz olindi", "debt_repaid": "Qarz qaytarildi",
    "credit_payment": "Kredit to'lovi", "credit_balance": "Kredit qoldig'i",
}


def _rows(transactions: list[dict]) -> list[list]:
    return [
        [
            t["created_at"], TYPE_NAMES.get(t["type"], t["type"]), t["amount"], t["currency"],
            t.get("category") or "", t.get("counterparty") or "", t.get("description") or "",
            t.get("wallet_name") or "",
        ]
        for t in transactions
    ]


def to_csv(transactions: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADERS)
    writer.writerows(_rows(transactions))
    return ("\ufeff" + buf.getvalue()).encode("utf-8")  # BOM — Excel UTF-8ni to'g'ri ochishi uchun


def to_excel(transactions: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Tranzaksiyalar"
    ws.append(HEADERS)
    for row in _rows(transactions):
        ws.append(row)
    for col_cells in ws.columns:
        width = max(len(str(c.value)) for c in col_cells if c.value is not None) if col_cells else 10
        ws.column_dimensions[col_cells[0].column_letter].width = min(max(width + 2, 10), 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def to_pdf(transactions: list[dict], user_name: str = "") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()

    elements = [
        Paragraph("Hisobchi AI — tranzaksiyalar hisoboti", styles["Title"]),
        Spacer(1, 4 * mm),
    ]
    if user_name:
        elements.append(Paragraph(user_name, styles["Normal"]))
    elements.append(Spacer(1, 6 * mm))

    data = [HEADERS] + [[str(c) if not isinstance(c, float) else money(c) for c in row]
                         for row in _rows(transactions)]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4C8BF5")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
    ]))
    elements.append(table)
    doc.build(elements)
    buf.seek(0)
    return buf.getvalue()


def to_backup_json(user_id: int, data: dict) -> bytes:
    """`data` — transactions, wallets, recurring, goals, budgets kabi to'liq ma'lumot to'plami."""
    payload = {"user_id": user_id, "format": "hisobchi-ai-backup-v1", **data}
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
