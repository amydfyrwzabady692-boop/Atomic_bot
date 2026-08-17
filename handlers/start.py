import asyncio

from telegram import Update
from telegram.ext import ContextTypes
from keyboards import main_menu
import appearance
from db import get_or_create_user, is_user_blocked, get_setting
from admin_notify import is_admin, is_premium_admin, is_credential_admin


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

    name = user.first_name or 'کاربر'
    welcome = "🆕 خوش اومدی!" if is_new else "👋 خوش برگشتی!"

    stored = (await asyncio.to_thread(get_setting, 'welcome_text', '')).strip()
    payload = appearance.message_kwargs(
        't.welcome', stored or appearance.DEFAULTS['t.welcome']
    )
    text = payload['text'].replace('{name}', name).replace('{welcome}', welcome)
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
