import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes
from keyboards import main_menu
import appearance
from db import get_or_create_user, is_user_blocked, get_setting
from admin_notify import is_admin, is_premium_admin, is_credential_admin

NEW_USER_GREETING = 'به اتومیک شاپ خوش اومدی ✨'
RETURNING_GREETING = 'خوش برگشتی! 🌟'


def welcome_kwargs(stored, name, is_new):
    """پیام /start: متنِ مدیر (تنظیمات) یا پیش‌فرض، با نام و سلامِ تازه/برگشته.

    پیش‌نمایشِ پنل هم همین را می‌سازد، پس مدیر دقیقاً همان را می‌بیند که کاربر.
    اگر به‌جای متن، برچسبِ یکی از دکمه‌های منو ذخیره شده باشد (مدیر وسطِ
    واردکردنِ متن دکمهٔ منو را زده بود)، نادیده گرفته می‌شود؛ وگرنه هر کاربر با
    /start فقط همان برچسب را می‌دید («🎮 محصولات فری‌فایر»).
    """
    stored = (stored or '').strip()
    if appearance.is_menu_tap(stored):
        stored = ''
    greeting = NEW_USER_GREETING if is_new else RETURNING_GREETING
    payload = appearance.message_kwargs(
        't.welcome', stored or appearance.DEFAULTS['t.welcome'], name=name, welcome=greeting
    )
    # اگر متنِ مدیر آکولادِ نامعتبر داشته باشد، format کار نمی‌کند؛ این‌جا جبران می‌شود.
    payload['text'] = payload['text'].replace('{name}', name).replace('{welcome}', greeting)
    return payload


async def start_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    blocked, admin = await asyncio.gather(
        asyncio.to_thread(is_user_blocked, user.id),
        asyncio.to_thread(is_admin, user.id),
    )
    if blocked and not admin:
        await update.message.reply_text(
            "🚫 حساب شما بلاک شده است.\nبرای پیگیری از پشتیبانی سایت اقدام کن."
        )
        return

    db_id, is_new = await asyncio.to_thread(
        get_or_create_user,
        telegram_id=user.id,
        first_name=user.first_name or '',
        last_name=user.last_name or '',
        username=user.username or '',
        is_premium=bool(user.is_premium),
    )
    ctx.user_data['db_id'] = db_id
    ctx.user_data['tg_id'] = user.id

    name = user.first_name or 'دوست عزیز'
    stored = await asyncio.to_thread(get_setting, 'welcome_text', '')
    payload = welcome_kwargs(stored, name, is_new)
    text = payload['text']
    if admin:
        text += "\n\n🛠 ادمین: دستور `/admin` را بزن."
    elif await asyncio.to_thread(is_credential_admin, user.id):
        text += "\n\n🔐 پشتیبان جم با اطلاعات: دستور `/credadmin` را بزن."
    elif bool(user.is_premium) and await asyncio.to_thread(is_premium_admin, user.id):
        text += "\n\n⭐ مدیر پریمیوم: دستور `/studio` را بزن."
    payload['text'] = text
    try:
        await update.message.reply_text(**payload, reply_markup=main_menu())
    except Exception:
        # متن سفارشی مدیر ممکن است Markdown نامعتبر داشته باشد؛ ربات نباید از کار بیفتد.
        await update.message.reply_text(text, reply_markup=main_menu())
    await _referral_after_start(update, ctx, db_id, is_new)


async def _referral_after_start(update, ctx, db_id, is_new):
    """لینک دعوت (ref_...)؛ هر خطایی در بخش رفرال نباید /start را خراب کند."""
    try:
        from handlers.referral import handle_start_payload, pop_start_payload
        payload = ctx.args[0] if ctx.args else pop_start_payload(ctx)
        if payload:
            await handle_start_payload(update, ctx, db_id, is_new, payload)
    except Exception:
        logging.getLogger(__name__).exception('Referral start hook failed')


async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    payload = appearance.message_kwargs('t.help', appearance.DEFAULTS['t.help'])
    text = payload['text']
    if is_admin(update.effective_user.id):
        text += "\n🛠 ادمین: `/admin`"
    elif is_credential_admin(update.effective_user.id):
        text += "\n🔐 پشتیبان جم با اطلاعات: `/credadmin`"
    payload['text'] = text
    try:
        await update.message.reply_text(**payload, reply_markup=main_menu())
    except Exception:
        await update.message.reply_text(text, reply_markup=main_menu())


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
    payload = appearance.message_kwargs('t.home', appearance.DEFAULTS['t.home'])
    try:
        await query.edit_message_text(**payload)
    except Exception:
        await query.edit_message_text(payload.get('text') or 'از منوی پایین یک گزینه انتخاب کن 👇')
    await query.message.reply_text("منوی اصلی:", reply_markup=main_menu())
