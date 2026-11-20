"""تایید/رد فوری رسید کارت‌به‌کارت سایت از داخل ربات."""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from admin_notify import is_admin
from keyboards import site_card_keyboard
from review_broadcast import admin_display_name, broadcast_reviewed, review_footer
from site_api import call_site_review


def _payment_id(data: str) -> int:
    return int(str(data).rsplit('_', 1)[-1])


def _want_action(data: str) -> str:
    raw = str(data or '')
    if '_no_' in raw or raw.startswith('site_no_'):
        return 'no'
    return 'ok'


async def site_review_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """تایید یک‌کلیکی است؛ رد اول دلیل می‌خواهد (همان برای خریدار فرستاده می‌شود)."""
    query = update.callback_query
    if _want_action(query.data) == 'no':
        if not is_admin(update.effective_user.id):
            await query.answer("دسترسی نداری", show_alert=True)
            return
        from handlers.receipt_reasons import reasons_keyboard

        await query.answer('دلیل رد را انتخاب کن')
        await query.edit_message_reply_markup(
            reply_markup=reasons_keyboard('s', _payment_id(query.data)))
        return
    await _finalize_site(update, ctx, want_action='ok')


async def site_review_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    payment_id = _payment_id(query.data)
    await query.answer("بازگشت")
    await query.edit_message_reply_markup(
        reply_markup=site_card_keyboard(payment_id)
    )


async def _finalize_site(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *, want_action: str):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    if want_action == 'no':
        from handlers.receipt_reasons import parse_reject

        payment_id, reason = parse_reject(query.data)
        await reject_site_receipt(ctx.bot, update.effective_user, payment_id, reason, query)
        return
    await _apply_site(ctx.bot, update.effective_user, _payment_id(query.data), 'approve', '', query)


async def reject_site_receipt(bot, admin_user, payment_id, reason, query):
    await _apply_site(bot, admin_user, payment_id, 'reject', reason, query)


async def _apply_site(bot, admin_user, payment_id, api_action, reason, query):
    from handlers.receipt_reasons import reason_line
    from site_api import call_site_review_with_reason

    name = admin_display_name(admin_user)
    if api_action == 'reject':
        result = await asyncio.to_thread(call_site_review_with_reason, payment_id,
                                         admin_user.id, name, reason)
    else:
        result = await asyncio.to_thread(call_site_review, payment_id, api_action,
                                         admin_user.id, name)
    approved = api_action == 'approve' or result.get('status') == 'approved'
    reviewed_by = result.get('reviewed_by') or name
    if result.get('ok') or result.get('already'):
        await query.answer('انجام شد')
        footer = review_footer(approved=approved, reviewer_name=reviewed_by)
        text = result.get('message') or footer if result.get('already') else footer
        if not approved and not result.get('already'):
            text += reason_line(reason)
        await broadcast_reviewed(
            bot, 'site', payment_id,
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
