"""اجبار عضویت در کانال‌های تنظیم‌شده پیش از استفاده از ربات."""
import asyncio
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from admin_notify import is_admin
from db import get_or_create_user, list_forced_join_channels
from forced_join_logic import member_is_joined
from keyboards import main_menu

_CHANNEL_CACHE_SECONDS = 30
_MEMBERSHIP_CACHE_SECONDS = 10 * 60
_ADMIN_CACHE_SECONDS = 60
_channels_cache = {'at': 0.0, 'rows': ()}


def invalidate_forced_join_cache():
    _channels_cache.update(at=0.0, rows=())


async def _forced_join_channels():
    now = time.monotonic()
    if now - _channels_cache['at'] < _CHANNEL_CACHE_SECONDS:
        return _channels_cache['rows']
    rows = tuple(await asyncio.to_thread(list_forced_join_channels, True))
    _channels_cache.update(at=now, rows=rows)
    return rows


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
    if not user:
        return

    # Most installations have forced join disabled.  Check the shared,
    # memory-cached channel list first so ordinary button clicks do not pay
    # for an unnecessary database admin lookup.
    channels = await _forced_join_channels()
    if not channels:
        return

    now = time.monotonic()
    admin_cache = ctx.user_data.get('_forced_join_admin') or {}
    if (
        admin_cache.get('user_id') == user.id
        and now - float(admin_cache.get('at') or 0) < _ADMIN_CACHE_SECONDS
    ):
        admin = bool(admin_cache.get('value'))
    else:
        admin = await asyncio.to_thread(is_admin, user.id)
        ctx.user_data['_forced_join_admin'] = {
            'user_id': user.id,
            'value': bool(admin),
            'at': now,
        }
    if admin:
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
        and time.monotonic() - float(cache.get('at') or 0) < _MEMBERSHIP_CACHE_SECONDS
    ):
        return

    missing, unavailable = await _membership_result(
        ctx.bot, user.id, channels
    )
    if not missing and not unavailable:
        ctx.user_data['_forced_join_ok'] = {
            'signature': signature,
            'at': time.monotonic(),
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
