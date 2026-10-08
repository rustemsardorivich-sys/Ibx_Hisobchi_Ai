"""To'lov tizimlari: Click.uz va Uzum Bank.

MUHIM FARQ ikkisi orasida:

  Click.uz — signatura formulasi Click'ning ochiq hujjatida (docs.click.uz) aniq
  yozilgan va shu yerda TO'G'RI qo'llanilgan (MD5, Prepare/Complete). Ishonchli.

  Uzum Bank — to'lov havolasi formati ochiq (quyida ishlatilgan), lekin webhook
  JSON'ining aniq tuzilishini Uzum ochiq internetda e'lon qilmaydi: buni ular
  siz bilan shartnoma va merchant kabinet ochilgandan keyin, SHAXSAN sizga
  yuboradigan hujjatda beradi. Bu yerda TO'G'RI qo'llangan yagona narsa —
  webhook Basic Auth bilan himoyalanishi (USERNAME/PASSWORD). JSON maydon
  nomlarini (order_id, amount va h.k.) birinchi TEST to'lovidan keyin
  webhook_server.py logiga tushgan xom (raw) ma'lumotga qarab moslashtiring —
  kodda aniq shu joy izohlangan.
"""
import base64
import hashlib

import config
# ==================== Karta ====================

def card_payment_details() -> str:
    """Qo'lda tekshiriladigan karta to'lovi uchun ko'rsatmalarni qaytaradi."""
    if not config.CARD_NUMBER or not config.ADMIN_IDS:
        return "Karta orqali to'lov hozircha sozlanmagan. Admin bilan bog'laning."
    details = f"💳 Karta raqami: <code>{config.CARD_NUMBER}</code>"
    if config.CARD_HOLDER_NAME:
        details += f"\n👤 Karta egasi: {config.CARD_HOLDER_NAME}"
    return details


# ==================== CLICK.UZ ====================

def create_click_link(merchant_trans_id: str, amount: int, return_url: str = "") -> str:
    """Foydalanuvchi shu havola orqali Click sahifasida to'laydi."""
    url = (
        "https://my.click.uz/services/pay"
        f"?service_id={config.CLICK_SERVICE_ID}"
        f"&merchant_id={config.CLICK_MERCHANT_ID}"
        f"&amount={amount}"
        f"&transaction_param={merchant_trans_id}"
    )
    if return_url:
        url += f"&return_url={return_url}"
    return url


def _click_sign(parts: list) -> str:
    raw = "".join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()


def verify_click_prepare(data: dict) -> bool:
    """Click 'Prepare' (action=0) so'rovining imzosini tekshiradi."""
    expected = _click_sign([
        data.get("click_trans_id"), data.get("service_id"), config.CLICK_SECRET_KEY,
        data.get("merchant_trans_id"), data.get("amount"), data.get("action"), data.get("sign_time"),
    ])
    return expected == data.get("sign_string")


def verify_click_complete(data: dict) -> bool:
    """Click 'Complete' (action=1) so'rovining imzosini tekshiradi."""
    expected = _click_sign([
        data.get("click_trans_id"), data.get("service_id"), config.CLICK_SECRET_KEY,
        data.get("merchant_trans_id"), data.get("merchant_prepare_id"),
        data.get("amount"), data.get("action"), data.get("sign_time"),
    ])
    return expected == data.get("sign_string")


# ==================== UZUM BANK ====================

def create_uzum_link(merchant_trans_id: str, amount: int, redirect_url: str = "") -> str:
    """Amount so'mda beriladi; Uzum havolasida tiyinda kutiladi (1 so'm = 100 tiyin)."""
    amount_tiyin = int(amount) * 100
    url = (
        "https://www.uzumbank.uz/open-service"
        f"?serviceId={config.UZUM_SERVICE_ID}"
        f"&order_id={merchant_trans_id}"
        f"&amount={amount_tiyin}"
    )
    if redirect_url:
        url += f"&redirectUrl={redirect_url}"
    return url


def verify_uzum_basic_auth(auth_header: str | None) -> bool:
    """Uzum webhook so'rovlari HTTP Basic Auth bilan keladi (USERNAME/PASSWORD)."""
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[len("Basic "):]).decode()
        username, _, password = decoded.partition(":")
    except Exception:
        return False
    return (
        username == config.UZUM_WEBHOOK_USERNAME
        and password == config.UZUM_WEBHOOK_PASSWORD
        and bool(username)
    )


def extract_uzum_fields(payload: dict) -> tuple[str | None, int | None]:
    """Uzum webhook JSON'idan buyurtma ID va summani (so'mda) topishga harakat qiladi.

    DIQQAT: quyidagi kalit nomlari (order_id, orderId, amount...) eng ko'p
    uchraydigan variantlar bo'yicha taxmin qilingan — Uzum sizga bergan
    haqiqiy hujjat/test payload bilan tekshirib, kerak bo'lsa shu funksiyani
    to'g'rilang. webhook_server.py xom payloadni logga yozadi, shu yordam beradi.
    """
    order_id = (
        payload.get("order_id") or payload.get("orderId")
        or payload.get("merchant_trans_id") or payload.get("account_id")
    )
    amount_tiyin = payload.get("amount") or payload.get("amountTiyin")
    amount_som = int(amount_tiyin) // 100 if amount_tiyin is not None else None
    return order_id, amount_som
