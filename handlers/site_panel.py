"""لیست جداگانه جم با اطلاعات و رسیدهای سایت داخل پنل ربات."""
from __future__ import annotations

import asyncio

from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from admin_notify import is_admin, is_credential_admin
from keyboards import site_card_keyboard
from site_api import (
    call_site_credential_action,
    fetch_site_credential,
    fetch_site_credentials,
    fetch_site_ops,
    fetch_site_receipt,
    fetch_site_receipts,
)

_STATUS = {
    'ready': '🟢 آماده بررسی',
    'needs_info': '🟠 اطلاعات ناقص',
    'awaiting_payment': '⏳ در انتظار پرداخت',
    'completed': '✅ تکمیل',
    'deleted': '🗑 حذف‌شده',
    'refunded': '💸 ریفاند',
}


def site_ops_counts():
    data = fetch_site_ops()
    return {
        'site_ready_creds': int(data.get('ready_credentials') or 0),
        'site_receipts': int(data.get('pending_receipts') or 0),
    }


def _kb(rows):
    return InlineKeyboardMarkup(rows)


def _back(callback):
    return [InlineKeyboardButton('🔙 بازگشت', callback_data=callback)]


def _html_esc(value):
    return (
        str(value or '')
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def _copy_btn(label, value):
    text = str(value or '').strip()
    if not text or len(text) > 256:
        return None
    return InlineKeyboardButton(label, copy_text=CopyTextButton(text=text))


def _split_backup_codes(backup):
    raw = str(backup or '').strip()
    if not raw:
        return []
    codes = []
    for line in raw.replace(',', '\n').replace(';', '\n').splitlines():
        part = line.strip()
        if part:
            codes.append(part)
    return codes


async def _edit(query, text, rows, *, markdown=False, html=False):
    parse = 'HTML' if html else ('Markdown' if markdown else None)
    try:
        await query.edit_message_text(
            text, parse_mode=parse, reply_markup=_kb(rows),
        )
    except Exception as exc:
        if 'not modified' in str(exc).lower():
            return
        if parse:
            try:
                await query.edit_message_text(text, reply_markup=_kb(rows))
            except Exception as exc2:
                if 'not modified' in str(exc2).lower():
                    return
                raise
        else:
            raise


def _nav_back(user_id, *, receipts=False):
    if receipts:
        return _back('admx_hub_orders')
    if is_admin(user_id):
        return _back('admx_hub_orders')
    return [InlineKeyboardButton('🔙 پنل جم با اطلاعات', callback_data='admx_credhub')]


async def _guard(update, *, receipts=False):
    uid = update.effective_user.id if update.effective_user else None
    query = update.callback_query
    if receipts:
        allowed = is_admin(uid)
    else:
        allowed = is_admin(uid) or is_credential_admin(uid)
    if allowed:
        return True
    if query:
        await query.answer('دسترسی ندارید.', show_alert=True)
    return False


def _cred_reveal_html(item):
    codes = _split_backup_codes(item.get('backup_code'))
    lines = [
        f'🔐 <b>ورود سریع — سفارش سایت #{item.get("order_id")}</b>',
        f'محصول: {_html_esc(item.get("product"))}',
        f'روش: {_html_esc(item.get("login_method_label") or item.get("login_method"))}',
        '',
        '👇 روی کادر بزن / دکمه کپی را بزن، بعد در صفحه ورود paste کن',
        '',
        '📧 <b>شناسه</b>',
        f'<code>{_html_esc(item.get("login_email"))}</code>',
        '',
        '🔑 <b>رمز</b>',
        f'<code>{_html_esc(item.get("login_password"))}</code>',
        '',
        '🛡 <b>بک‌آپ / بازیابی</b>',
    ]
    if codes:
        for i, code in enumerate(codes, 1):
            lines.append(f'{i}) <code>{_html_esc(code)}</code>')
    else:
        lines.append('<i>— ثبت نشده —</i>')
    lines.extend([
        '',
        f'کاربر سایت: {_html_esc(item.get("phone"))}',
        'این سفارش از <b>سایت</b> است؛ با سفارش‌های ربات قاطی نیست.',
    ])
    return '\n'.join(lines)


async def site_panel_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = str(query.data or '')
    receipts = data.startswith('admx_sitereceipt')
    if not await _guard(update, receipts=receipts):
        return
    await query.answer()

    if data == 'admx_sitecreds':
        await _list_site_credentials(update, ctx)
        return
    if data.startswith('admx_sitecredreveal_'):
        await _reveal_site_credential(update, int(data.rsplit('_', 1)[-1]))
        return
    if data.startswith('admx_sitecreddone_'):
        await _act_site_credential(update, int(data.rsplit('_', 1)[-1]), 'complete')
        return
    if data.startswith('admx_sitecredbad_'):
        await _act_site_credential(update, int(data.rsplit('_', 1)[-1]), 'needs_info')
        return
    if data.startswith('admx_sitecred_'):
        await _show_site_credential(update, int(data.rsplit('_', 1)[-1]))
        return
    if data == 'admx_sitereceipts':
        await _list_site_receipts(update, ctx)
        return
    if data.startswith('admx_sitereceipt_'):
        await _show_site_receipt(update, int(data.rsplit('_', 1)[-1]))


async def _list_site_credentials(update, ctx):
    query = update.callback_query
    paid_only = (
        is_credential_admin(update.effective_user.id)
        and not is_admin(update.effective_user.id)
    )
    data = await asyncio.to_thread(fetch_site_credentials, paid_only=paid_only)
    items = data.get('items') or []
    title = '🌐 *جم با اطلاعات سایت*'
    if paid_only:
        title = '🌐 *جم با اطلاعات سایت — پرداخت‌شده*'
    lines = [title, 'جدا از سفارش‌های خود ربات', '━━━━━━━━━━━━━━━']
    buttons = []
    if not data.get('ok'):
        err = data.get('message') or data.get('error') or 'خطای سایت'
        lines.append(f'⚠️ خواندن از سایت نشد: {err}')
    elif not items:
        lines.append('سفارش سایتی ثبت نشده است.')
    for item in items:
        oid = item.get('order_id')
        pk = item.get('id')
        status = _STATUS.get(item.get('credential_status'), item.get('credential_status') or '—')
        product = str(item.get('product') or 'جم با اطلاعات')[:40]
        amount = int(item.get('amount') or 0)
        lines.append(f'#{oid} · {product} · {amount:,} ت · {status}')
        buttons.append([InlineKeyboardButton(
            f'{status} · سایت #{oid}',
            callback_data=f'admx_sitecred_{pk}',
        )])
        if len('\n'.join(lines)) > 3400:
            lines.append('…')
            break
    buttons.extend([
        [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_sitecreds')],
        _nav_back(update.effective_user.id),
    ])
    await _edit(query, '\n'.join(lines), buttons, markdown=True)


async def _show_site_credential(update, pk):
    query = update.callback_query
    item = await asyncio.to_thread(fetch_site_credential, pk)
    if not item.get('ok'):
        err = item.get('message') or item.get('error') or 'پیدا نشد'
        await _edit(query, f'سفارش سایت پیدا نشد ({err}).', [_back('admx_sitecreds')])
        return
    status = _STATUS.get(item.get('credential_status'), item.get('credential_status') or '—')
    has_secret = bool((item.get('login_email') or item.get('login_password') or '').strip())
    order_status = item.get('order_status') or '—'
    text = (
        f'🌐 *سفارش سایت #{item.get("order_id")}*\n'
        f'━━━━━━━━━━━━━━━\n'
        f'محصول: {item.get("product") or "—"}\n'
        f'مبلغ: {int(item.get("amount") or 0):,} تومان\n'
        f'وضعیت سفارش: `{order_status}`\n'
        f'وضعیت اطلاعات: *{status}*\n'
        f'روش ورود: {item.get("login_method_label") or "—"}\n'
        f'کاربر سایت: {item.get("phone") or "—"}\n'
        f'تلگرام: `{item.get("telegram_id") or "—"}`\n'
        f'ایمیل: {item.get("email") or "—"}\n'
        f'منبع: سایت (نه ربات)'
    )
    if item.get('note'):
        text += f'\nیادداشت: {item.get("note")}'
    buttons = []
    if has_secret and order_status in ('paid', 'delivered'):
        buttons.append([InlineKeyboardButton(
            '👁 نمایش اطلاعات ورود', callback_data=f'admx_sitecredreveal_{pk}',
        )])
    if order_status in ('paid', 'delivered') and item.get('credential_status') != 'completed':
        buttons.append([
            InlineKeyboardButton('✅ انجام شد', callback_data=f'admx_sitecreddone_{pk}'),
            InlineKeyboardButton('⚠️ اطلاعات ناقص', callback_data=f'admx_sitecredbad_{pk}'),
        ])
    buttons.extend([
        [InlineKeyboardButton('🔙 لیست سایت', callback_data='admx_sitecreds')],
        _nav_back(update.effective_user.id),
    ])
    await _edit(query, text, buttons, markdown=True)


async def _reveal_site_credential(update, pk):
    query = update.callback_query
    item = await asyncio.to_thread(fetch_site_credential, pk)
    if not item.get('ok'):
        await query.answer('اطلاعات قابل نمایش نیست.', show_alert=True)
        return
    await asyncio.to_thread(
        call_site_credential_action, pk, 'viewed',
        update.effective_user.id, '',
    )
    html = _cred_reveal_html(item)
    identifier = str(item.get('login_email') or '').strip()
    password = str(item.get('login_password') or '').strip()
    codes = _split_backup_codes(item.get('backup_code'))
    buttons = []
    row = []
    id_btn = _copy_btn('📋 کپی شناسه', identifier)
    pass_btn = _copy_btn('📋 کپی رمز', password)
    if id_btn:
        row.append(id_btn)
    if pass_btn:
        row.append(pass_btn)
    if row:
        buttons.append(row)
    all_btn = _copy_btn('📋 کپی همه بک‌آپ‌ها', '\n'.join(codes))
    if all_btn:
        buttons.append([all_btn])
    if item.get('order_status') in ('paid', 'delivered') and item.get('credential_status') != 'completed':
        buttons.append([
            InlineKeyboardButton('✅ انجام شد', callback_data=f'admx_sitecreddone_{pk}'),
            InlineKeyboardButton('⚠️ اطلاعات ناقص', callback_data=f'admx_sitecredbad_{pk}'),
        ])
    buttons.extend([
        [InlineKeyboardButton('🔒 بستن جزئیات', callback_data=f'admx_sitecred_{pk}')],
        [InlineKeyboardButton('🔙 لیست سایت', callback_data='admx_sitecreds')],
    ])
    await _edit(query, html, buttons, html=True)


async def _act_site_credential(update, pk, action):
    query = update.callback_query
    result = await asyncio.to_thread(
        call_site_credential_action,
        pk, action, update.effective_user.id,
        update.effective_user.full_name or '',
    )
    if not result.get('ok'):
        err = result.get('message') or result.get('error') or 'انجام نشد'
        await query.answer(f'انجام نشد: {err}', show_alert=True)
        return
    if action == 'complete':
        text = '✅ سفارش سایت تکمیل شد و به کاربر سایت خبر داده شد.'
    else:
        text = '⚠️ اطلاعات ناقص برای سفارش سایت ثبت و به کاربر خبر داده شد.'
    if result.get('already'):
        text = result.get('message') or text
    await _edit(query, text, [_back('admx_sitecreds')])


async def _list_site_receipts(update, ctx):
    query = update.callback_query
    data = await asyncio.to_thread(fetch_site_receipts)
    items = data.get('items') or []
    lines = [
        '🌐 *رسیدهای کارت‌به‌کارت سایت*',
        'جدا از رسیدهای خود ربات',
        '━━━━━━━━━━━━━━━',
    ]
    buttons = []
    if not data.get('ok'):
        err = data.get('message') or data.get('error') or 'خطای سایت'
        lines.append(f'⚠️ خواندن از سایت نشد: {err}')
    elif not items:
        lines.append('✅ رسید تاییدنشده سایتی وجود ندارد.')
    for item in items:
        pid = item.get('id')
        amount = int(item.get('amount') or 0)
        kind = item.get('kind') or 'سایت'
        tracking = item.get('tracking_code') or '—'
        lines.append(
            f'#{pid} · {kind} · {amount:,} ت · `{tracking}`'
        )
        buttons.append([InlineKeyboardButton(
            f'🖼 رسید سایت #{pid}',
            callback_data=f'admx_sitereceipt_{pid}',
        )])
    buttons.extend([
        [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_sitereceipts')],
        _nav_back(update.effective_user.id, receipts=True),
    ])
    await _edit(query, '\n'.join(lines), buttons, markdown=True)


async def _show_site_receipt(update, pk):
    query = update.callback_query
    item = await asyncio.to_thread(fetch_site_receipt, pk)
    if not item.get('ok'):
        err = item.get('message') or item.get('error') or 'پیدا نشد'
        await _edit(query, f'رسید سایت پیدا نشد ({err}).', [_back('admx_sitereceipts')])
        return
    caption = (
        f'🌐 رسید سایت #{pk}\n'
        f'نوع: {item.get("kind") or "—"}\n'
        f'کد پیگیری: {item.get("tracking_code") or "—"}\n'
        f'مبلغ: {int(item.get("amount") or 0):,} تومان\n'
        f'سفارش: #{item.get("order_id") or "—"}\n'
        f'کاربر: {item.get("phone") or "—"}\n'
        f'وضعیت: {item.get("status") or "—"}'
    )
    review_kb = site_card_keyboard(pk)
    photo_url = (item.get('photo_url') or '').strip()
    sent = False
    if photo_url:
        try:
            await query.message.reply_photo(
                photo=photo_url, caption=caption, reply_markup=review_kb,
            )
            sent = True
        except Exception:
            try:
                await query.message.reply_document(
                    document=photo_url, caption=caption, reply_markup=review_kb,
                )
                sent = True
            except Exception:
                sent = False
    if sent:
        await query.edit_message_text(
            f'🖼 تصویر رسید سایت #{pk} در پیام بعدی است.\n'
            'از دکمه‌های زیر همان عکس تایید یا رد کن.',
            reply_markup=_kb([
                [InlineKeyboardButton('🔄 لیست رسیدهای سایت', callback_data='admx_sitereceipts')],
                _nav_back(update.effective_user.id, receipts=True),
            ]),
        )
        return
    extra = '\n\n⚠️ تصویر رسید از سایت نیامد؛ از همین‌جا تایید/رد کن.'
    await _edit(
        query,
        caption + extra,
        [
            [
                InlineKeyboardButton('✅ تأیید رسید', callback_data=f'site_review_ok_{pk}'),
                InlineKeyboardButton('❌ رد رسید', callback_data=f'site_review_no_{pk}'),
            ],
            [InlineKeyboardButton('🔙 لیست رسیدهای سایت', callback_data='admx_sitereceipts')],
        ],
    )
