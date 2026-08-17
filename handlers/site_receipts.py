"""تایید/رد رسید کارت‌به‌کارت سایت از داخل ربات."""
from __future__ import annotations

import time

from telegram import Update
from telegram.ext import ContextTypes

from admin_notify import is_admin
from keyboards import site_card_confirm_keyboard, site_card_keyboard
from review_broadcast import admin_display_name, broadcast_reviewed, review_footer
from site_api import call_site_review


def _payment_id(data: str) -> int:
    return int(str(data).rsplit('_', 1)[-1])


async def site_review_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    parts = query.data.split('_')
    action = parts[2]
    payment_id = int(parts[3])
    label = (
        'تأیید نهایی این رسید سایت و شروع تحویل/شارژ؟'
        if action == 'ok'
        else 'رد نهایی این رسید سایت؟'
    )
    ctx.user_data['site_receipt_review'] = {
        'action': action, 'payment_id': payment_id, 'armed_at': time.time(),
    }
    await query.answer(label, show_alert=True)
    await query.edit_message_reply_markup(
        reply_markup=site_card_confirm_keyboard(payment_id, action)
    )


async def site_review_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    payment_id = _payment_id(query.data)
    ctx.user_data.pop('site_receipt_review', None)
    await query.answer("بازگشت")
    await query.edit_message_reply_markup(
        reply_markup=site_card_keyboard(payment_id)
    )


async def _finalize_site(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *, want_action: str):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    payment_id = _payment_id(query.data)
    review = ctx.user_data.pop('site_receipt_review', None) or {}
    if (
        review.get('action') != want_action
        or review.get('payment_id') != payment_id
        or time.time() - float(review.get('armed_at') or 0) > 120
    ):
        await query.answer(
            "ابتدا دکمه بررسی را بزن؛ تأیید نهایی ۲ دقیقه اعتبار دارد.",
            show_alert=True,
        )
        return
    await query.answer()
    name = admin_display_name(update.effective_user)
    api_action = 'approve' if want_action == 'ok' else 'reject'
    result = call_site_review(
        payment_id, api_action, update.effective_user.id, name,
    )
    approved = api_action == 'approve'
    reviewed_by = result.get('reviewed_by') or name
    if result.get('ok') or result.get('already'):
        footer = review_footer(approved=approved or result.get('status') == 'approved', reviewer_name=reviewed_by)
        if result.get('already'):
            text = result.get('message') or footer
        else:
            text = footer
        await broadcast_reviewed(
            ctx.bot, 'site', payment_id,
            approved=approved or result.get('status') == 'approved',
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
