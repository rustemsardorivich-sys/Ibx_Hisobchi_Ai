"""Click va Uzum Bank to'lov havolalari va webhook signature tekshirishlari.

Ushbu modul PDF/eksport/PRO to'lovlar uchun minimal, ishlaydigan va xavfsizlikka
asoslangan API kontekstini ta'minlaydi. Real merchant konfiguratsiyasi uchun
.env faylida `CLICK_*` va `UZUM_*` maydonlari to'ldirilishi kerak.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from urllib.parse import urlencode

import config


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def create_click_link(merchant_trans_id: str, amount: int, *, currency: str = "8600") -> str:
    """Click.uz uchun to'lov havolasi yaratuvchi.

    Real Click Shop API ko'proq parametrlar bilan ishlaydi, lekin bu yerda bot
    ishlashi uchun yetarli minimal URL shakli ta'minlanadi.
    """
    if not config.CLICK_SERVICE_ID or not config.CLICK_MERCHANT_ID:
        raise RuntimeError("Click to'lovlari ishlashi uchun CLICK_SERVICE_ID va CLICK_MERCHANT_ID kerak.")

    params = {
        "service_id": config.CLICK_SERVICE_ID,
        "merchant_id": config.CLICK_MERCHANT_ID,
        "amount": int(amount),
        "merchant_trans_id": merchant_trans_id,
        "currency": currency,
        "return_url": f"{config.BASE_URL}/",
    }
    query = urlencode(params)
    return f"https://checkout.click.uz/{config.CLICK_SERVICE_ID}?{query}"


def create_uzum_link(merchant_trans_id: str, amount: int) -> str:
    """Uzum Bank uchun minimal to'lov havolasi."""
    if not config.UZUM_SERVICE_ID:
        raise RuntimeError("Uzum to'lovlari ishlashi uchun UZUM_SERVICE_ID kerak.")
    params = {
        "service_id": config.UZUM_SERVICE_ID,
        "merchant_trans_id": merchant_trans_id,
        "amount": int(amount),
        "currency": "UZS",
        "return_url": f"{config.BASE_URL}/",
    }
    query = urlencode(params)
    return f"https://pay.uzum.uz/checkout?{query}"


def verify_click_signature(payload: dict[str, Any], signature: str | None) -> bool:
    if not config.CLICK_SECRET_KEY:
        return True
    if not signature:
        return False
    raw = "".join(f"{key}{payload.get(key, '')}" for key in sorted(payload.keys()))
    digest = hmac.new(config.CLICK_SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.lower())


def verify_uzum_signature(payload: dict[str, Any], secret: str | None) -> bool:
    if not secret:
        return True
    if not payload:
        return False
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    digest = hmac.new(secret.encode(), encoded, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, (payload.get("signature") or "").lower())


def extract_click_fields(data: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "merchant_trans_id": ["merchant_trans_id", "merchantTransId", "transaction_id", "id"],
        "amount": ["amount", "amount_usd", "total_amount"],
        "status": ["status", "state"],
    }
    out: dict[str, Any] = {}
    for target, aliases in keys.items():
        for alias in aliases:
            if alias in data:
                out[target] = data[alias]
                break
    return out


def extract_uzum_fields(data: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "merchant_trans_id": ["order_id", "merchant_trans_id", "merchantTransId", "orderId", "transaction_id"],
        "amount": ["amount", "total_amount", "sum", "pay_amount"],
        "status": ["status", "state", "result"],
    }
    out: dict[str, Any] = {}
    for target, aliases in keys.items():
        for alias in aliases:
            if alias in data:
                out[target] = data[alias]
                break
    return out
