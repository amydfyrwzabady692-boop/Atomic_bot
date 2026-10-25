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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from admin_notify import admin_ids, credential_admin_ids, is_admin
from review_broadcast import admin_display_name, broadcast_reviewed, remember_notices, review_footer
from site_api import ack_site_outbox, call_site_card_review, fetch_site_outbox, fetch_site_photo

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


def _recipients(audience):
    ids = credential_admin_ids() if audience == 'credential' else []
    return ids or admin_ids()


async def _send_one(bot, chat_id, item, photo, markup):
    """(message_id یا None, file_id عکس برای ارسال‌های بعدی)."""
    text = str(item.get('text') or '')
    for parse_mode in ('HTML', None):
        try:
            if photo is not None:
                media = photo if isinstance(photo, str) else io.BytesIO(photo)
                msg = await bot.send_photo(chat_id=chat_id, photo=media,
                                           caption=text[:_CAPTION_LIMIT],
                                           parse_mode=parse_mode, reply_markup=markup)
                file_id = msg.photo[-1].file_id if msg.photo else None
                return msg.message_id, file_id
            msg = await bot.send_message(chat_id=chat_id, text=text[:_TEXT_LIMIT],
                                         parse_mode=parse_mode, reply_markup=markup,
                                         disable_web_page_preview=True)
            return msg.message_id, None
        except Exception as exc:  # noqa: BLE001 — HTML خراب یا چتِ بسته
            log.warning('site outbox send failed chat=%s id=%s mode=%s: %s',
                        chat_id, item.get('id'), parse_mode, exc)
    return None, None


async def deliver_site_outbox(bot):
    """یک دورِ برداشت و ارسال. تعداد تحویل‌شده‌ها را برمی‌گرداند."""
    data = await asyncio.to_thread(fetch_site_outbox, 10)
    if not data.get('ok'):
        if data.get('error') not in ('missing_secret',):
            log.debug('site outbox unavailable: %s', data.get('error'))
        return 0
    delivered = 0
    for item in data.get('items') or []:
        markup = _markup(item.get('reply_markup'))
        photo = None
        if item.get('photo_url'):
            photo = await asyncio.to_thread(fetch_site_photo, item['photo_url'])
        notices = []
        for chat_id in _recipients(item.get('audience')):
            mid, file_id = await _send_one(bot, chat_id, item, photo, markup)
            if mid:
                notices.append({'chat_id': int(chat_id), 'message_id': int(mid)})
                if file_id:
                    photo = file_id  # بقیهٔ ادمین‌ها: بدون آپلود دوباره
        kind, _, ref = str(item.get('ref') or '').partition(':')
        if notices and ref.isdigit():
            remember_notices('site' if kind == 'receipt' else f'site_{kind}', int(ref), notices)
        result = await asyncio.to_thread(ack_site_outbox, item['id'], notices)
        if not result.get('ok'):
            log.warning('site outbox ack failed id=%s: %s', item.get('id'), result.get('error'))
        if notices:
            delivered += 1
    return delivered


async def site_outbox_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            await deliver_site_outbox(app.bot)
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
