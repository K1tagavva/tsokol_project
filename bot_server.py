import os
import sys
import json
import asyncio
import sqlite3
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
    MenuButtonDefault
)
from aiogram.client.session.aiohttp import AiohttpSession

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW.isdigit() else (ADMIN_IDS[0] if ADMIN_IDS else 0)

REQUIRE_SBP_DEPOSIT = True

DB_NAME = "cokol.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            chat_id INTEGER,
            guest_msg_id INTEGER,
            name TEXT,
            username TEXT,
            phone TEXT,
            table_id TEXT,
            table_name TEXT,
            time_slot TEXT,
            end_time TEXT,
            booking_date TEXT,
            date_iso TEXT,
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS loyalty_clients (
            phone TEXT PRIMARY KEY,
            telegram_id INTEGER,
            name TEXT,
            spent REAL DEFAULT 0,
            balance REAL DEFAULT 0,
            tier TEXT DEFAULT 'SILVER'
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS table_schedules (
            date_iso TEXT,
            time_slot TEXT,
            table_id TEXT,
            status TEXT,
            booking_id TEXT,
            PRIMARY KEY (date_iso, time_slot, table_id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

session = None
try:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    if s.connect_ex(("127.0.0.1", 10809)) == 0:
        session = AiohttpSession(proxy="http://127.0.0.1:10809")
    s.close()
except Exception:
    pass

bot = Bot(token=BOT_TOKEN, session=session) if session else Bot(token=BOT_TOKEN)
dp = Dispatcher()

app = FastAPI(title="Cokol API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TIME_SLOTS = [
    '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
    '15:00', '15:30', '16:00', '16:30', '17:00', '17:30',
    '18:00', '18:30', '19:00', '19:30', '20:00', '20:30',
    '21:00', '21:30', '22:00', '22:30', '23:00', '23:30',
    '00:00', '00:30', '01:00'
]

class ExtendReq(BaseModel):
    booking_id: str
    extra_minutes: int = 90

class CloseReq(BaseModel):
    booking_id: str
    final_total: float

class StatusReq(BaseModel):
    booking_id: str
    status: str

class ToggleTableReq(BaseModel):
    date_iso: str
    time: str
    table_id: str
    busy: bool

class TableRelReq(BaseModel):
    date_iso: str
    table_id: str
    from_time: str

class ManualBookReq(BaseModel):
    date_iso: str
    date_label: str
    time: str
    end_time: str
    table_id: str
    table_name: str
    name: str
    phone: str
    hookahs: int = 1
    hookah_type: str = "Классика"
    comment: str = ""
    total_num: float = 1500.0

class LoyaltyAdjustReq(BaseModel):
    phone: str
    delta: float

class LoyaltyCheckReq(BaseModel):
    phone: str = ""
    telegram_id: str | None = None

@app.get("/api/schedule")
async def get_schedule(date: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT time_slot, table_id, id FROM bookings WHERE (date_iso = ? OR booking_date LIKE ?) AND status IN ('confirmed', 'seated')", (date, f"%{date[-2:]}%"))
    for t_slot, t_id, b_id in cur.fetchall():
        if t_id and t_slot in TIME_SLOTS:
            idx = TIME_SLOTS.index(t_slot)
            for s in TIME_SLOTS[idx:min(len(TIME_SLOTS), idx + 3)]:
                cur.execute("INSERT OR REPLACE INTO table_schedules VALUES (?, ?, ?, 'busy', ?)", (date, s, t_id, b_id))
    conn.commit()

    cur.execute("SELECT time_slot, table_id, status FROM table_schedules WHERE date_iso = ?", (date,))
    sched = {}
    for ts, tid, st in cur.fetchall():
        sched.setdefault(ts, {})[tid] = st
    conn.close()
    return sched

@app.post("/api/schedule/toggle")
async def toggle_table(req: ToggleTableReq):
    idx = TIME_SLOTS.index(req.time) if req.time in TIME_SLOTS else -1
    slots = TIME_SLOTS[idx:min(len(TIME_SLOTS), idx + 3)] if idx != -1 else [req.time]
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    for s in slots:
        if req.busy:
            cur.execute("INSERT OR REPLACE INTO table_schedules VALUES (?, ?, ?, 'busy', 'manual')", (req.date_iso, s, req.table_id))
        else:
            cur.execute("DELETE FROM table_schedules WHERE date_iso = ? AND time_slot = ? AND table_id = ?", (req.date_iso, s, req.table_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/bookings")
async def get_bookings(date: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, name, phone, table_id, table_name, time_slot, end_time, hookahs, hookah_type, drinks, comment, total_price, total_num, cashback, status, created_at FROM bookings WHERE date_iso = ? OR booking_date LIKE ? ORDER BY created_at DESC", (date, f"%{date[-2:]}%"))
    rows = cur.fetchall()
    conn.close()
    return [{
        "id": r[0], "name": r[1], "phone": r[2], "table_id": r[3], "table_name": r[4],
        "time": r[5], "end_time": r[6], "hookahs": r[7], "hookah_type": r[8],
        "drinks": r[9], "comment": r[10], "total": r[11], "total_num": r[12],
        "cashback": r[13], "status": r[14], "created_at": r[15]
    } for r in rows]

@app.post("/api/bookings/close_visit")
async def close_visit(req: CloseReq):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET status = 'completed', total_num = ?, total_price = ? WHERE id = ?", (req.final_total, f"{req.final_total:,.0f} ₽", req.booking_id))
    cur.execute("DELETE FROM table_schedules WHERE booking_id = ?", (req.booking_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/bookings/extend")
async def extend_booking(req: ExtendReq):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT table_id, end_time, date_iso FROM bookings WHERE id = ?", (req.booking_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"status": "error"}
    table_id, end_time, date_iso = row
    h, m = map(int, end_time.split(':'))
    new_tot = h * 60 + m + req.extra_minutes
    new_end = f"{((new_tot // 60) % 24):02d}:{(new_tot % 60):02d}"
    cur.execute("UPDATE bookings SET end_time = ? WHERE id = ?", (new_end, req.booking_id))
    conn.commit()
    conn.close()
    return {"status": "ok", "new_end_time": new_end}

@app.post("/api/bookings/update_status")
async def update_status(req: StatusReq):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET status = ? WHERE id = ?", (req.status, req.booking_id))
    if req.status in ("completed", "rejected"):
        cur.execute("DELETE FROM table_schedules WHERE booking_id = ?", (req.booking_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/bookings/manual")
async def create_manual(req: ManualBookReq):
    b_id = f"b_man_{int(datetime.now().timestamp())}"
    clean = "".join(filter(str.isdigit, req.phone))
    if len(clean) == 11 and clean[0] in ('7', '8'): clean = "7" + clean[1:]
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bookings (id, chat_id, guest_msg_id, name, username, phone, table_id, table_name, time_slot, end_time, booking_date, date_iso, hookahs, hookah_type, drinks, comment, total_price, total_num, cashback, status)
        VALUES (?, NULL, NULL, ?, 'звонок', ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, 0, 'confirmed')
    """, (b_id, req.name, req.phone, req.table_id, req.table_name, req.time, req.end_time, req.date_label, req.date_iso, req.hookahs, req.hookah_type, req.comment, f"{req.total_num:,.0f} ₽", req.total_num))

    idx = TIME_SLOTS.index(req.time) if req.time in TIME_SLOTS else -1
    slots = TIME_SLOTS[idx:min(len(TIME_SLOTS), idx + 3)] if idx != -1 else [req.time]
    for s in slots:
        cur.execute("INSERT OR REPLACE INTO table_schedules VALUES (?, ?, ?, 'busy', ?)", (date_iso, s, req.table_id, b_id))
    if clean:
        cur.execute("INSERT OR IGNORE INTO loyalty_clients VALUES (?, ?, ?, 0, 0, 'SILVER')", (clean, message.from_user.id, name))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/bookings/release")
async def release_table(req: TableRelReq):
    idx = TIME_SLOTS.index(req.from_time) if req.from_time in TIME_SLOTS else -1
    slots = TIME_SLOTS[idx:] if idx != -1 else [req.from_time]
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    for s in slots:
        cur.execute("DELETE FROM table_schedules WHERE date_iso = ? AND table_id = ? AND time_slot = ?", (req.date_iso, req.table_id, s))
    cur.execute("UPDATE bookings SET status = 'completed' WHERE (date_iso = ? OR booking_date LIKE ?) AND table_id = ? AND status IN ('confirmed', 'seated', 'pending')", (req.date_iso, f"%{req.date_iso[-2:]}%", req.table_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/bookings/clear_tests")
async def clear_tests():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM bookings")
    cur.execute("DELETE FROM table_schedules")
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/loyalty/search")
async def search_loyalty(phone: str):
    clean = "".join(filter(str.isdigit, phone))
    if len(clean) == 11 and clean[0] in ('7', '8'): clean = "7" + clean[1:]
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT phone, name, spent, balance, tier FROM loyalty_clients WHERE phone = ?", (clean,))
    row = cur.fetchone()
    conn.close()
    if row: return {"found": True, "phone": row[0], "name": row[1], "spent": row[2], "balance": row[3], "tier": row[4]}
    return {"found": False}

@app.post("/api/loyalty/adjust")
async def adjust_loyalty(req: LoyaltyAdjustReq):
    clean = "".join(filter(str.isdigit, req.phone))
    if len(clean) == 11 and clean[0] in ('7', '8'): clean = "7" + clean[1:]
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE loyalty_clients SET balance = MAX(0, balance + ?) WHERE phone = ?", (req.delta, clean))
    conn.commit()
    cur.execute("SELECT phone, name, balance FROM loyalty_clients WHERE phone = ?", (clean,))
    row = cur.fetchone()
    conn.close()
    if row: return {"status": "ok", "new_balance": row[2]}
    return {"status": "error"}

@app.post("/api/loyalty/check")
async def check_loyalty_endpoint(req: LoyaltyCheckReq):
    clean = "".join(filter(str.isdigit, req.phone))
    if len(clean) == 11 and clean[0] in ('7', '8'): clean = "7" + clean[1:]
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT phone, name, spent, balance, tier FROM loyalty_clients WHERE phone = ?", (clean,))
    row = cur.fetchone()
    if row and req.telegram_id and req.telegram_id.isdigit():
        cur.execute("UPDATE loyalty_clients SET telegram_id = ? WHERE phone = ?", (int(req.telegram_id), clean))
        conn.commit()
    conn.close()
    if row: return {"found": True, "phone": row[0], "name": row[1], "spent": row[2], "balance": row[3], "tier": row[4]}
    return {"found": False, "spent": 0, "balance": 0, "tier": "SILVER"}

def get_main_keyboard(is_admin: bool = False):
    keyboard = [
        [KeyboardButton(text="💨 Забронировать стол", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/?v=55"))]
    ]
    if is_admin:
        keyboard.append([
            KeyboardButton(text="🛋 Шахматка зала (Админ)", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/admin.html?v=55")),
            KeyboardButton(text="📊 Сводка смены")
        ])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    is_admin = message.from_user.id in ADMIN_IDS
    await message.answer(
        f"👋 Приветствуем в **ЦОКОЛЬ Lounge**, {message.from_user.first_name}!\n\n"
        "💨 Атмосферный лаунж-бар в Белгороде (ул. Королёва, 4Д)\n"
        "🎮 Отдельные VIP-комнаты с PlayStation 5 (депозит 2 000 ₽ на покур и бар)\n"
        "🍵 Премиальная китайская чайная карта и крафтовые лимонады\n"
        "🎬 Воскресные кинопоказы на большом экране\n\n"
        "Для выбора стола и бронирования нажмите кнопку ниже 👇",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(is_admin)
    )

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛋 Открыть шахматку столов", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/admin.html?v=55"))]
    ])
    await message.answer("🔐 **Панель управления залом ЦОКОЛЬ**\nПароль администратора: `tcklbel723daqw`", parse_mode="Markdown", reply_markup=kb)

@dp.message(F.text == "📊 Сводка смены")
async def cmd_summary(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), SUM(total_num) FROM bookings WHERE status IN ('confirmed', 'seated', 'completed')")
    c_count, c_sum = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM bookings WHERE status = 'pending'")
    p_count = cur.fetchone()[0] or 0
    conn.close()
    await message.answer(
        f"📊 **Текущая сводка смены (ЦОКОЛЬ Lounge)**\n\n"
        f"✅ Принятых броней: **{c_count or 0}**\n"
        f"💰 Оборот депозитов/чеков: **{(c_sum or 0):,.0f} ₽**\n"
        f"⏳ Ожидают проверки СБП: **{p_count}**\n\n"
        f"📅 Отчёт сформирован: {datetime.now().strftime('%H:%M, %d.%m.%Y')}",
        parse_mode="Markdown"
    )

@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        booking_id = data.get("id", f"b_{int(datetime.now().timestamp())}")
        name = data.get("name", message.from_user.first_name)
        username = message.from_user.username or "нет"
        phone = data.get("phone", "")
        table_id = data.get("tableId", "t1")
        table_name = data.get("tableName", "Стол")
        time_slot = data.get("time", "18:00")
        end_time = data.get("endTime", "19:30")
        booking_date = data.get("date", "Сегодня")
        date_iso = data.get("dateIso", datetime.now().strftime("%Y-%m-%d"))
        hookahs = data.get("hookahs", 1)
        hookah_type = data.get("hookahType", "Классика")
        drinks = data.get("drinks", "")
        comment = data.get("comment", "")
        total_price = data.get("total", "1 500 ₽")
        total_num = float(data.get("totalNum", 1500))
        cashback = float(data.get("cashbackEarned", 45))

        clean = "".join(filter(str.isdigit, phone))
        if len(clean) == 11 and clean[0] in ('7', '8'): clean = "7" + clean[1:]

        initial_status = 'pending' if REQUIRE_SBP_DEPOSIT else 'confirmed'
        status_text = "🔎 *Администратор сверяет платёж СБП. Статус обновится здесь автоматически.*" if REQUIRE_SBP_DEPOSIT else "✅ *Бронь подтверждена! Оплата счёта при визите на баре через терминал.*"

        guest_msg_text = (
            f"⏳ **Заявка на бронирование передана в ЦОКОЛЬ Lounge!**\n\n"
            f"📍 **{table_name}**\n"
            f"⏰ Сеанс: **{booking_date} в {time_slot} – {end_time}**\n"
            f"💨 Кальяны: **{hookahs} шт ({hookah_type})**\n"
            f"{f'🍵 Напитки: {drinks}\n' if drinks else ''}"
            f"{f'💬 Пожелания: «{comment}»\n' if comment else ''}"
            f"💳 Депозит брони: **{total_price}**\n\n"
            f"{status_text}"
        )
        sent = await message.answer(guest_msg_text, parse_mode="Markdown")

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO bookings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (booking_id, message.chat.id, sent.message_id, name, username, phone, table_id, table_name, time_slot, end_time, booking_date, date_iso, hookahs, hookah_type, drinks, comment, total_price, total_num, cashback, initial_status))

        idx = TIME_SLOTS.index(time_slot) if time_slot in TIME_SLOTS else -1
        slots = TIME_SLOTS[idx:min(len(TIME_SLOTS), idx + 3)] if idx != -1 else [time_slot]
        for s in slots:
            cur.execute("INSERT OR REPLACE INTO table_schedules VALUES (?, ?, ?, 'busy', ?)", (date_iso, s, table_id, booking_id))
        if clean:
            cur.execute("INSERT OR IGNORE INTO loyalty_clients VALUES (?, ?, ?, 0, 0, 'SILVER')", (clean, message.from_user.id, name))
        conn.commit()
        conn.close()

        admin_card = (
            f"🔔 **НОВАЯ БРОНЬ СТОЛА ({'Ожидает СБП' if REQUIRE_SBP_DEPOSIT else 'Терминал / Без предоплаты'})**\n\n"
            f"👤 Гость: **{name}** (@{username})\n"
            f"📞 Телефон: `{phone}`\n"
            f"📍 Стол: **{table_name}**\n"
            f"⏰ Время: **{booking_date} в {time_slot} – {end_time}**\n"
            f"💨 Кальяны: **{hookahs} шт ({hookah_type})**\n"
            f"{f'🍵 Бар: {drinks}\n' if drinks else ''}"
            f"{f'💬 Комментарий: «{comment}»\n' if comment else ''}"
            f"💰 Сумма: **{total_price}**"
        )
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"ok_{booking_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no_{booking_id}")
        ]])
        for aid in ADMIN_IDS:
            try: await bot.send_message(aid, admin_card, parse_mode="Markdown", reply_markup=admin_kb)
            except Exception: pass
    except Exception as e:
        print(f"Error webapp_data: {e}")

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def handle_admin_decision(call: types.CallbackQuery):
    action, booking_id = call.data.split("_")[0], "_".join(call.data.split("_")[1:])
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, guest_msg_id, name, phone, table_name, time_slot, end_time, booking_date, total_num FROM bookings WHERE id = ?", (booking_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await call.answer("Заявка не найдена в базе")
        return
    chat_id, guest_msg_id, name, phone, table_name, time_slot, end_time, booking_date, total_num = row

    if action == "ok":
        cur.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()
        if chat_id and guest_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=guest_msg_id,
                    text=f"🎉 **Бронь подтверждена! Ждём вас в ЦОКОЛЬ Lounge**\n\n📍 **{table_name}**\n⏰ Сеанс: **{booking_date} в {time_slot} – {end_time}**\n💳 Бронь зафиксирована. Оплата счёта на баре при визите.",
                    parse_mode="Markdown"
                )
            except Exception: pass
        await call.message.edit_text(call.message.text + "\n\n🟢 **Бронь подтверждена.** Стол зафиксирован.", parse_mode="Markdown", reply_markup=None)
        await call.answer("Подтверждено!")
    else:
        cur.execute("UPDATE bookings SET status = 'rejected' WHERE id = ?", (booking_id,))
        cur.execute("DELETE FROM table_schedules WHERE booking_id = ?", (booking_id,))
        conn.commit()
        conn.close()
        if chat_id and guest_msg_id:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=guest_msg_id, text="❌ **Бронирование отменено.**", parse_mode="Markdown")
            except Exception: pass
        await call.message.edit_text(call.message.text + "\n\n❌ **Бронь отклонена.** Стол свободен.", parse_mode="Markdown", reply_markup=None)
        await call.answer("Отклонено.")

async def main():
    print("🚀 Сервер ЦОКОЛЬ и Telegram-бот запущены!")
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    except Exception:
        pass

    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот остановлен.")