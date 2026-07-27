from telegram import Update
from telegram.ext import ContextTypes
from keyboards import main_menu
from db import get_or_create_user, is_user_blocked, get_setting
from admin_notify import is_admin, is_premium_admin


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
        is_premium=bool(user.is_premium),
    )
    ctx.user_data['db_id'] = db_id
    ctx.user_data['tg_id'] = user.id

    name = user.first_name or 'کاربر'
    welcome = "🆕 خوش اومدی!" if is_new else "👋 خوش برگشتی!"

    default_text = (
        "✨ به اتومیک شاپ خوش اومدی! ✨\n\n"
        "اینجا جاییه که سرعت، امنیت و قیمت مناسب کنار هم جمع شدن "
        "تا خرید راحت‌تری داشته باشی 🚀\n\n"
        "💎 خرید جم و محصولات بازی\n"
        "🎯 پک‌های حرفه‌ای سنسیویتی موبایل و PC\n"
        "💳 پرداخت امن با درگاه یا کارت‌به‌کارت\n"
        "🎁 کدهای هدیه و تخفیف‌های ویژه\n"
        "🧑‍💻 پشتیبانی مستقیم و پیگیری سفارش\n\n"
        "تمام سفارش‌ها و پرداخت‌های تو از داخل ربات قابل مشاهده و پیگیری هستند.\n\n"
        "👇 برای شروع، یکی از گزینه‌های منوی پایین را انتخاب کن.\n\n"
        "⚛️ Atomic Shop"
    )
    custom = get_setting('welcome_text', '').strip()
    text = custom.replace('{name}', name).replace('{welcome}', welcome) if custom else default_text
    if is_admin(user.id):
        text += "\n\n🛠 ادمین: دستور `/admin` را بزن."
    elif bool(user.is_premium) and is_premium_admin(user.id):
        text += "\n\n⭐ مدیر پریمیوم: دستور `/studio` را بزن."
    try:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())
    except Exception:
        # متن سفارشی مدیر ممکن است Markdown نامعتبر داشته باشد؛ ربات نباید از کار بیفتد.
        await update.message.reply_text(text, reply_markup=main_menu())


async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *راهنمای Atomic Bot*\n"
        "━━━━━━━━━━━━━━━\n"
        "🎮 *محصولات فری‌فایر* — خرید با آیدی (فعال)\n"
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
