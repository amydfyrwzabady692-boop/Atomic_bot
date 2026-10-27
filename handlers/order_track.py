"""پیگیری سفارش — سفارش سایت با کد پیگیری (AS…) یا سفارش ربات با شماره‌اش.

راه‌ها: دکمهٔ «🔎 پیگیری سفارش» زیر «سفارش‌های من»، فرمان ‎/track‎ (با یا بدون
کد)، یا فرستادنِ مستقیمِ خودِ کد در چت.
"""
from __future__ import annotations

import asyncio
import re
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from db import get_gem_infos_for_order, get_or_create_user, get_order_for_tracking, get_order_items
from site_api import fetch_site_order

SITE_CODE_RE = re.compile(r'^\s*(AS\d{8})\s*$', re.I)
BOT_ORDER_RE = re.compile(r'^\s*#?\s*(\d{1,9})\s*$')
_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')

# سقفِ محلی، پیش از رسیدن به سایت (سایت هم سقف خودش را دارد).
_WINDOW = 10 * 60
_LIMIT = 15
_hits: dict[int, list[float]] = {}

_BOT_STATUS = {
    'pending': ('⏳', 'در انتظار پرداخت/تایید'),
    'paid': ('✅', 'پرداخت شد'),
    'processing': ('🔄', 'در حال انجام'),
    'delivered': ('🎉', 'تحویل شد'),
    'completed': ('🎉', 'تکمیل شد'),
    'canceled': ('❌', 'لغو شد'),
    'cancelled': ('❌', 'لغو شد'),
    'failed': ('❌', 'ناموفق'),
}

PROMPT = ('🔎 *پیگیری سفارش*\n'
          'کد پیگیری سفارش سایت (مثل `AS12345678`) یا شمارهٔ سفارش ربات را بفرست.')


_FA_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


def to_jalali(year, month, day):
    """میلادی → شمسی (همان الگوریتمِ account_shop/jalali.py سایت)."""
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    if year > 1600:
        jy, gy = 979, year - 1600
    else:
        jy, gy = 0, year - 621
    gy2 = gy + 1 if month > 2 else gy
    days = (365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
            - 80 + day + g_d_m[month - 1])
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def fa_datetime(value):
    """datetime → «۱۴۰۵/۰۸/۰۵ ساعت ۱۶:۵۶» به وقت تهران."""
    try:
        from zoneinfo import ZoneInfo

        if getattr(value, 'tzinfo', None) is not None:
            value = value.astimezone(ZoneInfo('Asia/Tehran'))
    except Exception:  # noqa: BLE001 — بدون tzdata همان ساعتِ ذخیره‌شده
        pass
    jy, jm, jd = to_jalali(value.year, value.month, value.day)
    return f'{jy}/{jm:02d}/{jd:02d} ساعت {value.hour:02d}:{value.minute:02d}'.translate(_FA_DIGITS)


def track_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton('🔎 پیگیری سفارش', callback_data='track_order')]])


def _allowed(user_id: int) -> bool:
    now = time.monotonic()
    recent = [t for t in _hits.get(user_id, []) if now - t < _WINDOW]
    if len(recent) >= _LIMIT:
        _hits[user_id] = recent
        return False
    recent.append(now)
    _hits[user_id] = recent
    return True


def normalize(text) -> str:
    return str(text or '').translate(_DIGITS).strip()


def looks_like_tracking(text) -> bool:
    """فقط کد سایت؛ عددِ تنها می‌تواند جواب سؤالِ دیگری باشد (مثلاً مبلغ)."""
    return bool(SITE_CODE_RE.match(normalize(text)))


def format_site_order(data) -> str:
    lines = [
        f"📦 <b>سفارش {escape(data.get('code') or '')}</b> — سایت",
        f"{data.get('status_icon') or '•'} وضعیت: <b>{escape(data.get('status_label') or '')}</b>",
        f"💰 مبلغ: {int(data.get('total') or 0):,} تومان",
        f"🕒 ثبت: {escape(data.get('created_at') or '')}",
    ]
    if data.get('payment'):
        lines.append(f"💳 پرداخت: {escape(data['payment'])}")
    if int(data.get('refunded') or 0):
        lines.append(f"↩️ برگشت به کیف پول: {int(data['refunded']):,} تومان")
    items = data.get('lines') or []
    if items:
        lines.append('━━━━━━━━━━━━━━━')
        for line in items:
            state = f" — {escape(line['state'])}" if line.get('state') else ''
            lines.append(f"• {escape(line.get('title') or '')}{state}")
    if data.get('url'):
        lines.append('')
        lines.append(f"🔗 جزئیات کامل در سایت: {escape(data['url'])}")
    return '\n'.join(lines)


def format_bot_order(row, items, gem_infos) -> str:
    order_id, _user_id, _tg, total, status, method, created_at, wallet_paid = row
    icon, label = _BOT_STATUS.get(str(status or '').lower(), ('•', str(status or '—')))
    created = fa_datetime(created_at) if hasattr(created_at, 'year') else ''
    lines = [
        f"📦 <b>سفارش #{order_id}</b> — ربات",
        f"{icon} وضعیت: <b>{escape(label)}</b>",
        f"💰 مبلغ: {int(total or 0):,} تومان"
        + (f" (کیف پول: {int(wallet_paid):,})" if wallet_paid else ''),
    ]
    if created:
        lines.append(f"🕒 ثبت: {created}")
    if method:
        method_fa = {'card': 'کارت‌به‌کارت', 'card_transfer': 'کارت‌به‌کارت', 'wallet': 'کیف پول',
                     'zarinpal': 'درگاه'}.get(str(method), str(method))
        lines.append(f"💳 روش پرداخت: {escape(method_fa)}")
    if items:
        lines.append('━━━━━━━━━━━━━━━')
        for item in items[:10]:
            qty = int(item[3] or 1)
            lines.append(f"• {escape(str(item[1] or 'محصول'))}" + (f" × {qty}" if qty > 1 else ''))
    for info in (gem_infos or [])[:3]:
        if info[2]:
            lines.append(f"🎮 آیدی بازی: <code>{escape(str(info[2]))}</code>")
    return '\n'.join(lines)


async def _reply(update, text, *, html=True):
    kwargs = {'parse_mode': 'HTML'} if html else {'parse_mode': 'Markdown'}
    if update.callback_query:
        await update.callback_query.message.reply_text(text, disable_web_page_preview=True, **kwargs)
    else:
        await update.effective_message.reply_text(text, disable_web_page_preview=True, **kwargs)


async def track_lookup(update: Update, ctx: ContextTypes.DEFAULT_TYPE, raw) -> bool:
    """یک کد را پیگیری می‌کند. False یعنی متن شبیه کد نبود."""
    text = normalize(raw)
    user = update.effective_user
    site = SITE_CODE_RE.match(text)
    bot_match = None if site else BOT_ORDER_RE.match(text)
    if not site and not bot_match:
        return False
    if not _allowed(user.id):
        await _reply(update, 'درخواست پیگیری زیاد بود؛ چند دقیقهٔ دیگر دوباره امتحان کن.', html=False)
        return True

    if site:
        data = await asyncio.to_thread(fetch_site_order, site.group(1).upper(), user.id)
        if data.get('ok'):
            await _reply(update, format_site_order(data))
        elif data.get('error') == 'not_found':
            await _reply(update, '❌ سفارشی با این کد پیگیری پیدا نشد. کد را دوباره چک کن.', html=False)
        elif data.get('error') == 'rate_limited':
            await _reply(update, data.get('message') or 'درخواست زیاد بود.', html=False)
        else:
            await _reply(update, '⚠️ الان به سایت دسترسی نداریم؛ چند دقیقهٔ دیگر دوباره امتحان کن.',
                         html=False)
        return True

    order_id = int(bot_match.group(1))
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = await asyncio.to_thread(
            get_or_create_user, user.id, user.first_name or '', user.last_name or '',
            user.username or '')
        ctx.user_data['db_id'] = db_id
    row = await asyncio.to_thread(get_order_for_tracking, order_id)
    # سفارش ربات فقط برای صاحبش — شماره‌ها پشت سر هم‌اند و قابل حدس.
    if not row or not (str(row[1]) == str(db_id) or str(row[2] or '') == str(user.id)):
        await _reply(update, '❌ سفارشی با این شماره در حساب تو پیدا نشد.', html=False)
        return True
    items, gems = await asyncio.gather(
        asyncio.to_thread(get_order_items, order_id),
        asyncio.to_thread(get_gem_infos_for_order, order_id),
    )
    await _reply(update, format_bot_order(row, items, gems))
    return True


async def track_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    if args and await track_lookup(update, ctx, ' '.join(args)):
        return
    ctx.user_data['await_track'] = True
    await update.message.reply_text(PROMPT, parse_mode='Markdown')


async def track_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    ctx.user_data['await_track'] = True
    await update.callback_query.message.reply_text(PROMPT, parse_mode='Markdown')


async def track_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """از text_router: اگر منتظر کد بودیم یا متن خودش کد سایت است، پیگیری کن."""
    text = update.message.text if update.message else ''
    waiting = ctx.user_data.pop('await_track', False)
    if waiting or looks_like_tracking(text):
        if await track_lookup(update, ctx, text):
            return True
        if waiting:
            await update.message.reply_text(
                '❌ این شبیه کد پیگیری نیست. کد سایت با AS شروع می‌شود (مثل AS12345678) '
                'و سفارش ربات فقط عدد است.')
            return True
    return False
