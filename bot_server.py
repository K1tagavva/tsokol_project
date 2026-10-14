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

    cursor.execute("PRAGMA table_info(bookings)")
    cols = [c[1] for c in cursor.fetchall()]
    for col_name, col_type in [
        ("chat_id", "INTEGER"), 
        ("guest_msg_id", "INTEGER"), 
        ("table_id", "TEXT"),
        ("end_time", "TEXT"),
        ("date_iso", "TEXT"),
        ("total_num", "REAL DEFAULT 0"), 
        ("cashback", "REAL DEFAULT 0")
    ]:
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

    cursor.execute("""
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

app = FastAPI(title="Cokol Lounge Full Admin API")
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

class LoyaltyAdjustRequest(BaseModel):
    phone: str
    delta: float

class ManualBookingRequest(BaseModel):
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

class TableReleaseRequest(BaseModel):
    date_iso: str
    table_id: str
    from_time: str

class ToggleTableRequest(BaseModel):
    date_iso: str
    time: str
    table_id: str
    busy: bool

class UpdateStatusRequest(BaseModel):
    booking_id: str
    status: str

class CloseVisitRequest(BaseModel):
    booking_id: str
    final_total: float

class ExtendBookingRequest(BaseModel):
    booking_id: str
    extra_minutes: int = 90
    extra_amount: float = 1500.0

# API ЭНДПОИНТЫ
@app.get("/api/schedule")
async def get_schedule(date: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT time_slot, table_id, id FROM bookings
        WHERE (date_iso = ? OR booking_date LIKE ?)
          AND status IN ('confirmed', 'seated')
    """, (date, f"%{date[-2:]}%"))
    active_bookings = cursor.fetchall()

    time_slots = [
        '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
        '15:00', '15:30', '16:00', '16:30', '17:00', '17:30',
        '18:00', '18:30', '19:00', '19:30', '20:00', '20:30',
        '21:00', '21:30', '22:00', '22:30', '23:00', '23:30',
        '00:00', '00:30', '01:00'
    ]

    for t_slot, t_id, b_id in active_bookings:
        if t_id and t_slot in time_slots:
            idx = time_slots.index(t_slot)
            for s in time_slots[idx:min(len(time_slots), idx + 3)]:
                cursor.execute("""
                    INSERT OR REPLACE INTO table_schedules (date_iso, time_slot, table_id, status, booking_id)
                    VALUES (?, ?, ?, 'busy', ?)
                """, (date, s, t_id, b_id))
    conn.commit()

    cursor.execute("SELECT time_slot, table_id, status FROM table_schedules WHERE date_iso = ?", (date,))
    rows = cursor.fetchall()
    conn.close()

    schedule = {}
    for time_slot, table_id, status in rows:
        if time_slot not in schedule:
            schedule[time_slot] = {}
        schedule[time_slot][table_id] = status
    return schedule

@app.post("/api/schedule/toggle")
async def toggle_table(req: ToggleTableRequest):
    time_slots = [
        '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
        '15:00', '15:30', '16:00', '16:30', '17:00', '17:30',
        '18:00', '18:30', '19:00', '19:30', '20:00', '20:30',
        '21:00', '21:30', '22:00', '22:30', '23:00', '23:30',
        '00:00', '00:30', '01:00'
    ]
    idx = time_slots.index(req.time) if req.time in time_slots else -1
    slots_to_change = time_slots[idx:min(len(time_slots), idx + 3)] if idx != -1 else [req.time]

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    for s in slots_to_change:
        if req.busy:
            cursor.execute("""
                INSERT OR REPLACE INTO table_schedules (date_iso, time_slot, table_id, status, booking_id)
                VALUES (?, ?, ?, 'busy', 'admin_manual')
            """, (req.date_iso, s, req.table_id))
        else:
            cursor.execute("DELETE FROM table_schedules WHERE date_iso = ? AND time_slot = ? AND table_id = ?", (req.date_iso, s, req.table_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/bookings")
async def get_bookings(date: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, phone, table_id, table_name, time_slot, end_time, hookahs, hookah_type,
               drinks, comment, total_price, total_num, cashback, status, created_at
        FROM bookings
        WHERE date_iso = ? OR booking_date LIKE ?
        ORDER BY created_at DESC
    """, (date, f"%{date[-2:]}%"))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "id": r[0], "name": r[1], "phone": r[2], "table_id": r[3], "table_name": r[4],
            "time": r[5], "end_time": r[6], "hookahs": r[7], "hookah_type": r[8],
            "drinks": r[9], "comment": r[10], "total": r[11], "total_num": r[12],
            "cashback": r[13], "status": r[14], "created_at": r[15]
        })
    return result

@app.post("/api/bookings/close_visit")
async def close_visit_endpoint(req: CloseVisitRequest):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name, phone, table_id, date_iso FROM bookings WHERE id = ?", (req.booking_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return {"status": "error", "message": "Бронь не найдена"}

    name, phone, table_id, date_iso = row
    clean_phone = "".join(filter(str.isdigit, phone or ""))
    if len(clean_phone) == 11 and clean_phone[0] in ('7', '8'):
        clean_phone = "7" + clean_phone[1:]

    # Узнаем текущий статус накоплений клиента
    current_spent = 0.0
    if clean_phone:
        cursor.execute("SELECT spent FROM loyalty_clients WHERE phone = ?", (clean_phone,))
        spent_row = cursor.fetchone()
        if spent_row:
            current_spent = spent_row[0] or 0.0

    rate = 0.07 if current_spent >= 50000 else (0.05 if current_spent >= 25000 else 0.03)
    calc_cb = round(req.final_total * rate)

    # Обновляем бронь
    cursor.execute("""
        UPDATE bookings
        SET status = 'completed',
            total_num = ?,
            total_price = ?,
            cashback = ?
        WHERE id = ?
    """, (req.final_total, f"{req.final_total:,.0f} ₽", calc_cb, req.booking_id))

    # Освобождаем стол в расписании
    cursor.execute("DELETE FROM table_schedules WHERE booking_id = ?", (req.booking_id,))

    # Начисляем баллы
    if clean_phone:
        cursor.execute("""
            INSERT INTO loyalty_clients (phone, telegram_id, name, spent, balance, tier)
            VALUES (?, NULL, ?, ?, ?, 'SILVER')
            ON CONFLICT(phone) DO UPDATE SET
                spent = spent + excluded.spent,
                balance = balance + excluded.balance,
                tier = CASE
                    WHEN (spent + excluded.spent) >= 50000 THEN 'PLATINUM'
                    WHEN (spent + excluded.spent) >= 25000 THEN 'GOLD'
                    ELSE 'SILVER'
                END
        """, (clean_phone, name, req.final_total, calc_cb))

    conn.commit()
    conn.close()
    return {"status": "ok", "cashback": calc_cb, "final_total": req.final_total}

@app.post("/api/bookings/extend")
async def extend_booking_endpoint(req: ExtendBookingRequest):
    time_slots = [
        '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
        '15:00', '15:30', '16:00', '16:30', '17:00', '17:30',
        '18:00', '18:30', '19:00', '19:30', '20:00', '20:30',
        '21:00', '21:30', '22:00', '22:30', '23:00', '23:30',
        '00:00', '00:30', '01:00'
    ]

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT table_id, end_time, date_iso, total_num, hookahs FROM bookings WHERE id = ?", (req.booking_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return {"status": "error", "message": "Бронь не найдена"}

    table_id, end_time, date_iso, total_num, hookahs = row
    start_ext_idx = time_slots.index(end_time) if end_time in time_slots else -1

    if start_ext_idx == -1:
        conn.close()
        return {"status": "error", "message": "Неверное время окончания"}

    ext_slots = time_slots[start_ext_idx:min(len(time_slots), start_ext_idx + 3)]
    for s in ext_slots:
        cursor.execute("SELECT booking_id FROM table_schedules WHERE date_iso = ? AND time_slot = ? AND table_id = ?", (date_iso, s, table_id))
        conflict = cursor.fetchone()
        if conflict and conflict[0] != req.booking_id:
            conn.close()
            return {"status": "collision", "message": f"Слот {s} уже занят другой бронью!"}

    # Блокируем новые слоты
    for s in ext_slots:
        cursor.execute("""
            INSERT OR REPLACE INTO table_schedules (date_iso, time_slot, table_id, status, booking_id)
            VALUES (?, ?, ?, 'busy', ?)
        """, (date_iso, s, table_id, req.booking_id))

    # Рассчитываем новое время окончания (+90 мин)
    h, m = map(int, end_time.split(':'))
    new_end_total_m = h * 60 + m + req.extra_minutes
    new_end_time = f"{((new_end_total_m // 60) % 24):02d}:{(new_end_total_m % 60):02d}"
    new_total_num = (total_num or 0) + req.extra_amount
    new_hookahs = (hookahs or 1) + 1

    cursor.execute("""
        UPDATE bookings
        SET end_time = ?,
            total_num = ?,
            total_price = ?,
            hookahs = ?
        WHERE id = ?
    """, (new_end_time, new_total_num, f"{new_total_num:,.0f} ₽", new_hookahs, req.booking_id))

    conn.commit()
    conn.close()
    return {"status": "ok", "new_end_time": new_end_time, "new_total": new_total_num}

@app.post("/api/bookings/update_status")
async def update_booking_status(req: UpdateStatusRequest):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE bookings SET status = ? WHERE id = ?", (req.status, req.booking_id))
    if req.status in ("completed", "rejected"):
        cursor.execute("DELETE FROM table_schedules WHERE booking_id = ?", (req.booking_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/bookings/manual")
async def create_manual_booking(req: ManualBookingRequest):
    time_slots = [
        '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
        '15:00', '15:30', '16:00', '16:30', '17:00', '17:30',
        '18:00', '18:30', '19:00', '19:30', '20:00', '20:30',
        '21:00', '21:30', '22:00', '22:30', '23:00', '23:30',
        '00:00', '00:30', '01:00'
    ]
    b_id = f"b_man_{int(datetime.now().timestamp())}"
    clean_phone = "".join(filter(str.isdigit, req.phone))
    if len(clean_phone) == 11 and clean_phone[0] in ('7', '8'):
        clean_phone = "7" + clean_phone[1:]

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO bookings (
            id, chat_id, guest_msg_id, name, username, phone, table_id, table_name,
            time_slot, end_time, booking_date, date_iso, hookahs, hookah_type,
            drinks, comment, total_price, total_num, cashback, status
        ) VALUES (?, NULL, NULL, ?, 'звонок', ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, 0, 'confirmed')
    """, (
        b_id, req.name, req.phone, req.table_id, req.table_name,
        req.time, req.end_time, req.date_label, req.date_iso,
        req.hookahs, req.hookah_type, req.comment,
        f"{req.total_num:,.0f} ₽", req.total_num
    ))

    idx = time_slots.index(req.time) if req.time in time_slots else -1
    slots_to_change = time_slots[idx:min(len(time_slots), idx + 3)] if idx != -1 else [req.time]
    for s in slots_to_change:
        cursor.execute("""
            INSERT OR REPLACE INTO table_schedules (date_iso, time_slot, table_id, status, booking_id)
            VALUES (?, ?, ?, 'busy', ?)
        """, (req.date_iso, s, req.table_id, b_id))

    if clean_phone:
        cursor.execute("""
            INSERT OR IGNORE INTO loyalty_clients (phone, telegram_id, name, spent, balance, tier)
            VALUES (?, NULL, ?, 0, 0, 'SILVER')
        """, (clean_phone, req.name))

    conn.commit()
    conn.close()
    return {"status": "ok", "booking_id": b_id}

@app.post("/api/bookings/release")
async def release_table(req: TableReleaseRequest):
    time_slots = [
        '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
        '15:00', '15:30', '16:00', '16:30', '17:00', '17:30',
        '18:00', '18:30', '19:00', '19:30', '20:00', '20:30',
        '21:00', '21:30', '22:00', '22:30', '23:00', '23:30',
        '00:00', '00:30', '01:00'
    ]
    idx = time_slots.index(req.from_time) if req.from_time in time_slots else -1
    slots_to_free = time_slots[idx:] if idx != -1 else [req.from_time]

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    for s in slots_to_free:
        cursor.execute("DELETE FROM table_schedules WHERE date_iso = ? AND table_id = ? AND time_slot = ?", (req.date_iso, req.table_id, s))
    
    cursor.execute("""
        UPDATE bookings SET status = 'completed'
        WHERE (date_iso = ? OR booking_date LIKE ?)
          AND table_id = ?
          AND status IN ('confirmed', 'seated', 'pending')
    """, (req.date_iso, f"%{req.date_iso[-2:]}%", req.table_id))

    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/bookings/clear_tests")
async def clear_tests_endpoint():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bookings")
    cursor.execute("DELETE FROM table_schedules")
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/loyalty/search")
async def search_loyalty(phone: str):
    clean = "".join(filter(str.isdigit, phone))
    if len(clean) == 11 and clean[0] in ('7', '8'):
        clean = "7" + clean[1:]

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT phone, name, spent, balance, tier FROM loyalty_clients WHERE phone = ?", (clean,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return {"found": True, "phone": row[0], "name": row[1], "spent": row[2], "balance": row[3], "tier": row[4]}
    return {"found": False}

@app.post("/api/loyalty/adjust")
async def adjust_loyalty(req: LoyaltyAdjustRequest):
    clean = "".join(filter(str.isdigit, req.phone))
    if len(clean) == 11 and clean[0] in ('7', '8'):
        clean = "7" + clean[1:]

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE loyalty_clients
        SET balance = MAX(0, balance + ?)
        WHERE phone = ?
    """, (req.delta, clean))
    conn.commit()

    cursor.execute("SELECT phone, name, balance FROM loyalty_clients WHERE phone = ?", (clean,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return {"status": "ok", "phone": row[0], "name": row[1], "new_balance": row[2]}
    return {"status": "error", "message": "Клиент не найден"}

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
        [KeyboardButton(text="💨 Забронировать стол", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/?v=19"))]
    ]
    if is_admin:
        keyboard.append([
            KeyboardButton(text="🛋 Шахматка зала (Админ)", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/admin.html?v=19")),
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
        greeting += "\n\n⭐️ *Режим администратора активен.*"
    await message.answer(greeting, parse_mode="Markdown", reply_markup=get_main_keyboard(is_admin))

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав доступа к панели управления.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛋 Открыть шахматку столов", web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/admin.html?v=19"))]
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

        clean_phone = "".join(filter(str.isdigit, phone))
        if len(clean_phone) == 11 and clean_phone[0] in ('7', '8'):
            clean_phone = "7" + clean_phone[1:]

        guest_msg_text = (
            f"⏳ **Заявка на бронирование передана в ЦОКОЛЬ Lounge!**\n\n"
            f"📍 **{table_name}**\n"
            f"⏰ Сеанс: **{booking_date} в {time_slot} – {end_time}**\n"
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
                id, chat_id, guest_msg_id, name, username, phone, table_id, table_name,
                time_slot, end_time, booking_date, date_iso, hookahs, hookah_type,
                drinks, comment, total_price, total_num, cashback, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            booking_id, message.chat.id, sent_guest_msg.message_id, name, username, phone,
            table_id, table_name, time_slot, end_time, booking_date, date_iso,
            hookahs, hookah_type, drinks, comment, total_price, total_num, cashback
        ))

        time_slots = [
            '12:00', '12:30', '13:00', '13:30', '14:00', '14:30',
            '15:00', '15:30', '16:00', '16:30', '17:00', '17:30',
            '18:00', '18:30', '19:00', '19:30', '20:00', '20:30',
            '21:00', '21:30', '22:00', '22:30', '23:00', '23:30',
            '00:00', '00:30', '01:00'
        ]
        idx = time_slots.index(time_slot) if time_slot in time_slots else -1
        slots_to_change = time_slots[idx:min(len(time_slots), idx + 3)] if idx != -1 else [time_slot]
        for s in slots_to_change:
            cursor.execute("""
                INSERT OR REPLACE INTO table_schedules (date_iso, time_slot, table_id, status, booking_id)
                VALUES (?, ?, ?, 'busy', ?)
            """, (date_iso, s, table_id, booking_id))

        if clean_phone:
            cursor.execute("""
                INSERT OR IGNORE INTO loyalty_clients (phone, telegram_id, name, spent, balance, tier)
                VALUES (?, NULL, ?, 0, 0, 'SILVER')
            """, (clean_phone, message.from_user.id, name))
        conn.commit()
        conn.close()

        admin_card = (
            f"🔔 **НОВАЯ БРОНЬ СТОЛА (Ожидает СБП)**\n\n"
            f"👤 Гость: **{name}** (@{username})\n"
            f"📞 Телефон: `{phone}`\n"
            f"📍 Стол: **{table_name}**\n"
            f"⏰ Время: **{booking_date} в {time_slot} – {end_time}**\n"
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
        SELECT chat_id, guest_msg_id, name, phone, table_id, table_name, time_slot, end_time, booking_date, date_iso, total_num, cashback, status
        FROM bookings WHERE id = ?
    """, (booking_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        await call.answer("Заявка не найдена в базе", show_alert=True)
        return

    chat_id, guest_msg_id, name, phone, table_id, table_name, time_slot, end_time, booking_date, date_iso, total_num, cashback, status = row
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
                    f"⏰ Сеанс: **{booking_date} в {time_slot} – {end_time}**\n"
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

    # 2. ПОСАДКА ГОСТЕЙ
    elif action == "seated":
        cursor.execute("UPDATE bookings SET status = 'seated' WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()

        new_text = call.message.text.split("\n\n🟢")[0] + f"\n\n🛋 **Гости в зале.** Текущий счёт: **{total_num:,.0f} ₽**"
        await call.message.edit_text(new_text, parse_mode="Markdown", reply_markup=make_order_keyboard(booking_id))
        await call.answer("Посадка зафиксирована!")
        return

    # 3. ДОЗАКАЗЫ
    elif action == "add":
        sub = parts[1]
        real_id = "_".join(parts[2:])

        add_sum = 1500 if sub == "hookah" else (500 if sub == "tea500" else (700 if sub == "tea700" else 250))
        item_title = "Кальян" if sub == "hookah" else ("Чай" if sub == "tea500" else ("Премиум чай" if sub == "tea700" else "Лимонад"))

        new_total = total_num + add_sum
        new_cashback = cashback + round(add_sum * 0.05)

        cursor.execute("UPDATE bookings SET total_num = ?, cashback = ? WHERE id = ?", (new_total, new_cashback, real_id))
        conn.commit()
        conn.close()

        base_part = call.message.text.split("\n\n🛋")[0]
        new_text = base_part + f"\n\n🛋 **Добавлено: {item_title} (+{add_sum} ₽)**\nИтоговый счёт: **{new_total:,.0f} ₽**"
        await call.message.edit_text(new_text, parse_mode="Markdown", reply_markup=make_order_keyboard(real_id))
        await call.answer(f"Добавлено: {item_title}")
        return

    # 4. ФИНАЛЬНЫЙ РАСЧЕТ И ОСВОБОЖДЕНИЕ СТОЛА
    elif action == "finish":
        cursor.execute("UPDATE bookings SET status = 'completed' WHERE id = ?", (booking_id,))
        cursor.execute("DELETE FROM table_schedules WHERE booking_id = ?", (booking_id,))
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
        await call.answer("Визит завершен!")
        return

    # 5. ОТКЛОНЕНИЕ
    elif action == "no":
        cursor.execute("UPDATE bookings SET status = 'rejected' WHERE id = ?", (booking_id,))
        cursor.execute("DELETE FROM table_schedules WHERE booking_id = ?", (booking_id,))
        conn.commit()
        conn.close()

        if chat_id and guest_msg_id:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=guest_msg_id, text="❌ **Бронирование отменено:** Депозит не поступил.", parse_mode="Markdown")
            except Exception:
                pass

        await call.message.edit_text(call.message.text + "\n\n❌ **Бронь отклонена.** Стол свободен.", parse_mode="Markdown", reply_markup=None)
        await call.answer("Бронь отклонена.")
        return

    # 6. ВОЗВРАТ 70%
    elif action == "refund":
        refund_amount = round(total_num * 0.7)
        fee_amount = total_num - refund_amount
        cursor.execute("UPDATE bookings SET status = 'refund_pending' WHERE id = ?", (booking_id,))
        cursor.execute("DELETE FROM table_schedules WHERE booking_id = ?", (booking_id,))
        conn.commit()
        conn.close()

        owner_card = (
            f"🚨 **ЗАЯВКА НА ВОЗВРАТ ДЕПОЗИТА (70%)**\n\n"
            f"👤 Гость: **{name}**\n"
            f"📞 Телефон для СБП: `{phone}`\n"
            f"💰 Исходный депозит: {total_num:,.0f} ₽\n"
            f"📌 Удержание (30%): {fee_amount:,.0f} ₽\n"
            f"💸 **СУММА К ВОЗВРАТУ: {refund_amount:,.0f} ₽**"
        )
        owner_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Возврат выполнен (СБП)", callback_data=f"owner_refund_done_{booking_id}")]
        ])

        if OWNER_ID:
            try:
                await bot.send_message(OWNER_ID, owner_card, parse_mode="Markdown", reply_markup=owner_kb)
            except Exception:
                pass

        await call.message.edit_text(call.message.text + f"\n\n⚠️ **Запрос возврата 70% ({refund_amount:,.0f} ₽) отправлен владельцу.** Стол освобождён.", parse_mode="Markdown", reply_markup=None)
        await call.answer("Запрос возврата направлен!")

async def main():
    print("🚀 Сервер ЦОКОЛЬ и Telegram-бот запущены!")
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="💨 Забронировать",
                web_app=WebAppInfo(url="https://k1tagavva.github.io/tsokol_project/?v=19")
            )
        )
    except Exception:
        pass

    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот остановлен.")