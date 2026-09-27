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

                    try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=(
                    "✅ شارژ کیف پول تأیید شد.\n\n"
                    f"💰 مبلغ افزوده‌شده: {amount:,} تومان\n"
                    f"🏦 موجودی جدید: {new_balance:,} تومان"
                )
            )

        except Exception as e:
            print(
                "USER NOTIFICATION ERROR:",
                repr(e)
            )

        await query.edit_message_caption(
            caption=(
                "✅ شارژ تأیید شد.\n\n"
                f"💰 مبلغ: {amount:,} تومان\n"
                f"🏦 موجودی کاربر: {new_balance:,} تومان"
            )
        )

        return

    # =====================================================
    # REJECT
    # =====================================================

    if action == "reject":

        cur.execute("""
            UPDATE charge_requests
            SET status='rejected'
            WHERE id=?
            AND status='pending'
        """, (
            request_id,
        ))

        conn.commit()
        conn.close()

        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=(
                    "❌ درخواست شارژ شما رد شد.\n\n"
                    f"💰 مبلغ: {amount:,} تومان\n\n"
                    "در صورت اشتباه، با پشتیبانی تماس بگیرید."
                )
            )

        except Exception as e:
            print(
                "USER NOTIFICATION ERROR:",
                repr(e)
            )

        await query.edit_message_caption(
            caption=(
                "❌ شارژ رد شد.\n\n"
                f"💰 مبلغ: {amount:,} تومان"
            )
        )


# =========================================================
# BUY
# =========================================================

async def buy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    ensure_user(query.from_user)

    price = get_price_per_gb(
        query.from_user.id
    )

    role = get_role_text(
        query.from_user.id
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "5 GB",
                callback_data="traffic_5"
            ),
            InlineKeyboardButton(
                "10 GB",
                callback_data="traffic_10"
            ),
        ],
        [
            InlineKeyboardButton(
                "20 GB",
                callback_data="traffic_20"
            ),
            InlineKeyboardButton(
                "50 GB",
                callback_data="traffic_50"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="home"
            )
        ],
    ]

    await query.edit_message_text(
        "🔐 خرید اشتراک\n\n"
        f"👤 نوع حساب: {role}\n"
        f"💰 قیمت: {price:,} تومان به ازای هر GB\n\n"
        "📦 حجم سرویس را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# SELECT TRAFFIC
# =========================================================

async def select_traffic(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    traffic = int(
        query.data.split("_")[1]
    )

    context.user_data["traffic_gb"] = traffic

    keyboard = [
        [
            InlineKeyboardButton(
                "30 روز",
                callback_data="days_30"
            ),
            InlineKeyboardButton(
                "60 روز",
                callback_data="days_60"
            ),
        ],
        [
            InlineKeyboardButton(
                "90 روز",
                callback_data="days_90"
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="buy"
            )
        ],
    ]

    await query.edit_message_text(
        f"📦 حجم انتخابی: {traffic} GB\n\n"
        "📅 مدت سرویس را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# SELECT DAYS
# =========================================================

async def select_days(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    days = int(
        query.data.split("_")[1]
    )

    traffic = context.user_data.get(
        "traffic_gb"
    )

    if not traffic:
        await query.edit_message_text(
            "❌ سفارش منقضی شده است.\n"
            "دوباره خرید را شروع کنید.",
            reply_markup=main_menu()
        )
        return

    price_per_gb = get_price_per_gb(
        query.from_user.id
    )

    price = traffic * price_per_gb

    context.user_data["duration_days"] = days
    context.user_data["price"] = price

    balance = get_balance(
        query.from_user.id
    )

    role = get_role_text(
        query.from_user.id
    )

    if balance >= price:

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ تأیید و خرید",
                    callback_data="confirm_buy"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو",
                    callback_data="home"
                )
            ]
        ]

        status_text = "✅ موجودی کافی است."

    else:

        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 شارژ کیف پول",
                    callback_data="charge"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو",
                    callback_data="home"
                )
            ]
        ]

        status_text = "⚠️ موجودی کیف پول کافی نیست."

    await query.edit_message_text(
        "🧾 خلاصه سفارش\n\n"
        f"👤 نوع حساب: {role}\n"
        f"📦 حجم: {traffic} GB\n"
        f"📅 مدت: {days} روز\n"
        f"💰 قیمت هر GB: {price_per_gb:,} تومان\n"
        f"💵 مبلغ نهایی: {price:,} تومان\n"
        f"🏦 موجودی کیف پول: {balance:,} تومان\n\n"
        f"{status_text}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# CONFIRM BUY
# =========================================================

async def confirm_buy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    user = query.from_user
    ensure_user(user)

    telegram_id = user.id

    traffic = context.user_data.get(
        "traffic_gb"
    )

    days = context.user_data.get(
        "duration_days"
    )

    if not traffic or not days:
        await query.edit_message_text(
            "❌ سفارش منقضی شده است.",
            reply_markup=main_menu()
        )
        return

    price = (
        traffic *
        get_price_per_gb(telegram_id)
    )

    if not try_charge_balance(
        telegram_id,
        price
    ):

        await query.edit_message_text(
            "❌ موجودی کافی نیست.\n\n"
            f"💰 مبلغ لازم: {price:,} تومان\n"
            f"🏦 موجودی: "
            f"{get_balance(telegram_id):,} تومان",
            reply_markup=InlineKeyboardMarkup([
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
                ]
            ])
        )

        return

    client_name = (
        user.username
        or str(telegram_id)
    )

    payload = {
        "inbound_id": RESELLER_INBOUND_ID,
        "client_name": client_name,
        "traffic_gb": traffic,
        "duration_days": days
    }

    try:

        result = await api_request(
            "POST",
            "/services",
            json=payload
        )

    except Exception as e:

        print(
            "CREATE SERVICE ERROR:",
            repr(e)
        )

        # برگشت مبلغ
        change_balance(
            telegram_id,
            price
        )

        await query.edit_message_text(
            "❌ ساخت سرویس انجام نشد.\n\n"
            "💰 مبلغ به کیف پول شما برگشت داده شد.",
            reply_markup=main_menu()
        )

        return

    service_id = (
        result.get("id")
        or result.get("service_id")
    )

    if not service_id:

        data = result.get("data")

        if isinstance(data, dict):
            service_id = (
                data.get("id")
                or data.get("service_id")
            )

    if not service_id:

        change_balance(
            telegram_id,
            price
        )

        await query.edit_message_text(
            "❌ پاسخ پنل نامعتبر بود.\n\n"
            "💰 مبلغ به کیف پول برگشت داده شد.",
            reply_markup=main_menu()
        )

        return

    try:
        service_id = int(service_id)

    except Exception:

        change_balance(
            telegram_id,
            price
        )

        await query.edit_message_text(
            "❌ شناسه سرویس نامعتبر بود.\n\n"
            "💰 مبلغ برگشت داده شد.",
            reply_markup=main_menu()
        )

        return

    save_service(
        telegram_id,
        service_id,
        traffic,
        days,
        price
    )

    subscription_text = ""

    try:

        subscription = await api_request(
            "GET",
            f"/services/{service_id}/subscription"
        )

        if isinstance(subscription, dict):

            links = []

            possible_keys = [
                "subscription_url",
                "subscription_link",
                "url",
                "config_url"
            ]

            for key in possible_keys:

                if subscription.get(key):
                    links.append(
                        str(subscription[key])
                    )

            if links:

                subscription_text = (
                    "\n\n🔗 لینک اشتراک:\n"
                    + "\n".join(links)
                )

    except Exception as e:

        print(
            "SUBSCRIPTION ERROR:",
            repr(e)
        )

    context.user_data.clear()

    await query.edit_message_text(
        "🎉 خرید با موفقیت انجام شد!\n\n"
        f"📦 حجم: {traffic} GB\n"
        f"📅 مدت: {days} روز\n"
        f"💰 هزینه: {price:,} تومان\n"
        f"🆔 Service ID: {service_id}"
        f"{subscription_text}",
        reply_markup=main_menu()
    )


# =========================================================
# MY SERVICES
# =========================================================

async def services(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    rows = get_user_service_ids(
        query.from_user.id
    )

    if not rows:

        await query.edit_message_text(
            "🛡️ هنوز سرویسی از این ربات خریداری نکرده‌اید.",
            reply_markup=main_menu()
        )

        return

    parts = [
        "🛡️ سرویس‌های من"
    ]

    for row in rows[:10]:

        service_id = int(
            row["service_id"]
        )

        service_text = (
            f"🆔 {service_id}\n"
            f"📦 حجم: {row['traffic_gb']} GB\n"
            f"📅 مدت: {row['duration_days']} روز\n"
            f"💰 قیمت: {row['price']:,} تومان"
        )

        try:

            status = await api_request(
                "GET",
                f"/services/{service_id}"
            )

            if isinstance(status, dict):

                current_status = (
                    status.get("status")
                    or status.get("state")
                )

                if current_status:
                    service_text += (
                        f"\n📌 وضعیت: {current_status}"
                    )

        except Exception as e:

            print(
                "SERVICE STATUS ERROR:",
                repr(e)
            )

        parts.append(service_text)

    await query.edit_message_text(
        "\n\n".join(parts),
        reply_markup=main_menu()
    )


# =========================================================
# RENEW
# =========================================================

async def renew(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "♻️ تمدید سرویس\n\n"
        "این بخش فعلاً در حال تکمیل است.",
        reply_markup=main_menu()
    )


# =========================================================
# HELP
# =========================================================

async def help_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "📚 آموزش\n\n"
        "1️⃣ ابتدا کیف پول خود را شارژ کنید.\n\n"
        "2️⃣ حجم سرویس را انتخاب کنید.\n\n"
        "3️⃣ مدت سرویس را انتخاب کنید.\n\n"
        "4️⃣ خرید را تأیید کنید.\n\n"
        "5️⃣ سرویس از پنل ساخته می‌شود.",
        reply_markup=main_menu()
    )


# =========================================================
# SUPPORT
# =========================================================

async def support(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "☎️ پشتیبانی\n\n"
        f"برای پشتیبانی به ادمین پیام دهید:\n"
        f"@{ADMIN_USERNAME}",
        reply_markup=main_menu()
    )


# =========================================================
# REFERRAL
# =========================================================

async def referral(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "👥 زیرمجموعه‌گیری\n\n"
        "این بخش در حال تکمیل است.",
        reply_markup=main_menu()
    )


# =========================================================
# RESELLER
# =========================================================

async def reseller(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    telegram_id = query.from_user.id

    level = get_reseller_level(
        telegram_id
    )

    if level == 0:

        text = (
            "👤 حساب عادی\n\n"
            "💰 قیمت: ۸,۰۰۰ تومان/GB\n\n"
            "برای دریافت نمایندگی با پشتیبانی تماس بگیرید."
        )

    elif level == 1:

        text = (
            "🥈 نماینده سطح ۱\n\n"
            "💰 قیمت شما: ۶,۰۰۰ تومان/GB"
        )

    else:

        text = (
            "🥇 نماینده سطح ۲\n\n"
            "💰 قیمت شما: ۳,۲۰۰ تومان/GB"
        )

    await query.edit_message_text(
        text,
        reply_markup=main_menu()
    )


# =========================================================
# CANCEL
# =========================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    if update.message:

        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=main_menu()
        )

    return ConversationHandler.END


# =========================================================
# MAIN
# =========================================================

def main():

    init_db()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    charge_conversation = ConversationHandler(

        entry_points=[
            CallbackQueryHandler(
                charge_start,
                pattern=r"^charge$"
            )
        ],

        states={

            CHARGE_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    charge_amount
                )
            ],

            CHARGE_RECEIPT: [
                MessageHandler(
                    filters.PHOTO,
                    charge_receipt
                )
            ]

        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel
            )
        ]
    )

    # Commands
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "setrep",
            set_rep
        )
    )

    app.add_handler(
        CommandHandler(
            "cancel",
            cancel
        )
    )

    # Charge conversation
    app.add_handler(
        charge_conversation
    )

    # Admin
    app.add_handler(
        CallbackQueryHandler(
            admin_action,
            pattern=r"^(approve|reject)_\d+$"
        )
    )

    # Buy
    app.add_handler(
        CallbackQueryHandler(
            confirm_buy,
            pattern=r"^confirm_buy$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            select_traffic,
            pattern=r"^traffic_\d+$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            select_days,
            pattern=r"^days_\d+$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            buy,
            pattern=r"^buy$"
        )
    )

    # Wallet
    app.add_handler(
        CallbackQueryHandler(
            wallet,
            pattern=r"^wallet$"
        )
    )

    # Services
    app.add_handler(
        CallbackQueryHandler(
            services,
            pattern=r"^services$"
        )
    )

    # Other
    app.add_handler(
        CallbackQueryHandler(
            renew,
            pattern=r"^renew$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            help_page,
            pattern=r"^help$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            support,
            pattern=r"^support$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            referral,
            pattern=r"^referral$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            reseller,
            pattern=r"^reseller$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            home,
            pattern=r"^home$"
        )
    )

    print("BOT STARTED")

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
