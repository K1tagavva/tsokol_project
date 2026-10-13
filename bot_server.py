import os
import json
import asyncio
import sqlite3
import urllib.request
from typing import Optional
from dotenv import load_dotenv

from pydantic import BaseModel
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo
)
from aiogram.client.session.aiohttp import AiohttpSession

# 1. Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
STAFF_CHAT_ID = os.getenv("STAFF_CHAT_ID")
if STAFF_CHAT_ID:
    try:
        STAFF_CHAT_ID = int(STAFF_CHAT_ID.strip())
    except ValueError:
        STAFF_CHAT_ID = None

if not BOT_TOKEN:
    raise ValueError("ОШИБКА: Заполните BOT_TOKEN в файле .env!")

# 2. Подключение через системный канал Windows (прокси 127.0.0.1:10809)
system_proxies = urllib.request.getproxies()
proxy_url = system_proxies.get("https") or system_proxies.get("http")

if proxy_url:
    print(f"🌐 Обнаружен системный канал: {proxy_url}. Подключаем бота...")
    session = AiohttpSession(proxy=proxy_url)
    bot = Bot(token=BOT_TOKEN, session=session)
else:
    print("🌐 Подключение напрямую...")
    bot = Bot(token=BOT_TOKEN)

# 3. База данных SQLite
DB_PATH = "cokol.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            guest_tg_id INTEGER,
            guest_name TEXT,
            guest_phone TEXT,
            username TEXT,
            table_name TEXT,
            slot_date TEXT,
            slot_time TEXT,
            hookahs INTEGER,
            hookah_tier TEXT,
            drinks TEXT,
            comment TEXT,
            deposit TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# 4. Меню и клавиатуры с разделением ролей
def get_main_keyboard(is_admin: bool = False):
    keyboard = [
        [KeyboardButton(text="💨 Забронировать стол", web_app=WebAppInfo(url="https://k1tagavva.github.io/cokol-app/"))]
    ]
    if is_admin:
        keyboard.append([
            KeyboardButton(text="🛋 Шахматка зала (Админ)", web_app=WebAppInfo(url="https://k1tagavva.github.io/cokol-app/admin.html")),
            KeyboardButton(text="📊 Сводка смены")
        ])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_admin_chats():
    chats = set(ADMIN_IDS)
    if STAFF_CHAT_ID:
        chats.add(STAFF_CHAT_ID)
    return list(chats)

dp = Dispatcher()

# 5. Обработка команды /start
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    is_admin = msg.from_user.id in ADMIN_IDS
    kb = get_main_keyboard(is_admin=is_admin)

    admin_hint = (
        "\n\n🔑 **Вы авторизованы как администратор.**\n"
        "Вам доступны закрытая шахматка зала и сводка смены на кнопках внизу."
        if is_admin else ""
    )

    await msg.answer(
        f"👋 Привет, {msg.from_user.first_name}!\n\n"
        "Добро пожаловать в **ЦОКОЛЬ Lounge** (Белгород).\n"
        "Нажмите кнопку внизу экрана, чтобы открыть схему зала, чайную карту и забронировать стол:" + admin_hint,
        reply_markup=kb,
        parse_mode="Markdown"
    )

# 6. ПРИЁМ БРОНИ ИЗ MINI APP ЧЕРЕЗ sendData
@dp.message(F.web_app_data)
async def handle_web_app_data(msg: types.Message):
    try:
        data = json.loads(msg.web_app_data.data)
    except Exception as e:
        print(f"Ошибка чтения данных брони: {e}")
        return

    b_id = data.get("id") or f"b_{int(asyncio.get_event_loop().time() * 1000)}"
    guest_name = data.get("name") or (msg.from_user.first_name or "Гость")
    guest_phone = data.get("phone", "Не указан")
    username = msg.from_user.username or "нет"
    table_name = data.get("tableName", "Стол")
    slot_date = data.get("date", "Сегодня")
    slot_time = data.get("time", "18:00")
    hookahs = data.get("hookahs", 1)
    hookah_tier = data.get("hookahType", "Классика")
    drinks = data.get("drinks", "")
    comment = data.get("comment", "")
    deposit = data.get("total", "1 500 ₽")

    # Сохраняем в SQLite
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO bookings (id, guest_tg_id, guest_name, guest_phone, username, table_name, slot_date, slot_time, hookahs, hookah_tier, drinks, comment, deposit, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (
        b_id, msg.from_user.id, guest_name, guest_phone, username,
        table_name, slot_date, slot_time, hookahs,
        hookah_tier, drinks, comment, deposit
    ))
    conn.commit()
    conn.close()

    # Ответ гостю в чат
    await msg.answer(
        f"🎉 **{guest_name}, бронь принята в обработку!**\n\n"
        f"📅 Сеанс: **{slot_date} • {slot_time}**\n"
        f"📍 Локация: **{table_name}**\n"
        f"💨 Кальяны: **{hookahs} шт ({hookah_tier})**\n"
        f"{f'🍵 Чай/Бар: **{drinks}**\\n' if drinks else ''}"
        f"{f'💬 Пожелание: _{comment}_\\n' if comment else ''}"
        f"💳 Депозит СБП: **{deposit}**\n\n"
        f"⏳ Администратор сверяет поступление платежа и подтвердит бронь в течение 1–2 минут.",
        parse_mode="Markdown"
    )

    # Карточка заявки для администратора с кнопками
    alert_text = (
        f"🔔 **НОВАЯ БРОНЬ (СБП НА ПРОВЕРКЕ)**\n\n"
        f"👤 Гость: **{guest_name}** (@{username})\n"
        f"📞 Телефон: `{guest_phone}`\n"
        f"🛋 Место: **{table_name}**\n"
        f"📅 Сеанс: **{slot_date} • {slot_time}**\n"
        f"💨 Кальяны: **{hookahs} шт ({hookah_tier})**\n"
        f"🍵 Чай/Бар: **{drinks or 'без напитков'}**\n"
        f"💬 Пожелание: _{comment or 'нет'}_\n\n"
        f"💳 Сумма к зачислению: **{deposit}**"
    )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Депозит поступил", callback_data=f"ok:{b_id}:{msg.from_user.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no:{b_id}:{msg.from_user.id}")
        ]
    ])

    for admin_id in get_admin_chats():
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=alert_text,
                reply_markup=admin_kb,
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Ошибка отправки админу {admin_id}: {e}")

# 7. Подтверждение депозита
@dp.callback_query(F.data.startswith("ok:"))
async def on_confirm(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Нет доступа", show_alert=True)
        return

    parts = call.data.split(":")
    b_id = parts[1]
    guest_tg_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (b_id,))
    conn.commit()
    conn.close()

    await call.message.edit_text(
        call.message.text + "\n\n✅ **БРОНЬ ПОДТВЕРЖДЕНА (ДЕПОЗИТ ПОЛУЧЕН)**",
        parse_mode="Markdown"
    )
    await call.answer("Депозит зафиксирован!")

    if guest_tg_id:
        try:
            await bot.send_message(
                chat_id=guest_tg_id,
                text="🎉 **Ваш депозит подтверждён!**\n\nСтол зафиксирован за вами. Ждём вас в ЦОКОЛЬ Lounge!\n📍 Белгород, ул. Королёва, 4Д",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Не удалось отправить уведомление гостю: {e}")

# 8. Отклонение брони
@dp.callback_query(F.data.startswith("no:"))
async def on_reject(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Нет доступа", show_alert=True)
        return

    parts = call.data.split(":")
    b_id = parts[1]
    guest_tg_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (b_id,))
    conn.commit()
    conn.close()

    await call.message.edit_text(
        call.message.text + "\n\n❌ **БРОНЬ ОТКЛОНЕНА**",
        parse_mode="Markdown"
    )
    await call.answer("Бронь отклонена")

    if guest_tg_id:
        try:
            await bot.send_message(
                chat_id=guest_tg_id,
                text="❌ **Бронь не подтверждена**\n\nПеревод по СБП не был получен. Если произошла ошибка, свяжитесь с заведением.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

# 9. Сводка смены для админа (/admin или кнопка)
@dp.message(F.text == "📊 Сводка смены")
@dp.message(Command("admin"))
async def cmd_admin(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.answer("⛔ Доступ ограничен. Команда доступна только администраторам заведения.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) FROM bookings GROUP BY status")
    stats = dict(cur.fetchall())

    cur.execute("SELECT deposit FROM bookings WHERE status = 'confirmed'")
    confirmed_deposits = cur.fetchall()
    conn.close()

    total_revenue = 0
    for (dep,) in confirmed_deposits:
        digits = "".join(filter(str.isdigit, dep or ""))
        if digits:
            total_revenue += int(digits)

    pending_count = stats.get("pending", 0)
    confirmed_count = stats.get("confirmed", 0)
    cancelled_count = stats.get("cancelled", 0)
    total_count = pending_count + confirmed_count + cancelled_count

    admin_inline = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛋 Открыть шахматку столов", web_app=WebAppInfo(url="https://k1tagavva.github.io/cokol-app/admin.html"))
        ]
    ])

    await msg.answer(
        f"🔐 **Панель управления ЦОКОЛЬ Lounge**\n\n"
        f"💰 Подтверждённые депозиты: **{total_revenue:,} ₽**\n"
        f"⏳ Ожидают проверки СБП: **{pending_count}**\n"
        f"✅ Подтверждено броней: **{confirmed_count}**\n"
        f"❌ Отклонено: **{cancelled_count}**\n"
        f"📦 Всего заявок: **{total_count}**\n\n"
        "Для просмотра схемы зала нажмите кнопку ниже:",
        reply_markup=admin_inline,
        parse_mode="Markdown"
    )

# 10. FastAPI веб-сервер
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def main():
    print("🚀 Сервер ЦОКОЛЬ и Telegram-бот запущены!")
    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())