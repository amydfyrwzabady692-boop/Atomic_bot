from telegram import Update
from telegram.ext import ContextTypes
from keyboards import main_menu
from db import get_or_create_user


async def start_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_id, is_new = get_or_create_user(
        telegram_id=user.id,
        first_name=user.first_name or '',
        last_name=user.last_name or '',
        username=user.username or '',
    )
    ctx.user_data['db_id'] = db_id
    ctx.user_data['tg_id'] = user.id

    name = user.first_name or 'کاربر'
    welcome = "🆕 خوش اومدی!" if is_new else "👋 خوش برگشتی!"

    text = (
        f"{welcome} *{name}* عزیز 🎮\n"
        "━━━━━━━━━━━━━━━\n"
        "🏪 *Atomic Bot*\n"
        "شارژ جم فری‌فایر با آیدی + کیف پول\n\n"
        "💎 *جم با آیدی* — تایید اکانت و تحویل خودکار\n"
        "💰 *کیف پول* — شارژ و پرداخت سریع\n"
        "💳 زرین‌پال / کارت‌به‌کارت\n\n"
        "از منوی پایین شروع کن 👇"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())


async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *راهنمای Atomic Bot*\n"
        "━━━━━━━━━━━━━━━\n"
        "💎 *جم فری‌فایر* — خرید با آیدی (فعال)\n"
        "💰 *کیف پول* — شارژ و موجودی (فعال)\n"
        "📦 *سفارش‌های من* — وضعیت سفارش‌ها\n"
        "👤 *حساب من* — پروفایل و موجودی\n\n"
        "سایر بخش‌ها در حال بروزرسانی هستند.\n\n"
        "⚠️ هنگام پرداخت زرین‌پال *VPN را خاموش* کن.\n"
        "🆔 دستور `/myid` آیدی عددی تلگرام تو را نشان می‌دهد."
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())


async def myid_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🆔 آیدی عددی تلگرام شما:\n`{uid}`\n\n"
        "اگر ادمین هستی، همین عدد را در `.env` مقابل `ADMIN_CHAT_ID=` بگذار.",
        parse_mode='Markdown',
    )


async def home_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("از منوی پایین یک گزینه انتخاب کن 👇")
    await query.message.reply_text("منوی اصلی:", reply_markup=main_menu())
