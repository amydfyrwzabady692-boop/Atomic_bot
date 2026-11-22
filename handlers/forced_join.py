"""اجبار عضویت در کانال‌های تنظیم‌شده و استارت ربات وینزون بت پیش از استفاده از ربات."""
import asyncio
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from admin_notify import is_admin, is_credential_admin
from db import get_or_create_user, get_setting, list_forced_join_channels
from forced_join_logic import member_is_joined
from keyboards import main_menu

logger = logging.getLogger(__name__)

_CHANNEL_CACHE_SECONDS = 30
_MEMBERSHIP_CACHE_SECONDS = 10 * 60
_ADMIN_CACHE_SECONDS = 60
_channels_cache = {'at': 0.0, 'rows': ()}

WINZON_BOT_USERNAME = 'Winzonbet_bot'
WINZON_START_URL = 'https://t.me/Winzonbet_bot?start=atomic'


def invalidate_forced_join_cache():
    _channels_cache.update(at=0.0, rows=())


async def _forced_join_channels():
    now = time.monotonic()
    if now - _channels_cache['at'] < _CHANNEL_CACHE_SECONDS:
        return _channels_cache['rows']
    rows = tuple(await asyncio.to_thread(list_forced_join_channels, True))
    _channels_cache.update(at=now, rows=rows)
    return rows


async def check_user_started_winzon(user_id: int) -> bool:
    """استعلام وضعیت استارت کاربر در ربات وینزون بت از طریق API."""
    import aiohttp
    key = str(await asyncio.to_thread(get_setting, 'winzon_api_key_for_atomic', '') or '').strip()
    url = f'https://91-99-218-150.sslip.io/api/v1/user?user_id={user_id}&key={key}'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return bool(data.get('ok') and data.get('started') and not data.get('blocked'))
                logger.warning('Winzon API returned HTTP %s for user %s', resp.status, user_id)
    except Exception as e:
        logger.warning('Failed to query Winzon API: %s', e)
    return False


def _join_keyboard(channels, winzon_missing=True):
    rows = []
    if winzon_missing:
        rows.append([InlineKeyboardButton(
            '🎮 استارت ربات وینزون بت',
            url=WINZON_START_URL,
        )])
    for _channel_id, chat_id, title, invite_url, _active in channels:
        url = str(invite_url or '').strip()
        if url and not url.startswith(('http://', 'https://')):
            url = f'https://{url}'
        rows.append([InlineKeyboardButton(
            f'📢 عضویت در {title or chat_id}',
            url=url or f'https://t.me/{str(chat_id).lstrip("@")}',
        )])
    rows.append([
        InlineKeyboardButton(
            '✅ انجام دادم — بررسی شرایط',
            callback_data='forced_join_check',
        )
    ])
    return InlineKeyboardMarkup(rows)


async def _membership_result(bot, user_id, channels):
    missing = []
    unavailable = []
    for _channel_id, chat_id, title, _invite_url, _active in channels:
        try:
            target_id = int(chat_id) if (str(chat_id).startswith('-') and str(chat_id)[1:].isdigit()) else chat_id
            member = await bot.get_chat_member(chat_id=target_id, user_id=user_id)
            joined = member_is_joined(
                member.status,
                getattr(member, 'is_member', False),
            )
            if not joined:
                missing.append(title or chat_id)
        except Exception:
            unavailable.append(title or chat_id)
    return missing, unavailable


async def _show_join_prompt(update, channels, missing, unavailable, winzon_missing=False):
    lines = [
        '🔒 برای استفاده از ربات، لطفاً شرایط زیر را انجام دهید:',
        '',
    ]
    if winzon_missing:
        lines.append('• استارت ربات وینزون بت (@Winzonbet_bot)')
    if missing:
        lines.extend(f'• عضویت در کانال: {name}' for name in missing)
    if unavailable:
        lines.extend([
            '',
            '⚠️ بررسی عضویت فعلاً ممکن نیست.',
            'مدیر باید ربات را در کانال ادمین کند و شناسه کانال را بررسی کند.',
        ])
    text = '\n'.join(lines)
    keyboard = _join_keyboard(channels, winzon_missing=winzon_missing)
    query = update.callback_query
    if query:
        try:
            if winzon_missing and missing:
                await query.answer('هنوز ربات وینزون بت را استارت نکرده و عضو کانال‌ها نشده‌اید ❌', show_alert=True)
            elif winzon_missing:
                await query.answer('هنوز ربات وینزون بت (@Winzonbet_bot) را استارت نکرده‌اید ❌', show_alert=True)
            elif missing:
                await query.answer('ابتدا عضو تمام کانال‌ها شوید ❌', show_alert=True)
            else:
                await query.answer('در حال بررسی…')
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
    """در گروه -1 ثبت می‌شود و آپدیت کاربران غیرعضو یا استارت‌نکرده را در چت خصوصی (PV) متوقف می‌کند."""
    chat = update.effective_chat
    if not chat or chat.type != ChatType.PRIVATE:
        return

    user = update.effective_user
    if not user or user.is_bot:
        return

    # ۱. کاربر به محض تعامل با ربات در دیتابیس ثبت می‌شود تا API آن را started بداند
    # و نیازی نباشد حتماً شرایط اتومیک شاپ را انجام دهد تا وینزون بت استارت آن را تایید کند.
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

    # Check channels first so test_forced_join_skips_admin_db_lookup_when_disabled passes
    channels = await _forced_join_channels()

    now = time.monotonic()
    admin_cache = ctx.user_data.get('_forced_join_admin') or {}
    if (
        admin_cache.get('user_id') == user.id
        and now - float(admin_cache.get('at') or 0) < _ADMIN_CACHE_SECONDS
    ):
        admin = bool(admin_cache.get('value'))
    else:
        admin = await asyncio.to_thread(is_admin, user.id)
        if not admin:
            admin = await asyncio.to_thread(is_credential_admin, user.id)
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
        and cache.get('winzon_ok')
        and time.monotonic() - float(cache.get('at') or 0) < _MEMBERSHIP_CACHE_SECONDS
    ):
        return

    winzon_started = await check_user_started_winzon(user.id)
    missing, unavailable = await _membership_result(
        ctx.bot, user.id, channels
    )
    if winzon_started and not missing and not unavailable:
        ctx.user_data['_forced_join_ok'] = {
            'signature': signature,
            'winzon_ok': True,
            'at': time.monotonic(),
        }
        if checking_button:
            await update.callback_query.answer('شرایط با موفقیت تأیید شد ✅')
            await update.callback_query.edit_message_text(
                '✅ شرایط تأیید شد؛ حالا می‌توانی از ربات استفاده کنی.'
            )
            await update.callback_query.message.reply_text(
                'از منوی پایین انتخاب کن 👇',
                reply_markup=main_menu(),
            )
            await _resume_referral_start(update, ctx, db_id, is_new)
            raise ApplicationHandlerStop
        return

    _remember_referral_start(update, ctx)
    await _show_join_prompt(update, channels, missing, unavailable, winzon_missing=(not winzon_started))
    raise ApplicationHandlerStop


def _remember_referral_start(update, ctx):
    """لینک دعوت کاربری که هنوز عضو کانال نیست تا بعد از عضویت ثبت شود."""
    try:
        from handlers.referral import remember_start_payload
        remember_start_payload(update, ctx)
    except Exception:
        logging.getLogger(__name__).exception('Could not keep referral start payload')


async def _resume_referral_start(update, ctx, db_id, is_new):
    try:
        from handlers.referral import resume_start_payload
        await resume_start_payload(update, ctx, db_id, is_new)
    except Exception:
        logging.getLogger(__name__).exception('Referral resume after forced join failed')
