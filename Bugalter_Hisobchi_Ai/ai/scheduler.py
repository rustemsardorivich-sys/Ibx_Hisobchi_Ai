"""Fon vazifalari: (1) har kuni takroriy to'lovlarni avtomatik qo'shish,
(2) kunlik/haftalik/oylik hisobotlarni obuna bo'lgan foydalanuvchilarga yuborish."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import db
from formatting import format_summary, money

log = logging.getLogger("scheduler")


def setup(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=db.TZ)

    scheduler.add_job(_run_recurring, "cron", hour=9, minute=0, args=[bot], id="recurring")
    scheduler.add_job(
        _send_reports, "cron", hour=config.REPORT_HOUR, minute=0,
        args=[bot, "daily"], id="daily_report",
    )
    scheduler.add_job(
        _send_reports, "cron", day_of_week="sun", hour=config.REPORT_HOUR, minute=0,
        args=[bot, "weekly"], id="weekly_report",
    )
    scheduler.add_job(
        _send_reports, "cron", day=1, hour=config.REPORT_HOUR, minute=0,
        args=[bot, "monthly"], id="monthly_report",
    )
    scheduler.start()
    log.info("Scheduler ishga tushdi (takroriy to'lovlar 09:00da, hisobotlar %02d:00da)", config.REPORT_HOUR)
    return scheduler


async def _run_recurring(bot) -> None:
    period = db.now().strftime("%Y-%m")
    for r in await db.due_recurring_today():
        await db.add_transactions(r["user_id"], [{
            "type": r["type"], "amount": r["amount"], "currency": r["currency"],
            "category": r["category"], "counterparty": None, "wallet_id": r["wallet_id"],
            "description": f"Takroriy: {r['title']}",
        }])
        await db.mark_recurring_ran(r["id"], period)
        try:
            await bot.send_message(
                r["user_id"],
                f"🔁 Takroriy to'lov avtomatik qayd etildi: <b>{r['title']}</b> — "
                f"{money(r['amount'])} {r['currency']}",
            )
        except Exception:
            log.exception("Takroriy to'lov xabarini yuborib bo'lmadi: %s", r["user_id"])


async def _send_reports(bot, period: str) -> None:
    period_map = {"daily": "day", "weekly": "week", "monthly": "month"}
    titles = {"daily": "📅 Kunlik hisobot", "weekly": "📅 Haftalik hisobot", "monthly": "📅 Oylik hisobot"}
    for user_id in await db.get_report_subscribers(period):
        try:
            data = await db.summary(user_id, period_map[period])
            await bot.send_message(user_id, f"{titles[period]}\n\n{format_summary(data)}")
        except Exception:
            log.exception("Hisobot yuborib bo'lmadi: %s", user_id)
