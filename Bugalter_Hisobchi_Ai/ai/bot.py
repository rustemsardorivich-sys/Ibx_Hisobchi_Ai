"""Hisobchi AI — Telegram bot. Ishga tushirish: python bot.py"""
import asyncio
import logging
from datetime import date

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, Message, PreCheckoutQuery,
)
from aiohttp import web

import ai_client
import charts
import config
import db
import exports
import payments
import scheduler
import webhook_server
from formatting import (
    format_budgets, format_debts, format_goals, format_monthly_comparison, format_recurring,
    format_summary, format_wallets, money,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

router = Router()


# ==================== FSM HOLATLARI (hamyonlar o'rtasida o'tkazma) ====================

class TransferStates(StatesGroup):
    choosing_from = State()
    choosing_to = State()
    entering_amount = State()


# ==================== YORDAMCHI FUNKSIYALAR ====================

def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def _first_currency(monthly_data: dict) -> str:
    for month in monthly_data.values():
        for cur in month:
            return cur
    return "UZS"


def pro_offer_keyboard(price: int, stars_price: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Click orqali — {money(price)} so'm", callback_data="pay:click")],
        [InlineKeyboardButton(text=f"🟣 Uzum Bank orqali — {money(price)} so'm", callback_data="pay:uzum")],
        [InlineKeyboardButton(text=f"⭐ Telegram Stars — {stars_price} Stars", callback_data="pay:stars")],
    ])


async def send_pro_offer(message: Message, reason: str) -> None:
    price = await db.get_price()
    await message.answer(
        f"{reason}\n\n"
        f"👑 <b>PRO obuna</b> — oyiga {money(price)} so'm (yoki {config.STARS_PRICE} Telegram Stars).\n"
        f"PRO bilan sizga ochiladi:\n"
        f"  • Yillik to'liq moliyaviy hisobot\n"
        f"  • Shaxsiy AI buxgalter maslahati va AI yordamchi (savol-javob)\n\n"
        f"To'lov usulini tanlang:",
        reply_markup=pro_offer_keyboard(price, config.STARS_PRICE),
    )


async def handle_report(message: Message, user_id: int, period: str) -> None:
    period = period or "month"
    if period == "year" and not await db.is_pro(user_id):
        await send_pro_offer(message, "📅 Yillik hisobot faqat PRO obunachilar uchun ochiq.")
        return
    data = await db.summary(user_id, period)
    await message.answer(format_summary(data))


async def build_ai_payload(user_id: int) -> dict:
    return {
        "shu_oy": (await db.summary(user_id, "month"))["valyutalar"],
        "oxirgi_12_oy": await db.monthly(user_id),
        "qarzlar_va_kredit": await db.debts(user_id),
        "hamyonlar": [
            {"nomi": w["name"], "balanslar": w["balanslar"]} for w in await db.wallet_balances(user_id)
        ],
    }


async def handle_advice(message: Message, user_id: int) -> None:
    if not await db.is_pro(user_id):
        await send_pro_offer(message, "🧠 Shaxsiy AI buxgalter maslahati faqat PRO obunachilar uchun ochiq.")
        return
    await message.answer("Tahlil qilyapman, biroz kuting... ⏳")
    advice = await ai_client.get_advice(await build_ai_payload(user_id))
    await message.answer(advice)


async def handle_chat_query(message: Message, user_id: int, question: str) -> None:
    if not await db.is_pro(user_id):
        await send_pro_offer(message, "🧠 AI yordamchidan savol so'rash faqat PRO obunachilar uchun ochiq.")
        return
    await message.answer("O'ylab ko'ryapman... ⏳")
    answer = await ai_client.answer_question(await build_ai_payload(user_id), question)
    await message.answer(answer)


async def maybe_warn_budget(message: Message, user_id: int, currency: str) -> None:
    alert = await db.check_budget_alert(user_id, currency)
    if not alert:
        return
    if alert["level"] == "exceeded":
        text = (
            f"🚨 Diqqat! Bu oy {currency} bo'yicha byudjet limitidan oshib ketdingiz: "
            f"{money(alert['spent'])} / {money(alert['limit'])} {currency}."
        )
    else:
        text = (
            f"⚠️ Bu oy {currency} byudjet limitingizning {int(alert['ratio']*100)}%ini sarfladingiz: "
            f"{money(alert['spent'])} / {money(alert['limit'])} {currency}."
        )
    await message.answer(text)


# ==================== ODDIY BUYRUQLAR ====================

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await db.ensure_user(message.from_user.id, message.from_user.full_name)
    await message.answer(
        "Salom! 👋 Men <b>Hisobchi AI</b>man — moliyaviy yordamchingiz.\n\n"
        "Menga oddiy tilda yozing, masalan:\n"
        "  • «Palov uchun 40 ming ketdi»\n"
        "  • «Javohirga 300 ming qarz berdim»\n"
        "  • «Maoshim 5 mln keldi»\n"
        "  • «Click kartamdan taksiga 25 ming ketdi» — hamyon ko'rsatib\n\n"
        "To'liq buyruqlar ro'yxati uchun: /yordam\n"
        "PRO obuna haqida: /pro"
    )


@router.message(Command("yordam"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📋 <b>Buyruqlar</b>\n\n"
        "<b>Hisobotlar</b>\n"
        "/kunlik /haftalik /oylik /yillik — davr bo'yicha hisobot\n"
        "/taqqosla — joriy va o'tgan oyni taqqoslash\n"
        "/grafik — grafik (rasm) ko'rinishida statistika\n"
        "/qarzlar — kim kimga qarzdor, kredit qoldig'i\n"
        "/eslatmalar — avtomatik hisobotlarni yoqish/o'chirish\n\n"
        "<b>Hamyonlar</b>\n"
        "/hamyonlar — hamyonlar va balanslar\n"
        "/hamyon_qoshish &lt;nom&gt; — yangi hamyon\n"
        "/otkazma — hamyonlar orasida pul o'tkazish\n\n"
        "<b>Byudjet va jamg'arma</b>\n"
        "/byudjet &lt;summa&gt; — oylik xarajat limiti (UZS)\n"
        "/maqsad_qoshish Nomi | Summa | Sana(ixtiyoriy)\n"
        "/maqsadlar — jamg'arma maqsadlari\n"
        "/maqsad_toldirish &lt;ID&gt; &lt;summa&gt;\n\n"
        "<b>Takroriy to'lovlar</b>\n"
        "/takroriy_qoshish Nomi | Summa | expense|income | Kun(1-28)\n"
        "/takroriy — ro'yxat\n"
        "/takroriy_ochirish &lt;ID&gt;\n\n"
        "<b>Boshqa</b>\n"
        "/eksport — CSV / Excel / PDF / zaxira nusxa\n"
        "/maslahat — shaxsiy AI buxgalter (PRO)\n"
        "/bekor — oxirgi yozuvni o'chirish\n"
        "/pro — PRO obuna haqida\n\n"
        "Savol ham berishingiz mumkin, masalan: «Fevral va martni solishtir» (PRO)."
    )


@router.message(Command("pro"))
async def cmd_pro(message: Message) -> None:
    user_id = message.from_user.id
    await db.ensure_user(user_id, message.from_user.full_name)
    until = await db.get_pro_until(user_id)
    if until and await db.is_pro(user_id):
        await message.answer(f"👑 Sizda PRO obuna faol. Tugash sanasi: {until}.")
        return
    await send_pro_offer(message, "PRO obunangiz hali faollashtirilmagan.")


@router.callback_query(F.data.in_({"pay:click", "pay:uzum"}))
async def on_pay(call: CallbackQuery) -> None:
    provider = call.data.split(":")[1]
    user_id = call.from_user.id
    price = await db.get_price()
    merchant_trans_id = await db.create_payment(user_id, provider, price)

    if provider == "click":
        link = payments.create_click_link(merchant_trans_id, price)
    else:
        link = payments.create_uzum_link(merchant_trans_id, price)

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 To'lash", url=link)]])
    await call.message.answer(
        f"{money(price)} so'mlik to'lov havolasi tayyor. To'lagach, PRO obuna "
        f"bir necha soniyada avtomatik faollashadi. ✅",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data == "pay:stars")
async def on_pay_stars(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    merchant_trans_id = await db.create_payment(user_id, "stars", config.STARS_PRICE)
    await call.message.answer_invoice(
        title="Hisobchi AI — PRO obuna",
        description=f"{config.PRO_DAYS} kunlik PRO obuna",
        payload=merchant_trans_id,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="PRO obuna", amount=config.STARS_PRICE)],
    )
    await call.answer()


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    payment = await db.get_payment(pre_checkout_query.invoice_payload)
    if not payment or payment["status"] != "pending":
        await pre_checkout_query.answer(ok=False, error_message="To'lov topilmadi, qaytadan /pro bosing.")
        return
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    sp = message.successful_payment
    paid = await db.mark_payment_paid(sp.invoice_payload, sp.telegram_payment_charge_id)
    if not paid:
        return  # allaqachon ishlangan (masalan, Telegram qayta yuborgan)
    new_until = await db.grant_pro(paid["user_id"], config.PRO_DAYS)
    await message.answer(
        f"✅ To'lov qabul qilindi! PRO obuna {config.PRO_DAYS} kunga faollashtirildi "
        f"(tugash sanasi: {new_until}). 👑"
    )


@router.message(Command("bekor"))
async def cmd_undo(message: Message) -> None:
    removed = await db.delete_last(message.from_user.id)
    if not removed:
        await message.answer("O'chiriladigan yozuv topilmadi.")
        return
    await message.answer(
        f"O'chirildi: {removed['description']} — {money(removed['amount'])} {removed['currency']}. ❌"
    )


@router.message(Command("kunlik"))
async def cmd_daily(message: Message) -> None:
    await handle_report(message, message.from_user.id, "day")


@router.message(Command("haftalik"))
async def cmd_weekly(message: Message) -> None:
    await handle_report(message, message.from_user.id, "week")


@router.message(Command("oylik"))
async def cmd_monthly(message: Message) -> None:
    await handle_report(message, message.from_user.id, "month")


@router.message(Command("taqqosla"))
async def cmd_compare_months(message: Message) -> None:
    data = await db.monthly_comparison(message.from_user.id)
    await message.answer(format_monthly_comparison(data))


@router.message(Command("yillik"))
async def cmd_yearly(message: Message) -> None:
    await handle_report(message, message.from_user.id, "year")


@router.message(Command("qarzlar"))
async def cmd_debts(message: Message) -> None:
    data = await db.debts(message.from_user.id)
    await message.answer(format_debts(data))


@router.message(Command("maslahat"))
async def cmd_advice(message: Message) -> None:
    await handle_advice(message, message.from_user.id)


# ==================== HAMYONLAR VA O'TKAZMA ====================

@router.message(Command("hamyonlar"))
async def cmd_wallets(message: Message) -> None:
    data = await db.wallet_balances(message.from_user.id)
    await message.answer(format_wallets(data))


@router.message(Command("hamyon_qoshish"))
async def cmd_wallet_add(message: Message, command: CommandObject) -> None:
    name = (command.args or "").strip()
    if not name:
        await message.answer("Foydalanish: /hamyon_qoshish Click karta")
        return
    await db.create_wallet(message.from_user.id, name)
    await message.answer(f"✅ \"{name}\" hamyoni qo'shildi.")


@router.message(Command("otkazma"))
async def cmd_transfer_start(message: Message, state: FSMContext) -> None:
    wallets = await db.list_wallets(message.from_user.id)
    if len(wallets) < 2:
        await message.answer(
            "O'tkazma qilish uchun kamida 2 ta hamyon kerak. "
            "Avval /hamyon_qoshish bilan yangi hamyon qo'shing."
        )
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=w["name"], callback_data=f"tr_from:{w['id']}")] for w in wallets
    ])
    await state.set_state(TransferStates.choosing_from)
    await message.answer("Qaysi hamyondan o'tkazasiz?", reply_markup=kb)


@router.callback_query(TransferStates.choosing_from, F.data.startswith("tr_from:"))
async def on_transfer_from(call: CallbackQuery, state: FSMContext) -> None:
    from_id = int(call.data.split(":")[1])
    wallets = await db.list_wallets(call.from_user.id)
    others = [w for w in wallets if w["id"] != from_id]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=w["name"], callback_data=f"tr_to:{w['id']}")] for w in others
    ])
    await state.update_data(from_wallet_id=from_id)
    await state.set_state(TransferStates.choosing_to)
    await call.message.answer("Qaysi hamyonga o'tkazasiz?", reply_markup=kb)
    await call.answer()


@router.callback_query(TransferStates.choosing_to, F.data.startswith("tr_to:"))
async def on_transfer_to(call: CallbackQuery, state: FSMContext) -> None:
    to_id = int(call.data.split(":")[1])
    await state.update_data(to_wallet_id=to_id)
    await state.set_state(TransferStates.entering_amount)
    await call.message.answer("Summani kiriting (masalan: 100000):")
    await call.answer()


@router.message(TransferStates.entering_amount)
async def on_transfer_amount(message: Message, state: FSMContext) -> None:
    if message.text and message.text.startswith("/"):
        await state.clear()
        await message.answer("O'tkazma bekor qilindi.")
        return

    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.replace(".", "", 1).isdigit():
        await message.answer("Iltimos, summani faqat raqam bilan yozing (masalan: 100000).")
        return

    data = await state.get_data()
    amount = float(raw)
    wallets = {w["id"]: w for w in await db.list_wallets(message.from_user.id)}
    from_w = wallets.get(data.get("from_wallet_id"))
    to_w = wallets.get(data.get("to_wallet_id"))
    await state.clear()
    if not from_w or not to_w:
        await message.answer("Xatolik yuz berdi, qaytadan /otkazma deb urinib ko'ring.")
        return

    await db.create_transfer(message.from_user.id, from_w["id"], to_w["id"], amount, from_w["currency"])
    await message.answer(
        f"✅ {money(amount)} {from_w['currency']} \"{from_w['name']}\"dan \"{to_w['name']}\"ga o'tkazildi."
    )


# ==================== BYUDJET ====================

@router.message(Command("byudjet"))
async def cmd_budget(message: Message, command: CommandObject) -> None:
    user_id = message.from_user.id
    args = (command.args or "").strip()
    if args:
        amount_str = args.replace(" ", "").replace(",", "")
        if not amount_str.isdigit():
            await message.answer("Foydalanish: /byudjet 3000000 (so'mda, oylik limit)")
            return
        await db.set_budget(user_id, "UZS", float(amount_str))
        await message.answer(f"✅ Oylik byudjet limiti {money(float(amount_str))} so'm qilib belgilandi.")
        return

    budgets = await db.get_budgets(user_id)
    month_data = (await db.summary(user_id, "month"))["valyutalar"]
    spent_by_currency = {cur: v["xarajat"] for cur, v in month_data.items()}
    await message.answer(format_budgets(budgets, spent_by_currency))


# ==================== JAMG'ARMA MAQSADLARI ====================

@router.message(Command("maqsad_qoshish"))
async def cmd_goal_add(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split("|")
    if len(parts) < 2:
        await message.answer(
            "Foydalanish: /maqsad_qoshish Mashina uchun | 50000000 | 2027-06-01\n"
            "(oxirgi sana ixtiyoriy, summa UZS'da)"
        )
        return
    title = parts[0].strip()
    amount_str = parts[1].strip().replace(" ", "").replace(",", "")
    if not title or not amount_str.isdigit():
        await message.answer("Nom va summani to'g'ri formatda yozing (summa faqat raqam).")
        return
    target_date = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    if target_date:
        try:
            date.fromisoformat(target_date)
        except ValueError:
            await message.answer("Sanani YYYY-MM-DD ko'rinishida yozing, masalan: 2027-06-01.")
            return
    goal_id = await db.add_goal(message.from_user.id, title, float(amount_str), "UZS", target_date)
    await message.answer(f"🎯 \"{title}\" maqsadi qo'shildi (ID: {goal_id}).")


@router.message(Command("maqsadlar"))
async def cmd_goals(message: Message) -> None:
    goals = await db.list_goals(message.from_user.id)
    await message.answer(format_goals(goals))


@router.message(Command("maqsad_toldirish"))
async def cmd_goal_contribute(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].replace(".", "", 1).isdigit():
        await message.answer(
            "Foydalanish: /maqsad_toldirish <ID> <summa>  (masalan: /maqsad_toldirish 1 500000)"
        )
        return
    goal = await db.contribute_goal(int(parts[0]), message.from_user.id, float(parts[1]))
    if not goal:
        await message.answer("Bunday maqsad topilmadi.")
        return
    if goal["completed"]:
        await message.answer(f"🎉 Tabriklaymiz! \"{goal['title']}\" maqsadiga yetdingiz!")
    else:
        await message.answer(
            f"✅ \"{goal['title']}\"ga {money(float(parts[1]))} {goal['currency']} qo'shildi. "
            f"Jami: {money(goal['saved_amount'])} / {money(goal['target_amount'])} {goal['currency']}."
        )


# ==================== TAKRORIY TO'LOVLAR ====================

@router.message(Command("takroriy_qoshish"))
async def cmd_recurring_add(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split("|")
    if len(parts) < 4:
        await message.answer(
            "Foydalanish: /takroriy_qoshish Ijara | 1500000 | expense | 5\n"
            "(nomi | summa | expense yoki income | oyning kuni 1-28)"
        )
        return
    title = parts[0].strip()
    amount_str = parts[1].strip().replace(" ", "").replace(",", "")
    ttype = parts[2].strip().lower()
    day_str = parts[3].strip()
    if not title or not amount_str.isdigit() or ttype not in ("expense", "income") or not day_str.isdigit():
        await message.answer("Ma'lumot noto'g'ri formatda. Masalan: /takroriy_qoshish Ijara | 1500000 | expense | 5")
        return
    await db.add_recurring(
        message.from_user.id, title, ttype, float(amount_str), "UZS", None, None, int(day_str)
    )
    await message.answer(f"🔁 \"{title}\" takroriy to'lov qo'shildi — har oyning {day_str}-kunida.")


@router.message(Command("takroriy"))
async def cmd_recurring_list(message: Message) -> None:
    items = await db.list_recurring(message.from_user.id)
    await message.answer(format_recurring(items))


@router.message(Command("takroriy_ochirish"))
async def cmd_recurring_remove(message: Message, command: CommandObject) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Foydalanish: /takroriy_ochirish <ID>")
        return
    ok = await db.deactivate_recurring(int(command.args.strip()), message.from_user.id)
    await message.answer("✅ O'chirildi." if ok else "Bunday ID topilmadi.")


# ==================== ESLATMALAR (avtomatik hisobotlar) ====================

def reminders_keyboard(prefs: dict) -> InlineKeyboardMarkup:
    def label(name: str, key: str) -> str:
        return f"{'✅' if prefs[key] else '⬜️'} {name}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label("Kunlik", "daily"), callback_data="rep:daily")],
        [InlineKeyboardButton(text=label("Haftalik", "weekly"), callback_data="rep:weekly")],
        [InlineKeyboardButton(text=label("Oylik", "monthly"), callback_data="rep:monthly")],
    ])


@router.message(Command("eslatmalar"))
async def cmd_reminders(message: Message) -> None:
    prefs = await db.get_report_prefs(message.from_user.id)
    await message.answer(
        f"🔔 Avtomatik hisobotlar (soat {config.REPORT_HOUR}:00da, Toshkent vaqti bilan keladi). "
        f"Yoqish/o'chirish uchun bosing:",
        reply_markup=reminders_keyboard(prefs),
    )


@router.callback_query(F.data.startswith("rep:"))
async def on_reminder_toggle(call: CallbackQuery) -> None:
    period = call.data.split(":")[1]
    prefs = await db.get_report_prefs(call.from_user.id)
    prefs[period] = not prefs[period]
    await db.set_report_pref(call.from_user.id, period, prefs[period])
    await call.message.edit_reply_markup(reply_markup=reminders_keyboard(prefs))
    await call.answer("Yangilandi ✅")


# ==================== GRAFIK ====================

def chart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Bu oy toifalar bo'yicha", callback_data="chart:categories")],
        [InlineKeyboardButton(text="📈 12 oylik tendentsiya", callback_data="chart:trend")],
    ])


@router.message(Command("grafik"))
async def cmd_chart(message: Message) -> None:
    await message.answer("Qaysi grafikni ko'rsataman?", reply_markup=chart_keyboard())


@router.callback_query(F.data.startswith("chart:"))
async def on_chart(call: CallbackQuery) -> None:
    kind = call.data.split(":")[1]
    user_id = call.from_user.id
    await call.answer("Grafik tayyorlanyapti... ⏳")
    try:
        if kind == "categories":
            data = await db.summary(user_id, "month")
            currency = next(iter(data["valyutalar"]), None)
            if not currency:
                await call.message.answer("Bu oy hali xarajat yo'q.")
                return
            cats = data["valyutalar"][currency]["xarajat_kategoriyalari"]
            buf = charts.category_bar_chart(cats, currency, "Bu oy xarajatlar toifalar bo'yicha")
            fname = "toifalar.png"
        else:
            monthly_data = await db.monthly(user_id)
            currency = _first_currency(monthly_data)
            buf = charts.monthly_trend_chart(monthly_data, currency)
            fname = "tendentsiya.png"
    except ValueError:
        await call.message.answer("Hali grafik chizish uchun yetarli ma'lumot yo'q.")
        return
    await call.message.answer_photo(BufferedInputFile(buf.read(), filename=fname))


# ==================== EKSPORT ====================

def export_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 CSV", callback_data="exp:csv")],
        [InlineKeyboardButton(text="📊 Excel", callback_data="exp:xlsx")],
        [InlineKeyboardButton(text="📑 PDF", callback_data="exp:pdf")],
        [InlineKeyboardButton(text="💾 Zaxira nusxa (JSON)", callback_data="exp:backup")],
    ])


@router.message(Command("eksport"))
async def cmd_export(message: Message) -> None:
    await message.answer("Qaysi formatda eksport qilaman?", reply_markup=export_keyboard())


@router.callback_query(F.data.startswith("exp:"))
async def on_export(call: CallbackQuery) -> None:
    kind = call.data.split(":")[1]
    user_id = call.from_user.id
    await call.answer("Tayyorlanyapti... ⏳")

    txs = await db.get_all_transactions(user_id)
    if not txs and kind != "backup":
        await call.message.answer("Hali eksport qiladigan ma'lumot yo'q.")
        return

    if kind == "csv":
        content, fname, caption = exports.to_csv(txs), "hisobchi.csv", "CSV fayl tayyor. 📄"
    elif kind == "xlsx":
        content, fname, caption = exports.to_excel(txs), "hisobchi.xlsx", "Excel fayl tayyor. 📊"
    elif kind == "pdf":
        content = exports.to_pdf(txs, call.from_user.full_name)
        fname, caption = "hisobchi.pdf", "PDF fayl tayyor. 📑"
    else:
        backup_data = {
            "tranzaksiyalar": txs,
            "hamyonlar": await db.list_wallets(user_id),
            "takroriy_tolovlar": await db.list_recurring(user_id, active_only=False),
            "maqsadlar": await db.list_goals(user_id, active_only=False),
        }
        content = exports.to_backup_json(user_id, backup_data)
        fname, caption = "hisobchi_backup.json", "Zaxira nusxa tayyor. 💾"

    await call.message.answer_document(BufferedInputFile(content, filename=fname), caption=caption)


# ==================== ADMIN BUYRUQLARI ====================

@router.message(Command("setprice"))
async def cmd_setprice(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Foydalanish: /setprice 25000")
        return
    price = int(command.args.strip())
    await db.set_price(price)
    await message.answer(f"✅ PRO narxi endi {money(price)} so'm/oy.")


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split()
    if not parts or not parts[0].isdigit():
        await message.answer("Foydalanish: /grant <user_id> [kunlar=30]")
        return
    user_id = int(parts[0])
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else config.PRO_DAYS
    until = await db.grant_pro(user_id, days)
    await message.answer(f"✅ {user_id} ga PRO {days} kunga berildi. Tugash sanasi: {until}.")


@router.message(Command("revoke"))
async def cmd_revoke(message: Message, command: CommandObject) -> None:
    if not is_admin(message.from_user.id):
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Foydalanish: /revoke <user_id>")
        return
    await db.revoke_pro(int(command.args.strip()))
    await message.answer("✅ PRO obuna bekor qilindi.")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    s = await db.stats()
    await message.answer(
        f"👥 Foydalanuvchilar: {s['users']}\n"
        f"👑 PRO obunachilar: {s['pros']}\n"
        f"🧾 Jami yozuvlar: {s['transactions']}"
    )


# ==================== ASOSIY AI OQIMI (erkin matn) ====================

@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message) -> None:
    user_id = message.from_user.id
    await db.ensure_user(user_id, message.from_user.full_name)

    parsed = await ai_client.parse_message(message.text)

    if parsed.get("clarification_needed"):
        await message.answer(parsed["clarification_needed"])
        return

    intent = parsed.get("intent", "unknown")

    if intent == "transaction" and parsed.get("transactions"):
        resolved = []
        for t in parsed["transactions"]:
            wallet_name = t.pop("wallet", None)
            if wallet_name:
                w = await db.get_wallet_by_name(user_id, wallet_name)
                if w:
                    t["wallet_id"] = w["id"]
            resolved.append(t)

        await db.add_transactions(user_id, resolved)
        await message.answer(parsed.get("bot_reply") or "Qayd etildi. ✅")

        for cur in {t["currency"] for t in resolved if t["type"] == "expense"}:
            await maybe_warn_budget(message, user_id, cur)
        return

    if intent == "report_request":
        await handle_report(message, user_id, parsed.get("period"))
        return

    if intent == "advice_request":
        await handle_advice(message, user_id)
        return

    if intent == "chat_query" and parsed.get("question"):
        await handle_chat_query(message, user_id, parsed["question"])
        return

    await message.answer(
        parsed.get("bot_reply")
        or "Xarajat, daromad yoki qarzlaringizni oddiy tilda yozing — men hisobga olaman. 😊"
    )


# ==================== ISHGA TUSHIRISH ====================

async def main() -> None:
    await db.init_db()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # To'lov webhooklari uchun kichik veb-server, bot bilan bitta jarayonda ishlaydi.
    app = webhook_server.create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.WEBHOOK_SERVER_PORT)
    await site.start()
    log.info("Webhook server %s portda ishga tushdi", config.WEBHOOK_SERVER_PORT)
    if config.BASE_URL:
        log.info("Click Prepare URL: %s/click/prepare", config.BASE_URL)
        log.info("Click Complete URL: %s/click/complete", config.BASE_URL)
        log.info("Uzum Webhook URL: %s/uzum/webhook", config.BASE_URL)
    else:
        log.warning("BASE_URL .env da yozilmagan — to'lov tizimlari webhook yubora olmaydi!")

    scheduler.setup(bot)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
