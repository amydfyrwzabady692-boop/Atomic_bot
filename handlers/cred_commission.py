"""صورتحساب سهم ادمین جم با اطلاعات — ۴۰٪ سود بسته، طلب/تسویه."""
from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from admin_notify import admin_id, is_admin, is_credential_admin, notify_admin, notify_credential_admins
from db import (
    credential_commission_summary, get_credential_commission,
    list_credential_commissions, record_credential_commission_bot,
    record_credential_commission_site, settle_all_owed_credential_commissions,
    settle_credential_commissions,
)

log = logging.getLogger(__name__)


def _kb(rows):
    return InlineKeyboardMarkup(rows)


def _back_credhub():
    return [InlineKeyboardButton('🔙 پنل جم با اطلاعات', callback_data='admx_credhub')]


def _is_owner(user_id) -> bool:
    owner = admin_id()
    try:
        return bool(owner and int(user_id) == int(owner))
    except (TypeError, ValueError):
        return False


def _format_bill_lines(rows, *, summary=None) -> str:
    summary = summary or credential_commission_summary()
    lines = [
        '🧾 *صورتحساب جم با اطلاعات*',
        '━━━━━━━━━━━━━━━',
        f'طلب باز: *{summary["owed_sum"]:,}* تومان ({summary["owed_count"]} سفارش)',
        f'تسویه‌شده تا الان: {summary["settled_sum"]:,} تومان ({summary["settled_count"]})',
    ]
    if rows:
        lines.append('')
        for r in rows[:8]:
            st = 'طلب' if r['status'] == 'owed' else 'تسویه'
            lines.append(
                f'· {r["order_label"]} · {r["title"][:28]}\n'
                f'  فروش {r["sale"]:,} · سود {r["gross_profit"]:,} · '
                f'سهم {r["share_percent"]}% = *{r["commission"]:,}* ت · {st}'
            )
    return '\n'.join(lines)


async def notify_commission_earned(bot, rows, *, admin_tg=''):
    """بعد از انجام شد: صورتحساب برای ادمین اطلاعات + مالک."""
    if not rows:
        return
    summary = await asyncio.to_thread(credential_commission_summary)
    text = _format_bill_lines(rows, summary=summary)
    text += '\n\n(۴۰٪ از *سود بسته* — قفل‌شده روی قیمت/نرخ لحظهٔ پرداخت خریدار)'
    kb = _kb([[InlineKeyboardButton('📋 دفتر طلب/تسویه', callback_data='admx_credpay')]])
    await notify_credential_admins(bot, text, reply_markup=kb)
    # اگر انجام‌دهنده ادمین اطلاعات است و مالک جداست، مالک هم جداگانه می‌گیرد
    # (notify_credential_admins مالک را هم دارد؛ تکرار نکن مگر مالک در لیست نباشد)
    owner = admin_id()
    cred_ids = set()
    try:
        from admin_notify import credential_admin_ids
        cred_ids = set(credential_admin_ids())
    except Exception:
        pass
    if owner and owner not in cred_ids:
        await notify_admin(bot, text, reply_markup=kb)


async def notify_commission_settled(bot, rows, total, *, remaining_sum=0):
    if not rows:
        return
    lines = [
        '✅ *تسویه سهم جم با اطلاعات*',
        '━━━━━━━━━━━━━━━',
        f'مبلغ تسویه‌شده: *{int(total):,}* تومان',
        f'تعداد ردیف: {len(rows)}',
        f'ماندهٔ طلب باز: *{int(remaining_sum):,}* تومان',
    ]
    for r in rows[:6]:
        lines.append(f'· {r["order_label"]} · {r["commission"]:,} ت')
    if len(rows) > 6:
        lines.append('…')
    await notify_credential_admins(bot, '\n'.join(lines))


async def after_bot_credential_done(bot, order_id, *, admin_tg='', already=False):
    rows = await asyncio.to_thread(
        record_credential_commission_bot, order_id, admin_tg=admin_tg, already=already,
    )
    if rows and not already:
        await notify_commission_earned(bot, rows, admin_tg=admin_tg)
    return rows


async def after_site_credential_done(bot, item, *, admin_tg='', already=False):
    """item: پاسخ API سایت بعد از complete (یا detail)."""
    rows = await asyncio.to_thread(
        record_credential_commission_site,
        info_id=item.get('id') or item.get('info_id'),
        order_id=item.get('order_id'),
        sale_toman=item.get('line_amount') or item.get('amount'),
        cost_usd=item.get('supplier_cost_usd'),
        title=item.get('product') or '',
        admin_tg=admin_tg,
        plan_type=item.get('plan_type') or '',
        amount=item.get('gem_amount') or 0,
        tracking=item.get('tracking_code') or '',
        already=already,
    )
    if rows and not already:
        await notify_commission_earned(bot, rows, admin_tg=admin_tg)
    return rows


async def cred_commission_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    if not (is_admin(uid) or is_credential_admin(uid)):
        await query.answer('دسترسی نداری', show_alert=True)
        return
    data = str(query.data or '')
    await query.answer()
    owner = _is_owner(uid)

    if data == 'admx_credpay' or data.startswith('admx_credpay_list_'):
        status = 'owed'
        if data.startswith('admx_credpay_list_'):
            status = data.rsplit('_', 1)[-1]
        summary = await asyncio.to_thread(credential_commission_summary)
        rows = await asyncio.to_thread(list_credential_commissions, status=status, limit=15)
        lines = [
            '💰 *دفتر سهم جم با اطلاعات*',
            '━━━━━━━━━━━━━━━',
            f'طلب باز: *{summary["owed_sum"]:,}* ت ({summary["owed_count"]})',
            f'تسویه‌شده: {summary["settled_sum"]:,} ت ({summary["settled_count"]})',
            '',
            'سهم = ۴۰٪ از سود بسته (فروش − هزینه×نرخ لحظهٔ پرداخت).',
        ]
        buttons = []
        if not rows:
            lines.append('\nردیفی در این فیلتر نیست.')
        for r in rows:
            mark = '🟡 طلب' if r['status'] == 'owed' else '✅ تسویه'
            buttons.append([InlineKeyboardButton(
                f'{mark} · {r["order_label"]} · {r["commission"]:,} ت',
                callback_data=f'admx_credpay_one_{r["id"]}',
            )])
        buttons.append([
            InlineKeyboardButton('🟡 فقط طلب', callback_data='admx_credpay_list_owed'),
            InlineKeyboardButton('✅ تسویه‌شده', callback_data='admx_credpay_list_settled'),
        ])
        buttons.append([InlineKeyboardButton('📋 همه', callback_data='admx_credpay_list_all')])
        if owner and summary['owed_count']:
            buttons.append([InlineKeyboardButton(
                f'💸 تسویه همه ({summary["owed_sum"]:,} ت)',
                callback_data='admx_credpay_allask',
            )])
        buttons.append([InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_credpay')])
        buttons.append(_back_credhub())
        await query.edit_message_text(
            '\n'.join(lines), reply_markup=_kb(buttons), parse_mode='Markdown')
        return

    if data.startswith('admx_credpay_one_'):
        cid = int(data.rsplit('_', 1)[-1])
        row = await asyncio.to_thread(get_credential_commission, cid)
        if not row:
            await query.edit_message_text('این ردیف پیدا نشد.', reply_markup=_kb([_back_credhub()]))
            return
        st = 'طلب' if row['status'] == 'owed' else 'تسویه‌شده'
        text = (
            f'🧾 *{row["order_label"]}*\n'
            f'{row["title"]}\n'
            f'━━━━━━━━━━━━━━━\n'
            f'فروش: {row["sale"]:,} تومان\n'
            f'هزینه: {row["cost_usd"]} دلار × نرخ {row["usd_rate"]:,}\n'
            f'سود بسته: *{row["gross_profit"]:,}* تومان\n'
            f'سهم ادمین ({row["share_percent"]}%): *{row["commission"]:,}* تومان\n'
            f'وضعیت: *{st}*'
        )
        buttons = []
        if owner and row['status'] == 'owed':
            buttons.append([InlineKeyboardButton(
                '✅ تسویه این ردیف', callback_data=f'admx_credpay_ok_{cid}')])
        buttons.append([InlineKeyboardButton('↩️ دفتر', callback_data='admx_credpay')])
        buttons.append(_back_credhub())
        await query.edit_message_text(text, reply_markup=_kb(buttons), parse_mode='Markdown')
        return

    if data == 'admx_credpay_allask':
        if not owner:
            await query.answer('فقط مالک می‌تواند تسویه کند.', show_alert=True)
            return
        summary = await asyncio.to_thread(credential_commission_summary)
        if not summary['owed_count']:
            await query.answer('طلب بازی نیست.', show_alert=True)
            return
        await query.edit_message_text(
            f'همهٔ طلب‌های باز تسویه شوند؟\n'
            f'*{summary["owed_sum"]:,}* تومان برای *{summary["owed_count"]}* سفارش.\n'
            'بعد از واریز واقعی تأیید کن.',
            reply_markup=_kb([
                [InlineKeyboardButton('✅ بله، همه تسویه شد', callback_data='admx_credpay_allok')],
                [InlineKeyboardButton('↩️ نه', callback_data='admx_credpay')],
            ]),
            parse_mode='Markdown',
        )
        return

    if data == 'admx_credpay_allok':
        if not owner:
            await query.answer('فقط مالک می‌تواند تسویه کند.', show_alert=True)
            return
        ok, rows, total, err = await asyncio.to_thread(
            settle_all_owed_credential_commissions, settled_by=str(uid),
            note='تسویه همه از پنل مالک',
        )
        if not ok:
            await query.edit_message_text(err or 'انجام نشد.', reply_markup=_kb([
                [InlineKeyboardButton('↩️ دفتر', callback_data='admx_credpay')],
            ]))
            return
        summary = await asyncio.to_thread(credential_commission_summary)
        await notify_commission_settled(
            ctx.bot, rows, total, remaining_sum=summary['owed_sum'])
        await query.edit_message_text(
            f'✅ تسویه همه انجام شد.\nمبلغ: *{total:,}* تومان\n'
            f'مانده طلب: *{summary["owed_sum"]:,}* تومان',
            reply_markup=_kb([
                [InlineKeyboardButton('📋 دفتر', callback_data='admx_credpay')],
                _back_credhub(),
            ]),
            parse_mode='Markdown',
        )
        return

    if data.startswith('admx_credpay_ok_'):
        if not owner:
            await query.answer('فقط مالک می‌تواند تسویه کند.', show_alert=True)
            return
        cid = int(data.rsplit('_', 1)[-1])
        ok, rows, total, err = await asyncio.to_thread(
            settle_credential_commissions, [cid], settled_by=str(uid),
            note='تسویه تک‌ردیفی',
        )
        if not ok:
            await query.answer(err or 'انجام نشد', show_alert=True)
            return
        summary = await asyncio.to_thread(credential_commission_summary)
        await notify_commission_settled(
            ctx.bot, rows, total, remaining_sum=summary['owed_sum'])
        await query.edit_message_text(
            f'✅ تسویه شد: *{total:,}* تومان\nمانده طلب: *{summary["owed_sum"]:,}* تومان',
            reply_markup=_kb([
                [InlineKeyboardButton('📋 دفتر', callback_data='admx_credpay')],
                _back_credhub(),
            ]),
            parse_mode='Markdown',
        )
        return


def cred_commission_handlers():
    from telegram.ext import CallbackQueryHandler
    return [
        CallbackQueryHandler(cred_commission_panel, pattern=r'^admx_credpay'),
    ]
