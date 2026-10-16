"""AI qatlami: config.AI_PROVIDER ga qarab Claude yoki Gemini chaqiriladi.
Bot kodi (bot.py) faqat shu fayldagi parse_message(), get_advice() va answer_question()
ni chaqiradi — qaysi AI ishlatilayotgani bot.py uchun muhim emas."""
import json
import logging

import config
from prompts import ADVISOR_PROMPT, CHAT_ASSISTANT_PROMPT, PARSER_PROMPT

log = logging.getLogger("ai_client")

# Xabarni tahlil qilib qaytariladigan JSON tuzilmasi (ikkala AI uchun ham umumiy)
PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["transaction", "report_request", "advice_request", "chat_query", "unknown"],
        },
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "expense", "income", "debt_given", "debt_received",
                            "debt_borrowed", "debt_repaid", "credit_payment", "credit_balance",
                        ],
                    },
                    "amount": {"type": "number"},
                    "currency": {"type": "string", "enum": ["UZS", "USD"]},
                    "category": {"type": "string"},
                    "counterparty": {"type": "string"},
                    "wallet": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["type", "amount", "currency", "description"],
            },
        },
        "period": {"type": "string", "enum": ["day", "week", "month", "year"]},
        "question": {"type": "string"},
        "clarification_needed": {"type": "string"},
        "bot_reply": {"type": "string"},
    },
    "required": ["intent", "transactions", "bot_reply"],
}

FALLBACK_PARSE = {
    "intent": "unknown",
    "transactions": [],
    "period": None,
    "question": None,
    "clarification_needed": None,
    "bot_reply": "Kechirasiz, hozir javob bera olmadim. Birozdan keyin qayta urinib ko'ring. 🙏",
}

FALLBACK_TEXT = "Kechirasiz, tahlil tayyorlashda xatolik yuz berdi. Birozdan keyin qayta urinib ko'ring. 🙏"


# ==================== CLAUDE ====================

async def _claude_parse(text: str) -> dict:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=config.PARSER_MODEL,
        max_tokens=1024,
        system=PARSER_PROMPT,
        messages=[{"role": "user", "content": f"<xabar>{text}</xabar>"}],
        tools=[{
            "name": "save_parse",
            "description": "Tahlil natijasini qat'iy JSON ko'rinishida saqlaydi.",
            "input_schema": PARSE_SCHEMA,
        }],
        tool_choice={"type": "tool", "name": "save_parse"},
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Claude tool_use bloki qaytarmadi")


async def _claude_text(system_prompt: str, user_content: str, model: str, max_tokens: int = 1500) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ==================== GEMINI ====================

async def _gemini_parse(text: str) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    resp = await client.aio.models.generate_content(
        model=config.GEMINI_PARSER_MODEL,
        contents=f"<xabar>{text}</xabar>",
        config=types.GenerateContentConfig(
            system_instruction=PARSER_PROMPT,
            response_mime_type="application/json",
            response_schema=PARSE_SCHEMA,
        ),
    )
    return json.loads(resp.text)


async def _gemini_text(system_prompt: str, user_content: str, model: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    resp = await client.aio.models.generate_content(
        model=model,
        contents=user_content,
        config=types.GenerateContentConfig(system_instruction=system_prompt),
    )
    return (resp.text or "").strip()


# ==================== OMMAVIY INTERFEYS ====================

async def parse_message(text: str) -> dict:
    """Foydalanuvchi xabarini tahlil qilib, PARSE_SCHEMA ko'rinishidagi dict qaytaradi.
    Xato bo'lsa, FALLBACK_PARSE qaytariladi (bot bu holatda "xato" xabarini yuboradi)."""
    try:
        if config.AI_PROVIDER == "gemini":
            data = await _gemini_parse(text)
        else:
            data = await _claude_parse(text)
        data.setdefault("transactions", [])
        data.setdefault("period", None)
        data.setdefault("question", None)
        data.setdefault("clarification_needed", None)
        data.setdefault("bot_reply", "")
        return data
    except Exception:
        log.exception("AI parse xatosi")
        return dict(FALLBACK_PARSE)


async def get_advice(payload: dict) -> str:
    """PRO foydalanuvchi uchun shaxsiy buxgalter maslahatini qaytaradi."""
    try:
        content = json.dumps(payload, ensure_ascii=False)
        if config.AI_PROVIDER == "gemini":
            return await _gemini_text(ADVISOR_PROMPT, content, config.GEMINI_ADVISOR_MODEL)
        return await _claude_text(ADVISOR_PROMPT, content, config.ADVISOR_MODEL)
    except Exception:
        log.exception("AI advice xatosi")
        return FALLBACK_TEXT


async def answer_question(payload: dict, question: str) -> str:
    """PRO foydalanuvchi uchun AI yordamchi — foydalanuvchining aniq savoliga
    faqat berilgan ma'lumotlar asosida javob beradi (raqam to'qib chiqarmaydi)."""
    try:
        content = json.dumps({"malumotlar": payload, "savol": question}, ensure_ascii=False)
        if config.AI_PROVIDER == "gemini":
            return await _gemini_text(CHAT_ASSISTANT_PROMPT, content, config.GEMINI_ADVISOR_MODEL)
        return await _claude_text(CHAT_ASSISTANT_PROMPT, content, config.ADVISOR_MODEL)
    except Exception:
        log.exception("AI chat_query xatosi")
        return FALLBACK_TEXT
