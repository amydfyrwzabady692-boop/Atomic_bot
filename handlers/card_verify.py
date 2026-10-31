"""کارت‌های بانکیِ احرازشده — ابزارهای مشترک، و بررسیِ ردیف‌های قدیمی.

احراز کارت دیگر پیش از کارت‌به‌کارت یا درگاه خواسته نمی‌شود: احراز هویتِ
کامل (کارت بانکی، کارت ملی یا شناسنامه کنار دست‌نوشته) بعد از پرداخت و فقط بالای سقف است
(handlers/identity.py). کارتِ همان احراز این‌جا «احرازشده» ثبت می‌شود و درگاه
برای صاحبش به همان کارت محدود می‌ماند. دکمه‌های قدیمیِ «احراز کارت» به
احراز هویت می‌رسند؛ ادمین هنوز می‌تواند کارت‌های در صفِ قبلی را بررسی کند.
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from admin_notify import is_admin
from db import approved_cards, get_card_verification, review_card_verification
from review_broadcast import admin_display_name, broadcast_reviewed, review_footer

log = logging.getLogger(__name__)

_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
# فقط callbackهایی که بعد از تایید برای ادامهٔ پرداخت برمی‌گردیم.
_RESUME_RE = re.compile(r'^(?:pay_card_\d+|wpay_card_\d+|pay_zp_\d+|wpay_zp_\d+)$')


def normalize_card(raw) -> str:
    return re.sub(r'\D', '', str(raw or '').translate(_DIGITS))


def luhn_ok(number: str) -> bool:
    if not number.isdigit() or len(number) != 16:
        return False
    total = 0
    for index, ch in enumerate(reversed(number)):
        digit = int(ch)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def normalize_phone(raw) -> str:
    phone = re.sub(r'\D', '', str(raw or '').translate(_DIGITS))
    if phone.startswith('98') and len(phone) == 12:
        phone = '0' + phone[2:]
    return phone


def pretty_card(number: str) -> str:
    return '-'.join(number[i:i + 4] for i in range(0, len(number), 4))


def masked_card(number: str) -> str:
    return f'{number[:4]}-{number[4:6]}**-****-{number[12:]}' if len(number) == 16 else number


async def payer_cards_line(telegram_id) -> str:
    """یک خط برای کپشن رسیدِ ادمین: از کدام کارتِ تاییدشده باید واریز آمده باشد."""
    try:
        cards = await asyncio.to_thread(approved_cards, telegram_id)
    except Exception:  # noqa: BLE001
        return ''
    if not cards:
        return ('ℹ️ خریدار کارت احرازشده ندارد؛ نام صاحب حساب مبدأ را در بانک ببین. '
                'بالای سقفِ خرید، سفارش تا احراز هویتش تحویل نمی‌شود.')
    parts = [f'{pretty_card(c[1])} ({c[2]})' for c in cards[:3]]
    return '💳 کارت‌های احرازشده: ' + ' | '.join(parts) + '\nواریز باید از یکی از همین‌ها باشد.'


# ── دکمه‌های قدیمی ───────────────────────────────────────────────────


async def cverify_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """«احراز کارت»ِ پیام‌های قدیمی: دیگر لازم نیست (قاعدهٔ احراز گفته نمی‌شود)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        'احراز کارت جداگانه دیگر لازم نیست؛ کارت‌به‌کارت و درگاه بدون آن باز است.',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('🏠 منوی اصلی', callback_data='home')],
        ]))


def _resume_kb(resume):
    rows = []
    if resume and _RESUME_RE.match(resume):
        rows.append([InlineKeyboardButton('💳 ادامهٔ پرداخت', callback_data=resume)])
    rows.append([InlineKeyboardButton('🏠 منوی اصلی', callback_data='home')])
    return InlineKeyboardMarkup(rows)


# ── تصمیم ادمین ──────────────────────────────────────────────────────


async def cverify_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer('دسترسی نداری', show_alert=True)
        return
    approve = query.data.startswith('cverify_ok_')
    try:
        card_id = int(query.data.rsplit('_', 1)[-1])
    except ValueError:
        await query.answer('دادهٔ نامعتبر', show_alert=True)
        return
    name = admin_display_name(update.effective_user)
    decided = await asyncio.to_thread(
        review_card_verification, card_id, approve=approve, reviewer=name,
        reason='' if approve else 'مدارک نامعتبر یا ناخوانا')
    row = await asyncio.to_thread(get_card_verification, card_id)
    if not decided:
        who = (row[8] if row else '') or 'ادمین دیگری'
        await query.answer(f'قبلاً بررسی شده ({who})', show_alert=True)
        return
    await query.answer('انجام شد')
    footer = review_footer(approved=approve, reviewer_name=name)
    await broadcast_reviewed(ctx.bot, 'bot_card', card_id, approved=approve,
                             reviewer_name=name, result_text='', extra_admins=[])
    try:
        await query.edit_message_caption(caption=footer, reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    if not row:
        return
    tg, number, resume = row[1], row[2], row[7]
    try:
        if approve:
            await ctx.bot.send_message(
                chat_id=int(tg),
                text=(f'✅ کارت {masked_card(number)} تایید شد.\n'
                      'حالا می‌توانی با همین کارت کارت‌به‌کارت کنی.'),
                reply_markup=_resume_kb(resume))
        else:
            await ctx.bot.send_message(
                chat_id=int(tg),
                text=f'❌ کارت {masked_card(number)} تایید نشد.')
    except Exception as exc:  # noqa: BLE001 — کاربر ربات را بسته
        log.warning('card verification result not delivered tg=%s: %s', tg, exc)


def card_verify_conversation_handler():
    """دکمه‌های «احراز کارت» در پیام‌های قدیمی."""
    return CallbackQueryHandler(cverify_start, pattern=r'^cverify_go_[a-z_0-9]+$')
