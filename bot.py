import os
import sqlite3
import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
PANEL_API_KEY = os.environ["PANEL_API_KEY"]

BASE_URL = "https://panel.astravionix.site/api/v1"

# Inbound فعال پنل
RESELLER_INBOUND_ID = 3

# ادمین
ADMIN_USERNAME = "Raki_vpn"

# اطلاعات کارت شارژ
CARD_TEXT = (
    "♦️ بلو\n"
    "🌟6219861462979757🌟\n"
    "🌪️امیر حسین آقایی🌪️"
)

DB_FILE = "bot.db"


# =========================================================
# PRICES
# =========================================================

PRICE_NORMAL = 8000
PRICE_RESELLER_1 = 6000
PRICE_RESELLER_2 = 3200


# =========================================================
# STATES
# =========================================================

CHARGE_AMOUNT, CHARGE_RECEIPT = range(2)


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    # USERS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            balance INTEGER DEFAULT 0,
            reseller_level INTEGER DEFAULT 0,
            admin_chat_id INTEGER
        )
    """)

    # Migration برای دیتابیس قدیمی
    cur.execute("PRAGMA table_info(users)")
    columns = {row["name"] for row in cur.fetchall()}

    if "reseller_level" not in columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN reseller_level INTEGER DEFAULT 0"
        )

    if "admin_chat_id" not in columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN admin_chat_id INTEGER"
        )

    # CHARGE REQUESTS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS charge_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # USER SERVICES
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL UNIQUE,
            traffic_gb INTEGER,
            duration_days INTEGER,
            price INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# USER FUNCTIONS
# =========================================================

def ensure_user(user):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO users
        (
            telegram_id,
            username,
            balance,
            reseller_level
        )
        VALUES (?, ?, 0, 0)
    """, (
        user.id,
        user.username or ""
    ))

    cur.execute("""
        UPDATE users
        SET username=?
        WHERE telegram_id=?
    """, (
        user.username or "",
        user.id
    ))

    # اگر این کاربر ادمین است، Chat ID ذخیره شود
    if (user.username or "").lower() == ADMIN_USERNAME.lower():

        cur.execute("""
            UPDATE users
            SET admin_chat_id=?
            WHERE telegram_id=?
        """, (
            user.id,
            user.id
        ))

    conn.commit()
    conn.close()


def get_balance(telegram_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT balance
        FROM users
        WHERE telegram_id=?
    """, (
        telegram_id,
    ))

    row = cur.fetchone()

    conn.close()

    if row:
        return int(row["balance"])

    return 0


def get_reseller_level(telegram_id):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT reseller_level
        FROM users
        WHERE telegram_id=?
    """, (
        telegram_id,
    ))

    row = cur.fetchone()

    conn.close()

    if row:
        return int(row["reseller_level"])

    return 0


def get_price_per_gb(telegram_id):

    level = get_reseller_level(telegram_id)

    if level == 2:
        return PRICE_RESELLER_2

    if level == 1:
        return PRICE_RESELLER_1

    return PRICE_NORMAL


def get_role_text(telegram_id):

    level = get_reseller_level(telegram_id)

    if level == 2:
        return "🥇 نماینده سطح ۲"

    if level == 1:
        return "🥈 نماینده سطح ۱"

    return "👤 کاربر عادی"


# =========================================================
# WALLET FUNCTIONS
# =========================================================

def change_balance(telegram_id, amount):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE telegram_id=?
    """, (
        amount,
        telegram_id
    ))

    conn.commit()
    conn.close()


def try_charge_balance(telegram_id, amount):

    conn = db()
    cur = conn.cursor()

    try:

        cur.execute("BEGIN IMMEDIATE")

        cur.execute("""
            UPDATE users
            SET balance = balance - ?
            WHERE telegram_id=?
            AND balance >= ?
        """, (
            amount,
            telegram_id,
            amount
        ))

        success = cur.rowcount == 1

        conn.commit()

        return success

    except Exception:

        conn.rollback()
        raise

    finally:

        conn.close()


# =========================================================
# SERVICE DATABASE
# =========================================================

def save_service(
    telegram_id,
    service_id,
    traffic_gb,
    duration_days,
    price
):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO user_services
        (
            telegram_id,
            service_id,
            traffic_gb,
            duration_days,
            price
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        telegram_id,
        service_id,
        traffic_gb,
        duration_days,
        price
    ))

    conn.commit()
    conn.close()


def get_user_service_ids(telegram_id):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            service_id,
            traffic_gb,
            duration_days,
            price
        FROM user_services
        WHERE telegram_id=?
        ORDER BY id DESC
    """, (
        telegram_id,
    ))

    rows = cur.fetchall()

    conn.close()

    return rows


# =========================================================
# ADMIN
# =========================================================

def get_admin_chat_id():

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT admin_chat_id
        FROM users
        WHERE LOWER(username)=LOWER(?)
        AND admin_chat_id IS NOT NULL
        LIMIT 1
    """, (
        ADMIN_USERNAME,
    ))

    row = cur.fetchone()

    conn.close()

    if row:
        return int(row["admin_chat_id"])

    return None


# =========================================================
# ASTRAVIONIX API
# =========================================================

async def api_request(method, endpoint, **kwargs):

    headers = {
        "X-API-Key": PANEL_API_KEY,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:

        response = await client.request(
            method,
            BASE_URL + endpoint,
            headers=headers,
            **kwargs
        )

    print("API STATUS:", response.status_code)
    print("API RESPONSE:", response.text[:2000])

    response.raise_for_status()

    if not response.text:
        return {}

    return response.json()


# =========================================================
# MAIN MENU
# =========================================================

def main_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "🔐 خرید اشتراک",
                callback_data="buy"
            ),
            InlineKeyboardButton(
                "♻️ تمدید سرویس",
                callback_data="renew"
            ),
        ],

        [
            InlineKeyboardButton(
                "🏦 کیف پول + شارژ",
                callback_data="wallet"
            ),
            InlineKeyboardButton(
                "🛡️ سرویس های من",
                callback_data="services"
            ),
        ],

        [
            InlineKeyboardButton(
                "📚 آموزش",
                callback_data="help"
            ),
            InlineKeyboardButton(
                "☎️ پشتیبانی",
                callback_data="support"
            ),
        ],

        [
            InlineKeyboardButton(
                "👥 زیر مجموعه گیری",
                callback_data="referral"
            ),
            InlineKeyboardButton(
                "👨‍💻 پنل نمایندگی",
                callback_data="reseller"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ensure_user(update.effective_user)

    user = update.effective_user

    if (user.username or "").lower() == ADMIN_USERNAME.lower():

        await update.message.reply_text(
            "👨‍💻 پنل ادمین فعال شد.\n\n"
            "مدیریت نماینده‌ها:\n\n"
            "/setrep TELEGRAM_ID LEVEL\n\n"
            "LEVEL:\n"
            "0 = کاربر عادی\n"
            "1 = نماینده سطح ۱ → ۶۰۰۰ تومان/GB\n"
            "2 = نماینده سطح ۲ → ۳۲۰۰ تومان/GB"
        )

    await update.message.reply_text(
        "🏠 به صفحه اصلی خوش آمدید!\n\n"
        "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=main_menu()
    )


# =========================================================
# ADMIN SET REPRESENTATIVE
# =========================================================

async def set_rep(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if (user.username or "").lower() != ADMIN_USERNAME.lower():

        await update.message.reply_text(
            "⛔ دسترسی ندارید."
        )

        return

    if len(context.args) != 2:

        await update.message.reply_text(
            "فرمت صحیح:\n\n"
            "/setrep TELEGRAM_ID LEVEL\n\n"
            "مثال:\n"
            "/setrep 123456789 1"
        )

        return

    try:

        telegram_id = int(context.args[0])
        level = int(context.args[1])

        if level not in (0, 1, 2):
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ اطلاعات نامعتبر است.\n"
            "LEVEL باید 0 یا 1 یا 2 باشد."
        )

        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO users
        (
            telegram_id,
            username,
            balance,
            reseller_level
        )
        VALUES (?, '', 0, 0)
    """, (
        telegram_id,
    ))

    cur.execute("""
        UPDATE users
        SET reseller_level=?
        WHERE telegram_id=?
    """, (
        level,
        telegram_id
    ))

    conn.commit()
    conn.close()

    role = {
        0: "👤 کاربر عادی — ۸,۰۰۰ تومان/GB",
        1: "🥈 نماینده سطح ۱ — ۶,۰۰۰ تومان/GB",
        2: "🥇 نماینده سطح ۲ — ۳,۲۰۰ تومان/GB",
    }[level]

    await update.message.reply_text(
        "✅ سطح کاربر تغییر کرد.\n\n"
        f"🆔 ID: {telegram_id}\n"
        f"📌 سطح: {role}"
    )


# =========================================================
# HOME
# =========================================================

async def home(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🏠 صفحه اصلی:",
        reply_markup=main_menu()
    )


# =========================================================
# WALLET
# =========================================================

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    ensure_user(query.from_user)

    balance = get_balance(query.from_user.id)
    role = get_role_text(query.from_user.id)
    price = get_price_per_gb(query.from_user.id)

    keyboard = [

        [
            InlineKeyboardButton(
                "💳 شارژ کیف پول",
                callback_data="charge"
            )
        ],

        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="home"
            )
        ],

    ]

    await query.edit_message_text(

        f"🏦 کیف پول شما\n\n"
        f"💰 موجودی: {balance:,} تومان\n"
        f"👤 نوع حساب: {role}\n"
        f"📦 قیمت هر GB: {price:,} تومان",

        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# CHARGE START
# =========================================================

async def charge_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "💳 مبلغ شارژ کیف پول را به تومان وارد کنید.\n\n"
        "مثلاً:\n"
        "100000"
    )

    return CHARGE_AMOUNT


# =========================================================
# CHARGE AMOUNT
# =========================================================

async def charge_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        amount = int(
            update.message.text.strip()
        )

        if amount <= 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ مبلغ واردشده معتبر نیست.\n\n"
            "مثلاً 100000 وارد کنید."
        )

        return CHARGE_AMOUNT

    context.user_data["charge_amount"] = amount

    await update.message.reply_text(

        "💳 لطفاً مبلغ زیر را به کارت زیر واریز کنید:\n\n"

        f"{CARD_TEXT}\n\n"

        f"💰 مبلغ: {amount:,} تومان\n\n"

        "📸 بعد از واریز، تصویر رسید را همینجا ارسال کنید."
    )

    return CHARGE_RECEIPT


# =========================================================
# CHARGE RECEIPT
# =========================================================

async def charge_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.photo:

        await update.message.reply_text(
            "📸 لطفاً تصویر رسید واریز را ارسال کنید."
        )

        return CHARGE_RECEIPT

    user = update.effective_user

    ensure_user(user)

    amount = context.user_data.get(
        "charge_amount"
    )

    if not amount:

        await update.message.reply_text(
            "❌ درخواست شارژ منقضی شده است."
        )

        return ConversationHandler.END

    admin_chat_id = get_admin_chat_id()

    if not admin_chat_id:

        await update.message.reply_text(
            "⚠️ ادمین هنوز ربات را فعال نکرده است.\n\n"
            "ادمین باید یک‌بار /start را برای ربات ارسال کند."
        )

        return ConversationHandler.END

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO charge_requests
        (
            telegram_id,
            amount,
            status
        )
        VALUES (?, ?, 'pending')
    """, (
        user.id,
        amount
    ))

    request_id = cur.lastrowid

    conn.commit()
    conn.close()

    admin_message = (

        "💳 درخواست شارژ جدید\n\n"

        f"🆔 درخواست: {request_id}\n"

        f"👤 کاربر: {user.first_name}\n"

        f"🔹 Username: "
        f"@{user.username if user.username else 'ندارد'}\n"

        f"🆔 Telegram ID: {user.id}\n"

        f"💰 مبلغ: {amount:,} تومان\n\n"

        "رسید را بررسی کنید."
    )

    keyboard = InlineKeyboardMarkup([

        [

            InlineKeyboardButton(
                "✅ تأیید",
                callback_data=f"approve_{request_id}"
            ),

            InlineKeyboardButton(
                "❌ رد",
                callback_data=f"reject_{request_id}"
            ),

        ]

    ])

    try:

        await context.bot.send_photo(

            chat_id=admin_chat_id,

            photo=update.message.photo[-1].file_id,

            caption=admin_message,

            reply_markup=keyboard
        )

    except Exception as e:

        print(
            "ADMIN SEND ERROR:",
            repr(e)
        )

        conn = db()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM charge_requests
            WHERE id=?
            AND status='pending'
        """, (
            request_id,
        ))

        conn.commit()
        conn.close()

        await update.message.reply_text(
            "❌ ارسال رسید به ادمین انجام نشد."
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "✅ رسید شما برای ادمین ارسال شد.\n\n"
        "⏳ بعد از تأیید، مبلغ به کیف پول شما اضافه می‌شود."
    )

    context.user_data.clear()

    return ConversationHandler.END


# =========================================================
# ADMIN CHARGE ACTION
# =========================================================

async def admin_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    username = query.from_user.username or ""

    if username.lower() != ADMIN_USERNAME.lower():

        await query.answer(
            "⛔ شما دسترسی ادمین ندارید.",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data

    action, request_id_text = data.split("_", 1)

    request_id = int(request_id_text)

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            telegram_id,
            amount,
            status
        FROM charge_requests
        WHERE id=?
    """, (
        request_id,
    ))

    row = cur.fetchone()

    if not row:

        conn.close()

        await query.edit_message_caption(
            caption="❌ درخواست پیدا نشد."
        )

        return

    telegram_id = int(row["telegram_id"])
    amount = int(row["amount"])
    status = row["status"]

    if status != "pending":

        conn.close()

        await query.answer(
            "این درخواست قبلاً بررسی شده.",
            show_alert=True
        )

        return

    # APPROVE
    if action == "approve":

        try:

            cur.execute("BEGIN IMMEDIATE")

            cur.execute("""
                UPDATE charge_requests
                SET status='approved'
                WHERE id=?
                AND status='pending'
            """, (
                request_id,
            ))

            cur.execute("""
                UPDATE users
                SET balance=balance+?
                WHERE telegram_id=?
            """, (
                amount,
                telegram_id
            ))

            conn.commit()

        except Exception as e:

            conn.rollback()

            print(
                "APPROVE ERROR:",
                repr(e)
            )

            conn.close()

            await query.answer(
                "❌ خطا در تأیید.",
                show_alert=True
            )

            return

        conn.close()

        new_balance = get_balance(
            telegram_id
        )

        try:

            await context.bot.send_mes
