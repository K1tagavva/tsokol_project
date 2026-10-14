import os
import sys
import json
import asyncio
import sqlite3
import aiohttp
from datetime import datetime
from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.client.session.aiohttp import AiohttpSession

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

# База данных SQLite
DB_NAME = "cokol.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            name TEXT,
            username TEXT,
            phone TEXT,
            table_name TEXT,
            time_slot TEXT,
            booking_date TEXT,
            hookahs INTEGER,
            hookah_type TEXT,
            drinks TEXT,
            comment TEXT,
            total_price TEXT,
            total_num REAL,
            cashback REAL,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS loyalty_clients (
            phone TEXT PRIMARY KEY,
            telegram_id INTEGER,
            name TEXT,
            spent REAL DEFAULT 0,
            balance REAL DEFAULT 0,
            tier TEXT DEFAULT 'SILVER'
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Проверка локального системного прокси
session = None
proxy_url = "http://127.0.0.1:10809"

try:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    res = s.connect_ex(("127.0.0.1", 10809))
    s.close()
    if res == 0:
        print(f"🌐 Обнаружен рабочий локальный прокси: {proxy_url}")
        session = AiohttpSession(proxy=proxy_url)
except Exception:
    pass

bot = Bot(token=BOT_TOKEN, session=session) if session else Bot(token=BOT_TOKEN)
dp = Dispatcher()

# FastAPI приложение
app = FastAPI(title="Cokol Lounge API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class LoyaltyCheckRequest(BaseModel):
    phone: str = ""
    telegram_id: str | None = None

@app.post("/api/loyalty/check")
async def check_loyalty_endpoint(req: LoyaltyCheckRequest):
    clean = "".join(filter(str.isdigit, req.phone))
    if len(clean) == 11 and clean[0] in ('7', '8'):
        clean = "7" + clean[1:]

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Поиск по номеру телефона
    cursor.execute("SELECT phone, name, spent, balance, tier FROM loyalty_clients WHERE phone = ?", (clean,))
    row = cursor.fetchone()
    
    # Если найден и есть TG ID — привязываем
    if row and req.telegram_id and req.telegram_id.isdigit():
        cursor.execute("UPDATE loyalty_clients SET telegram_id = ? WHERE phone = ?", (int(req.telegram_id), clean))
        conn.commit()

    conn.close()

    if row:
        return {
            "found": True,
            "phone": row[0],
            "name": row[1],
            "spent": row[2],
            "balance": row[3],
            "tier": row[4]
        }
    return {"found": False, "spent": 0, "balance": 0, "tier": "SILVER"}

# Клавиатуры Telegram
def get_main_keyboard(is_admin: bool = False):
    keyboard = [
        [KeyboardButton(text="💨 Забронировать стол", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/?v=6"))]
    ]
    if is_admin:
        keyboard.append([
            KeyboardButton(text="🛋 Шахматка зала (Админ)", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/admin.html?v=6")),
            KeyboardButton(text="📊 Сводка смены")
        ])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# Команда /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    is_admin = message.from_user.id in ADMIN_IDS
    greeting = (
        f"👋 Приветствуем в **ЦОКОЛЬ Lounge**, {message.from_user.first_name}!\n\n"
        "💨 Атмосферный лаунж-бар в Белгороде (ул. Королёва, 4Д)\n"
        "🎮 Отдельные VIP-комнаты с PlayStation 5 (аренда 0 ₽, депозит 2 000 ₽ на покур и бар)\n"
        "🍵 Премиальная китайская чайная карта и крафтовые лимонады\n"
        "🎬 Воскресные кинопоказы на большом экране\n\n"
        "Для выбора стола и бронирования нажмите кнопку ниже 👇"
    )
    if is_admin:
        greeting += "\n\n⭐️ *Режим администратора активен: вам доступны шахматка столов и отчет смены.*"
    
    await message.answer(greeting, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin))

# Команда /admin
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав доступа к панели управления.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛋 Открыть шахматку столов", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/admin.html?v=6"))]
    ])
    await message.answer("🔐 **Панель управления залом ЦОКОЛЬ**\n\nУправляйте статусами столов, слотами времени и заявками гостей:", parse_mode="Markdown", reply_markup=kb)

# Сводка смены
@dp.message(F.text == "📊 Сводка смены")
async def cmd_summary(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), SUM(total_num) FROM bookings WHERE status = 'confirmed'")
    row = cursor.fetchone()
    confirmed_count = row[0] or 0
    confirmed_sum = row[1] or 0.0

    cursor.execute("SELECT COUNT(*) FROM bookings WHERE status = 'pending'")
    pending_count = cursor.fetchone()[0] or 0
    conn.close()

    text = (
        "📊 **Текущая сводка смены (ЦОКОЛЬ Lounge)**\n\n"
        f"✅ Подтверждённых броней: **{confirmed_count}**\n"
        f"💰 Собрано депозитов по СБП: **{confirmed_sum:,.0f} ₽**\n"
        f"⏳ Ожидают проверки СБП: **{pending_count}**\n\n"
        f"📅 Отчёт сформирован: {datetime.now().strftime('%H:%M, %d.%m.%Y')}"
    )
    await message.answer(text, parse_mode="Markdown")

# Прием данных из Mini App (sendData)
@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        booking_id = data.get("id", f"b_{int(datetime.now().timestamp())}")
        name = data.get("name", message.from_user.first_name)
        username = message.from_user.username or "без username"
        phone = data.get("phone", "")
        table_name = data.get("tableName", "Стол")
        time_slot = data.get("time", "18:00")
        booking_date = data.get("date", "Сегодня")
        hookahs = data.get("hookahs", 1)
        hookah_type = data.get("hookahType", "Классика")
        drinks = data.get("drinks", "")
        comment = data.get("comment", "")
        total_price = data.get("total", "1 500 ₽")
        total_num = float(data.get("totalNum", 1500))
        cashback = float(data.get("cashbackEarned", 45))

        clean_phone = "".join(filter(str.isdigit, phone))
        if len(clean_phone) == 11 and clean_phone[0] in ('7', '8'):
            clean_phone = "7" + clean_phone[1:]

        # Сохранение в БД
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO bookings (
                id, name, username, phone, table_name, time_slot, booking_date,
                hookahs, hookah_type, drinks, comment, total_price, total_num, cashback, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (booking_id, name, username, phone, table_name, time_slot, booking_date,
              hookahs, hookah_type, drinks, comment, total_price, total_num, cashback))
        
        # Регистрация гостя в лояльности если ещё не был
        if clean_phone:
            cursor.execute("""
                INSERT OR IGNORE INTO loyalty_clients (phone, telegram_id, name, spent, balance, tier)
                VALUES (?, ?, ?, 0, 0, 'SILVER')
            """, (clean_phone, message.from_user.id, name))

        conn.commit()
        conn.close()

        # 1. Сообщение гостю
        guest_msg = (
            f"✅ **Заявка на бронирование передана в ЦОКОЛЬ Lounge!**\n\n"
            f"📍 **{table_name}**\n"
            f"⏰ Сеанс: **{booking_date} в {time_slot}**\n"
            f"💨 Кальяны: **{hookahs} шт ({hookah_type})**\n"
            f"{f'🍵 Напитки: {drinks}\n' if drinks else ''}"
            f"💳 Депозит к зачислению: **{total_price}**\n\n"
            f"⏳ Администратор сверяет платёж СБП. После подтверждения стол окончательно закрепится за вами."
        )
        await message.answer(guest_msg, parse_mode="Markdown")

        # 2. Карточка администратору
        admin_card = (
            f"🔔 **НОВАЯ БРОНЬ СТОЛА (Ожидает СБП)**\n\n"
            f"👤 Гость: **{name}** (@{username})\n"
            f"📞 Телефон: `{phone}`\n"
            f"📍 Стол: **{table_name}**\n"
            f"⏰ Дата и время: **{booking_date} в {time_slot}**\n"
            f"💨 Кальяны: **{hookahs} шт ({hookah_type})**\n"
            f"{f'🍵 Бар: {drinks}\n' if drinks else ''}"
            f"{f'💬 Комментарий: «{comment}»\n' if comment else ''}"
            f"💰 Ожидаемый депозит СБП: **{total_price}**"
        )
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Депозит поступил", callback_data=f"ok_{booking_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no_{booking_id}")
            ]
        ])

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, admin_card, parse_mode="Markdown", reply_markup=admin_kb)
            except Exception as e:
                print(f"Не удалось отправить уведомление админу {admin_id}: {e}")

    except Exception as e:
        print(f"Ошибка при обработке webapp_data: {e}")
        await message.answer("⚠️ Произошла ошибка при сохранении заявки. Пожалуйста, обратитесь к администратору на баре.")

# Подтверждение / отклонение брони админом
@dp.callback_query(F.data.startswith("ok_") | F.data.startswith("no_"))
async def handle_booking_verdict(call: types.CallbackQuery):
    action, booking_id = call.data.split("_", 1)
    new_status = "confirmed" if action == "ok" else "rejected"

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name, phone, table_name, time_slot, total_num, cashback FROM bookings WHERE id = ?", (booking_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        await call.answer("Заявка не найдена в базе", show_alert=True)
        return

    name, phone, table_name, time_slot, total_num, cashback = row
    cursor.execute("UPDATE bookings SET status = ? WHERE id = ?", (new_status, booking_id))

    # Если подтвердили — начисляем сумму в профиль лояльности
    if new_status == "confirmed":
        clean_phone = "".join(filter(str.isdigit, phone))
        if len(clean_phone) == 11 and clean_phone[0] in ('7', '8'):
            clean_phone = "7" + clean_phone[1:]
        
        if clean_phone:
            cursor.execute("""
                UPDATE loyalty_clients
                SET spent = spent + ?,
                    balance = balance + ?,
                    tier = CASE
                        WHEN (spent + ?) >= 50000 THEN 'PLATINUM'
                        WHEN (spent + ?) >= 25000 THEN 'GOLD'
                        ELSE 'SILVER'
                    END
                WHERE phone = ?
            """, (total_num, cashback, total_num, total_num, clean_phone))

    conn.commit()
    conn.close()

    status_icon = "✅ ПОДТВЕРЖДЕНО (Депозит получен)" if new_status == "confirmed" else "❌ ОТКЛОНЕНО"
    new_text = call.message.text + f"\n\nСтатус смены: **{status_icon}**"
    await call.message.edit_text(new_text, parse_mode="Markdown", reply_markup=None)
    await call.answer(f"Статус изменен: {status_icon}")

async def main():
    print("🚀 Сервер ЦОКОЛЬ и Telegram-бот запущены!")
    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    
    await asyncio.gather(
        server.serve(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот и сервер остановлены.")