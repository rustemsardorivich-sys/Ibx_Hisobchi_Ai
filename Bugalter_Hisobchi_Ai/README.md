# Hisobchi AI — Telegram bot

Xarajat, daromad, qarz va kreditlarni AI yordamida yuritadigan Telegram bot.
Click va Uzum Bank orqali PRO obuna sotiladi; PRO uchun yillik hisobot va
shaxsiy AI buxgalter maslahati ochiladi.

## Fayllar tuzilishi

| Fayl | Vazifasi |
|---|---|
| `config.py` | `.env` dan barcha sozlamalarni o'qiydi |
| `db.py` | SQLite baza: foydalanuvchilar, tranzaksiyalar, to'lovlar, PRO holati |
| `prompts.py` | AI uchun matnli ko'rsatmalar (Claude va Gemini uchun umumiy) |
| `ai_client.py` | Claude / Gemini o'rtasida almashtiriladigan AI qatlami |
| `payments.py` | Click va Uzum Bank: link yaratish, imzo/autentifikatsiya tekshirish |
| `webhook_server.py` | Click/Uzum'dan keladigan to'lov xabarnomalarini qabul qiladi |
| `formatting.py` | Hisobot va qarzlar ro'yxatini o'qish uchun matnga aylantiradi |
| `bot.py` | Botning o'zi — shu faylni ishga tushirasiz |

## O'rnatish

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp env.example .env
```

`.env` faylini oching va quyidagilarni to'ldiring:

1. **BOT_TOKEN** — @BotFather dan (`/newbot`)
2. **OWNER_ID** — bot egasining Telegram ID'i (@userinfobot orqali bilib oling)
3. **ADMIN_IDS** — qo'shimcha adminlarning Telegram ID'lari, vergul bilan
4. **AI_PROVIDER** — `claude` yoki `gemini`, va shu provayderning API kaliti
5. **To'lov tizimlari** — quyida batafsil

Botni ishga tushirish:

```bash
python bot.py
```

## AI provayderni tanlash

`.env` faylida:

```
AI_PROVIDER=claude   # yoki: gemini
```

- `claude` bo'lsa — `ANTHROPIC_API_KEY` to'ldirilgan bo'lishi kerak (console.anthropic.com)
- `gemini` bo'lsa — `GEMINI_API_KEY` to'ldirilgan bo'lishi kerak (aistudio.google.com/app/apikey)

Ikkalasini ham to'ldirib qo'ysangiz, `.env` da `AI_PROVIDER` ni istalgan payt
almashtirib, botni qayta ishga tushirish kifoya.

## To'lov tizimlarini ulash

Botga webhook (to'lov xabarnomasi) kelishi uchun u **internetga ochiq
manzil**ga muhtoj. Lokal kompyuterda test qilish uchun [ngrok](https://ngrok.com)
ishlating:

```bash
ngrok http 8080
```

ngrok bergan `https://xxxx.ngrok-free.app` manzilini `.env` dagi `BASE_URL`
ga yozing. Productionda esa haqiqiy domeningiz (nginx + SSL bilan) ishlatiladi.

### Click.uz

1. https://merchant.click.uz da ro'yxatdan o'ting, xizmat (service) yarating.
2. `CLICK_SERVICE_ID`, `CLICK_MERCHANT_ID`, `CLICK_MERCHANT_USER_ID`,
   `CLICK_SECRET_KEY` — shu kabinetdan olinadi, `.env` ga yoziladi.
3. Xizmat sozlamalarida callback manzillarni kiriting:
   - **URL (Tasdiqlash manzili / Prepare):** `{BASE_URL}/click/prepare`
   - **To'liq URL (Natija manzili / Complete):** `{BASE_URL}/click/complete`
4. Click qo'llab-quvvatlash xizmatidan xizmatingizni faollashtirishni so'rang.

Bu qism to'liq test qilingan va ishonchli (Click'ning ochiq hujjatiga asoslangan).

### Uzum Bank

1. Uzum Bank bilan shartnoma tuzib, merchant kabinet oching.
2. Ular sizga `UZUM_SERVICE_ID`, hamda webhook uchun `UZUM_WEBHOOK_USERNAME` /
   `UZUM_WEBHOOK_PASSWORD` beradi (Basic Auth) — `.env` ga yozing.
3. Webhook manzili sifatida bering: `{BASE_URL}/uzum/webhook`

**⚠️ MUHIM:** Uzum Bank'ning webhook JSON tuzilishi (qaysi maydon nomi bilan
buyurtma ID va summa kelishi) ular tomonidan ommaviy internetda e'lon
qilinmagan — bu sizga alohida hujjat sifatida beriladi. `payments.py`
faylidagi `extract_uzum_fields()` funksiyasi eng ko'p uchraydigan nomlar
(`order_id`, `amount` va h.k.) bilan yozilgan, lekin **birinchi TEST
to'lovdan keyin** bot logida (`webhook_server.py`) chiqadigan xom JSON'ni
ko'rib, kerak bo'lsa shu funksiyani moslashtiring. Click bilan bunday muammo
yo'q — u to'liq ochiq hujjatlangan.

## Render'da joylashtirish (Web Service sifatida)

Render'da **New → Web Service** tanlab, GitHub repo'ingizni ulang. Sozlamalar:

| Maydon | Qiymat |
|---|---|
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python bot.py` |
| **Root Directory** | agar bot fayllari repo tub papkasida bo'lmasa, papka nomi (masalan `hisobchi_bot`) |

`.env` faylini Render'ga yuklamaysiz (u `.gitignore`da qolishi kerak) — barcha
qiymatlarni **Environment** bo'limida, bittalab, "Key / Value" ko'rinishida
kiritasiz (BOT_TOKEN, ANTHROPIC_API_KEY yoki GEMINI_API_KEY, CLICK_*, UZUM_*
va h.k.). `PORT` degan o'zgaruvchini o'zingiz qo'shmang — Render uni o'zi
avtomatik beradi, kodimiz (`config.py`) buni allaqachon o'qib oladi.

**Ketma-ketlik:**
1. Avval xizmatni shu sozlamalar bilan bir marta deploy qiling (BASE_URL'siz ham bo'ladi).
2. Render sizga `https://xizmat-nomi.onrender.com` ko'rinishidagi doimiy manzil beradi.
3. Shu manzilni **Environment** bo'limida `BASE_URL` qiymati qilib qo'shing va qayta deploy qiling (Render buni avtomatik qiladi, "Manual Deploy" tugmasi bilan ham mumkin).
4. Shu manzil asosidagi callback URL'larni (`/click/prepare`, `/click/complete`, `/uzum/webhook`) Click va Uzum kabinetlariga kiriting — yuqoridagi "To'lov tizimlarini ulash" bo'limiga qarang.
5. Brauzerda `https://xizmat-nomi.onrender.com/` ni oching — "Hisobchi AI bot ishlayapti ✅" chiqsa, veb-server to'g'ri ishlayapti.

**⚠️ Muhim — bepul (Free) tarif haqida:** Bot Telegramdan yangilanishlarni
*polling* (doimiy so'rab turish) orqali oladi, bu tashqi HTTP so'rov emas.
Render'ning bepul tarifi esa xizmatga ~15 daqiqa hech qanday tashqi HTTP
so'rov kelmasa, uni "uxlatib" qo'yadi — shunda bot Telegram xabarlariga
javob berishni to'xtatadi, toki xizmatga qandaydir HTTP so'rov kelmaguncha
(masalan, birov `/` sahifasini ochsa). Ikkita yechim bor:
  - **Oddiyi:** bepul xizmatdan foydalanib, [UptimeRobot](https://uptimerobot.com)
    kabi xizmat bilan `https://xizmat-nomi.onrender.com/` manziliga har
    10 daqiqada "ping" yuborib turing — bot doim uyg'oq turadi.
  - **To'g'risi:** Render'ning pullik (hech qachon uxlamaydigan) tarifiga
    o'tish, YOKI botni *polling* emas, Telegram *webhook* rejimiga
    o'tkazish (bu holda Telegram o'zi xabarni HTTP orqali yuboradi va
    xizmatni "uyg'otadi"). Kerak bo'lsa, shu o'zgarishni ham qilib beraman.

## Admin buyruqlari

- `/admin` — faqat bot egasi va adminlar uchun boshqaruv paneli
- `/setprice 25000` — PRO narxini o'zgartirish (so'mda)
- `/grant <user_id> [kunlar]` — foydalanuvchiga qo'lda PRO berish
- `/revoke <user_id>` — PRO ni bekor qilish
- `/stats` — umumiy statistika

Bot egasi (`OWNER_ID`) va adminlar (`ADMIN_IDS`) yillik hisobot hamda AI
maslahatidan PRO obunasiz foydalanadi. Oddiy foydalanuvchilar uchun bu
funksiyalar PRO obunasi bilan ochiladi. Oldingi yozuv summasini oddiy tilda
to'g'rilash mumkin, masalan: "Oldin 100 ming deb yozibman, aslida 90 ming
to'lov qilganman". Bir nechta mos yozuv topilsa, bot to'g'ri yozuvni tanlashni
so'raydi.

## Productionda ishga tushirish (systemd misoli)

```ini
# /etc/systemd/system/hisobchi-bot.service
[Unit]
Description=Hisobchi AI Telegram bot
After=network.target

[Service]
WorkingDirectory=/uy/papka/hisobchi_bot
ExecStart=/uy/papka/hisobchi_bot/venv/bin/python bot.py
Restart=always
User=sizning_user

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now hisobchi-bot
```

Nginx orqali `BASE_URL` domeningizni `127.0.0.1:8080` ga proksi qiling va
Certbot bilan SSL sertifikat oling (Click/Uzum faqat HTTPS manzilga webhook
yuboradi).

## Keyingi qadamlar (ixtiyoriy g'oyalar)

- **Ovozli xabarlar:** Gemini modeli audio faylni to'g'ridan-to'g'ri qabul
  qila oladi — ovozli xabar kelsa, uni Gemini'ga yuborib, matnga
  aylantirmasdan bevosita tahlil qildirish mumkin.
- **Eslatmalar:** har oyning oxirida PRO foydalanuvchilarga avtomatik
  hisobot yuborish (APScheduler yordamida).
