"""پنل مالی سایت داخل ادمین ربات: کاربر با شماره، کیف پول، لغو/تحویل سفارش."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop, CallbackQueryHandler, ContextTypes, MessageHandler, filters,
)

from admin_notify import is_admin
from review_broadcast import admin_display_name

log = logging.getLogger(__name__)

_PENDING: dict[int, dict] = {}
_TTL = 5 * 60


def _back_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔎 کاربر دیگر', callback_data='sfin_ask_user')],
        [InlineKeyboardButton('📦 سفارش با کد', callback_data='sfin_ask_order')],
        [InlineKeyboardButton('🔙 پرداخت و مالی', callback_data='admx_hub_pay')],
    ])


def _user_keyboard(user_id, orders=None):
    uid = int(user_id)
    rows = [
        [
            InlineKeyboardButton('➕ افزایش موجودی', callback_data=f'sfin_wadd_{uid}'),
            InlineKeyboardButton('➖ کاهش موجودی', callback_data=f'sfin_wsub_{uid}'),
        ],
    ]
    for o in (orders or [])[:5]:
        code = str(o.get('code') or o.get('id') or '').replace('-', '').lstrip('#')
        if not code:
            continue
        label = f'📦 {o.get("code") or code} · {o.get("status_label") or ""}'
        if o.get('awaiting_identity'):
            label += ' · 🪪'
        rows.append([InlineKeyboardButton(label[:60], callback_data=f'sfin_clook_{code}')])
    rows += [
        [InlineKeyboardButton('🔄 بروزرسانی', callback_data=f'sfin_user_{uid}')],
        [InlineKeyboardButton('📦 سفارش با کد', callback_data='sfin_ask_order')],
        [InlineKeyboardButton('🔎 کاربر دیگر', callback_data='sfin_ask_user')],
        [InlineKeyboardButton('🔙 پرداخت و مالی', callback_data='admx_hub_pay')],
    ]
    return InlineKeyboardMarkup(rows)


def _order_keyboard(code):
    safe = str(code).replace('-', '')
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💰 لغو + برگشت به کیف پول', callback_data=f'sfin_cref_{safe}')],
        [InlineKeyboardButton('✅ تحویل داده شد', callback_data=f'sfin_cdel_{safe}')],
        [InlineKeyboardButton('🔄 بروزرسانی', callback_data=f'sfin_clook_{safe}')],
        [InlineKeyboardButton('🔙 مالی سایت', callback_data='sfin_home')],
    ])


def _format_user(data: dict) -> str:
    u = data.get('user') or data
    lines = [
        '💰 *کاربر سایت*',
        '━━━━━━━━━━━━━━━',
        f'شناسه: `{u.get("id")}`',
        f'نام: {u.get("name") or "—"}',
        f'موبایل: `{u.get("phone") or "—"}`',
        f'ایمیل: `{u.get("email") or "—"}`',
        f'تلگرام: `{u.get("telegram_id") or "—"}`',
        f'موجودی کیف پول: *{int(u.get("wallet") or 0):,}* تومان',
        f'احراز: سطح {u.get("identity_level") or 0} · وضعیت `{u.get("identity_state") or "—"}`',
        f'سفارش منتظر احراز: {u.get("held_orders") or 0}',
        '',
        '*سفارش‌های اخیر:*',
    ]
    orders = u.get('orders') or []
    if not orders:
        lines.append('سفارشی نیست.')
    for o in orders[:8]:
        flag = ' · 🪪' if o.get('awaiting_identity') else ''
        lines.append(
            f'• `{o.get("code")}` · {o.get("status_label")} · '
            f'{int(o.get("total") or 0):,} ت{flag}'
        )
    return '\n'.join(lines)


def _format_order(o: dict) -> str:
    return (
        '📦 *سفارش سایت*\n━━━━━━━━━━━━━━━\n'
        f'کد: `{o.get("code")}`\n'
        f'وضعیت: *{o.get("status_label")}*\n'
        f'مبلغ: *{int(o.get("total") or 0):,}* تومان\n'
        f'برگشتی: {int(o.get("refunded") or 0):,} تومان\n'
        f'کاربر: `{o.get("user_id") or "—"}` · `{o.get("phone") or "—"}`\n'
        + ('🪪 منتظر احراز هویت\n' if o.get('awaiting_identity') else '')
    )


async def site_finance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer('دسترسی نداری', show_alert=True)
        return
    data = str(query.data or '')
    await query.answer()

    if data in ('sfin_home', 'admx_sitefinance'):
        await query.edit_message_text(
            '💰 *مالی سایت*\n━━━━━━━━━━━━━━━\n'
            'با شماره موبایل (یا ایمیل/شناسه) کاربر سایت را پیدا کن؛ '
            'موجودی را کم/زیاد کن، یا با کد سفارش لغو+ریفاند / تحویل بزن.',
            reply_markup=_back_home(), parse_mode='Markdown')
        return

    if data == 'sfin_ask_user':
        _PENDING[update.effective_user.id] = {'kind': 'user', 'at': time.time()}
        await query.edit_message_text(
            'شماره موبایل کاربر سایت را بفرست (مثال: `09121234567`).\n'
            'ایمیل یا شناسه هم قبول است.\nانصراف: انصراف',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('↩️ مالی سایت', callback_data='sfin_home')],
            ]), parse_mode='Markdown')
        return

    if data == 'sfin_ask_order':
        _PENDING[update.effective_user.id] = {'kind': 'order', 'at': time.time()}
        await query.edit_message_text(
            'کد پیگیری سفارش سایت را بفرست (مثال: `AS123456` یا شماره ردیف).\nانصراف: انصراف',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('↩️ مالی سایت', callback_data='sfin_home')],
            ]), parse_mode='Markdown')
        return

    parts = data.split('_')
    # sfin_user_12 | sfin_wadd_12 | sfin_wsub_12 | sfin_cref_AS123 | sfin_cdel_AS123 | sfin_clook_AS123
    if len(parts) >= 3 and parts[1] == 'user' and parts[2].isdigit():
        await _show_user(query, ctx, int(parts[2]))
        return
    if len(parts) >= 3 and parts[1] in ('wadd', 'wsub') and parts[2].isdigit():
        uid = int(parts[2])
        mode = 'add' if parts[1] == 'wadd' else 'sub'
        _PENDING[update.effective_user.id] = {
            'kind': 'wallet', 'user_id': uid, 'mode': mode, 'at': time.time()}
        label = 'افزایش' if mode == 'add' else 'کاهش'
        await query.edit_message_text(
            f'مبلغ {label} موجودی کاربر `{uid}` را به تومان بفرست.\n'
            'در خط بعد (یا همان پیام با `|`) دلیل را بنویس.\n'
            'مثال: `50000 جبران سفارش`\nانصراف: انصراف',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('↩️ کاربر', callback_data=f'sfin_user_{uid}')],
            ]), parse_mode='Markdown')
        return
    if len(parts) >= 3 and parts[1] in ('cref', 'cdel', 'clook'):
        code = parts[2]
        if parts[1] == 'clook':
            await _show_order(query, ctx, code)
            return
        if parts[1] == 'cdel':
            await _do_order(query, ctx, code, 'deliver')
            return
        # confirm cancel
        await query.edit_message_text(
            f'سفارش `{code}` لغو شود و پول به کیف پول خریدار برگردد؟',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ بله، لغو و برگشت پول',
                                      callback_data=f'sfin_cok_{code}')],
                [InlineKeyboardButton('↩️ نه', callback_data=f'sfin_clook_{code}')],
            ]), parse_mode='Markdown')
        return
    if len(parts) >= 3 and parts[1] == 'cok':
        await _do_order(query, ctx, parts[2], 'cancel_refund')
        return


async def _show_user(query, ctx, user_id):
    from site_api import fetch_site_admin_user

    data = await asyncio.to_thread(fetch_site_admin_user, str(user_id), query.from_user.id)
    if not data.get('ok'):
        await query.edit_message_text(
            f'پیدا نشد: {data.get("message") or data.get("error") or "خطا"}',
            reply_markup=_back_home())
        return
    user = data.get('user') or {}
    await query.edit_message_text(
        _format_user(data),
        reply_markup=_user_keyboard(user_id, user.get('orders')),
        parse_mode='Markdown')


async def _show_order(query, ctx, code):
    from site_api import call_site_admin_order

    name = admin_display_name(query.from_user)
    data = await asyncio.to_thread(
        call_site_admin_order, 'lookup', code, query.from_user.id, name)
    if not data.get('ok'):
        await query.edit_message_text(
            f'انجام نشد: {data.get("message") or data.get("error") or "خطا"}',
            reply_markup=_back_home())
        return
    order = data.get('order') or {}
    await query.edit_message_text(
        _format_order(order),
        reply_markup=_order_keyboard(order.get('code') or code),
        parse_mode='Markdown')


async def _do_order(query, ctx, code, action):
    from site_api import call_site_admin_order

    name = admin_display_name(query.from_user)
    data = await asyncio.to_thread(
        call_site_admin_order, action, code, query.from_user.id, name,
        'لغو از پنل مالی ربات' if action == 'cancel_refund' else '')
    if not data.get('ok'):
        err = data.get('message') or data.get('error') or 'خطای سایت'
        await query.edit_message_text(f'❌ {err}', reply_markup=_order_keyboard(code))
        return
    order = data.get('order') or {}
    await query.edit_message_text(
        f'✅ {data.get("message") or "انجام شد"}\n\n{_format_order(order)}',
        reply_markup=_order_keyboard(order.get('code') or code),
        parse_mode='Markdown')


class _AwaitingSiteFinance(filters.MessageFilter):
    def filter(self, message):
        user = getattr(message, 'from_user', None)
        pending = _PENDING.get(getattr(user, 'id', None))
        if pending and time.time() - pending['at'] > _TTL:
            _PENDING.pop(user.id, None)
            return False
        return bool(pending)


async def site_finance_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    admin = update.effective_user
    pending = _PENDING.pop(getattr(admin, 'id', None), None)
    if not pending or not is_admin(admin.id):
        return
    text = ' '.join((message.text or '').split())
    if text in ('انصراف', 'لغو', 'cancel'):
        await message.reply_text('لغو شد.', reply_markup=_back_home())
        raise ApplicationHandlerStop
    name = admin_display_name(admin)

    if pending['kind'] == 'user':
        from site_api import fetch_site_admin_user

        data = await asyncio.to_thread(fetch_site_admin_user, text, admin.id)
        if not data.get('ok'):
            _PENDING[admin.id] = pending
            pending['at'] = time.time()
            await message.reply_text(
                f'پیدا نشد: {data.get("message") or data.get("error")}\nشماره دیگر بفرست یا: انصراف')
            raise ApplicationHandlerStop
        user = data['user']
        await message.reply_text(
            _format_user(data),
            reply_markup=_user_keyboard(user['id'], user.get('orders')),
            parse_mode='Markdown')
        raise ApplicationHandlerStop

    if pending['kind'] == 'order':
        from site_api import call_site_admin_order

        data = await asyncio.to_thread(
            call_site_admin_order, 'lookup', text, admin.id, name)
        if not data.get('ok'):
            _PENDING[admin.id] = pending
            pending['at'] = time.time()
            await message.reply_text(
                f'پیدا نشد: {data.get("message") or data.get("error")}\nکد دیگر بفرست یا: انصراف')
            raise ApplicationHandlerStop
        order = data['order']
        await message.reply_text(
            _format_order(order),
            reply_markup=_order_keyboard(order.get('code') or text),
            parse_mode='Markdown')
        raise ApplicationHandlerStop

    if pending['kind'] == 'wallet':
        from site_api import call_site_admin_wallet

        # "50000 دلیل" یا "50000|دلیل"
        raw = text.replace('|', ' ', 1)
        m = re.match(r'^(-?\d+)\s*(.*)$', raw)
        if not m:
            _PENDING[admin.id] = pending
            pending['at'] = time.time()
            await message.reply_text('مبلغ را عدد بفرست؛ مثال: `50000 جبران سفارش`',
                                     parse_mode='Markdown')
            raise ApplicationHandlerStop
        amount = abs(int(m.group(1)))
        reason = (m.group(2) or '').strip() or 'اصلاح از پنل مالی ربات'
        if pending['mode'] == 'sub':
            amount = -amount
        data = await asyncio.to_thread(
            call_site_admin_wallet, pending['user_id'], amount, admin.id, name, reason)
        if not data.get('ok'):
            await message.reply_text(
                f'❌ {data.get("message") or data.get("error") or "خطا"}',
                reply_markup=_user_keyboard(pending['user_id']))
            raise ApplicationHandlerStop
        await message.reply_text(
            f'✅ {data.get("message")}\nدلیل: {reason}',
            reply_markup=_user_keyboard(pending['user_id']))
        raise ApplicationHandlerStop


def site_finance_handlers():
    return [
        CallbackQueryHandler(site_finance, pattern=r'^(?:sfin_|admx_sitefinance)'),
    ]


def site_finance_text_handler():
    return MessageHandler(filters.TEXT & ~filters.COMMAND & _AwaitingSiteFinance(),
                          site_finance_text)
