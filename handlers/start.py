from telegram import Update
from telegram.ext import ContextTypes
from keyboards import main_menu
from db import get_or_create_user, is_user_blocked, get_setting
from admin_notify import is_admin


async def start_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_blocked(user.id) and not is_admin(user.id):
        await update.message.reply_text(
            "🚫 حساب شما بلاک شده است.\nبرای پیگیری از پشتیبانی سایت اقدام کن."
        )
        return

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

    default_text = (
        f"{welcome} *{name}* عزیز 🎮\n"
        "━━━━━━━━━━━━━━━\n"
        "🏪 *Atomic Bot*\n"
        "شارژ جم فری‌فایر با آیدی + کیف پول\n\n"
        "💎 *جم با آیدی* — تایید اکانت و تحویل خودکار\n"
        "💰 *کیف پول* — شارژ و پرداخت سریع\n"
        "💳 زرین‌پال / کارت‌به‌کارت\n"
        "🎧 *پشتیبانی* — چت مستقیم با ادمین\n\n"
        "از منوی پایین شروع کن 👇"
    )
    custom = get_setting('welcome_text', '').strip()
    text = custom.replace('{name}', name).replace('{welcome}', welcome) if custom else default_text
    if is_admin(user.id):
        text += "\n\n🛠 ادمین: دستور `/admin` را بزن."
    try:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())
    except Exception:
        # متن سفارشی مدیر ممکن است Markdown نامعتبر داشته باشد؛ ربات نباید از کار بیفتد.
        await update.message.reply_text(text, reply_markup=main_menu())


async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *راهنمای Atomic Bot*\n"
        "━━━━━━━━━━━━━━━\n"
        "💎 *جم فری‌فایر* — خرید با آیدی (فعال)\n"
        "💰 *کیف پول* — شارژ و موجودی (فعال)\n"
        "📦 *سفارش‌های من* — وضعیت سفارش‌ها\n"
        "👤 *حساب من* — پروفایل و موجودی\n"
        "🎧 *پشتیبانی* — پیام به ادمین\n\n"
        "سایر بخش‌ها در حال بروزرسانی هستند.\n\n"
        "⚠️ پرداخت زرین‌پال: لینک را *کپی* کن → VPN خاموش → در مرورگر باز کن.\n"
        "🆔 دستور `/myid` آیدی عددی تلگرام تو را نشان می‌دهد."
    )
    if is_admin(update.effective_user.id):
        text += "\n🛠 ادمین: `/admin`"
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())


async def myid_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    uname = update.effective_user.username
    handle = f"@{uname}" if uname else "—"
    text = (
        f"آیدی تلگرام: *{handle}*\n"
        f"شناسه عددی: `{uid}`"
    )
    if is_admin(uid):
        text += "\n\n_(ادمین: همین شناسه عددی در سرور ست شده)_"
    await update.message.reply_text(text, parse_mode='Markdown')



async def home_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("از منوی پایین یک گزینه انتخاب کن 👇")
    await query.message.reply_text("منوی اصلی:", reply_markup=main_menu())
