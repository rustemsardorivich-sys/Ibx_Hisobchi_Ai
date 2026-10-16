"""Webhook server for Click and Uzum payment notifications.

Bot ichida ishlash uchun minimal HTTP server taqdim etadi va kelgan webhook
xabarnomalarni `db.mark_payment_paid()` bilan bog'laydi. Bu modul real to'lov
serverlari bilan ishlashga tayyor, ammo ko'proq botning ishlashini ta'minlashga
qaratilgan.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web

import config
import db
import payments

log = logging.getLogger("webhook_server")


async def _read_json(request: web.Request) -> dict[str, Any]:
    if request.content_type == "application/json":
        try:
            text = await request.text()
            if not text:
                return {}
            return json.loads(text)
        except (TypeError, ValueError):
            return {}

    try:
        return dict(await request.post())
    except Exception:
        return {}


async def _complete_payment(merchant_trans_id: str, provider: str, provider_trans_id: str | None = None) -> web.Response:
    if not merchant_trans_id:
        return web.json_response({"status": "error", "message": "merchant_trans_id topilmadi"}, status=400)
    payment = await db.mark_payment_paid(merchant_trans_id, provider_trans_id or f"{provider}-webhook")
    if payment is None:
        return web.json_response({"status": "ok", "message": "already_processed"})
    return web.json_response({"status": "ok", "merchant_trans_id": merchant_trans_id})


async def click_prepare(request: web.Request) -> web.Response:
    payload = await _read_json(request)
    fields = payments.extract_click_fields(payload)
    merchant_trans_id = fields.get("merchant_trans_id")
    if not merchant_trans_id:
        return web.json_response({"status": "error", "message": "merchant_trans_id missing"}, status=400)
    return web.json_response({"status": "ok", "merchant_trans_id": merchant_trans_id})


async def click_complete(request: web.Request) -> web.Response:
    payload = await _read_json(request)
    fields = payments.extract_click_fields(payload)
    merchant_trans_id = fields.get("merchant_trans_id")
    if config.CLICK_SECRET_KEY:
        signature = request.headers.get("X-Signature") or payload.get("signature")
        if not payments.verify_click_signature(payload, signature):
            return web.json_response({"status": "error", "message": "invalid signature"}, status=401)
    return await _complete_payment(str(merchant_trans_id), "click", str(fields.get("transaction_id") or fields.get("id") or "click-complete"))


async def uzum_webhook(request: web.Request) -> web.Response:
    payload = await _read_json(request)
    if not payload:
        return web.json_response({"status": "error", "message": "empty payload"}, status=400)
    fields = payments.extract_uzum_fields(payload)
    merchant_trans_id = fields.get("merchant_trans_id")
    if config.UZUM_WEBHOOK_PASSWORD:
        # Basic auth is handled by the reverse proxy or the platform before this
        # request reaches the app; here we only validate payload if a secret was set.
        secret = config.UZUM_WEBHOOK_PASSWORD
        if not payments.verify_uzum_signature(payload, secret):
            return web.json_response({"status": "error", "message": "invalid signature"}, status=401)
    return await _complete_payment(str(merchant_trans_id), "uzum", str(fields.get("merchant_trans_id") or fields.get("order_id") or "uzum-webhook"))


async def health(request: web.Request) -> web.Response:
    return web.Response(text="Hisobchi AI bot ishlayapti ✅")


def create_app(bot) -> web.Application:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_post("/click/prepare", click_prepare)
    app.router.add_post("/click/complete", click_complete)
    app.router.add_post("/uzum/webhook", uzum_webhook)
    return app
