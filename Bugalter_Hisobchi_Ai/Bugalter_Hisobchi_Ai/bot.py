"""Hisobchi AI — Telegram bot. Ishga tushirish: python bot.py"""
import asyncio
import logging
from datetime import datetime
from html import escape
from math import isfinite
from typing import Any, cast

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from aiohttp import web

import ai_client
import config
import db
import payments
import webhook_server
from formatting import format_debts, format_summary, money

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

router = Router()


# ==================== YORDAMCHI FUNKSIYALAR ====================

def is_admin(user_id: int) -> bool:
    return user_id == config.OWNER_ID or user_id in config.ADMIN_IDS


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Statistika", callback_data="admin:stats"),
            InlineKeyboardButton(text="💰 PRO narxi", callback_data="admin:price"),
        ],
        [InlineKeyboardButton(text="ℹ️ Admin buyruqlari", callback_data="admin:commands")],
    ])


def pro_offer_keyboard(price: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Click orqali — {money(price)} so'm", callback_data="pay:click")],
        [InlineKeyboardButton(text=f"🟣 Uzum Bank orqali — {money(price)} so'm", callback_data="pay:uzum")],
        [InlineKeyboardButton(text=f"💳 Karta orqali — {money(price)} so'm", callback_data="pay:card")],
    ])


async def send_pro_offer(message: Message, reason: str) -> None:
    price = await db.get_price()
    await message.answer(
        f"{reason}\n\n"
        f"👑 <b>PRO obuna</b> — oyiga {money(price)} so'm.\n"
        f"PRO bilan sizga ochiladi:\n"
        f"  • Yillik to'liq moliyaviy hisobot\n"
        f"  • Shaxsiy sun'iy intellekt buxgalter maslahati\n\n"
        f"To'lov usulini tanlang:",
        reply_markup=pro_offer_keyboard(price),
    )


async def handle_report(message: Message, user_id: int, period: str) -> None:
    period = period or "month"
    if period == "year" and not is_admin(user_id) and not await db.is_pro(user_id):
        await send_pro_offer(message, "📅 Yillik hisobot faqat PRO obunachilar uchun ochiq.")
        return
    data = await db.summary(user_id, period)
    await message.answer(format_summary(data))


async def handle_advice(message: Message, user_id: int) -> None:
    if not is_admin(user_id) and not await db.is_pro(user_id):
        await send_pro_offer(message, "🧠 Shaxsiy AI buxgalter maslahati faqat PRO obunachilar uchun ochiq.")
        return
    await message.answer("Tahlil qilyapman, biroz kuting... ⏳")
    payload = {
        "shu_oy": (await db.summary(user_id, "month"))["valyutalar"],
        "oxirgi_12_oy": await db.monthly(user_id),
        "qarzlar_va_kredit": await db.debts(user_id),
    }
    advice = await ai_client.get_advice(payload)
    await message.answer(advice)


# ==================== ODDIY BUYRUQLAR ====================

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await db.ensure_user(message.from_user.id, message.from_user.full_name)
    admin_command = "\n/admin — admin panel" if is_admin(message.from_user.id) else ""
    await message.answer(
        "Salom! 👋 Men <b>Hisobchi AI</b>man — moliyaviy yordamchingiz.\n\n"
        "Menga oddiy tilda yozing, masalan:\n"
        "  • «Palov uchun 40 ming ketdi»\n"
        "  • «Javohirga 300 ming qarz berdim»\n"
        "  • «Maoshim 5 mln keldi»\n"
        "  • «100 ming emas, 90 ming to'lov qilganman» (eski yozuvni tuzatish)\n\n"
        "Men avtomatik hisobga olib boraman. Buyruqlar:\n"
        "/kunlik /haftalik /oylik /yillik — hisobotlar\n"
        "/qarzlar — kim kimga qarzdor\n"
        "/maslahat — shaxsiy AI buxgalter (PRO; admin va egaga bepul)\n"
        "/bekor — oxirgi yozuvni o'chirish\n"
        "/pro — PRO obuna haqida"
        f"{admin_command}"
    )


@router.message(Command("pro"))
async def cmd_pro(message: Message) -> None:
    user_id = message.from_user.id
    await db.ensure_user(user_id, message.from_user.full_name)
    if is_admin(user_id):
        await message.answer("👑 Admin va bot egasi uchun PRO funksiyalari obunasiz ochiq.")
        return
    until = await db.get_pro_until(user_id)
    if until and await db.is_pro(user_id):
        await message.answer(f"👑 Sizda PRO obuna faol. Tugash sanasi: {until}.")
        return
    await send_pro_offer(message, "PRO obunangiz hali faollashtirilmagan.")


@router.callback_query(F.data.in_({"pay:click", "pay:uzum", "pay:card"}))
async def on_pay(call: CallbackQuery) -> None:
    provider = call.data.split(":")[1]
    user_id = call.from_user.id
    price = await db.get_price()
    if provider == "card" and (not config.CARD_NUMBER or not config.ADMIN_IDS):
        await call.message.answer(payments.card_payment_details())
        await call.answer()
        return
    merchant_trans_id = await db.create_payment(user_id, provider, price)

    if provider == "card":
        await call.message.answer(
            f"{payments.card_payment_details()}\n\n"
            f"To'lov summasi: <b>{money(price)} so'm</b>. Pul o'tkazgach, chek rasmini "
            f"izohida to'langan summani yozib yuboring (masalan: <code>{price}</code>). "
            "Admin tekshirganidan keyin PRO faollashadi."
        )
        await call.answer()
        return

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


@router.message(F.photo)
async def on_card_receipt(message: Message) -> None:
    user_id = message.from_user.id
    payment = await db.get_latest_card_payment(user_id)
    if not payment:
        await message.answer("Chek yuborishdan oldin /pro bo'limidan karta to'lovini tanlang.")
        return
    caption = (message.caption or "").strip()
    amount_text = "".join(char for char in caption if char.isdigit())
    if not amount_text:
        await message.answer("Chek rasmini izohida to'langan summani so'mda yozib yuboring.")
        return
    amount = int(amount_text)
    receipt_file_id = message.photo[-1].file_id
    submitted = await db.submit_card_receipt(payment["merchant_trans_id"], receipt_file_id, amount)
    if not submitted:
        await message.answer("Bu to'lov uchun chek allaqachon yuborilgan yoki bekor qilingan.")
        return

    user = message.from_user
    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Tasdiqlash", callback_data=f"card:approve:{payment['merchant_trans_id']}"
        ),
        InlineKeyboardButton(
            text="❌ Rad etish", callback_data=f"card:reject:{payment['merchant_trans_id']}"
        ),
    ]])
    admin_text = (
        "🧾 <b>Karta to'lovi tekshiruvi</b>\n"
        f"Foydalanuvchi: {escape(user.full_name)} (<code>{user.id}</code>)\n"
        f"To'lov summasi: <b>{money(amount)} so'm</b>\n"
        f"Kutilgan summa: {money(payment['amount'])} so'm\n"
        f"To'lov ID: <code>{payment['merchant_trans_id']}</code>"
    )
    for admin_id in config.ADMIN_IDS:
        await message.bot.send_photo(
            admin_id, receipt_file_id, caption=admin_text, reply_markup=admin_keyboard
        )
    await message.answer("Chek adminga yuborildi. Tekshiruvdan so'ng natijani xabar qilaman. ✅")


@router.callback_query(F.data.startswith("card:"))
async def on_card_payment_review(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Bu amal faqat admin uchun.", show_alert=True)
        return
    _, action, merchant_trans_id = call.data.split(":", 2)
    payment = await db.get_payment(merchant_trans_id)
    if not payment or payment["status"] != "review":
        await call.answer("Bu to'lov allaqachon ko'rib chiqilgan.", show_alert=True)
        return

    if action == "approve":
        paid = await db.mark_payment_paid(merchant_trans_id, f"manual:{call.from_user.id}")
        if not paid:
            await call.answer("To'lov allaqachon tasdiqlangan.", show_alert=True)
            return
        until = await db.grant_pro(payment["user_id"], config.PRO_DAYS)
        await call.bot.send_message(
            payment["user_id"],
            f"✅ To'lov qabul qilindi! PRO obuna {config.PRO_DAYS} kunga faollashtirildi "
            f"(tugash sanasi: {until}). 👑",
        )
        await call.message.edit_caption(
            (call.message.caption or "") + "\n\n✅ Admin tasdiqladi.", reply_markup=None
        )
        await call.answer("To'lov tasdiqlandi.")
        return

    rejected = await db.reject_card_payment(merchant_trans_id)
    if rejected:
        await call.bot.send_message(
            payment["user_id"],
            "❌ Karta to'lovi tasdiqlanmadi. Iltimos, chek va summani tekshirib, qayta yuboring.",
        )
        await call.message.edit_caption(
            (call.message.caption or "") + "\n\n❌ Admin rad etdi.", reply_markup=None
        )
        await call.answer("To'lov rad etildi.")
    else:
        await call.answer("Bu to'lov allaqachon ko'rib chiqilgan.", show_alert=True)


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


# ==================== ADMIN BUYRUQLARI ====================

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("Bu bo'lim faqat admin va bot egasi uchun.")
        return
    await message.answer(
        "🛠 <b>Admin panel</b>\nKerakli bo'limni tanlang yoki quyidagi buyruqlardan foydalaning.",
        reply_markup=admin_panel_keyboard(),
    )


@router.callback_query(F.data.startswith("admin:"))
async def on_admin_panel_action(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Bu bo'lim faqat admin uchun.", show_alert=True)
        return

    action = call.data.split(":", 1)[1]
    if action == "stats":
        stats = await db.stats()
        text = (
            "📊 <b>Bot statistikasi</b>\n"
            f"👥 Foydalanuvchilar: {stats['users']}\n"
            f"👑 Faol PRO obunalar: {stats['pros']}\n"
            f"🧾 Jami yozuvlar: {stats['transactions']}"
        )
    elif action == "price":
        text = f"💰 Joriy PRO narxi: <b>{money(await db.get_price())} so'm/oy</b>\n/setprice <summa>"
    elif action == "commands":
        text = (
            "ℹ️ <b>Admin buyruqlari</b>\n"
            "/setprice 25000 — PRO narxini o'zgartirish\n"
            "/grant &lt;user_id&gt; [kunlar] — PRO berish\n"
            "/revoke &lt;user_id&gt; — PRO'ni bekor qilish\n"
            "/stats — statistika\n"
            "/admin — panelga qaytish"
        )
    else:
        text = (
            "🛠 <b>Admin panel</b>\n"
            "Kerakli bo'limni tanlang yoki quyidagi buyruqlardan foydalaning."
        )

    if isinstance(call.message, Message):
        await call.message.edit_text(text, reply_markup=admin_panel_keyboard())
    await call.answer()


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

async def handle_transaction_correction(
    message: Message, user_id: int, correction: dict[str, Any]
) -> None:
    old_amount = correction.get("old_amount")
    new_amount = correction.get("new_amount")
    if (
        not isinstance(old_amount, (int, float))
        or isinstance(old_amount, bool)
        or not isinstance(new_amount, (int, float))
        or isinstance(new_amount, bool)
    ):
        await message.answer("Qaysi eski summani qaysi yangi summaga almashtirish kerakligini yozing.")
        return
    if not isfinite(old_amount) or not isfinite(new_amount) or old_amount <= 0 or new_amount <= 0:
        await message.answer("Eski va yangi summa noldan katta bo'lishi kerak.")
        return

    date_value = correction.get("date")
    if date_value is not None and date_value != "":
        if not isinstance(date_value, str):
            await message.answer("Sanani YYYY-MM-DD shaklida yozing.")
            return
        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except (TypeError, ValueError):
            await message.answer("Sanani aniqlashtiring: YYYY-MM-DD shaklida yozing.")
            return
        date = date_value
    else:
        date = None

    matches = await db.find_transactions_for_correction(user_id, old_amount, date)
    if not matches:
        date_text = f" {date} sanasida" if date else ""
        await message.answer(
            f"{date_text} {money(old_amount)} so'm summali mos yozuv topilmadi. "
            "Sanani yoki eski summani aniqlashtirib qayta yozing."
        )
        return

    if len(matches) == 1:
        transaction = matches[0]
        updated = await db.update_transaction_amount(
            user_id, transaction["id"], old_amount, new_amount
        )
        if not updated:
            await message.answer("Bu yozuv o'zgargan ekan. Tuzatishni qayta yuboring.")
            return
        await message.answer(
            f"✅ {transaction['created_at'][:10]} dagi "
            f"{escape(transaction['description'] or 'yozuv')} summasi "
            f"{money(old_amount)} emas, {money(new_amount)} so'm qilib tuzatildi."
        )
        return

    buttons: list[list[InlineKeyboardButton]] = []
    for transaction in matches:
        label = (
            f"{transaction['created_at'][:10]} — "
            f"{(transaction['description'] or transaction['type'])[:28]} "
            f"({money(transaction['amount'])} {transaction['currency']})"
        )
        old_text = format(transaction["amount"], "g")
        new_text = format(new_amount, "g")
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"fix:{transaction['id']}:{old_text}:{new_text}",
        )])
    await message.answer(
        f"{money(old_amount)} so'mlik bir nechta yozuv topildi. Qaysi birini "
        f"{money(new_amount)} so'mga tuzatay?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("fix:"))
async def on_transaction_correction_choice(call: CallbackQuery) -> None:
    data = call.data
    if not isinstance(data, str):
        await call.answer("Tuzatish ma'lumotini o'qib bo'lmadi.", show_alert=True)
        return
    try:
        _, transaction_id_text, old_text, new_text = data.split(":", 3)
        transaction_id = int(transaction_id_text)
        old_amount = float(old_text)
        new_amount = float(new_text)
    except (AttributeError, TypeError, ValueError):
        await call.answer("Tuzatish ma'lumotini o'qib bo'lmadi.", show_alert=True)
        return
    if not isfinite(old_amount) or not isfinite(new_amount) or new_amount <= 0:
        await call.answer("Yangi summa noto'g'ri.", show_alert=True)
        return

    updated = await db.update_transaction_amount(
        call.from_user.id, transaction_id, old_amount, new_amount
    )
    if not updated:
        await call.answer("Yozuv topilmadi yoki u allaqachon o'zgartirilgan.", show_alert=True)
        return
    if isinstance(call.message, Message):
        await call.message.edit_text(
            f"✅ Yozuv summasi {money(old_amount)} emas, {money(new_amount)} so'm qilib tuzatildi."
        )
    await call.answer("Yozuv tuzatildi.")


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
        await db.add_transactions(user_id, parsed["transactions"])
        await message.answer(parsed.get("bot_reply") or "Qayd etildi. ✅")
        return

    if intent == "transaction_correction":
        correction = parsed.get("correction")
        if not isinstance(correction, dict):
            await message.answer("Qaysi yozuvdagi summani tuzatish kerakligini aniqlashtirib yozing.")
            return
        correction_data = cast(dict[str, Any], correction)
        await handle_transaction_correction(message, user_id, correction_data)
        return

    if intent == "report_request":
        await handle_report(message, user_id, parsed.get("period"))
        return

    if intent == "advice_request":
        await handle_advice(message, user_id)
        return

    await message.answer(
        parsed.get("bot_reply")
        or "Xarajat, daromad yoki qarzlaringizni oddiy tilda yozing — men hisobga olaman. 😊"
    )


# ==================== ISHGA TUSHIRISH ====================

async def main() -> None:
    await db.init_db()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
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

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
