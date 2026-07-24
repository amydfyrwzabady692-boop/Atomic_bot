"""اجبار عضویت در کانال‌های تنظیم‌شده پیش از استفاده از ربات."""
import asyncio
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from admin_notify import is_admin
from db import get_or_create_user, list_forced_join_channels
from forced_join_logic import member_is_joined
from keyboards import main_menu


def _join_keyboard(channels):
    rows = [
        [InlineKeyboardButton(
            f'📢 عضویت در {title or chat_id}',
            url=invite_url,
        )]
        for _channel_id, chat_id, title, invite_url, _active in channels
    ]
    rows.append([
        InlineKeyboardButton(
            '✅ عضو شدم — بررسی عضویت',
            callback_data='forced_join_check',
        )
    ])
    return InlineKeyboardMarkup(rows)


async def _membership_result(bot, user_id, channels):
    missing = []
    unavailable = []
    for _channel_id, chat_id, title, _invite_url, _active in channels:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            joined = member_is_joined(
                member.status,
                getattr(member, 'is_member', False),
            )
            if not joined:
                missing.append(title or chat_id)
        except Exception:
            unavailable.append(title or chat_id)
    return missing, unavailable


async def _show_join_prompt(update, channels, missing, unavailable):
    lines = [
        '🔒 برای استفاده از ربات ابتدا عضو کانال زیر شو:',
        '',
    ]
    if missing:
        lines.extend(f'• {name}' for name in missing)
    if unavailable:
        lines.extend([
            '',
            '⚠️ بررسی عضویت فعلاً ممکن نیست.',
            'مدیر باید ربات را در کانال ادمین کند و شناسه کانال را بررسی کند.',
        ])
    text = '\n'.join(lines)
    keyboard = _join_keyboard(channels)
    query = update.callback_query
    if query:
        try:
            await query.answer(
                'ابتدا عضو کانال شو.' if missing else 'در حال بررسی…'
            )
        except Exception:
            pass
        if query.data == 'forced_join_check':
            try:
                await query.edit_message_text(text, reply_markup=keyboard)
                return
            except Exception:
                pass
        await query.message.reply_text(text, reply_markup=keyboard)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


async def force_join_guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """در گروه -1 ثبت می‌شود و آپدیت کاربران غیرعضو را متوقف می‌کند."""
    user = update.effective_user
    if not user or is_admin(user.id):
        return

    channels = await asyncio.to_thread(list_forced_join_channels, True)
    if not channels:
        return

    signature = tuple(
        (row[0], row[1], row[3], bool(row[4])) for row in channels
    )
    cache = ctx.user_data.get('_forced_join_ok') or {}
    checking_button = bool(
        update.callback_query
        and update.callback_query.data == 'forced_join_check'
    )
    if (
        not checking_button
        and cache.get('signature') == signature
        and time.time() - float(cache.get('at') or 0) < 60
    ):
        return

    missing, unavailable = await _membership_result(
        ctx.bot, user.id, channels
    )
    if not missing and not unavailable:
        ctx.user_data['_forced_join_ok'] = {
            'signature': signature,
            'at': time.time(),
        }
        if checking_button:
            db_id, _is_new = await asyncio.to_thread(
                get_or_create_user,
                telegram_id=user.id,
                first_name=user.first_name or '',
                last_name=user.last_name or '',
                username=user.username or '',
                is_premium=bool(user.is_premium),
            )
            ctx.user_data['db_id'] = db_id
            ctx.user_data['tg_id'] = user.id
            await update.callback_query.answer('عضویت تأیید شد ✅')
            await update.callback_query.edit_message_text(
                '✅ عضویتت تأیید شد؛ حالا می‌توانی از ربات استفاده کنی.'
            )
            await update.callback_query.message.reply_text(
                'از منوی پایین انتخاب کن 👇',
                reply_markup=main_menu(),
            )
            raise ApplicationHandlerStop
        return

    await _show_join_prompt(update, channels, missing, unavailable)
    raise ApplicationHandlerStop
