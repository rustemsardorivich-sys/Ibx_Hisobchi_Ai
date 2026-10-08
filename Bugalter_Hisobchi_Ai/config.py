"""Sozlamalar. Hamma qiymat .env faylidan o'qiladi (kodga parol yozilmaydi)."""
import os

from dotenv import load_dotenv

load_dotenv()  # .env faylini o'qiydi


def _kerak(nom: str) -> str:
    qiymat = os.getenv(nom, "").strip()
    if not qiymat:
        raise RuntimeError(f".env faylida {nom} yozilmagan. env.example ni .env qilib to'ldiring.")
    return qiymat


BOT_TOKEN = _kerak("BOT_TOKEN")  # @BotFather beradi

# --- AI provayder: "claude" yoki "gemini" ---
AI_PROVIDER = os.getenv("AI_PROVIDER", "claude").strip().lower()

# Anthropic (Claude). https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
PARSER_MODEL = os.getenv("PARSER_MODEL", "claude-haiku-4-5-20251001")
ADVISOR_MODEL = os.getenv("ADVISOR_MODEL", "claude-sonnet-5")

# Google Gemini. https://aistudio.google.com/app/apikey
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_PARSER_MODEL = os.getenv("GEMINI_PARSER_MODEL", "gemini-3.1-flash-lite")
GEMINI_ADVISOR_MODEL = os.getenv("GEMINI_ADVISOR_MODEL", "gemini-3.5-flash")

if AI_PROVIDER == "claude" and not ANTHROPIC_API_KEY:
    raise RuntimeError("AI_PROVIDER=claude, lekin ANTHROPIC_API_KEY yozilmagan (.env).")
if AI_PROVIDER == "gemini" and not GEMINI_API_KEY:
    raise RuntimeError("AI_PROVIDER=gemini, lekin GEMINI_API_KEY yozilmagan (.env).")
if AI_PROVIDER not in ("claude", "gemini"):
    raise RuntimeError("AI_PROVIDER faqat 'claude' yoki 'gemini' bo'lishi kerak (.env).")

# Adminlar Telegram ID lari, vergul bilan: 123456789,987654321
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
OWNER_ID = int(os.getenv("OWNER_ID", "").strip() or "0")
if OWNER_ID:
    ADMIN_IDS.add(OWNER_ID)

DB_PATH = os.getenv("DB_PATH", "hisobchi.db")
PRO_DAYS = 30

# --- Click.uz (Shop API) ---
# Merchant kabinet: https://merchant.click.uz
CLICK_SERVICE_ID = os.getenv("CLICK_SERVICE_ID", "").strip()
CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "").strip()
CLICK_MERCHANT_USER_ID = os.getenv("CLICK_MERCHANT_USER_ID", "").strip()
CLICK_SECRET_KEY = os.getenv("CLICK_SECRET_KEY", "").strip()

# --- Uzum Bank (Open Service / Biller) ---
UZUM_SERVICE_ID = os.getenv("UZUM_SERVICE_ID", "").strip()
UZUM_WEBHOOK_USERNAME = os.getenv("UZUM_WEBHOOK_USERNAME", "").strip()
UZUM_WEBHOOK_PASSWORD = os.getenv("UZUM_WEBHOOK_PASSWORD", "").strip()

# Botning tashqi (internetga ochiq) manzili — Click/Uzum shu manzilga webhook yuboradi.
# Masalan: https://sizning-bot.onrender.com  (lokal test uchun ngrok ishlating)
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

# Render, Railway kabi platformalar portni o'zi PORT nomli o'zgaruvchida beradi —
# shuning uchun avval PORT ni, topilmasa WEBHOOK_SERVER_PORT ni, u ham bo'lmasa 8080 ni olamiz.
WEBHOOK_SERVER_PORT = int(os.getenv("PORT") or os.getenv("WEBHOOK_SERVER_PORT", "8080"))
