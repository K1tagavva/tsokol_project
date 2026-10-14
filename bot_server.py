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
    InlineKeyboardButton,
    MenuButtonWebApp
)
from aiogram.client.session.aiohttp import AiohttpSession

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW.isdigit() else (ADMIN_IDS[0] if ADMIN_IDS else 0)

DB_NAME = "cokol.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            chat_id INTEGER,
            guest_msg_id INTEGER,
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
            total_num REAL DEFAULT 0,
            cashback REAL DEFAULT 0,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("PRAGMA table_info(bookings)")
    cols = [c[1] for c in cursor.fetchall()]
    for col_name, col_type in [("chat_id", "INTEGER"), ("guest_msg_id", "INTEGER"), ("total_num", "REAL DEFAULT 0"), ("cashback", "REAL DEFAULT 0")]:
        if col_name not in cols:
            cursor.execute(f"ALTER TABLE bookings ADD COLUMN {col_name} {col_type}")

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

session = None
proxy_url = "http://127.0.0.1:10809"
try:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    res = s.connect_ex(("127.0.0.1", 10809))
    s.close()
    if res == 0:
        session = AiohttpSession(proxy=proxy_url)
except Exception:
    pass

bot = Bot(token=BOT_TOKEN, session=session) if session else Bot(token=BOT_TOKEN)
dp = Dispatcher()

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
    cursor.execute("SELECT phone, name, spent, balance, tier FROM loyalty_clients WHERE phone = ?", (clean,))
    row = cursor.fetchone()
    
    if row and req.telegram_id and req.telegram_id.isdigit():
        cursor.execute("UPDATE loyalty_clients SET telegram_id = ? WHERE phone = ?", (int(req.telegram_id), clean))
        conn.commit()
    conn.close()

    if row:
        return {"found": True, "phone": row[0], "name": row[1], "spent": row[2], "balance": row[3], "tier": row[4]}
    return {"found": False, "spent": 0, "balance": 0, "tier": "SILVER"}

def get_main_keyboard(is_admin: bool = False):
    keyboard = [
        [KeyboardButton(text="💨 Забронировать стол", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/?v=12"))]
    ]
    if is_admin:
        keyboard.append([
            KeyboardButton(text="🛋 Шахматка зала (Админ)", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/admin.html?v=12")),
            KeyboardButton(text="📊 Сводка смены")
        ])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    is_admin = message.from_user.id in ADMIN_IDS
    greeting = (
        f"👋 Приветствуем в **ЦОКОЛЬ Lounge**, {message.from_user.first_name}!\n\n"
        "💨 Атмосферный лаунж-бар в Белгороде (ул. Королёва, 4Д)\n"
        "🎮 Отдельные VIP-комнаты с PlayStation 5 (депозит 2 000 ₽ на покур и бар)\n"
        "🍵 Премиальная китайская чайная карта и крафтовые лимонады\n"
        "🎬 Воскресные кинопоказы на большом экране\n\n"
        "Для выбора стола и бронирования нажмите кнопку ниже 👇"
    )
    if is_admin:
        greeting += "\n\n⭐️ *Режим администратора активен: вам доступны шахматка столов и отчет смены.*"
    await message.answer(greeting, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin))

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав доступа к панели управления.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛋 Открыть шахматку столов", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/admin.html?v=12"))]
    ])
    await message.answer("🔐 **Панель управления залом ЦОКОЛЬ**\n\nПароль администратора: `tcklbel723daqw`", parse_mode="Markdown", reply_markup=kb)

@dp.message(F.text == "📊 Сводка смены")
async def cmd_summary(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), SUM(total_num) FROM bookings WHERE status IN ('confirmed', 'seated', 'completed')")
    row = cursor.fetchone()
    confirmed_count = row[0] or 0
    confirmed_sum = row[1] or 0.0

    cursor.execute("SELECT COUNT(*) FROM bookings WHERE status = 'pending'")
    pending_count = cursor.fetchone()[0] or 0
    conn.close()

    text = (
        "📊 **Текущая сводка смены (ЦОКОЛЬ Lounge)**\n\n"
        f"✅ Принятых броней: **{confirmed_count}**\n"
        f"💰 Оборот депозитов/чеков: **{confirmed_sum:,.0f} ₽**\n"
        f"⏳ Ожидают проверки СБП: **{pending_count}**\n\n"
        f"📅 Отчёт сформирован: {datetime.now().strftime('%H:%M, %d.%m.%Y')}"
    )
    await message.answer(text, parse_mode="Markdown")

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

        guest_msg_text = (
            f"⏳ **Заявка на бронирование передана в ЦОКОЛЬ Lounge!**\n\n"
            f"📍 **{table_name}**\n"
            f"⏰ Сеанс: **{booking_date} в {time_slot}**\n"
            f"💨 Кальяны: **{hookahs} шт ({hookah_type})**\n"
            f"{f'🍵 Напитки: {drinks}\n' if drinks else ''}"
            f"💳 Депозит брони: **{total_price}**\n\n"
            f"🔎 *Администратор сверяет платёж СБП. Статус обновится здесь автоматически.*"
        )
        sent_guest_msg = await message.answer(guest_msg_text, parse_mode="Markdown")

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO bookings (
                id, chat_id, guest_msg_id, name, username, phone, table_name, time_slot, booking_date,
                hookahs, hookah_type, drinks, comment, total_price, total_num, cashback, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (booking_id, message.chat.id, sent_guest_msg.message_id, name, username, phone, table_name, time_slot, booking_date,
              hookahs, hookah_type, drinks, comment, total_price, total_num, cashback))
        
        if clean_phone:
            cursor.execute("""
                INSERT OR IGNORE INTO loyalty_clients (phone, telegram_id, name, spent, balance, tier)
                VALUES (?, ?, ?, 0, 0, 'SILVER')
            """, (clean_phone, message.from_user.id, name))
        conn.commit()
        conn.close()

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
                print(f"Ошибка отправки админу {admin_id}: {e}")

    except Exception as e:
        print(f"Ошибка при обработке webapp_data: {e}")

# Меню бара для дозаказов в зале
def make_order_keyboard(booking_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Кальян (+1 500 ₽)", callback_data=f"add_hookah_{booking_id}"),
            InlineKeyboardButton(text="🍵 Чай (+500 ₽)", callback_data=f"add_tea500_{booking_id}")
        ],
        [
            InlineKeyboardButton(text="🫖 Премиум/ГАБА (+700 ₽)", callback_data=f"add_tea700_{booking_id}"),
            InlineKeyboardButton(text="🥤 Лимонад (+250 ₽)", callback_data=f"add_lem250_{booking_id}")
        ],
        [
            InlineKeyboardButton(text="🏁 Рассчитать и начислить кэшбэк", callback_data=f"finish_{booking_id}")
        ]
    ])

@dp.callback_query(F.data.startswith(("ok_", "no_", "seated_", "add_", "finish_", "refund_", "owner_refund_done_")))
async def handle_booking_callback(call: types.CallbackQuery):
    data = call.data

    if data.startswith("owner_refund_done_"):
        await call.message.edit_text(call.message.text + "\n\n✅ **Возврат средств выполнен владельцем по СБП.**", parse_mode="Markdown", reply_markup=None)
        await call.answer("Возврат подтвержден!")
        return

    parts = data.split("_")
    action = parts[0]
    booking_id = "_".join(parts[1:])

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT chat_id, guest_msg_id, name, phone, table_name, time_slot, booking_date, total_num, cashback, status
        FROM bookings WHERE id = ?
    """, (booking_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        await call.answer("Заявка не найдена в базе", show_alert=True)
        return

    chat_id, guest_msg_id, name, phone, table_name, time_slot, booking_date, total_num, cashback, status = row
    clean_phone = "".join(filter(str.isdigit, phone))
    if len(clean_phone) == 11 and clean_phone[0] in ('7', '8'):
        clean_phone = "7" + clean_phone[1:]

    # 1. ПОДТВЕРЖДЕНИЕ ДЕПОЗИТА
    if action == "ok":
        cursor.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()

        if chat_id and guest_msg_id:
            try:
                confirmed_guest_text = (
                    f"🎉 **Бронь подтверждена! Ждём вас в ЦОКОЛЬ Lounge**\n\n"
                    f"📍 **{table_name}**\n"
                    f"⏰ Сеанс: **{booking_date} в {time_slot}**\n"
                    f"💳 Депозит **{total_num:,.0f} ₽** успешно зачислен.\n\n"
                    f"✨ Бонусы лояльности будут начислены на номер `{phone}` при закрытии счёта на месте."
                )
                await bot.edit_message_text(chat_id=chat_id, message_id=guest_msg_id, text=confirmed_guest_text, parse_mode="Markdown")
            except Exception as e:
                print(f"Не удалось обновить статус у гостя: {e}")

        next_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛋 Гости в зале (Посадка)", callback_data=f"seated_{booking_id}")],
            [InlineKeyboardButton(text="⚠️ Запрос возврата (70%)", callback_data=f"refund_{booking_id}")]
        ])
        await call.message.edit_text(call.message.text + "\n\n🟢 **Депозит подтверждён.** Стол зафиксирован.", parse_mode="Markdown", reply_markup=next_kb)
        await call.answer("Депозит принят!")
        return

    # 2. ПОСАДКА ГОСТЕЙ В ЗАЛЕ
    elif action == "seated":
        cursor.execute("UPDATE bookings SET status = 'seated' WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()

        new_text = call.message.text.split("\n\n🟢")[0] + f"\n\n🛋 **Гости в зале.** Текущий счёт: **{total_num:,.0f} ₽**"
        await call.message.edit_text(new_text, parse_mode="Markdown", reply_markup=make_order_keyboard(booking_id))
        await call.answer("Посадка зафиксирована!")
        return

    # 3. ДОЗАКАЗЫ В ЗАЛЕ
    elif action == "add":
        sub = parts[1]
        real_id = "_".join(parts[2:])

        add_sum = 0
        item_title = ""
        if sub == "hookah":
            add_sum = 1500
            item_title = "Кальян"
        elif sub == "tea500":
            add_sum = 500
            item_title = "Чай"
        elif sub == "tea700":
            add_sum = 700
            item_title = "Премиум чай / ГАБА"
        elif sub == "lem250":
            add_sum = 250
            item_title = "Крафтовый лимонад"

        new_total = total_num + add_sum
        new_cashback = cashback + round(add_sum * 0.05)

        cursor.execute("UPDATE bookings SET total_num = ?, cashback = ? WHERE id = ?", (new_total, new_cashback, real_id))
        conn.commit()
        conn.close()

        base_part = call.message.text.split("\n\n🛋")[0]
        new_text = base_part + f"\n\n🛋 **Добавлено: {item_title} (+{add_sum} ₽)**\nИтоговый счёт: **{new_total:,.0f} ₽** (Кэшбэк: +{new_cashback:,.0f} ₽)"
        await call.message.edit_text(new_text, parse_mode="Markdown", reply_markup=make_order_keyboard(real_id))
        await call.answer(f"Добавлено: {item_title}")
        return

    # 4. ФИНАЛЬНЫЙ РАСЧЕТ И НАЧИСЛЕНИЕ ЛОЯЛЬНОСТИ
    elif action == "finish":
        cursor.execute("UPDATE bookings SET status = 'completed' WHERE id = ?", (booking_id,))
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

        final_text = call.message.text.split("\n\n🛋")[0] + f"\n\n🏁 **Смена закрыта:** Чек **{total_num:,.0f} ₽**. На карту `{phone}` начислен кэшбэк **+{cashback:,.0f} ₽**."
        await call.message.edit_text(final_text, parse_mode="Markdown", reply_markup=None)
        await call.answer("Визит завершен, баллы начислены!")
        return

    # 5. ОТКЛОНЕНИЕ БРОНИ
    elif action == "no":
        cursor.execute("UPDATE bookings SET status = 'rejected' WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()

        if chat_id and guest_msg_id:
            try:
                rejected_text = (
                    f"❌ **Бронирование отменено**\n\n"
                    f"Депозит по СБП не был обнаружен администратором.\n"
                    f"Если произошла ошибка перевода, пожалуйста, свяжитесь с баром напрямую."
                )
                await bot.edit_message_text(chat_id=chat_id, message_id=guest_msg_id, text=rejected_text, parse_mode="Markdown")
            except Exception as e:
                print(f"Ошибка обновления отклонения: {e}")

        await call.message.edit_text(call.message.text + "\n\n❌ **Бронь отклонена (депозит не поступил).**", parse_mode="Markdown", reply_markup=None)
        await call.answer("Бронь отклонена.")
        return

    # 6. ВОЗВРАТ 70% ВЛАДЕЛЬЦУ (ПРАВИЛО МЕНЕЕ 1.5 ЧАСА)
    elif action == "refund":
        refund_amount = round(total_num * 0.7)
        fee_amount = total_num - refund_amount
        cursor.execute("UPDATE bookings SET status = 'refund_pending' WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()

        owner_card = (
            f"🚨 **ЗАЯВКА НА ВОЗВРАТ ДЕПОЗИТА (70%)**\n\n"
            f"👤 Гость: **{name}**\n"
            f"📞 Телефон для СБП: `{phone}`\n"
            f"📍 Стол: {table_name} ({booking_date} в {time_slot})\n"
            f"💰 Внесённый депозит: {total_num:,.0f} ₽\n"
            f"📌 Удержание за отмену (30%): {fee_amount:,.0f} ₽\n"
            f"💸 **СУММА К ВОЗВРАТУ: {refund_amount:,.0f} ₽**\n\n"
            f"*(Переведите средства гостю по СБП и подтвердите кнопкой ниже)*"
        )
        owner_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Возврат выполнен (СБП)", callback_data=f"owner_refund_done_{booking_id}")]
        ])

        if OWNER_ID:
            try:
                await bot.send_message(OWNER_ID, owner_card, parse_mode="Markdown", reply_markup=owner_kb)
            except Exception as e:
                print(f"Не удалось отправить владельцу: {e}")

        await call.message.edit_text(call.message.text + f"\n\n⚠️ **Запрос возврата 70% ({refund_amount:,.0f} ₽) отправлен владельцу заведения.**", parse_mode="Markdown", reply_markup=None)
        
        if chat_id and guest_msg_id:
            try:
                await bot.send_message(chat_id, f"↩️ Ваша бронь отменена. По регламенту заведения сумма к возврату (70%) составляет **{refund_amount:,.0f} ₽**. Ожидайте поступления средств по СБП.", parse_mode="Markdown")
            except Exception:
                pass
        await call.answer("Запрос возврата направлен владельцу!")

async def main():
    print("🚀 Сервер ЦОКОЛЬ и Telegram-бот запущены!")
    
    # Сброс кэша кнопки меню в Telegram
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="💨 Забронировать",
                web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/?v=12")
            )
        )
        print("✅ Системная кнопка меню Telegram обновлена до v=12!")
    except Exception as e:
        print(f"Кнопка меню: {e}")

    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот и сервер остановлены.")