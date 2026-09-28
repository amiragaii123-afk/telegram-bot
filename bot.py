import os
import sqlite3
from datetime import datetime

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

BOT_TOKEN = os.environ["BOT_TOKEN"]
PANEL_API_KEY = os.environ["PANEL_API_KEY"]

ADMIN_USERNAME = "Raki_vpn"

BASE_URL = "https://panel.astravionix.site/api/v1"
RESELLER_INBOUND_ID = 3

def is_admin(user):
    if not user:
        return False

    username = (user.username or "").strip().lower()

    return username == ADMIN_USERNAME.lower()


def main_menu(user):
    buttons = [
        [
            InlineKeyboardButton(
                "🛒 خرید VPN",
                callback_data="buy"
            ),
            InlineKeyboardButton(
                "💰 کیف پول",
                callback_data="wallet"
            )
        ],
        [
            InlineKeyboardButton(
                "📦 سرویس‌های من",
                callback_data="services"
            ),
            InlineKeyboardButton(
                "🔄 تمدید",
                callback_data="renew"
            )
        ],
        [
            InlineKeyboardButton(
                "🎧 پشتیبانی",
                callback_data="support"
            )
        ]
    ]

    
CARD_TEXT = (
    "💳 اطلاعات کارت برای شارژ کیف پول:\n\n"
    "♦️ بلو\n"
    "🌟6219861462979757🌟\n"
    "🌪️امیر حسین آقایی🌪️"
)

DB_FILE = "bot.db"

PRICE_NORMAL = 8000
PRICE_RESELLER_1 = 6000
PRICE_RESELLER_2 = 3200

CHARGE_AMOUNT, CHARGE_RECEIPT = range(2)


# ================= DATABASE =================

def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER NOT NULL DEFAULT 0,
            reseller_level INTEGER NOT NULL DEFAULT 0,
            admin_chat_id INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS charge_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            service_id TEXT NOT NULL UNIQUE,
            traffic_gb INTEGER NOT NULL,
            duration_days INTEGER NOT NULL,
            price INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def ensure_user(user):
    conn = db()

    conn.execute("""
        INSERT INTO users (telegram_id, username)
        VALUES (?, ?)
        ON CONFLICT(telegram_id)
        DO UPDATE SET username=excluded.username
    """, (
        user.id,
        user.username or ""
    ))

    conn.commit()
    conn.close()


def get_balance(telegram_id):
    conn = db()

    row = conn.execute(
        "SELECT balance FROM users WHERE telegram_id=?",
        (telegram_id,)
    ).fetchone()

    conn.close()

    return int(row["balance"]) if row else 0


def get_level(telegram_id):
    conn = db()

    row = conn.execute(
        "SELECT reseller_level FROM users WHERE telegram_id=?",
        (telegram_id,)
    ).fetchone()

    conn.close()

    return int(row["reseller_level"]) if row else 0


def get_price(telegram_id):
    level = get_level(telegram_id)

    if level == 2:
        return PRICE_RESELLER_2

    if level == 1:
        return PRICE_RESELLER_1

    return PRICE_NORMAL


def role_text(level):
    if level == 2:
        return "نماینده ویژه"

    if level == 1:
        return "نماینده"

    return "کاربر عادی"


def add_balance(telegram_id, amount):
    conn = db()

    conn.execute(
        "UPDATE users SET balance=balance+? WHERE telegram_id=?",
        (amount, telegram_id)
    )

    conn.commit()

    row = conn.execute(
        "SELECT balance FROM users WHERE telegram_id=?",
        (telegram_id,)
    ).fetchone()

    conn.close()

    return int(row["balance"])


def deduct_balance(telegram_id, amount):
    conn = db()

    try:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            "SELECT balance FROM users WHERE telegram_id=?",
            (telegram_id,)
        ).fetchone()

        if not row or int(row["balance"]) < amount:
            conn.rollback()
            return False

        conn.execute(
            "UPDATE users SET balance=balance-? WHERE telegram_id=?",
            (amount, telegram_id)
        )

        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def save_service(
    telegram_id,
    service_id,
    traffic,
    days,
    price
):
    conn = db()

    conn.execute("""
        INSERT OR IGNORE INTO user_services
        (
            telegram_id,
            service_id,
            traffic_gb,
            duration_days,
            price,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        telegram_id,
        str(service_id),
        traffic,
        days,
        price,
        datetime.now().isoformat(timespec="seconds")
    ))

    conn.commit()
    conn.close()


def get_services(telegram_id):
    conn = db()

    rows = conn.execute("""
        SELECT
            service_id,
            traffic_gb,
            duration_days,
            price,
            created_at
        FROM user_services
        WHERE telegram_id=?
        ORDER BY id DESC
    """, (telegram_id,)).fetchall()

    conn.close()

    return rows


def get_admin_chat_id():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT telegram_id
        FROM users
        WHERE lower(username) = lower(?)
        LIMIT 1
    """, (ADMIN_USERNAME,))

    row = cur.fetchone()

    conn.close()

    if row:
        return row[0]

    return None


def save_admin_chat_id(user):
    if not is_admin(user):
        return

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (
            telegram_id,
            username,
            balance,
            reseller_level,
            admin_chat_id
        )
        VALUES (?, ?, 0, 0, ?)
        ON CONFLICT(telegram_id)
        DO UPDATE SET
            username = excluded.username,
            admin_chat_id = excluded.admin_chat_id
    """, (
        user.id,
        user.username or "",
        user.id
    ))

    conn.commit()
    conn.close()

    print("ADMIN CHAT ID SAVED:", user.id)

# ================= API =================

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


# ================= MENU =================



# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ensure_user(user)

    if is_admin(user):
        save_admin_chat_id(user)
        print(
            "MAIN ADMIN LOGIN:",
            user.id,
            user.username
        )

    await update.message.reply_text(
        "سلام 👋\n\n"
        "به ربات فروش VPN خوش آمدید.\n\n"
        "از منوی زیر استفاده کنید:",
        reply_markup=main_menu(user)
    )


# ================= BUY =================

async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("5 GB", callback_data="traffic:5"),
            InlineKeyboardButton("10 GB", callback_data="traffic:10"),
        ],
        [
            InlineKeyboardButton("20 GB", callback_data="traffic:20"),
            InlineKeyboardButton("30 GB", callback_data="traffic:30"),
        ],
        [
            InlineKeyboardButton("40 GB", callback_data="traffic:40"),
            InlineKeyboardButton("50 GB", callback_data="traffic:50"),
        ],
        [
            InlineKeyboardButton("100 GB", callback_data="traffic:100"),
        ],
        [
            InlineKeyboardButton("❌ لغو", callback_data="back")
        ]
    ])

    await query.edit_message_text(
    "📦 حجم سرویس را انتخاب کنید:",
    reply_markup=keyboard
    )

async def traffic_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    traffic = int(query.data.split(":")[1])

    context.user_data["traffic_gb"] = traffic
    context.user_data["duration_days"] = 30

    price_per_gb = get_price(query.from_user.id)
    total = traffic * price_per_gb
    balance = get_balance(query.from_user.id)
    level = get_level(query.from_user.id)

    if balance >= total:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ تأیید و خرید",
                    callback_data="confirm_buy"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو",
                    callback_data="back"
                )
            ]
        ])

        pay_text = "✅ موجودی برای خرید کافی است."
    else:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💳 شارژ کیف پول",
                    callback_data="charge"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو",
                    callback_data="back"
                )
            ]
        ])

        pay_text = "❌ موجودی برای خرید کافی نیست."

    await query.edit_message_text(
        "🛒 خلاصه خرید\n\n"
        f"📦 حجم: {traffic} GB\n"
        "📅 مدت: 30 روز\n"
        f"👤 نوع حساب: {role_text(level)}\n"
        f"💵 قیمت هر GB: {price_per_gb:,} تومان\n"
        f"💰 مبلغ کل: {total:,} تومان\n"
        f"🏦 موجودی: {balance:,} تومان\n\n"
        f"{pay_text}",
        reply_markup=keyboard
    )

    


    query = update.callback_query
    await query.answer()

    days = int(query.data.split(":")[1])
    traffic = int(context.user_data["traffic_gb"])

    context.user_data["duration_days"] = days

    price_per_gb = get_price(query.from_user.id)
    total = traffic * price_per_gb

    balance = get_balance(query.from_user.id)
    level = get_level(query.from_user.id)

    if balance >= total:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ تأیید و خرید",
                    callback_data="confirm_buy"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو",
                    callback_data="back"
                )
            ]
        ])

        pay_text = "✅ موجودی برای خرید کافی است."

    else:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💳 شارژ کیف پول",
                    callback_data="charge"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو",
                    callback_data="back"
                )
            ]
        ])

        pay_text = "❌ موجودی برای خرید کافی نیست."

    await query.edit_message_text(
        "🛒 خلاصه خرید\n\n"
        f"📦 حجم: {traffic} GB\n"
        f"📅 مدت: {days} روز\n"
        f"👤 نوع حساب: {role_text(level)}\n"
        f"💵 قیمت هر GB: {price_per_gb:,} تومان\n"
        f"💰 مبلغ کل: {total:,} تومان\n"
        f"🏦 موجودی: {balance:,} تومان\n\n"
        f"{pay_text}",
        reply_markup=keyboard
    )


async def confirm_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user

    ensure_user(user)

    traffic = int(
        context.user_data.get("traffic_gb", 0)
    )

    days = int(
        context.user_data.get("duration_days", 0)
    )

    if traffic <= 0 or days <= 0:
        await query.edit_message_text(
            "❌ اطلاعات خرید ناقص است."
        )
        return

    price_per_gb = get_price(user.id)
    total = traffic * price_per_gb

    # بدون موجودی کافی، اصلاً API صدا زده نمی‌شود.
    balance = get_balance(user.id)

    if balance < total:
        await query.edit_message_text(
            "❌ موجودی کافی نیست.\n\n"
            f"💰 مبلغ موردنیاز: {total:,} تومان\n"
            f"🏦 موجودی فعلی: {balance:,} تومان",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💳 شارژ کیف پول",
                        callback_data="charge"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 منوی اصلی",
                        callback_data="back"
                    )
                ]
            ])
        )
        return

    await query.edit_message_text(
        "⏳ در حال ثبت خرید..."
    )

    # ابتدا پول کسر می‌شود.
    if not deduct_balance(user.id, total):
        await query.edit_message_text(
            "❌ موجودی کافی نیست."
        )
        return

    client_name = (
        user.username
        or f"user_{user.id}"
    )

    # همان ساختار API قبلی که برای شما کار می‌کرد.
    payload = {
        "reseller_inbound_id": RESELLER_INBOUND_ID,
        "traffic_gb": traffic,
        "duration_days": days,
        "client_name": client_name,
        "max_ip": 0,
        "notes": "",
    }

    try:
        result = await api_request(
            "POST",
            "/services",
            json=payload
        )

        if not result.get("success"):
            add_balance(
                user.id,
                total
            )

            await query.edit_message_text(
                "❌ ساخت سرویس انجام نشد.\n"
                f"{result.get('msg', 'خطای نامشخص')}\n\n"
                "💰 مبلغ به کیف پول شما برگشت داده شد."
            )
            return

        service = result.get(
            "obj",
            {}
        ) or {}

        service_id = (
            service.get("id")
            or service.get("service_id")
        )

        if not service_id:
            add_balance(
                user.id,
                total
            )

            await query.edit_message_text(
                "❌ پنل شماره سرویس را برنگرداند.\n\n"
                "💰 مبلغ به کیف پول شما برگشت داده شد."
            )
            return

        save_service(
            user.id,
            service_id,
            traffic,
            days,
            total
        )

        new_balance = get_balance(
            user.id
        )

        await query.edit_message_text(
            "✅ خرید با موفقیت انجام شد!\n\n"
            f"👤 نام: {client_name}\n"
            f"📦 حجم: {traffic} GB\n"
            f"📅 مدت: {days} روز\n"
            f"💰 مبلغ پرداختی: {total:,} تومان\n"
            f"🏦 موجودی جدید: {new_balance:,} تومان\n"
            f"🆔 شماره سرویس: {service_id}"
        )

        # دریافت لینک اشتراک
        try:
            sub = await api_request(
                "GET",
                f"/services/{service_id}/subscription"
            )

            subscription_url = None

            if isinstance(sub, dict):
                subscription_url = (
                    sub.get("subscription_url")
                    or sub.get("subscription_link")
                    or sub.get("url")
                    or sub.get("config_url")
                )

                if isinstance(
                    sub.get("obj"),
                    dict
                ):
                    obj = sub["obj"]

                    subscription_url = (
                        subscription_url
                        or obj.get("subscription_url")
                        or obj.get("subscription_link")
                        or obj.get("url")
                        or obj.get("config_url")
                    )

            if subscription_url:
                await query.message.reply_text(
                    "🔗 لینک اشتراک:\n\n"
                    f"{subscription_url}"
                )
            else:
                await query.message.reply_text(
                    "🔗 اطلاعات اشتراک:\n\n"
                    f"{sub}"
                )

        except Exception as e:
            print(
                "SUBSCRIPTION ERROR:",
                repr(e)
            )

    except Exception as e:
        print(
            "API ERROR:",
            repr(e)
        )

        # اگر پنل خطا داد، پول برمی‌گردد.
        add_balance(
            user.id,
            total
        )

        await query.edit_message_text(
            "❌ هنگام ارتباط با پنل خطایی رخ داد.\n\n"
            "💰 مبلغ خرید به کیف پول شما برگشت داده شد.\n"
            "جزئیات خطا در لاگ TeamCity ثبت شد."
        )


# ================= WALLET =================

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user

    ensure_user(user)

    balance = get_balance(user.id)
    level = get_level(user.id)
    price = get_price(user.id)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💳 شارژ کیف پول",
                callback_data="charge"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 منوی اصلی",
                callback_data="back"
            )
        ]
    ])

    await query.edit_message_text(
        "💰 کیف پول\n\n"
        f"🏦 موجودی: {balance:,} تومان\n"
        f"👤 سطح حساب: {role_text(level)}\n"
        f"💵 قیمت هر GB: {price:,} تومان",
        reply_markup=keyboard
    )


# ================= CHARGE =================

async def charge_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "💳 مبلغ شارژ را به تومان وارد کنید.\n\n"
        "مثلاً:\n"
        "100000",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "↩️ برگشت",
                    callback_data="back"
                )
            ]
        ])
    )

    return CHARGE_AMOUNT


async def charge_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = (
        update.message.text
        .strip()
        .replace(",", "")
        .replace("٬", "")
    )

    try:
        amount = int(text)

        if amount <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "❌ مبلغ نامعتبر است.\n"
            "فقط عدد وارد کنید.\n\n"
            "مثال: 100000"
        )
        return CHARGE_AMOUNT

    context.user_data[
        "charge_amount"
    ] = amount

    await update.message.reply_text(
        f"{CARD_TEXT}\n\n"
        f"💰 مبلغ پرداختی: {amount:,} تومان\n\n"
        "بعد از واریز، لطفاً عکس رسید پرداخت را همینجا ارسال کنید."
    )

    return CHARGE_RECEIPT


async def charge_receipt(
async def charge_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    ensure_user(user)

    if not update.message.photo:
        await update.message.reply_text(
            "❌ لطفاً عکس رسید را ارسال کنید.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "↩️ برگشت",
                        callback_data="back"
                    )
                ]
            ])
        )
        return CHARGE_RECEIPT

    amount = int(
        context.user_data.get(
            "charge_amount",
            0
        )
    )

    if amount <= 0:
        await update.message.reply_text(
            "❌ مبلغ شارژ پیدا نشد.\n"
            "دوباره از کیف پول شروع کنید.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "↩️ برگشت به منو",
                        callback_data="back"
                    )
                ]
            ])
        )
        return ConversationHandler.END

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO charge_requests (
            telegram_id,
            amount,
            status,
            created_at
        )
        VALUES (?, ?, 'pending', ?)
    """, (
        user.id,
        amount,
        datetime.datetime.now().isoformat()
    ))

    request_id = cur.lastrowid

    conn.commit()
    conn.close()

    # پیدا کردن ادمین
    admin_chat_id = get_admin_chat_id()

    # اگر خود کاربر ادمین باشد، شناسه خودش را ذخیره کن
    if is_admin(user):
        save_admin_chat_id(user)
        admin_chat_id = user.id

    if admin_chat_id:
        try:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ تایید پرداخت",
                        callback_data=f"charge_approve:{request_id}"
                    ),
                    InlineKeyboardButton(
                        "❌ رد پرداخت",
                        callback_data=f"charge_reject:{request_id}"
                    )
                ]
            ])

            await context.bot.send_photo(
                chat_id=admin_chat_id,
                photo=update.message.photo[-1].file_id,
                caption=(
                    "💳 درخواست شارژ جدید\n\n"
                    f"👤 کاربر: {user.first_name}\n"
                    f"🆔 Telegram ID: {user.id}\n"
                    f"🔹 Username: @{user.username or 'ندارد'}\n"
                    f"💰 مبلغ: {amount:,} تومان\n"
                    f"🧾 شماره درخواست: #{request_id}\n\n"
                    "لطفاً رسید را بررسی کنید."
                ),
                reply_markup=keyboard
            )

            print(
                "CHARGE RECEIPT SENT TO ADMIN:",
                admin_chat_id
            )

        except Exception as e:
            print(
                "ADMIN SEND ERROR:",
                repr(e)
            )

            await update.message.reply_text(
                "⚠️ رسید ثبت شد، اما ارسال آن برای ادمین با مشکل مواجه شد.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "↩️ برگشت به منو",
                            callback_data="back"
                        )
                    ]
                ])
            )

            return ConversationHandler.END

    else:
        await update.message.reply_text(
            "⚠️ رسید ثبت شد، اما ادمین هنوز ربات را فعال نکرده است.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "↩️ برگشت به منو",
                        callback_data="back"
                    )
                ]
            ])
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "✅ رسید پرداخت با موفقیت ثبت شد.\n\n"
        "⏳ رسید برای ادمین ارسال شد و پس از بررسی، "
        "مبلغ به کیف پول شما اضافه می‌شود.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "↩️ برگشت به منو",
                    callback_data="back"
                )
            ]
        ])
    )

    context.user_data.pop("charge_amount", None)

    return ConversationHandler.END
    conn = db()

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO charge_requests
        (
            telegram_id,
            amount,
            status,
            created_at
        )
        VALUES (?, ?, 'pending', ?)
    """, (
        user.id,
        amount,
        datetime.now().isoformat(
            timespec="seconds"
        )
    ))

    request_id = cur.lastrowid

    conn.commit()
    conn.close()

    admin_chat_id = get_admin_chat_id()

    if not admin_chat_id:
        await update.message.reply_text(
            "✅ رسید ثبت شد.\n\n"
            "⚠️ ادمین هنوز /start را در ربات نزده است."
        )
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ تأیید شارژ",
                callback_data=f"approve:{request_id}"
            ),
            InlineKeyboardButton(
                "❌ رد شارژ",
                callback_data=f"reject:{request_id}"
            )
        ]
    ])

    caption = (
        "💳 درخواست شارژ جدید\n\n"
        f"🆔 درخواست: {request_id}\n"
        f"👤 کاربر: {user.first_name}\n"
        f"🔢 آیدی تلگرام: {user.id}\n"
        f"📱 Username: @{user.username if user.username else 'ندارد'}\n"
        f"💰 مبلغ: {amount:,} تومان\n\n"
        "برای بررسی رسید، یکی از دکمه‌های زیر را بزنید."
    )

    try:
        await context.bot.send_photo(
            chat_id=admin_chat_id,
            photo=update.message.photo[-1].file_id,
            caption=caption,
            reply_markup=keyboard
        )

        await update.message.reply_text(
            "✅ رسید شما برای ادمین ارسال شد.\n\n"
            "⏳ بعد از تأیید ادمین، مبلغ به کیف پول اضافه می‌شود."
        )

    except Exception as e:
        print(
            "ADMIN RECEIPT ERROR:",
            repr(e)
        )

        await update.message.reply_text(
            "❌ ارسال رسید به ادمین انجام نشد."
        )

    return ConversationHandler.END


# ================= ADMIN CHARGE =================

async def admin_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    username = query.from_user.username or ""

    if username.lower() != ADMIN_USERNAME.lower():
        await query.answer(
            "⛔ فقط ادمین می‌تواند این کار را انجام دهد.",
            show_alert=True
        )
        return

    action, request_id_text = (
        query.data.split(":")
    )

    request_id = int(
        request_id_text
    )

    conn = db()

    cur = conn.cursor()

    row = cur.execute("""
        SELECT
            id,
            telegram_id,
            amount,
            status
        FROM charge_requests
        WHERE id=?
    """, (
        request_id,
    )).fetchone()

    if not row:
        conn.close()

        await query.edit_message_caption(
            caption="❌ درخواست پیدا نشد."
        )
        return

    if row["status"] != "pending":
        conn.close()

        await query.edit_message_caption(
            caption="⚠️ این درخواست قبلاً بررسی شده است."
        )
        return

    telegram_id = int(
        row["telegram_id"]
    )

    amount = int(
        row["amount"]
    )

    if action == "approve":

        cur.execute(
            """
            UPDATE charge_requests
            SET status='approved'
            WHERE id=?
            """,
            (request_id,)
        )

        cur.execute(
            """
            UPDATE users
            SET balance=balance+?
            WHERE telegram_id=?
            """,
            (
                amount,
                telegram_id
            )
        )

        conn.commit()

        row_balance = cur.execute(
            """
            SELECT balance
            FROM users
            WHERE telegram_id=?
            """,
            (telegram_id,)
        ).fetchone()

        new_balance = int(
            row_balance["balance"]
        )

        conn.close()

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
                "✅ درخواست شارژ تأیید شد.\n\n"
                f"🆔 درخواست: {request_id}\n"
                f"👤 کاربر: {telegram_id}\n"
                f"💰 مبلغ: {amount:,} تومان"
            )
        )

        return

    # REJECT

    cur.execute(
        """
        UPDATE charge_requests
        SET status='rejected'
        WHERE id=?
        """,
        (request_id,)
    )

    conn.commit()
    conn.close()

    try:
        await context.bot.send_message(
            chat_id=telegram_id,
            text=(
                "❌ درخواست شارژ شما رد شد.\n\n"
                f"💰 مبلغ: {amount:,} تومان\n"
                "در صورت نیاز دوباره رسید صحیح ارسال کنید."
            )
        )

    except Exception as e:
        print(
            "USER NOTIFICATION ERROR:",
            repr(e)
        )

    await query.edit_message_caption(
        caption=(
            "❌ درخواست شارژ رد شد.\n\n"
            f"🆔 درخواست: {request_id}\n"
            f"👤 کاربر: {telegram_id}\n"
            f"💰 مبلغ: {amount:,} تومان"
        )
    )


# ================= SERVICES =================

async def services(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    user = query.from_user

    rows = get_services(
        user.id
    )

    if not rows:
        await query.edit_message_text(
            "📦 هنوز سرویسی برای شما ثبت نشده است.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛒 خرید VPN",
                        callback_data="buy"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 منوی اصلی",
                        callback_data="back"
                    )
                ]
            ])
        )
        return

    text = "📦 سرویس‌های شما:\n\n"

    for row in rows:
        text += (
            f"🆔 سرویس: {row['service_id']}\n"
            f"📦 حجم: {row['traffic_gb']} GB\n"
            f"📅 مدت: {row['duration_days']} روز\n"
            f"💰 مبلغ: {row['price']:,} تومان\n"
            f"🕐 ثبت: {row['created_at']}\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🛒 خرید جدید",
                    callback_data="buy"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 منوی اصلی",
                    callback_data="back"
                )
            ]
        ])
    )


# ================= OTHER =================

async def renew(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🔄 بخش تمدید در حال تکمیل است.\n\n"
        "برای خرید سرویس جدید از گزینه «خرید VPN» استفاده کنید.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🛒 خرید VPN",
                    callback_data="buy"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 منوی اصلی",
                    callback_data="back"
                )
            ]
        ])
    )


async def support(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🎧 پشتیبانی\n\n"
        f"برای پشتیبانی به @{ADMIN_USERNAME} پیام دهید.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🏠 منوی اصلی",
                    callback_data="back"
                )
            ]
        ])
    )


async def reseller(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    user = query.from_user

    level = get_level(
        user.id
    )

    await query.edit_message_text(
        "👑 نمایندگی\n\n"
        "کاربر عادی: 8,000 تومان / GB\n"
        "نماینده: 6,000 تومان / GB\n"
        "نماینده ویژه: 3,200 تومان / GB\n\n"
        f"سطح فعلی شما: {role_text(level)}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🏠 منوی اصلی",
                    callback_data="back"
                )
            ]
        ])
    )


async def back(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🏠 منوی اصلی",
        reply_markup=main_menu()
    )


# ================= ADMIN SET REPRESENTATIVE =================

async def setrep(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    if (
        (user.username or "").lower()
        != ADMIN_USERNAME.lower()
    ):
        await update.message.reply_text(
            "⛔ فقط ادمین."
        )
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "فرمت:\n"
            "/setrep TELEGRAM_ID LEVEL\n\n"
            "0 = کاربر عادی\n"
            "1 = نماینده 6000\n"
            "2 = نماینده ویژه 3200"
        )
        return

    try:
        telegram_id = int(
            context.args[0]
        )

        level = int(
            context.args[1]
        )

        if level not in (0, 1, 2):
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "❌ مقدار نامعتبر است."
        )
        return

    conn = db()

    conn.execute("""
        INSERT INTO users
        (
            telegram_id,
            reseller_level
        )
        VALUES (?, ?)
        ON CONFLICT(telegram_id)
        DO UPDATE SET
            reseller_level=excluded.reseller_level
    """, (
        telegram_id,
        level
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ سطح کاربر {telegram_id} روی {level} تنظیم شد."
    )


# ================= CANCEL =================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_menu()
    )

    return ConversationHandler.END


# ================= MAIN =================

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
                ),

                MessageHandler(
                    filters.ALL
                    & ~filters.PHOTO
                    & ~filters.COMMAND,
                    charge_receipt
                )
            ]
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                cancel
            )
        ],

        per_user=True,
        per_chat=True
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "setrep",
            setrep
        )
    )

    app.add_handler(
        charge_conversation
    )

    app.add_handler(
        CallbackQueryHandler(
            buy_start,
            pattern=r"^buy$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            wallet,
            pattern=r"^wallet$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            services,
            pattern=r"^services$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            renew,
            pattern=r"^renew$"
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
            reseller,
            pattern=r"^reseller$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            back,
            pattern=r"^back$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            traffic_selected,
            pattern=r"^traffic:\d+$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            days_selected,
            pattern=r"^days:\d+$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            confirm_buy,
            pattern=r"^confirm_buy$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_action,
            pattern=r"^(approve|reject):\d+$"
        )
    )

    print("Bot is running...")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
