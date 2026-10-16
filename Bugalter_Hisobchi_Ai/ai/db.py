"""Ma'lumotlar bazasi (SQLite). Barcha vaqtlar Toshkent vaqti bilan saqlanadi."""
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

import config

TZ = ZoneInfo("Asia/Tashkent")
FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_PRICE = 20000  # so'm, birinchi ishga tushganda; keyin admin /setprice bilan o'zgartiradi

# Har bir tranzaksiya turi hamyon naqd balansiga qanday ta'sir qilishi (+1 / -1 / 0)
CASH_SIGN = {
    "income": 1, "expense": -1,
    "debt_given": -1, "debt_received": 1,
    "debt_borrowed": 1, "debt_repaid": -1,
    "credit_payment": -1, "credit_balance": 0,  # kredit qoldig'ini belgilaydi, naqd harakatiga kirmaydi
}


def now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def now_str() -> str:
    return now().strftime(FMT)


async def _add_column_if_missing(db, table: str, coldef: str) -> None:
    """Eski bazalarni yangi ustunlar bilan moslashtirish uchun (xavfsiz migratsiya)."""
    try:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
    except Exception:
        pass  # ustun allaqachon bor


async def init_db() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id    INTEGER PRIMARY KEY,
                name           TEXT,
                pro_until      TEXT,
                created_at     TEXT,
                daily_report   INTEGER NOT NULL DEFAULT 0,
                weekly_report  INTEGER NOT NULL DEFAULT 0,
                monthly_report INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS wallets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT NOT NULL,
                currency   TEXT NOT NULL DEFAULT 'UZS',
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wallets_user ON wallets(user_id);
            CREATE TABLE IF NOT EXISTS transactions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                wallet_id    INTEGER,
                type         TEXT NOT NULL,
                amount       REAL NOT NULL,
                currency     TEXT NOT NULL,
                category     TEXT,
                counterparty TEXT,
                description  TEXT,
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, created_at);
            CREATE TABLE IF NOT EXISTS transfers (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                from_wallet_id INTEGER NOT NULL,
                to_wallet_id   INTEGER NOT NULL,
                amount         REAL NOT NULL,
                currency       TEXT NOT NULL,
                note           TEXT,
                created_at     TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recurring_payments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                title        TEXT NOT NULL,
                type         TEXT NOT NULL,
                amount       REAL NOT NULL,
                currency     TEXT NOT NULL,
                category     TEXT,
                wallet_id    INTEGER,
                day_of_month INTEGER NOT NULL,
                active       INTEGER NOT NULL DEFAULT 1,
                last_run     TEXT,
                created_at   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS budget_limits (
                user_id       INTEGER NOT NULL,
                currency      TEXT NOT NULL,
                monthly_limit REAL NOT NULL,
                warned_month  TEXT,
                warned_level  TEXT,
                PRIMARY KEY (user_id, currency)
            );
            CREATE TABLE IF NOT EXISTS savings_goals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                title         TEXT NOT NULL,
                target_amount REAL NOT NULL,
                saved_amount  REAL NOT NULL DEFAULT 0,
                currency      TEXT NOT NULL,
                target_date   TEXT,
                active        INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS payments (
                merchant_trans_id  TEXT PRIMARY KEY,
                user_id            INTEGER NOT NULL,
                provider           TEXT NOT NULL,          -- 'click' | 'uzum' | 'stars'
                amount             INTEGER NOT NULL,       -- 'stars' uchun Stars sonida, boshqalar so'mda
                status             TEXT NOT NULL DEFAULT 'pending',
                provider_trans_id  TEXT,
                created_at         TEXT NOT NULL,
                paid_at            TEXT
            );
            """
        )
        # Eski bazalarda yo'q bo'lishi mumkin bo'lgan ustunlar (xavfsiz migratsiya):
        for coldef in (
            "daily_report INTEGER NOT NULL DEFAULT 0",
            "weekly_report INTEGER NOT NULL DEFAULT 0",
            "monthly_report INTEGER NOT NULL DEFAULT 0",
        ):
            await _add_column_if_missing(db, "users", coldef)
        await _add_column_if_missing(db, "transactions", "wallet_id INTEGER")

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
        async with db.execute(
            "SELECT id FROM wallets WHERE user_id=? AND is_default=1", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO wallets(user_id, name, currency, is_default, created_at) "
                "VALUES (?, 'Asosiy', 'UZS', 1, ?)",
                (user_id, now_str()),
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


async def set_report_pref(user_id: int, period: str, enabled: bool) -> None:
    col = {"daily": "daily_report", "weekly": "weekly_report", "monthly": "monthly_report"}[period]
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(f"UPDATE users SET {col}=? WHERE telegram_id=?", (int(enabled), user_id))
        await db.commit()


async def get_report_prefs(user_id: int) -> dict:
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "SELECT daily_report, weekly_report, monthly_report FROM users WHERE telegram_id=?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {"daily": False, "weekly": False, "monthly": False}
    return {"daily": bool(row[0]), "weekly": bool(row[1]), "monthly": bool(row[2])}


async def get_report_subscribers(period: str) -> list[int]:
    col = {"daily": "daily_report", "weekly": "weekly_report", "monthly": "monthly_report"}[period]
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(f"SELECT telegram_id FROM users WHERE {col}=1") as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


# ---------- Hamyonlar ----------

async def list_wallets(user_id: int) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM wallets WHERE user_id=? ORDER BY is_default DESC, id", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_default_wallet(user_id: int) -> dict:
    wallets = await list_wallets(user_id)
    for w in wallets:
        if w["is_default"]:
            return w
    # Odatda ensure_user() allaqachon yaratgan, lekin ehtiyot chorasi:
    wid = await create_wallet(user_id, "Asosiy", "UZS", is_default=True)
    return {"id": wid, "user_id": user_id, "name": "Asosiy", "currency": "UZS", "is_default": 1}


async def get_wallet_by_name(user_id: int, name: str) -> dict | None:
    if not name:
        return None
    wallets = await list_wallets(user_id)
    name_l = name.strip().lower()
    for w in wallets:
        if w["name"].strip().lower() == name_l:
            return w
    return None


async def create_wallet(user_id: int, name: str, currency: str = "UZS", is_default: bool = False) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO wallets(user_id, name, currency, is_default, created_at) VALUES(?,?,?,?,?)",
            (user_id, name, currency, int(is_default), now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def wallet_balances(user_id: int) -> list[dict]:
    """Har bir hamyon bo'yicha joriy balans (tranzaksiya + o'tkazmalar hisobga olinadi)."""
    wallets = await list_wallets(user_id)
    if not wallets:
        return []
    default_id = next((w["id"] for w in wallets if w["is_default"]), wallets[0]["id"])
    balances: dict = defaultdict(float)  # (wallet_id, currency) -> summa

    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(wallet_id, ?), type, currency, SUM(amount) FROM transactions "
            "WHERE user_id=? GROUP BY 1, 2, 3",
            (default_id, user_id),
        ) as cur:
            tx_rows = await cur.fetchall()
        async with db.execute(
            "SELECT from_wallet_id, to_wallet_id, currency, SUM(amount) FROM transfers "
            "WHERE user_id=? GROUP BY 1, 2, 3",
            (user_id,),
        ) as cur:
            tr_rows = await cur.fetchall()

    for wid, ttype, cur_code, total in tx_rows:
        balances[(wid, cur_code)] += CASH_SIGN.get(ttype, 0) * total
    for from_wid, to_wid, cur_code, total in tr_rows:
        balances[(from_wid, cur_code)] -= total
        balances[(to_wid, cur_code)] += total

    out = []
    for w in wallets:
        w_balances = {cur: round(v, 2) for (wid, cur), v in balances.items() if wid == w["id"]}
        out.append({**w, "balanslar": w_balances or {w["currency"]: 0.0}})
    return out


async def create_transfer(
    user_id: int, from_wallet_id: int, to_wallet_id: int, amount: float, currency: str, note: str | None = None
) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO transfers(user_id, from_wallet_id, to_wallet_id, amount, currency, note, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, from_wallet_id, to_wallet_id, amount, currency, note, now_str()),
        )
        await db.commit()
        return cur.lastrowid


# ---------- To'lovlar (Click / Uzum / Stars) ----------

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
    (bu — bir xil webhook/to'lov tasdig'i qayta kelganda PRO ni ikki marta
    berib yubormaslik uchun himoya)."""
    payment = await get_payment(merchant_trans_id)
    if not payment or payment["status"] == "paid":
        return None
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET status='paid', provider_trans_id=?, paid_at=? WHERE merchant_trans_id=?",
            (provider_trans_id, now_str(), merchant_trans_id),
        )
        await db.commit()
    payment["status"] = "paid"
    return payment


async def mark_payment_cancelled(merchant_trans_id: str) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET status='cancelled' WHERE merchant_trans_id=? AND status='pending'",
            (merchant_trans_id,),
        )
        await db.commit()


async def stats() -> dict:
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

async def add_transactions(user_id: int, items: list[dict]) -> None:
    created = now_str()
    default_wallet = await get_default_wallet(user_id)
    rows = [
        (
            user_id, t.get("wallet_id") or default_wallet["id"], t["type"], t["amount"], t["currency"],
            t.get("category"), t.get("counterparty"), t.get("description"), created,
        )
        for t in items
    ]
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executemany(
            """INSERT INTO transactions
               (user_id, wallet_id, type, amount, currency, category, counterparty, description, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        await db.commit()


async def delete_last(user_id: int) -> dict | None:
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


async def get_all_transactions(user_id: int) -> list[dict]:
    """Eksport (CSV/Excel/PDF/zaxira) uchun — foydalanuvchining barcha yozuvlari."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT t.created_at, t.type, t.amount, t.currency, t.category, t.counterparty,
                      t.description, COALESCE(w.name, 'Asosiy') AS wallet_name
               FROM transactions t LEFT JOIN wallets w ON w.id = t.wallet_id
               WHERE t.user_id=? ORDER BY t.created_at""",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


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


async def monthly_comparison(user_id: int) -> dict:
    """Joriy kalendar oy va undan oldingi oy bo'yicha moliyaviy jami va toifalar."""
    current_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_end = current_start
    previous_start = (current_start - timedelta(days=1)).replace(day=1)
    current_period = current_start.strftime("%Y-%m")
    previous_period = previous_start.strftime("%Y-%m")

    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            """SELECT substr(created_at, 1, 7), type, currency,
                      COALESCE(category, 'Boshqa'), SUM(amount)
               FROM transactions
               WHERE user_id=? AND created_at>=? AND created_at<?
                 AND type IN ('income','expense','credit_payment')
               GROUP BY 1, 2, 3, 4""",
            (user_id, previous_start.strftime(FMT), now().strftime(FMT)),
        ) as cur:
            rows = await cur.fetchall()

    result: dict = {
        previous_period: {},
        current_period: {},
    }
    names = {"income": "daromad", "expense": "xarajat", "credit_payment": "kredit_tolovlari"}
    for period, ttype, currency, category, total in rows:
        month = result[period].setdefault(
            currency,
            {"daromad": 0.0, "xarajat": 0.0, "kredit_tolovlari": 0.0, "xarajat_kategoriyalari": {}},
        )
        month[names[ttype]] += total
        if ttype == "expense":
            categories = month["xarajat_kategoriyalari"]
            categories[category] = categories.get(category, 0.0) + total

    for period_data in result.values():
        for month in period_data.values():
            month["qolgan"] = month["daromad"] - month["xarajat"] - month["kredit_tolovlari"]
            for key in ("daromad", "xarajat", "kredit_tolovlari", "qolgan"):
                month[key] = round(month[key], 2)
            month["xarajat_kategoriyalari"] = {
                category: round(amount, 2)
                for category, amount in sorted(
                    month["xarajat_kategoriyalari"].items(), key=lambda item: -item[1]
                )
            }

    return {
        "oldingi_oy": previous_period,
        "joriy_oy": current_period,
        "valyutalar": result,
    }


async def debts(user_id: int) -> dict:
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


# ---------- Takroriy to'lovlar ----------

async def add_recurring(
    user_id: int, title: str, ttype: str, amount: float, currency: str,
    category: str | None, wallet_id: int | None, day_of_month: int,
) -> int:
    day_of_month = max(1, min(28, day_of_month))  # 29-31 oylarda muammo bermasligi uchun 28 bilan cheklanadi
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO recurring_payments
               (user_id, title, type, amount, currency, category, wallet_id, day_of_month, active, created_at)
               VALUES (?,?,?,?,?,?,?,?,1,?)""",
            (user_id, title, ttype, amount, currency, category, wallet_id, day_of_month, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def list_recurring(user_id: int, active_only: bool = True) -> list[dict]:
    q = "SELECT * FROM recurring_payments WHERE user_id=?"
    if active_only:
        q += " AND active=1"
    q += " ORDER BY day_of_month"
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(q, (user_id,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def deactivate_recurring(recurring_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            "UPDATE recurring_payments SET active=0 WHERE id=? AND user_id=?", (recurring_id, user_id)
        )
        await db.commit()
        return cur.rowcount > 0


async def due_recurring_today() -> list[dict]:
    """Bugun kuni (day_of_month) to'g'ri keladigan va shu oy hali ishlamagan
    barcha foydalanuvchilarning takroriy to'lovlari. scheduler.py har kuni chaqiradi."""
    today = now()
    period = today.strftime("%Y-%m")
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM recurring_payments
               WHERE active=1 AND day_of_month=? AND (last_run IS NULL OR last_run != ?)""",
            (today.day, period),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def mark_recurring_ran(recurring_id: int, period: str) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("UPDATE recurring_payments SET last_run=? WHERE id=?", (period, recurring_id))
        await db.commit()


# ---------- Oylik byudjet limiti ----------

async def set_budget(user_id: int, currency: str, monthly_limit: float) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """INSERT INTO budget_limits(user_id, currency, monthly_limit, warned_month, warned_level)
               VALUES (?,?,?,NULL,NULL)
               ON CONFLICT(user_id, currency) DO UPDATE SET
                 monthly_limit=excluded.monthly_limit, warned_month=NULL, warned_level=NULL""",
            (user_id, currency, monthly_limit),
        )
        await db.commit()


async def get_budgets(user_id: int) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM budget_limits WHERE user_id=?", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def check_budget_alert(user_id: int, currency: str) -> dict | None:
    """Shu oy uchun xarajat limitga qancha yaqinlashganini tekshiradi.
    Faqat bosqich (80% yoki 100%) ENDIGINA kesib o'tilgan bo'lsa natija qaytaradi —
    shunda har safar xarajat kiritilganda qayta-qayta ogohlantirib yubormaydi."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM budget_limits WHERE user_id=? AND currency=?", (user_id, currency)
        ) as cur:
            budget = await cur.fetchone()
    if not budget:
        return None
    budget = dict(budget)

    since = period_start("month")
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions "
            "WHERE user_id=? AND currency=? AND type='expense' AND created_at>=?",
            (user_id, currency, since),
        ) as cur:
            spent = (await cur.fetchone())[0]

    limit = budget["monthly_limit"]
    ratio = spent / limit if limit else 0
    period = now().strftime("%Y-%m")
    already_warned = budget["warned_month"] == period

    level = "exceeded" if ratio >= 1.0 else "approaching" if ratio >= 0.8 else "ok"
    if level == "ok" or (already_warned and budget["warned_level"] == level):
        return None  # hali chegaraga yetmagan, yoki shu bosqich uchun allaqachon ogohlantirilgan

    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE budget_limits SET warned_month=?, warned_level=? WHERE user_id=? AND currency=?",
            (period, level, user_id, currency),
        )
        await db.commit()

    return {"level": level, "spent": round(spent, 2), "limit": limit, "ratio": round(ratio, 3)}


# ---------- Jamg'arma maqsadlari ----------

async def add_goal(
    user_id: int, title: str, target_amount: float, currency: str, target_date: str | None = None
) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO savings_goals(user_id, title, target_amount, saved_amount, currency, target_date,
                                          active, created_at)
               VALUES (?,?,?,0,?,?,1,?)""",
            (user_id, title, target_amount, currency, target_date, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def list_goals(user_id: int, active_only: bool = True) -> list[dict]:
    q = "SELECT * FROM savings_goals WHERE user_id=?"
    if active_only:
        q += " AND active=1"
    q += " ORDER BY id"
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(q, (user_id,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def contribute_goal(goal_id: int, user_id: int, amount: float) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM savings_goals WHERE id=? AND user_id=?", (goal_id, user_id)
        ) as cur:
            goal = await cur.fetchone()
        if not goal:
            return None
        goal = dict(goal)
        new_saved = goal["saved_amount"] + amount
        completed = new_saved >= goal["target_amount"]
        await db.execute(
            "UPDATE savings_goals SET saved_amount=?, active=? WHERE id=?",
            (new_saved, 0 if completed else 1, goal_id),
        )
        await db.commit()
    goal["saved_amount"] = new_saved
    goal["completed"] = completed
    return goal
