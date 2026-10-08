"""Click.uz va Uzum Bank'dan keladigan to'lov xabarnomalarini (webhook) qabul qiladi.
Bot bilan bitta jarayonda, bitta asyncio event loop'da ishga tushadi (bot.py ga qarang)."""
import logging

from aiohttp import web

import config
import db
import payments

log = logging.getLogger("webhook")


def create_app(bot) -> web.Application:
    """`bot` — to'lov muvaffaqiyatli bo'lganda foydalanuvchiga xabar yuborish uchun kerak."""
    app = web.Application()

    async def _grant_and_notify(payment: dict) -> None:
        new_until = await db.grant_pro(payment["user_id"], config.PRO_DAYS)
        try:
            await bot.send_message(
                payment["user_id"],
                f"✅ To'lov qabul qilindi! PRO obuna {config.PRO_DAYS} kunga faollashtirildi "
                f"(tugash sanasi: {new_until}). Endi yillik hisobot va shaxsiy moliyaviy "
                f"maslahatdan foydalanishingiz mumkin. 👑",
            )
        except Exception:
            log.exception("Foydalanuvchiga xabar yuborib bo'lmadi: %s", payment["user_id"])

    # ---------------- CLICK ----------------

    async def click_prepare(request: web.Request) -> web.Response:
        data = dict(await request.post())
        log.info("Click prepare: %s", data)

        if not payments.verify_click_prepare(data):
            return web.json_response({"error": -1, "error_note": "SIGN CHECK FAILED"})

        payment = await db.get_payment(data.get("merchant_trans_id", ""))
        if not payment or payment["status"] == "cancelled":
            return web.json_response({"error": -5, "error_note": "Order not found"})
        if int(float(data.get("amount", 0))) != int(payment["amount"]):
            return web.json_response({"error": -2, "error_note": "Incorrect amount"})

        return web.json_response({
            "click_trans_id": data.get("click_trans_id"),
            "merchant_trans_id": data.get("merchant_trans_id"),
            "merchant_prepare_id": data.get("merchant_trans_id"),
            "error": 0,
            "error_note": "Success",
        })

    async def click_complete(request: web.Request) -> web.Response:
        data = dict(await request.post())
        log.info("Click complete: %s", data)

        if not payments.verify_click_complete(data):
            return web.json_response({"error": -1, "error_note": "SIGN CHECK FAILED"})

        merchant_trans_id = data.get("merchant_trans_id", "")
        payment = await db.get_payment(merchant_trans_id)
        if not payment:
            return web.json_response({"error": -5, "error_note": "Order not found"})

        if int(data.get("error", 0)) == 0:
            paid = await db.mark_payment_paid(merchant_trans_id, str(data.get("click_trans_id")))
            if paid:
                await _grant_and_notify(paid)
            return web.json_response({
                "click_trans_id": data.get("click_trans_id"),
                "merchant_trans_id": merchant_trans_id,
                "merchant_confirm_id": merchant_trans_id,
                "error": 0,
                "error_note": "Success",
            })

        await db.mark_payment_cancelled(merchant_trans_id)
        return web.json_response({
            "click_trans_id": data.get("click_trans_id"),
            "merchant_trans_id": merchant_trans_id,
            "merchant_confirm_id": merchant_trans_id,
            "error": 0,
            "error_note": "Success",
        })

    # ---------------- UZUM ----------------

    async def uzum_webhook(request: web.Request) -> web.Response:
        if not payments.verify_uzum_basic_auth(request.headers.get("Authorization")):
            return web.json_response({"error": "unauthorized"}, status=401)

        payload = await request.json()
        log.info("Uzum webhook payload (field nomlarini shu yerdan tekshiring): %s", payload)

        order_id, amount = payments.extract_uzum_fields(payload)
        payment = await db.get_payment(order_id or "")
        if not payment:
            return web.json_response({"status": "error", "message": "order not found"}, status=404)
        if amount is not None and amount != int(payment["amount"]):
            return web.json_response({"status": "error", "message": "amount mismatch"}, status=400)

        paid = await db.mark_payment_paid(order_id, "uzum")
        if paid:
            await _grant_and_notify(paid)
        return web.json_response({"status": "success"})

    # Render (va shunga o'xshash platformalar) portni ochiq deb bilishi uchun,
    # shuningdek tashqi "keep-alive" pingerlar uchun oddiy health-check.
    async def health(request: web.Request) -> web.Response:
        return web.Response(text="Hisobchi AI bot ishlayapti ✅")

    app.router.add_get("/", health)
    app.router.add_post("/click/prepare", click_prepare)
    app.router.add_post("/click/complete", click_complete)
    app.router.add_post("/uzum/webhook", uzum_webhook)
    return app
