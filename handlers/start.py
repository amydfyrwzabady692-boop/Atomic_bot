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
        "🏪 *Atomic Shop*\n"
        "فروشگاه اکانت، الماس و پک سنس فری‌فایر\n\n"
        "💎 شارژ مستقیم جم با آیدی یا اطلاعات اکانت\n"
        "⚡️ تحویل سریع | 🔒 پرداخت امن | 🎧 پشتیبانی ۲۴/۷\n\n"
        "از منوی پایین شروع کن 👇"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())


async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *راهنمای Atomic Shop*\n"
        "━━━━━━━━━━━━━━━\n"
        "🛍 *فروشگاه اکانت* — خرید اکانت فری‌فایر\n"
        "💎 *جم فری‌فایر* — شارژ الماس با آیدی یا اطلاعات اکانت\n"
        "🎯 *پک سنس* — سنسیتیویتی هدشات\n"
        "🛒 *سبد خرید* — مدیریت و تسویه\n"
        "📦 *سفارش‌های من* — تاریخچه سفارش‌ها\n"
        "🎧 *پشتیبانی* — ارسال تیکت\n"
        "👤 *حساب من* — اطلاعات کاربری\n"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())
