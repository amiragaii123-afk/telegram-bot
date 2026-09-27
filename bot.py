import os
import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
PANEL_API_KEY = os.environ["PANEL_API_KEY"]

BASE_URL = "https://panel.astravionix.site/api/v1"
RESELLER_INBOUND_ID = 12

NAME, TRAFFIC, DAYS = range(3)


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
    return response.json()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\n"
        "به ربات فروش VPN خوش آمدید.\n\n"
        "برای خرید، ابتدا نام کاربری موردنظر را ارسال کنید:"
    )
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_name"] = update.message.text.strip()

    await update.message.reply_text(
        "حجم سرویس را به GB وارد کنید.\n"
        "مثلاً: 50"
    )
    return TRAFFIC


async def get_traffic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        traffic = int(update.message.text.strip())

        if traffic <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "لطفاً فقط یک عدد معتبر وارد کن. مثلاً 50"
        )
        return TRAFFIC

    context.user_data["traffic_gb"] = traffic

    await update.message.reply_text(
        "مدت سرویس را به روز وارد کنید.\n"
        "مثلاً: 30"
    )
    return DAYS


async def get_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text.strip())

        if days <= 0:
            raise ValueError

    except ValueError:
        await update.message.reply_text(
            "لطفاً فقط یک عدد معتبر وارد کن. مثلاً 30"
        )
        return DAYS

    context.user_data["duration_days"] = days

    await update.message.reply_text("⏳ در حال ساخت سرویس...")

    payload = {
        "reseller_inbound_id": RESELLER_INBOUND_ID,
        "traffic_gb": context.user_data["traffic_gb"],
        "duration_days": days,
        "client_name": context.user_data["client_name"],
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
            await update.message.reply_text(
                "❌ ساخت سرویس انجام نشد.\n"
                f"{result.get('msg', 'خطای نامشخص')}"
            )
            return ConversationHandler.END

        service = result.get("obj", {})

        service_id = (
            service.get("id")
            or service.get("service_id")
        )

        message = (
            "✅ سرویس با موفقیت ساخته شد!\n\n"
            f"👤 نام: {context.user_data['client_name']}\n"
            f"📦 حجم: {context.user_data['traffic_gb']} GB\n"
            f"📅 مدت: {days} روز\n"
        )

        if service_id:
            message += f"\n🆔 شماره سرویس: {service_id}"

        await update.message.reply_text(message)

        if service_id:
            try:
                sub = await api_request(
                    "GET",
                    f"/services/{service_id}/subscription"
                )

                await update.message.reply_text(
                    "🔗 اطلاعات اشتراک:\n\n"
                    + str(sub)
                )

            except Exception as e:
                print("SUBSCRIPTION ERROR:", repr(e))

    except Exception as e:
        print("API ERROR:", repr(e))

        await update.message.reply_text(
            "❌ هنگام ارتباط با پنل خطایی رخ داد.\n"
            "جزئیات خطا در لاگ سرور ثبت شد."
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_name
                )
            ],
            TRAFFIC: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_traffic
                )
            ],
            DAYS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_days
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
    )

    app.add_handler(conversation)

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
