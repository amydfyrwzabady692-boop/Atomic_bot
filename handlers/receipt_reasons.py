"""دلیلِ ردِ رسیدِ کارت‌به‌کارت — دکمه‌های آماده یا متنِ نوشته‌شده.

همان متن برای خریدار فرستاده می‌شود تا بداند چه چیزی را درست کند. سه نوع رسید:
o = رسیدِ سفارشِ ربات، w = رسیدِ شارژِ کیف پولِ ربات، s = رسیدِ سایت.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationHandlerStop, CallbackQueryHandler, ContextTypes,
                          MessageHandler, filters)

from admin_notify import is_admin

# همان متن‌های سایت (account/card_transfer.RECEIPT_REJECT_REASONS).
RECEIPT_REJECT_REASONS = {
    'amount': 'مبلغ واریزی با مبلغ سفارش یکی نیست',
    'missing': 'واریزی به حساب فروشگاه نرسیده است',
    'blur': 'رسید ناخوانا یا ناقص است',
    'dup': 'این رسید قبلاً استفاده شده است',
    'card': 'کارت واریزکننده با کارت ثبت‌شده یکی نیست',
}
_REJECT_PREFIX = {'o': 'admin_no', 'w': 'wadmin_no', 's': 'site_no'}
_BACK_PREFIX = {'o': 'admin_review_back', 'w': 'wadmin_review_back', 's': 'site_review_back'}

# ادمینی که «نوشتن دلیل» را زده: پیامِ متنیِ بعدی‌اش دلیلِ رد است.
_PENDING: dict[int, dict] = {}
REASON_TTL = 15 * 60


def reasons_keyboard(kind, item_id):
    item_id = int(item_id)
    rows = [[InlineKeyboardButton(f'❌ {label}',
                                  callback_data=f'{_REJECT_PREFIX[kind]}_{item_id}_{code}')]
            for code, label in RECEIPT_REJECT_REASONS.items()]
    rows.append([InlineKeyboardButton('✍️ نوشتن دلیل دیگر', callback_data=f'rcpt_why_{kind}_{item_id}')])
    rows.append([InlineKeyboardButton('🔙 بازگشت', callback_data=f'{_BACK_PREFIX[kind]}_{item_id}')])
    return InlineKeyboardMarkup(rows)


def parse_reject(data):
    """('admin_no_12_amount') → (12, 'مبلغ ...'); بدون کد → (12, '')."""
    parts = str(data or '').split('_')
    if parts and parts[-1].isdigit():
        return int(parts[-1]), ''
    if len(parts) >= 2 and parts[-2].isdigit():
        return int(parts[-2]), RECEIPT_REJECT_REASONS.get(parts[-1], '')
    raise ValueError(f'bad receipt reject data: {data!r}')


def reason_line(reason):
    return f'\nدلیل: {reason}' if reason else ''


class TypedQuery:
    """دلیلِ نوشته‌شده جای کلیک: همان کارهای CallbackQuery روی پیامِ رسیدِ ذخیره‌شده."""

    def __init__(self, bot, message, pending):
        self._bot = bot
        self._reply_to = message
        self._pending = pending
        self.from_user = message.from_user
        self.message = SimpleNamespace(photo=pending.get('photo'), document=None,
                                       chat_id=pending.get('chat_id'),
                                       caption=pending.get('caption'), text=pending.get('text'))

    async def answer(self, text='', show_alert=False):
        if text:
            await self._reply_to.reply_text(text)

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        if self._pending.get('message_id'):
            await self._bot.edit_message_text(chat_id=self._pending['chat_id'],
                                              message_id=self._pending['message_id'],
                                              text=text, reply_markup=reply_markup,
                                              parse_mode=parse_mode)

    async def edit_message_caption(self, caption, reply_markup=None, parse_mode=None):
        if self._pending.get('message_id'):
            await self._bot.edit_message_caption(chat_id=self._pending['chat_id'],
                                                 message_id=self._pending['message_id'],
                                                 caption=caption, reply_markup=reply_markup,
                                                 parse_mode=parse_mode)


async def receipt_reason_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """rcpt_why_<o|w|s>_<id>: دلیل را بنویس؛ پیامِ بعدیِ همین ادمین دلیل است."""
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer('دسترسی نداری', show_alert=True)
        return
    parts = str(query.data or '').split('_')
    if len(parts) != 4 or parts[2] not in _REJECT_PREFIX or not parts[3].isdigit():
        await query.answer('دادهٔ نامعتبر', show_alert=True)
        return
    message = query.message
    _PENDING[update.effective_user.id] = {
        'kind': parts[2], 'id': int(parts[3]), 'at': time.time(),
        'chat_id': message.chat_id if message else None,
        'message_id': message.message_id if message else None,
        'photo': bool(message and (message.photo or message.document)),
        'caption': getattr(message, 'caption', None), 'text': getattr(message, 'text', None),
    }
    await query.answer()
    await ctx.bot.send_message(
        chat_id=update.effective_user.id,
        text=('✍️ دلیل ردِ این رسید را بنویس و بفرست؛ همین متن برای خریدار فرستاده می‌شود.\n'
              'برای انصراف بنویس: انصراف'),
        reply_markup=ForceReply(selective=True, input_field_placeholder='دلیل رد…'))


class _Awaiting(filters.MessageFilter):
    def filter(self, message):
        user = getattr(message, 'from_user', None)
        pending = _PENDING.get(getattr(user, 'id', None))
        if pending and time.time() - pending['at'] > REASON_TTL:
            _PENDING.pop(user.id, None)
            return False
        return bool(pending)


async def receipt_reason_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    admin = update.effective_user
    pending = _PENDING.pop(admin.id, None)
    if not pending or not is_admin(admin.id):
        return
    reason = ' '.join((message.text or '').split())[:255]
    if reason in ('انصراف', 'لغو', 'cancel'):
        await message.reply_text('رد لغو شد؛ رسید هنوز منتظر بررسی است.')
        raise ApplicationHandlerStop
    if len(reason) < 3:
        _PENDING[admin.id] = pending
        await message.reply_text('دلیل خیلی کوتاه است؛ یک جملهٔ روشن بنویس (یا: انصراف).')
        raise ApplicationHandlerStop
    query = TypedQuery(ctx.bot, message, pending)
    if pending['kind'] == 'o':
        from handlers.payment import reject_order_receipt

        await reject_order_receipt(ctx.bot, admin, pending['id'], reason, query)
    elif pending['kind'] == 'w':
        from handlers.wallet import reject_wallet_receipt

        await reject_wallet_receipt(ctx.bot, admin, pending['id'], reason, query)
    else:
        from handlers.site_receipts import reject_site_receipt

        await reject_site_receipt(ctx.bot, admin, pending['id'], reason, query)
    await message.reply_text('✅ رسید با همین دلیل رد شد.')
    raise ApplicationHandlerStop


def receipt_reason_handlers():
    """(handler, group) — متنِ دلیل پیش از قفلِ عضویت و بقیهٔ گفتگوها."""
    return [
        (CallbackQueryHandler(receipt_reason_prompt, pattern=r'^rcpt_why_[ows]_\d+$'), 0),
        (MessageHandler(filters.TEXT & ~filters.COMMAND & _Awaiting(), receipt_reason_text), -2),
    ]
