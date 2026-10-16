"""Ma'lumotlar bazasi (SQLite). Barcha vaqtlar Toshkent vaqti bilan saqlanadi."""
import time
from collections import defaultdict
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

import aiosqlite

import config

TZ = ZoneInfo("Asia/Tashkent")
FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_PRICE = 20000  # so'm, birinchi ishga tushganda; keyin admin /setprice bilan o'zgartiradi


class TransactionRecord(TypedDict):
    id: int
    type: str
    amount: float
    currency: str
    category: str | None
    counterparty: str | None
    description: str | None
    created_at: str


def now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def now_str() -> str:
    return now().strftime(FMT)


async def init_db() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                name        TEXT,
                pro_until   TEXT,
                created_at  TEXT
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                type         TEXT NOT NULL,
                amount       REAL NOT NULL,
                currency     TEXT NOT NULL,
                category     TEXT,
                counterparty TEXT,
                description  TEXT,
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, created_at);
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS payments (
                merchant_trans_id  TEXT PRIMARY KEY,
                user_id            INTEGER NOT NULL,
                provider           TEXT NOT NULL,          -- 'click' | 'uzum'
                amount             INTEGER NOT NULL,       -- so'mda
                status             TEXT NOT NULL DEFAULT 'pending',  -- pending | review | paid | cancelled | rejected
                provider_trans_id  TEXT,
                receipt_file_id    TEXT,
                submitted_amount   INTEGER,
                created_at         TEXT NOT NULL,
                paid_at            TEXT
            );
            """
        )
        async with db.execute("PRAGMA table_info(payments)") as cur:
            payment_columns = {row[1] for row in await cur.fetchall()}
        if "receipt_file_id" not in payment_columns:
            await db.execute("ALTER TABLE payments ADD COLUMN receipt_file_id TEXT")
        if "submitted_amount" not in payment_columns:
            await db.execute("ALTER TABLE payments ADD COLUMN submitted_amount INTEGER")
        await db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('pro_price', ?)",
            (str(DEFAULT_PRICE),),
        )
        await db.commit()


# ---------- Foydalanuvchilar va PRO ----------

async def ensure_user(user_id: int, name: str) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(telegram_id, name, created_at) VALUES(?,?,?)",
            (user_id, name, now_str()),
        )
        await db.commit()


async def get_pro_until(user_id: int) -> str | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "SELECT pro_until FROM users WHERE telegram_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] else None


async def is_pro(user_id: int) -> bool:
    until = await get_pro_until(user_id)
    return bool(until and until > now_str())


async def grant_pro(user_id: int, days: int) -> str:
    """PRO ni `days` kunga uzaytiradi. Agar hali faol bo'lsa, oxiridan boshlab qo'shadi."""
    current = await get_pro_until(user_id)
    start = now()
    if current and current > now_str():
        start = datetime.strptime(current, FMT)
    new_until = (start + timedelta(days=days)).strftime(FMT)
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """INSERT INTO users(telegram_id, pro_until, created_at) VALUES(?,?,?)
               ON CONFLICT(telegram_id) DO UPDATE SET pro_until=excluded.pro_until""",
            (user_id, new_until, now_str()),
        )
        await db.commit()
    return new_until


async def revoke_pro(user_id: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("UPDATE users SET pro_until=NULL WHERE telegram_id=?", (user_id,))
        await db.commit()


async def get_price() -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key='pro_price'") as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else DEFAULT_PRICE


async def set_price(price: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings(key, value) VALUES('pro_price', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(price),),
        )
        await db.commit()


# ---------- To'lovlar (Click / Uzum) ----------

async def create_payment(user_id: int, provider: str, amount: int) -> str:
    """Yangi to'lov yozuvi yaratadi va unikal merchant_trans_id qaytaradi."""
    merchant_trans_id = f"{user_id}-{int(time.time() * 1000)}"
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """INSERT INTO payments(merchant_trans_id, user_id, provider, amount, status, created_at)
               VALUES (?,?,?,?, 'pending', ?)""",
            (merchant_trans_id, user_id, provider, amount, now_str()),
        )
        await db.commit()
    return merchant_trans_id


async def get_latest_card_payment(user_id: int) -> dict | None:
    """Foydalanuvchining chek kutilayotgan eng yangi karta to'lovini topadi."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM payments WHERE user_id=? AND provider='card' AND status='pending' "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def submit_card_receipt(merchant_trans_id: str, receipt_file_id: str, amount: int) -> bool:
    """Chekni ko'rib chiqish navbatiga o'tkazadi."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "UPDATE payments SET status='review', receipt_file_id=?, submitted_amount=? "
            "WHERE merchant_trans_id=? AND provider='card' AND status='pending'",
            (receipt_file_id, amount, merchant_trans_id),
        )
        await db.commit()
    return cur.rowcount > 0


async def reject_card_payment(merchant_trans_id: str) -> dict | None:
    """Ko'rib chiqilayotgan karta to'lovini rad etadi."""
    payment = await get_payment(merchant_trans_id)
    if not payment or payment["provider"] != "card" or payment["status"] != "review":
        return None
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "UPDATE payments SET status='rejected' WHERE merchant_trans_id=? AND status='review'",
            (merchant_trans_id,),
        )
        await db.commit()
    return payment if cur.rowcount else None


async def get_payment(merchant_trans_id: str) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM payments WHERE merchant_trans_id=?", (merchant_trans_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def mark_payment_paid(merchant_trans_id: str, provider_trans_id: str | None = None) -> dict | None:
    """To'lovni 'paid' qiladi. Agar u ALLAQACHON paid bo'lsa, None qaytaradi
    (bu — Click/Uzum bir xil webhookni qayta yuborganda PRO ni ikki marta
    berib yubormaslik uchun himoya)."""
    payment = await get_payment(merchant_trans_id)
    if not payment or payment["status"] == "paid":
        return None
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "UPDATE payments SET status='paid', provider_trans_id=?, paid_at=? "
            "WHERE merchant_trans_id=? AND status!='paid'",
            (provider_trans_id, now_str(), merchant_trans_id),
        )
        await db.commit()
    if not cur.rowcount:
        return None
    payment["status"] = "paid"
    return payment


async def mark_payment_cancelled(merchant_trans_id: str) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET status='cancelled' WHERE merchant_trans_id=? AND status='pending'",
            (merchant_trans_id,),
        )
        await db.commit()


async def stats() -> dict[str, int]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            users = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE pro_until > ?", (now_str(),)
        ) as c:
            pros = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM transactions") as c:
            txs = (await c.fetchone())[0]
    return {"users": users, "pros": pros, "transactions": txs}


# ---------- Operatsiyalar ----------

async def add_transactions(user_id: int, items: list[dict[str, Any]]) -> None:
    transaction_types = {
        "expense", "income", "debt_given", "debt_received", "debt_borrowed",
        "debt_repaid", "credit_payment", "credit_balance",
    }
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("AI returned an invalid transaction")
        amount = item.get("amount")
        transaction_type = item.get("type")
        currency = item.get("currency")
        if (
            not isinstance(transaction_type, str)
            or transaction_type not in transaction_types
            or not isinstance(currency, str)
            or currency not in {"UZS", "USD"}
            or not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or not isfinite(amount)
            or amount <= 0
            or not isinstance(item.get("description"), str)
        ):
            raise ValueError("AI returned an invalid transaction")
        for field in ("category", "counterparty"):
            if item.get(field) is not None and not isinstance(item[field], str):
                raise ValueError(f"AI returned an invalid transaction {field}")

    created = now_str()
    rows = [
        (
            user_id, t["type"], t["amount"], t["currency"],
            t.get("category"), t.get("counterparty"), t.get("description"), created,
        )
        for t in items
    ]
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executemany(
            """INSERT INTO transactions
               (user_id, type, amount, currency, category, counterparty, description, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
        await db.commit()


async def find_transactions_for_correction(
    user_id: int, old_amount: int | float, date: str | None = None
) -> list[TransactionRecord]:
    """Foydalanuvchining eski summa (va berilgan sana) bo'yicha yozuvlarini topadi."""
    query = (
        "SELECT id, type, amount, currency, category, counterparty, description, created_at "
        "FROM transactions WHERE user_id=? AND amount=?"
    )
    parameters: list[int | float | str] = [user_id, old_amount]
    if date:
        query += " AND substr(created_at, 1, 10)=?"
        parameters.append(date)
    query += " ORDER BY id DESC LIMIT 10"

    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, parameters) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": int(row["id"]),
            "type": str(row["type"]),
            "amount": float(row["amount"]),
            "currency": str(row["currency"]),
            "category": str(row["category"]) if row["category"] is not None else None,
            "counterparty": str(row["counterparty"]) if row["counterparty"] is not None else None,
            "description": str(row["description"]) if row["description"] is not None else None,
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


async def find_transactions(
    user_id: int, filters: dict[str, Any]
) -> list[TransactionRecord]:
    """Find a user's transactions using only explicitly supported filters."""
    clauses = ["user_id=?"]
    parameters: list[int | float | str] = [user_id]
    if "amount" in filters:
        clauses.append("amount=?")
        parameters.append(filters["amount"])
    if "date" in filters:
        clauses.append("substr(created_at, 1, 10)=?")
        parameters.append(filters["date"])
    for field in ("type", "currency"):
        if field in filters:
            clauses.append(f"{field}=?")
            parameters.append(filters[field])
    for field in ("description", "category", "counterparty"):
        if field in filters:
            value = str(filters[field])
            escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append(f"COALESCE({field}, '') LIKE ? ESCAPE '\\' COLLATE NOCASE")
            parameters.append(f"%{escaped}%")
    limit = 1 if filters.get("latest") is True else 20

    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, type, amount, currency, category, counterparty, description, created_at "
            f"FROM transactions WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT {limit}",
            parameters,
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": int(row["id"]),
            "type": str(row["type"]),
            "amount": float(row["amount"]),
            "currency": str(row["currency"]),
            "category": str(row["category"]) if row["category"] is not None else None,
            "counterparty": str(row["counterparty"]) if row["counterparty"] is not None else None,
            "description": str(row["description"]) if row["description"] is not None else None,
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


async def update_transaction_fields(
    user_id: int,
    transaction: TransactionRecord,
    changes: dict[str, Any],
) -> bool:
    """Atomically update selected fields only if the displayed record is unchanged."""
    columns = {
        "amount": "amount",
        "type": "type",
        "currency": "currency",
        "category": "category",
        "counterparty": "counterparty",
        "description": "description",
    }
    updates: dict[str, object] = {
        columns[field]: value for field, value in changes.items() if field in columns
    }
    if "amount" in updates:
        amount = updates["amount"]
        if (
            not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or not isfinite(amount)
            or amount <= 0
        ):
            return False
    if "date" in changes:
        try:
            datetime.strptime(changes["date"], "%Y-%m-%d")
        except (TypeError, ValueError):
            return False
        updates["created_at"] = f"{changes['date']}{transaction['created_at'][10:]}"
    if not updates:
        return False

    set_clause = ", ".join(f"{column}=?" for column in updates)
    values = list(updates.values())
    values.extend([
        transaction["id"], user_id, transaction["amount"], transaction["type"],
        transaction["currency"], transaction["category"], transaction["counterparty"],
        transaction["description"], transaction["created_at"],
    ])
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE transactions SET {set_clause} "
            "WHERE id=? AND user_id=? AND amount=? AND type=? AND currency=? "
            "AND category IS ? AND counterparty IS ? AND description IS ? AND created_at=?",
            values,
        )
        await db.commit()
    return cur.rowcount > 0


async def delete_transaction(user_id: int, transaction: TransactionRecord) -> bool:
    """Delete a user's selected transaction only if its contents are unchanged."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM transactions WHERE id=? AND user_id=? AND amount=? AND type=? "
            "AND currency=? AND category IS ? AND counterparty IS ? AND description IS ? "
            "AND created_at=?",
            (
                transaction["id"], user_id, transaction["amount"], transaction["type"],
                transaction["currency"], transaction["category"], transaction["counterparty"],
                transaction["description"], transaction["created_at"],
            ),
        )
        await db.commit()
    return cur.rowcount > 0


async def update_transaction_amount(
    user_id: int, transaction_id: int, old_amount: int | float, new_amount: int | float
) -> bool:
    """Faqat egasining, summasi kutilgan qiymatda turgan yozuvini atomar tahrirlaydi."""
    if not isfinite(new_amount) or new_amount <= 0:
        return False
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "UPDATE transactions SET amount=? WHERE id=? AND user_id=? AND amount=?",
            (new_amount, transaction_id, user_id, old_amount),
        )
        await db.commit()
    return cur.rowcount > 0


async def delete_last(user_id: int) -> dict[str, int | float | str | None] | None:
    """Oxirgi yozuvni o'chiradi (AI xato tushungan bo'lsa, /bekor uchun)."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "SELECT id, type, amount, currency, description FROM transactions "
            "WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        await db.execute("DELETE FROM transactions WHERE id=?", (row[0],))
        await db.commit()
    return {"type": row[1], "amount": row[2], "currency": row[3], "description": row[4]}


# ---------- Hisobotlar (summalarni AI emas, mana shu kod hisoblaydi) ----------

def period_start(period: str) -> str:
    n = now()
    if period == "day":
        s = n.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        s = n - timedelta(days=7)
    elif period == "month":
        s = n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:  # "year" = oxirgi 365 kun
        s = n - timedelta(days=365)
    return s.strftime(FMT)


async def summary(user_id: int, period: str) -> dict:
    since = period_start(period)
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            """SELECT type, currency, COALESCE(category, 'Boshqa'), SUM(amount)
               FROM transactions
               WHERE user_id=? AND created_at>=? AND type IN ('income','expense','credit_payment')
               GROUP BY type, currency, 3""",
            (user_id, since),
        ) as cur:
            rows = await cur.fetchall()

    result: dict = {}
    for ttype, cur_code, cat, total in rows:
        r = result.setdefault(
            cur_code,
            {"daromad": 0.0, "xarajat": 0.0, "kredit_tolovlari": 0.0, "xarajat_kategoriyalari": {}},
        )
        if ttype == "income":
            r["daromad"] += total
        elif ttype == "expense":
            r["xarajat"] += total
            r["xarajat_kategoriyalari"][cat] = r["xarajat_kategoriyalari"].get(cat, 0) + total
        else:
            r["kredit_tolovlari"] += total

    for r in result.values():
        r["qolgan"] = r["daromad"] - r["xarajat"] - r["kredit_tolovlari"]
        r["xarajat_kategoriyalari"] = {
            k: round(v, 2)
            for k, v in sorted(r["xarajat_kategoriyalari"].items(), key=lambda kv: -kv[1])
        }
        for k in ("daromad", "xarajat", "kredit_tolovlari", "qolgan"):
            r[k] = round(r[k], 2)
    return {"davr": period, "boshlanish": since, "valyutalar": result}


async def monthly(user_id: int) -> dict:
    """Oxirgi 12 oy bo'yicha oylik daromad/xarajat/kredit to'lovlari."""
    since = period_start("year")
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            """SELECT substr(created_at, 1, 7), type, currency, SUM(amount)
               FROM transactions
               WHERE user_id=? AND created_at>=? AND type IN ('income','expense','credit_payment')
               GROUP BY 1, 2, 3 ORDER BY 1""",
            (user_id, since),
        ) as cur:
            rows = await cur.fetchall()

    names = {"income": "daromad", "expense": "xarajat", "credit_payment": "kredit_tolovlari"}
    out: dict = {}
    for ym, ttype, cur_code, total in rows:
        month = out.setdefault(ym, {}).setdefault(
            cur_code, {"daromad": 0, "xarajat": 0, "kredit_tolovlari": 0}
        )
        month[names[ttype]] = round(total, 2)
    return out


async def debts(user_id: int) -> dict[str, Any]:
    """Kim menga qarzdor, men kimga qarzdorman, kredit qoldig'i."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            """SELECT type, currency, LOWER(TRIM(COALESCE(counterparty, ''))), SUM(amount)
               FROM transactions
               WHERE user_id=? AND type IN
                 ('debt_given','debt_received','debt_borrowed','debt_repaid','credit_balance','credit_payment')
               GROUP BY 1, 2, 3""",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()

    owed_to_me: dict = defaultdict(float)
    i_owe: dict = defaultdict(float)
    credit: dict = defaultdict(float)
    for ttype, cur_code, name, total in rows:
        key = (name or "noma'lum", cur_code)
        if ttype == "debt_given":
            owed_to_me[key] += total
        elif ttype == "debt_received":
            owed_to_me[key] -= total
        elif ttype == "debt_borrowed":
            i_owe[key] += total
        elif ttype == "debt_repaid":
            i_owe[key] -= total
        elif ttype == "credit_balance":
            credit[cur_code] += total
        elif ttype == "credit_payment":
            credit[cur_code] -= total

    def to_list(d: dict) -> list[dict]:
        return [
            {"kim": name.title(), "valyuta": cur, "summa": round(v, 2)}
            for (name, cur), v in d.items() if v > 0
        ]

    return {
        "menga_qarzdor": to_list(owed_to_me),
        "men_qarzdorman": to_list(i_owe),
        "kredit_qoldigi": {c: round(v, 2) for c, v in credit.items() if v > 0},
    }
