"""تحویلِ صفِ اعلان‌های سایت به ادمین‌ها، و تایید/رد احراز کارتِ سایت.

سایت روی سرور ایران است و api.telegram.org از آن‌جا فیلتر است؛ ارسال
مستقیمِ رسید و سفارش جم بی‌صدا شکست می‌خورد. سایت هر اعلانی را که مستقیم
نرسید در صف نگه می‌دارد و این حلقه هر چند ثانیه برش می‌دارد.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from admin_notify import admin_ids, credential_admin_ids, is_admin
from review_broadcast import admin_display_name, broadcast_reviewed, remember_notices, review_footer
from site_api import (
    ack_site_outbox, call_site_card_review, fetch_site_outbox, fetch_site_photo, fetch_site_ping,
)

log = logging.getLogger(__name__)

OUTBOX_INTERVAL = max(5, int(os.getenv('SITE_OUTBOX_INTERVAL', '15') or 15))
_CAPTION_LIMIT = 1024
_TEXT_LIMIT = 4096


def _markup(raw):
    """دیکشنریِ inline_keyboard سایت → InlineKeyboardMarkup."""
    rows = []
    for row in (raw or {}).get('inline_keyboard') or []:
        buttons = []
        for btn in row or []:
            text = str(btn.get('text') or '').strip()
            if not text:
                continue
            if btn.get('callback_data'):
                buttons.append(InlineKeyboardButton(text, callback_data=str(btn['callback_data'])[:64]))
            elif btn.get('url'):
                buttons.append(InlineKeyboardButton(text, url=str(btn['url'])))
        if buttons:
            rows.append(buttons)
    return InlineKeyboardMarkup(rows) if rows else None


def _recipients(item):
    audience = item.get('audience')
    if audience == 'user':
        # پیامِ خریدار: فقط به خودش، هرگز به ادمین‌ها.
        try:
            return [int(item.get('chat_id'))]
        except (TypeError, ValueError):
            return []
    ids = credential_admin_ids() if audience == 'credential' else []
    return ids or admin_ids()


# خریداری که ربات را بلاک کرده یا هرگز استارت نزده: تلاش دوباره بی‌فایده است.
_PERMANENT_ERRORS = ('Forbidden', 'chat not found', 'user is deactivated',
                     'bot was blocked', 'bot can\'t initiate')


def _is_permanent(exc) -> bool:
    text = f'{type(exc).__name__}: {exc}'
    return any(marker.lower() in text.lower() for marker in _PERMANENT_ERRORS)


async def _send_one(bot, chat_id, item, photo, markup):
    """(message_id یا None, file_id عکس برای ارسال‌های بعدی, خطای دائمی؟)."""
    text = str(item.get('text') or '')
    permanent = False
    for parse_mode in ('HTML', None):
        try:
            if photo is not None:
                media = photo if isinstance(photo, str) else io.BytesIO(photo)
                msg = await bot.send_photo(chat_id=chat_id, photo=media,
                                           caption=text[:_CAPTION_LIMIT],
                                           parse_mode=parse_mode, reply_markup=markup)
                file_id = msg.photo[-1].file_id if msg.photo else None
                return msg.message_id, file_id, False
            msg = await bot.send_message(chat_id=chat_id, text=text[:_TEXT_LIMIT],
                                         parse_mode=parse_mode, reply_markup=markup,
                                         disable_web_page_preview=True)
            return msg.message_id, None, False
        except Exception as exc:  # noqa: BLE001 — HTML خراب یا چتِ بسته
            log.warning('site outbox send failed chat=%s id=%s mode=%s: %s',
                        chat_id, item.get('id'), parse_mode, exc)
            if _is_permanent(exc):
                permanent = True
                break
    return None, None, permanent


# ── سلامت ارتباط ─────────────────────────────────────────────────────
# اگر ربات چند دقیقه به سایت نرسد، رسید و سفارش جم به هیچ ادمینی نمی‌رسد و
# کسی هم نمی‌فهمد. یک‌بار هشدار، و یک‌بار خبرِ برقراری دوباره.
LINK_ALERT_AFTER = 10 * 60
REMIND_EVERY = 60 * 60
BACKLOG_MINUTES = 15
PING_EVERY = 5 * 60

LINK = {'last_ok': None, 'first_fail': None, 'last_error': '', 'alerted': False,
        'last_reminder': 0.0, 'last_ping': 0.0}


async def _tell_admins(bot, text):
    for aid in admin_ids():
        try:
            await bot.send_message(chat_id=aid, text=text)
        except Exception as exc:  # noqa: BLE001
            log.warning('link alert failed admin=%s: %s', aid, exc)


async def _link_ok(bot, now):
    if LINK['alerted']:
        down = int((now - (LINK['first_fail'] or now)) // 60)
        await _tell_admins(bot, f'✅ ارتباط ربات با سایت دوباره برقرار شد (حدود {down} دقیقه قطع بود).\n'
                                'رسیدها و سفارش‌های جاافتاده همین حالا فرستاده می‌شوند.')
    LINK.update(last_ok=now, first_fail=None, last_error='', alerted=False)


async def _link_failed(bot, now, error):
    LINK['last_error'] = str(error or '')[:200]
    if LINK['first_fail'] is None:
        LINK['first_fail'] = now
    if not LINK['alerted'] and now - LINK['first_fail'] >= LINK_ALERT_AFTER:
        LINK['alerted'] = True
        await _tell_admins(bot, (
            '⚠️ ربات بیش از ۱۰ دقیقه است به سایت نمی‌رسد.\n'
            f'خطا: {LINK["last_error"] or "نامشخص"}\n\n'
            'تا وقتی وصل نشود رسیدهای سایت و سفارش‌های جم به تلگرام نمی‌آیند — '
            'در پنل سایت (بخش رسیدها) ببین. SITE_API_URL و BOT_INTERNAL_SECRET را '
            'روی هر دو سرور چک کن.'))


async def _remind_backlog(bot, now):
    """رسید یا احراز کارتی که بیش از ۱۵ دقیقه منتظر مانده — ساعتی یک یادآوری."""
    if now - LINK['last_ping'] < PING_EVERY:
        return
    LINK['last_ping'] = now
    data = await asyncio.to_thread(fetch_site_ping)
    if not data.get('ok') or now - LINK['last_reminder'] < REMIND_EVERY:
        return
    parts = []
    if (data.get('receipt_oldest_minutes') or 0) >= BACKLOG_MINUTES:
        parts.append(f'🧾 {data.get("pending_receipts")} رسید سایت منتظر بررسی '
                     f'(قدیمی‌ترین: {data["receipt_oldest_minutes"]} دقیقه)')
    if (data.get('card_oldest_minutes') or 0) >= BACKLOG_MINUTES:
        parts.append(f'💳 {data.get("pending_cards")} احراز کارت منتظر بررسی '
                     f'(قدیمی‌ترین: {data["card_oldest_minutes"]} دقیقه)')
    if parts:
        LINK['last_reminder'] = now
        await _tell_admins(bot, '⏰ یادآوری\n' + '\n'.join(parts) +
                           '\n\nخریدار منتظر است؛ از پیام‌های بالا یا پنل سایت بررسی کن.')


async def deliver_site_outbox(bot):
    """یک دورِ برداشت و ارسال. تعداد تحویل‌شده‌ها را برمی‌گرداند."""
    data = await asyncio.to_thread(fetch_site_outbox, 10)
    now = time.time()
    if not data.get('ok'):
        if data.get('error') != 'missing_secret':
            log.warning('site outbox unavailable: %s', data.get('error'))
            await _link_failed(bot, now, data.get('error'))
        return 0
    await _link_ok(bot, now)
    delivered = 0
    for item in data.get('items') or []:
        markup = _markup(item.get('reply_markup'))
        photo = None
        if item.get('photo_url'):
            photo = await asyncio.to_thread(fetch_site_photo, item['photo_url'])
        notices = []
        for chat_id in _recipients(item):
            mid, file_id, permanent = await _send_one(bot, chat_id, item, photo, markup)
            if mid:
                notices.append({'chat_id': int(chat_id), 'message_id': int(mid)})
                if file_id:
                    photo = file_id  # بقیهٔ ادمین‌ها: بدون آپلود دوباره
            elif permanent and item.get('audience') == 'user':
                # خریدار ربات را بلاک کرده: «تحویل‌شده» با message_id صفر، تا سایت
                # هر دو دقیقه تا یک هفته دوباره نفرستدش.
                log.info('buyer chat %s unreachable; dropping notice %s', chat_id, item.get('id'))
                notices.append({'chat_id': int(chat_id), 'message_id': 0})
        kind, _, ref = str(item.get('ref') or '').partition(':')
        if notices and ref.isdigit() and item.get('audience') != 'user':
            remember_notices('site' if kind == 'receipt' else f'site_{kind}', int(ref), notices)
        result = await asyncio.to_thread(ack_site_outbox, item['id'], notices)
        if not result.get('ok'):
            log.warning('site outbox ack failed id=%s: %s', item.get('id'), result.get('error'))
        if any(n['message_id'] for n in notices):
            delivered += 1
    return delivered


async def site_outbox_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            await deliver_site_outbox(app.bot)
            if LINK['last_ok'] and LINK['first_fail'] is None:
                await _remind_backlog(app.bot, time.time())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — حلقه نباید بمیرد
            log.exception('site outbox loop failed')
        await asyncio.sleep(OUTBOX_INTERVAL)


# ── تایید/رد احراز کارت سایت ─────────────────────────────────────────


async def site_card_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer('دسترسی نداری', show_alert=True)
        return
    data = str(query.data or '')
    approve = data.startswith('site_card_ok_')
    try:
        card_id = int(data.rsplit('_', 1)[-1])
    except ValueError:
        await query.answer('دادهٔ نامعتبر', show_alert=True)
        return
    name = admin_display_name(update.effective_user)
    result = await asyncio.to_thread(
        call_site_card_review, card_id, 'approve' if approve else 'reject',
        update.effective_user.id, name,
    )
    if not (result.get('ok') or result.get('already')):
        err = result.get('message') or result.get('error') or 'خطای سایت'
        await query.answer(f'انجام نشد: {err}', show_alert=True)
        return
    approved = result.get('status') == 'approved' if result.get('already') else approve
    reviewer = result.get('reviewed_by') or name
    await query.answer('انجام شد')
    footer = review_footer(approved=approved, reviewer_name=reviewer)
    await broadcast_reviewed(ctx.bot, 'site_card', card_id, approved=approved,
                             reviewer_name=reviewer, result_text='', extra_admins=[])
    try:
        if query.message and (query.message.photo or query.message.document):
            await query.edit_message_caption(caption=footer, reply_markup=None)
        else:
            await query.edit_message_text(text=footer, reply_markup=None)
    except Exception:  # noqa: BLE001 — همان پیام قبلاً ویرایش شده
        pass



async def site_status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """‎/sitestatus‎ — فقط ادمین: ارتباط با سایت و صف‌ها."""
    if not is_admin(update.effective_user.id):
        return
    data = await asyncio.to_thread(fetch_site_ping)
    now = time.time()
    if LINK['last_ok']:
        last = f'{int((now - LINK["last_ok"]) // 60)} دقیقه پیش' if now - LINK['last_ok'] >= 60 else 'همین حالا'
    else:
        last = 'هنوز نه'
    lines = ['🔌 وضعیت ارتباط با سایت', '━━━━━━━━━━━━━━━',
             f'آخرین برداشت موفق از صف: {last}']
    if data.get('ok'):
        lines += [
            '✅ سایت در دسترس است.',
            f'اعلانِ در صف: {data.get("outbox_waiting", 0)}',
            f'رسید منتظر بررسی: {data.get("pending_receipts", 0)}',
            f'احراز کارت منتظر بررسی: {data.get("pending_cards", 0)}',
        ]
    else:
        lines += ['❌ سایت جواب نمی‌دهد.', f'خطا: {data.get("error") or LINK["last_error"] or "نامشخص"}']
        if data.get('error') == 'forbidden':
            lines.append('رمز BOT_INTERNAL_SECRET دو سرور یکی نیست، یا IP ربات در BOT_ALLOWED_IPS سایت نیست.')
        elif data.get('error') == 'missing_secret':
            lines.append('BOT_INTERNAL_SECRET روی سرور ربات تنظیم نشده.')
    await update.message.reply_text('\n'.join(lines))
