"""تایید/رد فوری رسید کارت‌به‌کارت سایت از داخل ربات همراه با دلیل رد و پیام به کاربر."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes, filters, MessageHandler

from admin_notify import is_admin
from keyboards import site_card_keyboard
from review_broadcast import admin_display_name, broadcast_reviewed, review_footer
from site_api import call_site_review

RECEIPT_REJECT_REASONS = {
    'blur': 'تصویر فیش/رسید تار، ناقص یا ناخوانا است',
    'amount': 'مبلغ واریزی یا شماره حساب مقصد اشتباه است',
    'fake': 'رسید جعلی، نامعتبر یا تکراری است',
    'unpaid': 'تراکنش ناموفق است و وجهی به حساب واریز نشده است',
    'other': 'رسید واریزی معتبر نیست یا مغایر با سفارش است',
}

_PENDING_RECEIPT: dict[int, dict] = {}
RECEIPT_TTL = 15 * 60


def receipt_reasons_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    pid = int(payment_id)
    rows = [
        [InlineKeyboardButton(f'❌ {label}', callback_data=f'site_rcpt_rj_{pid}_{code}')]
        for code, label in RECEIPT_REJECT_REASONS.items()
    ]
    rows.append([InlineKeyboardButton('✍️ نوشتن دلیل رد دلخواه', callback_data=f'site_rcpt_why_{pid}')])
    rows.append([InlineKeyboardButton('💬 ارسال پیام به کاربر (بدون رد)', callback_data=f'site_rcpt_msg_{pid}')])
    rows.append([InlineKeyboardButton('↩️ بازگشت', callback_data=f'site_review_back_{pid}')])
    return InlineKeyboardMarkup(rows)


def _payment_id(data: str) -> int:
    return int(str(data).rsplit('_', 1)[-1])


def _want_action(data: str) -> str:
    raw = str(data or '')
    if '_no_' in raw or raw.startswith('site_no_'):
        return 'no'
    return 'ok'


async def site_review_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    data = str(query.data or '')
    if '_no_' in data:
        payment_id = _payment_id(data)
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=receipt_reasons_keyboard(payment_id))
        except Exception:
            pass
        return
    await _finalize_site(update, ctx, want_action='ok')


async def site_review_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    payment_id = _payment_id(query.data)
    await query.answer("بازگشت")
    try:
        await query.edit_message_reply_markup(
            reply_markup=site_card_keyboard(payment_id)
        )
    except Exception:
        pass


async def site_receipt_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """مدیریت دکمه‌های گزینه‌های رد و پیام رسید سایت (site_rcpt_...)."""
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    data = str(query.data or '')
    if data.startswith('site_rcpt_rj_'):
        parts = data.split('_')
        payment_id = int(parts[3])
        code = parts[4] if len(parts) > 4 else 'other'
        reason = RECEIPT_REJECT_REASONS.get(code, 'رسید مورد تایید نیست.')
        await _finalize_site(update, ctx, want_action='no', reason=reason)
        return
    if data.startswith('site_rcpt_why_'):
        payment_id = int(data.rsplit('_', 1)[-1])
        msg = query.message
        _PENDING_RECEIPT[update.effective_user.id] = {
            'id': payment_id, 'action': 'reject', 'at': time.time(),
            'chat_id': msg.chat_id if msg else None,
            'message_id': msg.message_id if msg else None,
            'photo': bool(msg and (msg.photo or msg.document)),
        }
        await query.answer()
        await ctx.bot.send_message(
            chat_id=update.effective_user.id,
            text=(f'✍️ دلیل رد رسید سایت #{payment_id} را بنویس و بفرست؛ '
                  'یک تیکت پشتیبانی در داشبورد کاربر ثبت می‌شود.\n'
                  'برای انصراف بنویس: انصراف'),
            reply_markup=ForceReply(selective=True, input_field_placeholder='دلیل رد…'))
        return
    if data.startswith('site_rcpt_msg_'):
        payment_id = int(data.rsplit('_', 1)[-1])
        msg = query.message
        _PENDING_RECEIPT[update.effective_user.id] = {
            'id': payment_id, 'action': 'message', 'at': time.time(),
            'chat_id': msg.chat_id if msg else None,
            'message_id': msg.message_id if msg else None,
            'photo': bool(msg and (msg.photo or msg.document)),
        }
        await query.answer()
        await ctx.bot.send_message(
            chat_id=update.effective_user.id,
            text=(f'💬 پیام مورد نظر برای ارسال به کاربر رسید سایت #{payment_id} را بنویس و بفرست؛ '
                  'این پیام در قالب تیکت پشتیبانی در داشبورد کاربر ایجاد می‌شود و وضعیت رسید تغییر نمی‌کند.\n'
                  'برای انصراف بنویس: انصراف'),
            reply_markup=ForceReply(selective=True, input_field_placeholder='پیام به کاربر…'))
        return


async def _finalize_site(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *, want_action: str, reason: str = ''):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    payment_id = _payment_id(query.data)
    name = admin_display_name(update.effective_user)
    api_action = 'approve' if want_action == 'ok' else 'reject'
    result = await asyncio.to_thread(
        call_site_review,
        payment_id, api_action, update.effective_user.id, name, note=reason,
    )
    approved = api_action == 'approve' or result.get('status') == 'approved'
    reviewed_by = result.get('reviewed_by') or name
    if result.get('ok') or result.get('already'):
        await query.answer('انجام شد')
        footer = review_footer(approved=approved, reviewer_name=reviewed_by)
        if approved:
            text = footer
        else:
            reason_text = f'\nعلت رد: {reason}' if reason else ''
            text = f'❌ رسید سایت #{payment_id} رد شد.{reason_text}\n\n{footer}'
        await broadcast_reviewed(
            ctx.bot, 'site', payment_id,
            approved=approved,
            reviewer_name=reviewed_by,
            result_text='',
            extra_admins=[],
        )
        try:
            if query.message and (query.message.photo or query.message.document):
                await query.edit_message_caption(caption=text, reply_markup=None)
            else:
                await query.edit_message_text(text=text, reply_markup=None)
        except Exception:
            pass
        return
    err = result.get('message') or result.get('error') or 'خطای سایت'
    await query.answer(f'انجام نشد: {err}', show_alert=True)


async def site_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _finalize_site(update, ctx, want_action='ok')


async def site_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _finalize_site(update, ctx, want_action='no')


class _AwaitingReceiptReason(filters.MessageFilter):
    def filter(self, message):
        user = getattr(message, 'from_user', None)
        pending = _PENDING_RECEIPT.get(getattr(user, 'id', None))
        if pending and time.time() - pending['at'] > RECEIPT_TTL:
            _PENDING_RECEIPT.pop(user.id, None)
            return False
        return bool(pending)


async def receipt_reason_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    admin = update.effective_user
    pending = _PENDING_RECEIPT.pop(admin.id, None)
    if not pending or not is_admin(admin.id):
        return
    text = ' '.join((message.text or '').split())
    if text in ('انصراف', 'لغو', 'cancel'):
        await message.reply_text('عملیات لغو شد؛ رسید هنوز منتظر بررسی است.')
        raise ApplicationHandlerStop
    if len(text) < 3:
        _PENDING_RECEIPT[admin.id] = pending
        await message.reply_text('متن خیلی کوتاه است؛ حداقل چند کلمه بنویس (یا: انصراف).')
        raise ApplicationHandlerStop

    name = admin_display_name(admin)
    payment_id = pending['id']
    action = pending['action']

    if action == 'message':
        res = await asyncio.to_thread(call_site_review, payment_id, 'message', admin.id, name, note=text)
        if res.get('ok'):
            await message.reply_text(f'✅ پیام شما با موفقیت در تیکت پشتیبانی داشبورد کاربر برای رسید #{payment_id} ثبت شد.\nمتن پیام: {text}')
        else:
            await message.reply_text(f'❌ خطا در ثبت پیام: {res.get("message") or res.get("error")}')
        raise ApplicationHandlerStop

    # Rejection with typed reason
    res = await asyncio.to_thread(call_site_review, payment_id, 'reject', admin.id, name, note=text)
    if res.get('ok') or res.get('already'):
        footer = review_footer(approved=False, reviewer_name=name)
        result_str = f'❌ رسید سایت #{payment_id} رد شد.\nعلت رد: {text}\n\n{footer}'
        await broadcast_reviewed(
            ctx.bot, 'site', payment_id,
            approved=False,
            reviewer_name=name,
            result_text='',
            extra_admins=[],
        )
        try:
            if pending.get('photo'):
                await ctx.bot.edit_message_caption(
                    chat_id=pending['chat_id'], message_id=pending['message_id'],
                    caption=result_str, reply_markup=None,
                )
            else:
                await ctx.bot.edit_message_text(
                    chat_id=pending['chat_id'], message_id=pending['message_id'],
                    text=result_str, reply_markup=None,
                )
        except Exception:
            pass
        await message.reply_text(f'✅ رسید #{payment_id} رد شد و تیکت پشتیبانی برای کاربر ایجاد گردید.')
    else:
        err = res.get('message') or res.get('error') or 'خطای سرور سایت'
        await message.reply_text(f'❌ انجام نشد: {err}')
    raise ApplicationHandlerStop


def receipt_reason_handler():
    return MessageHandler(filters.TEXT & ~filters.COMMAND & _AwaitingReceiptReason(), receipt_reason_text)
