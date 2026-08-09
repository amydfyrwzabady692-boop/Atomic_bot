import asyncio
import time
import uuid
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters,
)

from admin_notify import admin_id, invalidate_role_cache, is_admin
import g2bulk
import profitability
from credential_vault import CredentialVaultError, decrypt_credentials, mask_identifier
from forced_join_logic import (
    valid_forced_join_chat_id, valid_telegram_invite_url,
)
from db import (
    add_bot_admin, add_category, add_department, add_gem_package, add_promo_code,
    add_sense_package, add_store_product, admin_list_gems, admin_stats_full,
    delete_simple_record, get_gem_admin, get_order_admin, get_payment_receipt,
    get_promo_code, get_sense_package, get_setting, get_store_product,
    list_all_telegram_ids, list_bot_admins, list_pending_receipts,
    list_pending_wallet_card_charges, list_sense_packages, list_users_filtered, mass_charge_wallets,
    remove_bot_admin, set_setting, simple_list, update_gem_package,
    move_catalogue_item, update_promo_code, update_store_product,
    update_sense_package, list_payment_attempts, payment_attempt_stats,
    list_profit_snapshots, profit_report_stats,
    sync_gem_prices_daily,
    add_forced_join_channel, list_forced_join_channels,
    remove_forced_join_channel,
    financial_health_snapshot, list_admin_actions, log_admin_action,
    admin_operations_snapshot, list_stuck_processing_orders,
    list_low_stock_items, list_wallet_refunded_orders,
    list_credential_orders, get_credential_order, mark_credential_viewed,
    admin_complete_credential_order, admin_reject_credential_info,
)
from handlers.forced_join import invalidate_forced_join_cache
from keyboards import (
    admin_card_keyboard, admin_home_keyboard,
    admin_hub_orders_keyboard, admin_hub_users_keyboard,
    admin_hub_reports_keyboard, admin_hub_support_keyboard,
    admin_hub_system_keyboard,
)

WAIT_VALUE = 50

COMPOUND_FIELDS = {
    'product': (
        ('عنوان محصول', 'مثال: اکانت لول 70'),
        ('قیمت به تومان', 'مثال: 500000'),
        ('موجودی', 'مثال: 2'),
        ('شناسه دسته‌بندی', 'اگر دسته ندارد، عدد 0 را بفرست'),
    ),
    'promo:gift': (
        ('کد هدیه', 'مثال: GIFT100'),
        ('مبلغ هدیه به تومان', 'مثال: 100000'),
        ('تعداد استفاده', 'مثال: 5'),
    ),
    'promo:discount': (
        ('کد تخفیف', 'مثال: OFF20'),
        ('درصد تخفیف', 'عددی بین 1 تا 99'),
        ('تعداد استفاده', 'مثال: 100'),
    ),
    'gemadd': (
        ('عنوان بسته', 'مثال: بسته 110 جمی'),
        ('مقدار جم', 'مثال: 110'),
        ('قیمت به تومان', 'مثال: 200000'),
        ('موجودی', 'مثال: 9999'),
    ),
    'senseadd': (
        ('عنوان پک سنس', 'مثال: پک سنس حرفه‌ای'),
        ('پلتفرم', 'فقط pc یا mobile'),
        ('قیمت به تومان', 'مثال: 1000000'),
        ('توضیح', 'متن کوتاه؛ برای خالی بودن یک خط تیره بفرست'),
    ),
    'adminadd': (
        ('شناسه عددی تلگرام', 'مثال: 123456789'),
        ('نام مدیر', 'مثال: علی'),
    ),
    'premiumadminadd': (
        ('شناسه عددی تلگرام', 'کاربر باید قبلاً ربات را /start کرده باشد'),
        ('نام مدیر پریمیوم', 'مثال: طراح فروشگاه'),
    ),
    'forcedjoinadd': (
        (
            'شناسه کانال',
            'کانال عمومی: @Omid_AtomicFF — کانال خصوصی: شناسه -100...',
        ),
        ('لینک ورود', 'مثال: https://t.me/Omid_AtomicFF یا لینک دعوت خصوصی'),
        ('نام نمایشی', 'مثال: کانال اصلی فروشگاه'),
    ),
}


def _compound_prompt(action, index):
    fields = COMPOUND_FIELDS[action]
    title, hint = fields[index]
    return (
        f"مرحله {index + 1} از {len(fields)} — *{title}*\n"
        f"{hint}\n\n/cancel برای انصراف"
    )


def _split_compound(raw):
    """ورودی یک‌خطی قدیمی را نیز با |، خط جدید یا جداکننده فارسی می‌پذیرد."""
    normalized = raw.replace('│', '|').replace('｜', '|')
    if '|' in normalized:
        return [part.strip() for part in normalized.split('|')]
    if '\n' in normalized:
        return [part.strip() for part in normalized.splitlines() if part.strip()]
    return None


def _kb(rows):
    return InlineKeyboardMarkup(rows)


def _back(target='adm_home'):
    return [InlineKeyboardButton('🔙 بازگشت', callback_data=target)]


async def _guard(update):
    if is_admin(update.effective_user.id):
        return True
    if update.callback_query:
        await update.callback_query.answer('دسترسی ندارید.', show_alert=True)
    elif update.message:
        await update.message.reply_text('دسترسی ندارید.')
    return False


async def _owner_guard(update):
    owner = admin_id()
    if owner and update.effective_user and int(update.effective_user.id) == int(owner):
        return True
    if update.callback_query:
        await update.callback_query.answer(
            'مدیریت دسترسی‌ها فقط برای مدیر اصلی فعال است.', show_alert=True
        )
    elif update.message:
        await update.message.reply_text(
            'مدیریت دسترسی‌ها فقط برای مدیر اصلی فعال است.'
        )
    return False


def _mask_secret(value):
    value = str(value or '')
    if len(value) < 9:
        return 'تنظیم نشده' if not value else '••••'
    return f'{value[:4]}…{value[-4:]}'


def _md_safe(value, limit=80):
    text = str(value or '—').replace('\n', ' ')[:limit]
    for char in ('_', '*', '`', '['):
        text = text.replace(char, ' ')
    return text


def _low_stock_threshold():
    try:
        return max(0, min(int(get_setting('low_stock_threshold', '5') or 5), 10_000))
    except (TypeError, ValueError):
        return 5


async def _edit(query, text, rows, markdown=False):
    try:
        await query.edit_message_text(
            text, parse_mode='Markdown' if markdown else None, reply_markup=_kb(rows)
        )
    except Exception as exc:
        msg = str(exc).lower()
        if 'not modified' in msg:
            return
        if markdown:
            try:
                await query.edit_message_text(text, reply_markup=_kb(rows))
                return
            except Exception as exc2:
                if 'not modified' in str(exc2).lower():
                    return
                raise
        raise


async def admin_ext_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # _guard خودش در صورت عدم دسترسی answer می‌زند — دوباره نزن
    if not await _guard(update):
        return
    data = query.data

    # این شاخه‌ها خودشان answer می‌زنند (جلوگیری از Query is too old / double answer)
    self_answer = (
        data in ('admx_noop', 'admx_pricesync')
        or data.startswith('admx_delconfirm_')
        or data.startswith('admx_massconfirm_')
        or data.startswith('admx_adminremove_')
        or data.startswith('admx_gemtoggle_')
    )
    if not self_answer:
        await query.answer()

    if data == 'admx_hub_orders':
        await query.edit_message_text(
            '📦 *مدیریت سفارش‌ها*\n'
            'باز · ناموفق · گیرکرده · ریفاند · رسید',
            parse_mode='Markdown',
            reply_markup=admin_hub_orders_keyboard(),
        )
        return
    if data == 'admx_hub_users':
        await query.edit_message_text(
            '👥 *مدیریت کاربران*\n'
            'جستجو · موجودی · پیام/شارژ',
            parse_mode='Markdown',
            reply_markup=admin_hub_users_keyboard(),
        )
        return
    if data == 'admx_hub_reports':
        await query.edit_message_text(
            '📊 *گزارش و آمار*\n'
            'فروش · سود جم · سلامت مالی',
            parse_mode='Markdown',
            reply_markup=admin_hub_reports_keyboard(),
        )
        return
    if data == 'admx_hub_support':
        await query.edit_message_text(
            '🎧 *پشتیبانی*\n'
            'تیکت‌ها و دپارتمان‌ها',
            parse_mode='Markdown',
            reply_markup=admin_hub_support_keyboard(),
        )
        return
    if data == 'admx_hub_system':
        await query.edit_message_text(
            '⚙️ *تنظیمات سیستم*\n'
            'عمومی · جوین · مدیران · ظاهر',
            parse_mode='Markdown',
            reply_markup=admin_hub_system_keyboard(),
        )
        return

    if data == 'admx_credentials':
        rows = await asyncio.to_thread(list_credential_orders, 30)
        lines = ['🔐 *سفارش‌های جم با اطلاعات*', '━━━━━━━━━━━━━━━']
        buttons = []
        status_labels = {
            'ready': '🟢 آماده بررسی', 'needs_info': '🟠 اطلاعات ناقص',
            'awaiting_payment': '⏳ در انتظار پرداخت',
            'completed': '✅ تکمیل', 'deleted': '🗑 حذف‌شده',
        }
        for row in rows:
            oid, _tg, amount, order_status, title, method, cred_status = row[:7]
            label = status_labels.get(cred_status, cred_status or order_status)
            lines.append(
                f'#{oid} · {_md_safe(title, 40)} · {amount:,} ت · {label}'
            )
            buttons.append([InlineKeyboardButton(
                f'{label} · #{oid}', callback_data=f'admx_credential_{oid}'
            )])
            if len('\n'.join(lines)) > 3400:
                lines.append('…')
                break
        if not rows:
            lines.append('سفارشی ثبت نشده است.')
        buttons.extend([
            [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_credentials')],
            _back('admx_hub_orders'),
        ])
        await _edit(query, '\n'.join(lines), buttons, markdown=True)
        return

    if data.startswith('admx_credential_'):
        order_id = int(data.rsplit('_', 1)[1])
        row = await asyncio.to_thread(get_credential_order, order_id)
        if not row:
            await _edit(query, 'سفارش اطلاعاتی پیدا نشد.', [_back('admx_credentials')])
            return
        (oid, tg, amount, order_status, title, plan, method, ciphertext,
         cred_status, two_factor, note, viewed_at, deleted_at, username,
         first_name, last_name, _info_id) = row
        method_label = {'google': 'Gmail/Google', 'facebook': 'Facebook', 'vk': 'VK'}.get(method, method)
        status_label = {
            'ready': 'آماده بررسی', 'needs_info': 'اطلاعات ناقص',
            'awaiting_payment': 'در انتظار پرداخت', 'completed': 'تکمیل‌شده',
            'deleted': 'حذف‌شده',
        }.get(cred_status, cred_status or '—')
        masked = '—'
        if ciphertext:
            try:
                masked = mask_identifier(decrypt_credentials(ciphertext)['identifier'])
            except CredentialVaultError:
                masked = 'خطای رمزگشایی'
        text = (
            f'🔐 *سفارش #{oid}*\n'
            f'━━━━━━━━━━━━━━━\n'
            f'محصول: {_md_safe(title, 100)}\n'
            f'مبلغ: {amount:,} تومان\n'
            f'وضعیت سفارش: `{order_status}`\n'
            f'وضعیت اطلاعات: *{status_label}*\n'
            f'روش ورود: {method_label}\n'
            f'شناسه ماسک‌شده: `{_md_safe(masked, 120)}`\n'
            f'تأیید دومرحله‌ای: {"فعال" if two_factor else "غیرفعال"}\n'
            f'کاربر: {_md_safe(first_name)} {_md_safe(last_name)} '
            f'@{_md_safe(username)} · `{tg}`\n'
            f'آخرین مشاهده: {str(viewed_at)[:19] if viewed_at else "هرگز"}\n'
            f'حذف رمز: {str(deleted_at)[:19] if deleted_at else "هنوز نگهداری می‌شود"}'
        )
        if note:
            text += f'\nیادداشت: {_md_safe(note, 300)}'
        buttons = []
        if ciphertext and order_status in ('paid', 'processing'):
            buttons.append([InlineKeyboardButton(
                '👁 نمایش امن اطلاعات (۶۰ ثانیه)', callback_data=f'admx_credreveal_{oid}'
            )])
        if order_status in ('paid', 'processing'):
            buttons.append([
                InlineKeyboardButton('✅ انجام شد', callback_data=f'admx_creddone_{oid}'),
                InlineKeyboardButton('⚠️ اطلاعات ناقص', callback_data=f'admx_credbad_{oid}'),
            ])
        buttons.extend([_back('admx_credentials'), _back('admx_hub_orders')])
        await _edit(query, text, buttons, markdown=True)
        return

    if data.startswith('admx_credreveal_'):
        order_id = int(data.rsplit('_', 1)[1])
        row = await asyncio.to_thread(get_credential_order, order_id)
        if not row or row[3] not in ('paid', 'processing') or not row[7]:
            await query.answer('اطلاعات قابل نمایش نیست.', show_alert=True)
            return
        try:
            secret = decrypt_credentials(row[7])
        except CredentialVaultError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await asyncio.to_thread(mark_credential_viewed, order_id)
        await asyncio.to_thread(
            log_admin_action, update.effective_user.id, 'credential_revealed',
            'order', order_id, 'temporary reveal',
        )
        message = await query.message.reply_text(
            f'🔐 اطلاعات موقت سفارش #{order_id}\n'
            f'روش: {row[6]}\n'
            f'شناسه: {secret["identifier"]}\n'
            f'رمز موقت: {secret["password"]}\n\n'
            'این پیام حداکثر تا ۶۰ ثانیه دیگر حذف می‌شود. کد OTP را از کاربر '
            'در لحظه ورود بگیر و جایی ذخیره نکن.',
            protect_content=True,
            reply_markup=_kb([[InlineKeyboardButton(
                '🗑 همین حالا حذف کن', callback_data='admx_secretdelete'
            )]]),
        )
        async def delete_later():
            await asyncio.sleep(60)
            try:
                await message.delete()
            except Exception:
                pass
        asyncio.create_task(delete_later())
        return

    if data == 'admx_secretdelete':
        try:
            await query.message.delete()
        except Exception:
            await query.answer('حذف پیام ممکن نشد.', show_alert=True)
        return

    if data.startswith(('admx_creddone_', 'admx_credbad_')):
        order_id = int(data.rsplit('_', 1)[1])
        is_done = data.startswith('admx_creddone_')
        fn = admin_complete_credential_order if is_done else admin_reject_credential_info
        ok, tg_id, result = await asyncio.to_thread(fn, order_id)
        if not ok:
            await query.answer(str(result), show_alert=True)
            return
        support_id = get_setting('credential_support_id', '') or get_setting('support_id', '')
        support_id = support_id.strip()
        if support_id and not support_id.startswith('@'):
            support_id = '@' + support_id
        if tg_id:
            try:
                if is_done:
                    user_text = (
                        f'✅ سفارش #{order_id} با موفقیت انجام شد.\n'
                        'اطلاعات ورود ذخیره‌شده حذف شد. برای امنیت، رمز اکانت را تغییر بده.'
                    )
                else:
                    user_text = (
                        f'⚠️ اطلاعات سفارش #{order_id} صحیح یا کامل نیست.\n'
                        f'برای اصلاح اطلاعات با پشتیبانی {support_id or "فروشگاه"} در ارتباط باش '
                        f'و حتماً شماره سفارش #{order_id} را ارسال کن.'
                    )
                await ctx.bot.send_message(chat_id=int(tg_id), text=user_text)
            except Exception:
                pass
        await asyncio.to_thread(
            log_admin_action, update.effective_user.id,
            'credential_completed' if is_done else 'credential_needs_info',
            'order', order_id, 'secret erased',
        )
        await query.edit_message_text(
            ('✅ سفارش تکمیل و اطلاعات ورود حذف شد.' if is_done else
             '⚠️ اطلاعات ناقص ثبت، اطلاعات قبلی حذف و کاربر مطلع شد.'),
            reply_markup=_kb([_back('admx_credentials')]),
        )
        return

    if data == 'admx_ops':
        threshold = _low_stock_threshold()
        ops = admin_operations_snapshot(threshold)
        sales = get_setting('sales_enabled', '1') != '0'
        payments = get_setting('payments_enabled', '1') != '0'
        alerts_enabled = get_setting('admin_alerts_enabled', '1') != '0'
        alert_total = (
            ops['pending_receipts'] + ops['stuck_processing']
            + ops['failed_payments_24h'] + ops['open_tickets']
            + ops['low_gem_stock'] + ops['low_store_stock']
        )
        text = (
            '🚨 *مرکز عملیات مدیر*\n'
            '━━━━━━━━━━━━━━━\n'
            f'فروش: {"✅ فعال" if sales else "⛔ متوقف"} · '
            f'پرداخت: {"✅ فعال" if payments else "⛔ متوقف"}\n'
            f'اعلان خودکار: {"🔔 روشن" if alerts_enabled else "🔕 خاموش"}\n'
            f'هشدار قابل اقدام: *{alert_total:,}*\n\n'
            f'🧾 رسید در انتظار: *{ops["pending_receipts"]:,}*\n'
            f'⏳ سفارش گیرکرده: *{ops["stuck_processing"]:,}*\n'
            f'❌ خطای پرداخت ۲۴ ساعت: *{ops["failed_payments_24h"]:,}*\n'
            f'🎧 تیکت باز: *{ops["open_tickets"]:,}*\n'
            f'📦 موجودی کم: *{ops["low_gem_stock"] + ops["low_store_stock"]:,}*\n'
            f'💰 برگشت به کیف پول (۷ روز): *{ops.get("wallet_refunds_7d", 0):,}*\n\n'
            f'فروش امروز: *{ops["sales_today_amount"]:,} تومان* '
            f'از {ops["sales_today_count"]:,} سفارش'
        )
        await _edit(query, text, [
            [InlineKeyboardButton(
                f'🧾 رسیدها ({ops["pending_receipts"]})', callback_data='admx_receipts'
            ), InlineKeyboardButton(
                f'⏳ گیرکرده‌ها ({ops["stuck_processing"]})', callback_data='admx_stuck'
            )],
            [InlineKeyboardButton(
                f'💰 برگشت کیف پول ({ops.get("wallet_refunds_7d", 0)})',
                callback_data='admx_refunds',
            ), InlineKeyboardButton(
                '📦 منوی سفارش‌ها', callback_data='admx_hub_orders',
            )],
            [InlineKeyboardButton(
                f'📦 موجودی کم ({ops["low_gem_stock"] + ops["low_store_stock"]})',
                callback_data='admx_lowstock',
            ), InlineKeyboardButton(
                f'🎫 تیکت‌ها ({ops["open_tickets"]})', callback_data='adm_tickets'
            )],
            [InlineKeyboardButton('📅 گزارش امروز', callback_data='admx_daily'),
             InlineKeyboardButton('🩺 سلامت مالی', callback_data='admx_health')],
            [InlineKeyboardButton('❌ پرداخت‌های ناموفق', callback_data='admx_payments_failed')],
            [InlineKeyboardButton(
                '🔔 روشن/خاموش اعلان خودکار', callback_data='admx_toggle_alerts'
            )],
            [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_ops')],
            _back(),
        ], markdown=True)
    elif data == 'admx_daily':
        ops = admin_operations_snapshot(_low_stock_threshold())
        text = (
            '📅 *گزارش امروز*\n'
            '━━━━━━━━━━━━━━━\n'
            f'کاربر جدید: *{ops["new_users_today"]:,}*\n'
            f'کل سفارش‌های ساخته‌شده: *{ops["orders_today"]:,}*\n'
            f'فروش موفق: *{ops["sales_today_count"]:,}*\n'
            f'مبلغ فروش: *{ops["sales_today_amount"]:,} تومان*\n'
            f'خطای پرداخت ۲۴ ساعت اخیر: *{ops["failed_payments_24h"]:,}*\n'
            f'رسید منتظر بررسی: *{ops["pending_receipts"]:,}*\n'
            f'سفارش گیرکرده: *{ops["stuck_processing"]:,}*'
        )
        await _edit(query, text, [
            [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_daily')],
            _back('admx_ops'),
        ], markdown=True)
    elif data == 'admx_stuck':
        rows = list_stuck_processing_orders(30)
        lines = ['⏳ *سفارش‌های گیرکرده در پردازش*', '━━━━━━━━━━━━━━━']
        buttons = []
        for oid, tg, total, method, verified_at, g2_status in rows:
            safe_method = str(method or '—').replace('_', '-')
            safe_g2 = str(g2_status or '—').replace('_', '-')
            lines.append(
                f'• `#{oid}` · {int(total):,} ت · {safe_method}\n'
                f'  کاربر `{tg or "—"}` · G2: {safe_g2} · {str(verified_at)[:16]}'
            )
            buttons.append([
                InlineKeyboardButton(
                    f'✅ انجام شد #{oid}', callback_data=f'adm_done_{oid}'
                ),
                InlineKeyboardButton(
                    f'🗑 لغو #{oid}', callback_data=f'adm_cancel_{oid}'
                ),
            ])
            buttons.append([InlineKeyboardButton(
                f'🔁 تلاش مجدد سفارش #{oid}', callback_data=f'adm_retry_{oid}'
            )])
            if len('\n'.join(lines)) > 3500:
                lines.append('…')
                break
        if not rows:
            lines.append('✅ سفارش گیرکرده‌ای وجود ندارد.')
        buttons.extend([
            [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_stuck')],
            _back('admx_hub_orders'),
        ])
        await _edit(query, '\n'.join(lines), buttons, markdown=True)
    elif data == 'admx_refunds':
        rows = list_wallet_refunded_orders(15)
        lines = [
            '💰 *برگشت به کیف پول*',
            'سفارش‌هایی که پولشان به کیف پول کاربر برگشته',
            '━━━━━━━━━━━━━━━',
        ]
        buttons = []
        for (
            oid, tg, total, status, method, refunded, kind, refunded_at, g2_st
        ) in rows:
            kind_label = 'لغو ادمین' if kind == 'admin' else 'شکست G2B'
            when = str(refunded_at)[:16] if refunded_at else '—'
            safe_method = str(method or '—').replace('_', '-')
            safe_g2 = str(g2_st or '—').replace('_', '-')
            lines.append(
                f'• `#{oid}` · ✅ *{int(refunded or 0):,}* ت برگشت\n'
                f'  {kind_label} · سفارش {int(total or 0):,} ت · `{status}`\n'
                f'  کاربر `{tg or "—"}` · {safe_method} · G2:{safe_g2}\n'
                f'  🕒 {when}'
            )
            if tg:
                buttons.append([InlineKeyboardButton(
                    f'👤 کاربر #{oid}', callback_data=f'adm_user_{tg}'
                )])
            if len('\n'.join(lines)) > 3400:
                lines.append('…')
                break
        if not rows:
            lines.append('موردی ثبت نشده است.')
        buttons.extend([
            [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_refunds')],
            _back('admx_hub_orders'),
        ])
        await _edit(query, '\n'.join(lines), buttons, markdown=True)
    elif data == 'admx_lowstock':
        threshold = _low_stock_threshold()
        rows = list_low_stock_items(threshold, 50)
        lines = [
            f'📦 *هشدار موجودی کم — حد {threshold}*',
            '━━━━━━━━━━━━━━━',
        ]
        buttons = []
        for kind, item_id, title, stock in rows:
            label = 'جم دستی' if kind == 'gem' else 'محصول'
            safe_title = _md_safe(title)
            lines.append(f'• {label} `#{item_id}` · {safe_title} · موجودی *{stock}*')
            if kind == 'gem':
                buttons.append([InlineKeyboardButton(
                    f'✏️ مدیریت {str(title)[:24]}', callback_data=f'admx_gem_{item_id}'
                )])
        if not rows:
            lines.append('✅ هیچ موجودی فعالی زیر حد هشدار نیست.')
        buttons.extend([
            [InlineKeyboardButton('⚙️ تغییر حد هشدار', callback_data='admi_lowstock'),
             InlineKeyboardButton('📦 محصولات', callback_data='admx_products')],
            [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_lowstock')],
            _back('admx_ops'),
        ])
        await _edit(query, '\n'.join(lines), buttons, markdown=True)
    elif data == 'admx_shop':
        await _edit(query, '🛍 مدیریت فروشگاه', [
            [InlineKeyboardButton('💎 بسته‌های جم', callback_data='admx_gems')],
            [InlineKeyboardButton('🎯 پک‌های سنس', callback_data='admx_sense')],
            [InlineKeyboardButton('📦 محصولات', callback_data='admx_products'),
             InlineKeyboardButton('🗂 دسته‌بندی‌ها', callback_data='admx_categories')],
            [InlineKeyboardButton('🎁 کد هدیه', callback_data='admx_gift'),
             InlineKeyboardButton('🏷 کد تخفیف', callback_data='admx_discount')],
            _back(),
        ])
    elif data == 'admx_finance':
        zp = get_setting('zarinpal_enabled', '1') != '0'
        card = get_setting('card_transfer_enabled', '1') != '0'
        payments = get_setting('payments_enabled', '1') != '0'
        sales = get_setting('sales_enabled', '1') != '0'
        number = get_setting('card_number', '') or 'تنظیم نشده'
        merchant = get_setting('zarinpal_merchant_id', '')
        await _edit(query, (
            '💳 امور مالی\n\n'
            f'فروش: {"✅" if sales else "⛔ متوقف"}\n'
            f'پرداخت‌های جدید: {"✅" if payments else "⛔ متوقف"}\n'
            f'زرین‌پال: {"✅" if zp else "❌"}\n'
            f'مرچنت: {_mask_secret(merchant) if merchant else "از env سرور"}\n'
            f'کارت‌به‌کارت: {"✅" if card else "❌"}\n'
            f'شماره کارت: {number}'
        ), [
            [InlineKeyboardButton('🚨 توقف/فعال‌سازی فروش', callback_data='admx_toggle_sales')],
            [InlineKeyboardButton('🛡 توقف/فعال‌سازی پرداخت', callback_data='admx_toggle_payments')],
            [InlineKeyboardButton('🩺 سلامت مالی', callback_data='admx_health'),
             InlineKeyboardButton('🧭 تاریخچه مدیران', callback_data='admx_audit')],
            [InlineKeyboardButton('روشن/خاموش زرین‌پال', callback_data='admx_toggle_zp')],
            [InlineKeyboardButton('✏️ مرچنت زرین‌پال', callback_data='admi_zpmerchant')],
            [InlineKeyboardButton('✏️ آدرس callback', callback_data='admi_callback')],
            [InlineKeyboardButton('روشن/خاموش کارت', callback_data='admx_toggle_card')],
            [InlineKeyboardButton('✏️ شماره کارت', callback_data='admi_cardnumber')],
            [InlineKeyboardButton('✏️ صاحب کارت', callback_data='admi_cardholder')],
            [InlineKeyboardButton('✏️ نام بانک', callback_data='admi_cardbank')],
            [InlineKeyboardButton('🧾 رسیدهای تاییدنشده', callback_data='admx_receipts')],
            [InlineKeyboardButton('📒 گزارش پرداخت‌ها', callback_data='admx_payments_all')],
            [InlineKeyboardButton('📈 سود فروش جم', callback_data='admx_profit')],
            [InlineKeyboardButton(
                '💱 نرخ زنده دلار و بهای واقعی پک‌ها',
                callback_data='admx_g2balance',
            )],
            [InlineKeyboardButton('📈 درصد سود جم', callback_data='admi_gemprofit')],
            [InlineKeyboardButton('💱 نرخ دستی دلار (پشتیبان)', callback_data='admi_usdrate')],
            _back(),
        ])
    elif data == 'admx_health':
        health = financial_health_snapshot()
        warning = (
            health['verified_pending_orders']
            + health['expired_pending_orders']
            + health['wallet_mismatches']
        )
        text = (
            '🩺 سلامت مالی ربات\n\n'
            f'{"⚠️ نیازمند بررسی" if warning else "✅ وضعیت عادی"}\n'
            f'سفارش‌های در انتظار: {health["pending_orders"]:,}\n'
            f'سفارش منقضیِ باز: {health["expired_pending_orders"]:,}\n'
            f'پرداخت‌شده ولی هنوز pending: {health["verified_pending_orders"]:,}\n'
            f'سفارش در حال پردازش: {health["processing_orders"]:,}\n'
            f'رسید در انتظار بررسی: {health["pending_receipts"]:,}\n'
            f'شارژ کیف پول پرداخت‌نشده: {health["unpaid_wallet_charges"]:,}\n'
            f'مغایرت موجودی با دفتر کیف پول: {health["wallet_mismatches"]:,}\n'
            f'خطای پرداخت ۲۴ ساعت اخیر: {health["failed_payments_24h"]:,}'
        )
        await _edit(query, text, [
            [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_health')],
            _back('admx_finance'),
        ])
    elif data == 'admx_audit':
        rows = list_admin_actions(30)
        lines = ['🧭 تاریخچه عملیات حساس مدیران', '━━━━━━━━━━━━━━━']
        for _row_id, admin_tg, action, target_type, target_id, details, created in rows:
            target = f'{target_type} #{target_id}' if target_id else target_type
            lines.append(
                f'• {action} · {target or "—"}\n'
                f'  مدیر {admin_tg} · {str(created)[:16]}'
                + (f'\n  {details}' if details else '')
            )
            if len('\n'.join(lines)) > 3700:
                lines.append('…')
                break
        if not rows:
            lines.append('هنوز عملیاتی ثبت نشده است.')
        await _edit(query, '\n'.join(lines), [
            [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_audit')],
            _back('admx_finance'),
        ])
    elif data.startswith('admx_payments_'):
        selected = data.replace('admx_payments_', '')
        status = None if selected == 'all' else selected
        rows = list_payment_attempts(status=status, limit=30)
        counts, success_sum = payment_attempt_stats()
        lines = [
            '📒 *گزارش پرداخت‌ها — ۳۰ روز اخیر*',
            '━━━━━━━━━━━━━━━',
            f'موفق: *{counts.get("success", 0):,}* · '
            f'ناموفق: *{counts.get("failed", 0):,}* · '
            f'لغو/رد: *{counts.get("canceled", 0) + counts.get("rejected", 0):,}*',
            f'جمع رویدادهای موفق: *{success_sum:,}* تومان',
            '',
        ]
        for row in rows:
            (_pid, oid, txid, tg, provider, event, st, amount,
             _authority, ref_id, _message, created) = row
            target = f'سفارش #{oid}' if oid else f'کیف #{txid}' if txid else '—'
            icon = '✅' if st == 'success' else '⏳' if st == 'pending' else '❌'
            provider_label = str(provider).replace('_', '-').replace('`', '')
            event_label = str(event).replace('_', '-').replace('`', '')
            safe_ref = ''.join(
                c for c in str(ref_id or '') if c.isalnum() or c in '-.'
            )[:100]
            entry = (
                f'{icon} {target} · {provider_label}/{event_label}\n'
                f'   {int(amount or 0):,} ت · `{tg or "—"}` · {str(created)[:16]}'
                + (f'\n   پیگیری: `{safe_ref}`' if safe_ref else '')
            )
            if len('\n'.join(lines)) + len(entry) > 3700:
                lines.append('… ادامه رویدادها در فیلترهای بالا قابل مشاهده است.')
                break
            lines.append(entry)
        if not rows:
            lines.append('رویدادی ثبت نشده است.')
        await _edit(query, '\n'.join(lines), [
            [InlineKeyboardButton('همه', callback_data='admx_payments_all'),
             InlineKeyboardButton('موفق', callback_data='admx_payments_success'),
             InlineKeyboardButton('ناموفق', callback_data='admx_payments_failed')],
            _back('admx_finance'),
        ], markdown=True)
    elif data == 'admx_g2balance':
        snapshot, fx = await asyncio.gather(
            asyncio.to_thread(g2bulk.get_inventory_snapshot, True),
            asyncio.to_thread(profitability.get_usd_toman_rate, True),
        )
        if not snapshot.get('ok'):
            rate_line = (
                f'نرخ زنده مبنا: {int(fx["rate"]):,} تومان\n'
                f'منبع: {str(fx.get("source") or "—").replace("_", "-")}\n\n'
                if fx.get('ok') else
                f'نرخ زنده مبنا دریافت نشد: {fx.get("error") or "نامشخص"}\n\n'
            )
            text = (
                '💱 نرخ زنده دلار و بهای واقعی پک‌ها\n'
                '━━━━━━━━━━━━━━━\n'
                f'{rate_line}'
                '❌ دریافت موجودی G2Bulk ناموفق بود.\n'
                f'{snapshot.get("error") or "خطای نامشخص"}'
            )
        else:
            balance = snapshot['balance']
            lines = [
                '💱 *نرخ زنده دلار و بهای واقعی پک‌ها*',
                '━━━━━━━━━━━━━━━',
                (
                    f'نرخ زنده مبنا: *{int(fx["rate"]):,} تومان*'
                    if fx.get('ok') else
                    'نرخ زنده مبنا: *دریافت نشد*'
                ),
                (
                    f'منبع: `{str(fx.get("source") or "—").replace("_", "-")}`'
                    if fx.get('ok') else
                    f'خطا: {fx.get("error") or "نامشخص"}'
                ),
                f'موجودی: *${balance:,.4f} {snapshot["currency"]}*',
                '',
                'بهای تمام‌شده و سود فعلی:',
            ]
            configured = [
                (g[0], g[1], g[2], g[4])
                for g in admin_list_gems() if g[12]
            ]
            live_rows = (
                profitability.calculate_live_pack_costs(
                    configured, snapshot['prices'], fx['rate']
                )
                if fx.get('ok') else []
            )
            for row in live_rows:
                count = (
                    int(balance // row['cost_usd'])
                    if row['cost_usd'] > 0 else 0
                )
                lines.append(
                    f'• {row["amount"]:,} جم · هزینه '
                    f'*{row["cost_toman"]:,} ت* (${row["cost_usd"]:.4f})\n'
                    f'  فروش {row["sale_toman"]:,} ت · سود '
                    f'*{row["gross_profit_toman"]:,} ت* '
                    f'({row["margin_percent"]:.1f}٪) · توان {count}'
                )
            if fx.get('fallback'):
                lines.extend([
                    '',
                    '⚠️ نرخ زنده در دسترس نبود؛ محاسبات بالا با نرخ دستی پشتیبان است.',
                ])
            if not live_rows:
                lines.append('محاسبه قیمت واقعی نیازمند نرخ معتبر و تطبیق پک فعال با کاتالوگ است.')
            text = '\n'.join(lines)
        await _edit(query, text, [
            [
                InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_g2balance'),
                InlineKeyboardButton(
                    '💾 اعمال قیمت با سود ۷٪',
                    callback_data='admx_pricesync',
                ),
            ],
            _back('admx_finance'),
        ], markdown=snapshot.get('ok', False))
    elif data == 'admx_pricesync':
        await query.answer('در حال بروزرسانی قیمت جم…')
        profit = int(get_setting('gem_profit_percent', '7') or '7')
        try:
            updated = await asyncio.to_thread(sync_gem_prices_daily, True)
        except TypeError:
            updated = 0
        if updated:
            text = f'✅ قیمت {updated} بسته جم با نرخ لحظه‌ای و سود {profit}٪ به‌روزرسانی شد.'
        else:
            text = 'ℹ️ قیمت‌ها بروزرسانی شد؛ اگر تغییری نکرد یعنی همان قیمت قبلی معتبر است.'
        await _edit(query, text, [
            _back('adm_home'),
        ], markdown=False)
    elif data == 'admx_profit':
        current_fx = await asyncio.to_thread(
            profitability.get_usd_toman_rate, True
        )
        stats = profit_report_stats()
        margin = (
            stats['profit'] * 100 / stats['sales'] if stats['sales'] else 0
        )
        lines = [
            '📈 *سود ناخالص فروش جم*',
            '━━━━━━━━━━━━━━━',
            (
                f'نرخ زنده فعلی: *{int(current_fx["rate"]):,} تومان*'
                if current_fx.get('ok') else
                'نرخ زنده فعلی: *دریافت نشد*'
            ),
            'نرخ هر فروش از snapshot همان لحظه محاسبه می‌شود و ثابت می‌ماند.',
            '',
            f'فروش‌های دارای snapshot: *{stats["count"]:,}*',
            f'جمع فروش: *{stats["sales"]:,} تومان*',
            f'هزینه تأمین: *{stats["cost"]:,} تومان* '
            f'(${stats["cost_usd"]:,.4f})',
            f'سود ناخالص کل: *{stats["profit"]:,} تومان*',
            f'حاشیه سود: *{margin:.2f}%*',
            '',
            f'۳۰ روز اخیر: *{stats["month_profit"]:,} تومان* سود '
            f'از {stats["month_count"]:,} فروش',
        ]
        if stats['missing']:
            lines.extend([
                '',
                f'⚠️ {stats["missing"]:,} سفارش قدیمی/بدون نرخ دقیق در سود کل '
                'محاسبه نشده است.',
            ])
        rows = list_profit_snapshots(15)
        if rows:
            lines.extend(['', 'آخرین snapshotها:'])
        for row in rows:
            (oid, gems, sale, cost_usd, rate, cost_toman, profit,
             source, created, g2_status) = row
            if profit is None:
                profit_text = 'نرخ ناموجود'
            else:
                profit_text = f'{int(profit):,} ت'
            source_label = str(source or '').replace('_', '-')
            lines.append(
                f'• #{oid} · {gems} جم · فروش {int(sale):,} ت · '
                f'هزینه ${cost_usd:.4f} × '
                f'{int(rate or 0):,} · سود {profit_text}\n'
                f'  {source_label} · {g2_status} · {str(created)[:16]}'
            )
            if len('\n'.join(lines)) > 3650:
                lines.append('…')
                break
        await _edit(query, '\n'.join(lines), [
            [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_profit')],
            _back('admx_finance'),
        ], markdown=True)
    elif data == 'admx_actions':
        await _edit(query, '📨 عملیات کاربران و سفارش‌ها', [
            [InlineKeyboardButton('📣 ارسال پیام همگانی', callback_data='admi_broadcast')],
            [InlineKeyboardButton('💰 شارژ همگانی', callback_data='admi_masscharge')],
            [InlineKeyboardButton('🔎 جستجوی سفارش', callback_data='admi_ordersearch')],
            [InlineKeyboardButton('💵 کاربران دارای موجودی', callback_data='admx_users_balance')],
            [InlineKeyboardButton('👥 کاربران دارای زیرمجموعه', callback_data='admx_users_referral')],
            [InlineKeyboardButton('💳 شماره کارت‌های فعال', callback_data='admx_users_card')],
            _back(),
        ])
    elif data == 'admx_support':
        support_id = get_setting('support_id', '') or 'تنظیم نشده'
        credential_support = get_setting('credential_support_id', '') or 'تنظیم نشده'
        await _edit(query, (
            f'🎧 تنظیمات پشتیبانی\n\nآیدی عمومی: {support_id}\n'
            f'پشتیبان جم با اطلاعات: {credential_support}'
        ), [
            [InlineKeyboardButton('✏️ تنظیم آیدی پشتیبانی', callback_data='admi_supportid')],
            [InlineKeyboardButton(
                '🔐 پشتیبان جم با اطلاعات', callback_data='admi_credentialsupportid'
            )],
            [InlineKeyboardButton('➕ افزودن دپارتمان', callback_data='admi_department')],
            [InlineKeyboardButton('📋 دپارتمان‌ها', callback_data='admx_departments')],
            [InlineKeyboardButton('💬 تیکت‌های باز', callback_data='adm_tickets')],
            _back(),
        ])
    elif data == 'admx_settings':
        await _edit(query, '⚙️ تنظیمات ربات و فروشگاه', [
            [InlineKeyboardButton('✏️ نام فروشگاه', callback_data='admi_shopname')],
            [InlineKeyboardButton('📝 متن خوش‌آمد', callback_data='admi_welcome')],
            [InlineKeyboardButton('📝 متن پشتیبانی', callback_data='admi_supporttext')],
            [InlineKeyboardButton(
                f'📦 حد هشدار موجودی: {_low_stock_threshold()}',
                callback_data='admi_lowstock',
            )],
            [InlineKeyboardButton(
                '📢 مدیریت جوین اجباری', callback_data='admx_forcedjoin'
            )],
            [InlineKeyboardButton('👮 مدیران ربات', callback_data='admx_admins')],
            _back(),
        ])
    elif data == 'admx_forcedjoin':
        channels = list_forced_join_channels(active_only=False)
        lines = [
            '📢 مدیریت جوین اجباری',
            '━━━━━━━━━━━━━━━',
            'ربات باید در هر کانال ادمین باشد تا عضویت کاربران را بررسی کند.',
            '',
        ]
        buttons = []
        for channel_id, chat_id, title, invite_url, active in channels:
            lines.append(
                f'{"✅" if active else "❌"} #{channel_id} · '
                f'{title or chat_id}\n{chat_id}\n{invite_url}'
            )
            buttons.append([InlineKeyboardButton(
                f'🗑 حذف {title or chat_id}',
                callback_data=f'admx_fjdel_{channel_id}',
            )])
        if not channels:
            lines.append('هیچ کانال اجباری ثبت نشده است.')
        buttons.extend([
            [InlineKeyboardButton(
                '➕ افزودن کانال', callback_data='admi_forcedjoin'
            )],
            _back('admx_settings'),
        ])
        await _edit(query, '\n'.join(lines), buttons)
    elif data.startswith('admx_fjdel_'):
        channel_id = int(data.rsplit('_', 1)[1])
        removed = remove_forced_join_channel(channel_id)
        if removed:
            invalidate_forced_join_cache()
        await query.edit_message_text(
            '✅ کانال از جوین اجباری حذف شد.'
            if removed else 'کانال پیدا نشد.',
            reply_markup=_kb([_back('admx_forcedjoin')]),
        )
    elif data == 'admx_stats':
        s = admin_stats_full()
        profit = profit_report_stats()
        text = (
            '📊 *آمار کلی ربات*\n'
            '━━━━━━━━━━━━━━━\n'
            f'تعداد کل کاربران: *{s["users"]:,}*\n'
            f'کاربران دارای خرید: *{s["buyers"]:,}*\n'
            f'موجودی کل کاربران: *{s["wallet_sum"]:,}* تومان\n'
            f'تعداد کل فروش: *{s["sales_count"]:,}*\n'
            f'جمع کل فروش: *{s["sales_sum"]:,}* تومان\n'
            f'سود ناخالص ثبت‌شده جم: *{profit["profit"]:,}* تومان\n'
            f'سفارش‌های باز: *{s["open_orders"]:,}*\n'
            f'رسیدهای در انتظار: *{len(list_pending_receipts(100)):,}*\n'
            f'تیکت باز: *{s["open_tickets"]:,}*'
        )
        await _edit(query, text, [
            [InlineKeyboardButton('📅 گزارش امروز', callback_data='admx_daily'),
             InlineKeyboardButton('🚨 مرکز عملیات', callback_data='admx_ops')],
            _back(),
        ], markdown=True)
    elif data.startswith('admx_users_'):
        kind = data.replace('admx_users_', '')
        titles = {'balance': 'دارای موجودی', 'referral': 'دارای زیرمجموعه',
                  'card': 'شماره کارت فعال'}
        rows = list_users_filtered(kind, limit=12)
        lines = [f'👥 کاربران {titles.get(kind, "")}', '━━━━━━━━━━━━━━━']
        buttons = []
        for tg, name, username, balance, refs, card in rows:
            handle = f'@{username}' if username else (name or '—')
            if kind == 'balance':
                extra = f'{balance:,} ت'
            elif kind == 'referral':
                extra = f'{refs} زیرمجموعه · موجودی {balance:,} ت'
            else:
                extra = f'{card or "—"} · موجودی {balance:,} ت'
            lines.append(f'{_md_safe(handle)} · `{tg}` · {extra}')
            buttons.append([
                InlineKeyboardButton(
                    f'👤 {_md_safe(handle, 18)}', callback_data=f'adm_user_{tg}'
                ),
            ])
            buttons.append([
                InlineKeyboardButton(f'➖ کسر', callback_data=f'adm_wdeduct_{tg}'),
                InlineKeyboardButton(f'➕ شارژ', callback_data=f'adm_wal_{tg}'),
            ])
        if not rows:
            lines.append('موردی ثبت نشده است.')
        else:
            lines.append('\nحداکثر ۱۲ مورد اخیر — برای بقیه از جستجوی کاربر استفاده کن.')
        buttons.append(_back('admx_actions'))
        await _edit(query, '\n'.join(lines), buttons, markdown=True)
    elif data == 'admx_receipts':
        rows = list_pending_receipts()
        wallet_rows = list_pending_wallet_card_charges(30)
        lines = ['🧾 *رسیدهای تاییدنشده*', '━━━━━━━━━━━━━━━']
        buttons = []
        for oid, tg, total, _created, _file_id, _rid in rows:
            lines.append(f'🛒 سفارش `#{oid}` · {total:,} ت · `{tg}`')
            buttons.append([InlineKeyboardButton(
                f'🖼 بررسی رسید سفارش #{oid}', callback_data=f'admx_receipt_{oid}'
            )])
        for row in wallet_rows:
            txid, amount, _authority, _uid, tg, name = row[:6]
            lines.append(
                f'💰 شارژ `#{txid}` · {amount:,} ت · `{tg}` · {_md_safe(name)}'
            )
            buttons.append([InlineKeyboardButton(
                f'🖼 بررسی رسید شارژ #{txid}', callback_data=f'admx_wreceipt_{txid}'
            )])
        if not rows and not wallet_rows:
            lines.append('✅ رسید تاییدنشده‌ای وجود ندارد.')
        buttons.append([InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_receipts')])
        buttons.append(_back('admx_finance'))
        await _edit(query, '\n'.join(lines), buttons, markdown=True)
    elif data.startswith('admx_receipt_'):
        oid = int(data.rsplit('_', 1)[1])
        order = get_order_admin(oid)
        if not order:
            await _edit(query, 'سفارش پیدا نشد.', [_back('admx_receipts')])
        else:
            caption = (
                f'🧾 سفارش #{oid}\n'
                f'کاربر: {_md_safe(order[7])} @{_md_safe(order[8])}\n'
                f'شناسه: {order[1]}\nمبلغ: {order[2]:,} تومان\n'
                f'روش: {order[4]}\nوضعیت: {order[5]}'
            )
            receipt = get_payment_receipt(order_id=oid, pending_only=True)
            review_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        '✅ بررسی برای تأیید',
                        callback_data=f'admin_review_ok_{oid}',
                    ),
                    InlineKeyboardButton(
                        '❌ بررسی برای رد',
                        callback_data=f'admin_review_no_{oid}',
                    ),
                ],
                [InlineKeyboardButton('🔙 لیست رسیدها', callback_data='admx_receipts')],
            ])
            if receipt and receipt[2]:
                sent = False
                try:
                    await query.message.reply_photo(
                        photo=receipt[2], caption=caption,
                        reply_markup=review_kb,
                    )
                    sent = True
                except Exception:
                    try:
                        await query.message.reply_document(
                            document=receipt[2], caption=caption,
                            reply_markup=review_kb,
                        )
                        sent = True
                    except Exception:
                        sent = False
                if sent:
                    await query.edit_message_text(
                        f'🧾 تصویر رسید سفارش #{oid} در پیام بعدی نمایش داده شد.\n'
                        'از دکمه‌های زیر همان عکس تایید/رد کن.',
                        reply_markup=_kb([
                            [InlineKeyboardButton('🔄 لیست رسیدها', callback_data='admx_receipts')],
                            _back('admx_finance'),
                        ]),
                    )
                else:
                    await _edit(
                        query,
                        caption + '\n\n⚠️ ارسال تصویر رسید ناموفق بود؛ file_id نامعتبر است.',
                        [
                            [InlineKeyboardButton(
                                '❌ بررسی برای رد',
                                callback_data=f'admin_review_no_{oid}',
                            )],
                            _back('admx_receipts'),
                        ],
                    )
            else:
                await _edit(query, caption + '\n\n⚠️ فایل تصویری برای این رسید ثبت نشده.', [
                    [InlineKeyboardButton(
                        '❌ بررسی برای رد',
                        callback_data=f'admin_review_no_{oid}',
                    )],
                    _back('admx_receipts'),
                ])
    elif data.startswith('admx_wreceipt_'):
        txid = int(data.rsplit('_', 1)[1])
        from db import get_wallet_tx
        from keyboards import admin_wallet_card_keyboard
        row = get_wallet_tx(txid)
        receipt = get_payment_receipt(wallet_tx_id=txid, pending_only=True)
        if not row:
            await _edit(query, 'تراکنش پیدا نشد.', [_back('admx_receipts')])
        else:
            _id, amount, authority, is_paid, _uid, tg, bal = row
            caption = (
                f'💰 شارژ کیف پول #{txid}\n'
                f'مبلغ: {amount:,} تومان\n'
                f'کاربر: `{tg}`\n'
                f'موجودی فعلی: {int(bal or 0):,} ت\n'
                f'وضعیت: {"پرداخت‌شده" if is_paid else "در انتظار"}'
            )
            if receipt and receipt[2] and not is_paid:
                try:
                    await query.message.reply_photo(
                        photo=receipt[2], caption=caption,
                        reply_markup=admin_wallet_card_keyboard(txid),
                        parse_mode='Markdown',
                    )
                except Exception:
                    try:
                        await query.message.reply_document(
                            document=receipt[2], caption=caption,
                            reply_markup=admin_wallet_card_keyboard(txid),
                            parse_mode='Markdown',
                        )
                    except Exception:
                        await _edit(
                            query,
                            caption + '\n\n⚠️ ارسال تصویر ناموفق بود.',
                            [_back('admx_receipts')],
                            markdown=True,
                        )
                        return
                await query.edit_message_text(
                    f'🧾 تصویر رسید شارژ #{txid} در پیام بعدی نمایش داده شد.',
                    reply_markup=_kb([
                        [InlineKeyboardButton('🔄 لیست رسیدها', callback_data='admx_receipts')],
                        _back('admx_finance'),
                    ]),
                )
            else:
                await _edit(
                    query,
                    caption + '\n\n⚠️ رسید تصویری pending پیدا نشد.',
                    [_back('admx_receipts')],
                    markdown=True,
                )
    elif data == 'admx_gems':
        rows = admin_list_gems()
        buttons = [[InlineKeyboardButton(
            f'{"✅" if r[12] else "❌"} {r[1]} · {r[4]:,} ت',
            callback_data=f'admx_gem_{r[0]}'
        )] for r in rows]
        buttons.extend([
            [InlineKeyboardButton('➕ افزودن بسته جم', callback_data='admi_gemadd')],
            _back('admx_shop'),
        ])
        await _edit(query, '💎 مدیریت بسته‌های جم\nبرای ویرایش یک بسته را انتخاب کن.', buttons)
    elif data.startswith('admx_gem_'):
        gid = int(data.rsplit('_', 1)[1])
        g = get_gem_admin(gid)
        if not g:
            await _edit(query, 'بسته پیدا نشد.', [_back('admx_gems')])
        else:
            await _edit(query, (
                f'💎 {g[1]}\nشناسه: {g[0]}\nمقدار: {g[2]}\n'
                f'قیمت: {g[4]:,} تومان\nموجودی: {g[10]}\n'
                f'قابل خرید (موجود): {"بله" if g[11] else "خیر"}\n'
                f'فعال در کاتالوگ: {"بله" if g[12] else "خیر"}'
            ), [
                [InlineKeyboardButton('✏️ قیمت', callback_data=f'admi_gemprice_{gid}'),
                 InlineKeyboardButton('✏️ عنوان', callback_data=f'admi_gemtitle_{gid}')],
                [InlineKeyboardButton('✏️ موجودی', callback_data=f'admi_gemstock_{gid}'),
                 InlineKeyboardButton('فعال/غیرفعال', callback_data=f'admx_gemtoggle_{gid}')],
                [InlineKeyboardButton('⬆️ بالاتر', callback_data=f'admx_gemmove_up_{gid}'),
                 InlineKeyboardButton('⬇️ پایین‌تر', callback_data=f'admx_gemmove_down_{gid}')],
                _back('admx_gems'),
            ])
    elif data.startswith('admx_gemmove_'):
        _, _, direction, gid = data.split('_', 3)
        move_catalogue_item('gem', int(gid), direction)
        await query.edit_message_text(
            '✅ ترتیب بسته جم تغییر کرد.',
            reply_markup=_kb([_back('admx_gems')]),
        )
    elif data.startswith('admx_gemtoggle_'):
        gid = int(data.rsplit('_', 1)[1])
        g = get_gem_admin(gid)
        if not g:
            await _edit(query, 'بسته پیدا نشد.', [_back('admx_gems')])
        else:
            update_gem_package(gid, 'IsActive', not bool(g[12]))
            g = get_gem_admin(gid)
            await _edit(query, (
                f'💎 {g[1]}\nشناسه: {g[0]}\nمقدار: {g[2]}\n'
                f'قیمت: {g[4]:,} تومان\nموجودی: {g[10]}\n'
                f'قابل خرید (موجود): {"بله" if g[11] else "خیر"}\n'
                f'فعال در کاتالوگ: {"بله" if g[12] else "خیر"}\n\n'
                '✅ وضعیت فعال بودن بسته تغییر کرد.'
            ), [
                [InlineKeyboardButton('✏️ قیمت', callback_data=f'admi_gemprice_{gid}'),
                 InlineKeyboardButton('✏️ عنوان', callback_data=f'admi_gemtitle_{gid}')],
                [InlineKeyboardButton('✏️ موجودی', callback_data=f'admi_gemstock_{gid}'),
                 InlineKeyboardButton('فعال/غیرفعال', callback_data=f'admx_gemtoggle_{gid}')],
                [InlineKeyboardButton('⬆️ بالاتر', callback_data=f'admx_gemmove_up_{gid}'),
                 InlineKeyboardButton('⬇️ پایین‌تر', callback_data=f'admx_gemmove_down_{gid}')],
                _back('admx_gems'),
            ])
    elif data == 'admx_sense':
        rows = list_sense_packages()
        buttons = [[InlineKeyboardButton(
            f'{"✅" if r[5] else "❌"} {r[1]} · {r[2]} · {r[3]:,} ت',
            callback_data=f'admx_senseitem_{r[0]}'
        )] for r in rows]
        buttons.extend([
            [InlineKeyboardButton('➕ افزودن پک سنس', callback_data='admi_senseadd')],
            _back('admx_shop'),
        ])
        await _edit(query, '🎯 مدیریت پک‌های سنس', buttons)
    elif data.startswith('admx_senseitem_'):
        sid = int(data.rsplit('_', 1)[1])
        p = get_sense_package(sid)
        if not p:
            await _edit(query, 'پک پیدا نشد.', [_back('admx_sense')])
        else:
            await _edit(query, (
                f'🎯 {_md_safe(p[1])}\nپلتفرم: {p[2]}\nقیمت: {p[3]:,} تومان\n'
                f'توضیح: {_md_safe(p[4])}\nفعال: {"بله" if p[5] else "خیر"}'
            ), [
                [InlineKeyboardButton('✏️ قیمت', callback_data=f'admi_senseprice_{sid}'),
                 InlineKeyboardButton('✏️ عنوان', callback_data=f'admi_sensetitle_{sid}')],
                [InlineKeyboardButton('✏️ توضیح', callback_data=f'admi_sensedesc_{sid}'),
                 InlineKeyboardButton('فعال/غیرفعال', callback_data=f'admx_sensetoggle_{sid}')],
                [InlineKeyboardButton('⬆️ بالاتر', callback_data=f'admx_sensemove_up_{sid}'),
                 InlineKeyboardButton('⬇️ پایین‌تر', callback_data=f'admx_sensemove_down_{sid}')],
                _back('admx_sense'),
            ])
    elif data.startswith('admx_sensemove_'):
        _, _, direction, sid = data.split('_', 3)
        move_catalogue_item('sense', int(sid), direction)
        await query.edit_message_text(
            '✅ ترتیب پک سنس تغییر کرد.',
            reply_markup=_kb([_back('admx_sense')]),
        )
    elif data.startswith('admx_sensetoggle_'):
        sid = int(data.rsplit('_', 1)[1])
        p = get_sense_package(sid)
        if not p:
            await _edit(query, 'پک پیدا نشد.', [_back('admx_sense')])
        else:
            update_sense_package(sid, 'IsActive', not bool(p[5]))
            await query.edit_message_text(
                '✅ وضعیت پک تغییر کرد.',
                reply_markup=_kb([_back('admx_sense')]),
            )
    elif data.startswith('admx_product_'):
        pid = int(data.rsplit('_', 1)[1])
        p = get_store_product(pid)
        if not p:
            await _edit(query, 'محصول پیدا نشد.', [_back('admx_products')])
        else:
            await _edit(query, (
                f'📦 {p[1]}\nشناسه: {p[0]}\nقیمت: {p[2]:,} تومان\n'
                f'موجودی: {p[3]}\nتوضیح: {p[4] or "—"}\n'
                f'دسته: {p[6] or "—"}\nفعال: {"بله" if p[5] else "خیر"}'
            ), [
                [InlineKeyboardButton('✏️ قیمت', callback_data=f'admi_productprice_{pid}'),
                 InlineKeyboardButton('✏️ عنوان', callback_data=f'admi_producttitle_{pid}')],
                [InlineKeyboardButton('✏️ موجودی', callback_data=f'admi_productstock_{pid}'),
                 InlineKeyboardButton('فعال/غیرفعال', callback_data=f'admx_producttoggle_{pid}')],
                [InlineKeyboardButton(
                    '🗑 حذف', callback_data=f'admx_del_product_{pid}'
                )],
                _back('admx_products'),
            ])
    elif data.startswith('admx_producttoggle_'):
        pid = int(data.rsplit('_', 1)[1])
        p = get_store_product(pid)
        if not p:
            await _edit(query, 'محصول پیدا نشد.', [_back('admx_products')])
        else:
            update_store_product(pid, 'IsActive', not bool(p[5]))
            await query.edit_message_text(
                '✅ وضعیت محصول تغییر کرد.',
                reply_markup=_kb([_back('admx_products')]),
            )
    elif data.startswith('admx_promo_'):
        pid = int(data.rsplit('_', 1)[1])
        p = get_promo_code(pid)
        if not p:
            await _edit(query, 'کد پیدا نشد.', [_back('admx_shop')])
        else:
            kind_label = 'هدیه' if p[2] == 'gift' else 'تخفیف'
            back_cb = 'admx_gift' if p[2] == 'gift' else 'admx_discount'
            await _edit(query, (
                f'🏷 کد {kind_label}: `{p[1]}`\nمقدار: {p[3]}\n'
                f'استفاده: {p[5]}/{p[4]}\nفعال: {"بله" if p[6] else "خیر"}'
            ), [
                [InlineKeyboardButton(
                    'فعال/غیرفعال', callback_data=f'admx_promotoggle_{pid}'
                )],
                [InlineKeyboardButton(
                    '🗑 حذف', callback_data=f'admx_del_code_{pid}'
                )],
                _back(back_cb),
            ], markdown=True)
    elif data.startswith('admx_promotoggle_'):
        pid = int(data.rsplit('_', 1)[1])
        p = get_promo_code(pid)
        if not p:
            await _edit(query, 'کد پیدا نشد.', [_back('admx_shop')])
        else:
            update_promo_code(pid, 'IsActive', not bool(p[6]))
            back_cb = 'admx_gift' if p[2] == 'gift' else 'admx_discount'
            await query.edit_message_text(
                '✅ وضعیت کد تغییر کرد.',
                reply_markup=_kb([_back(back_cb)]),
            )
    elif data == 'admx_noop':
        await query.answer('مدیر اصلی از env خوانده می‌شود و قابل حذف نیست.', show_alert=True)
    elif data in ('admx_categories', 'admx_products', 'admx_departments',
                  'admx_gift', 'admx_discount', 'admx_admins'):
        await _show_simple_list(query, data)
    elif data.startswith('admx_del_'):
        _, _, kind, rid = data.split('_', 3)
        labels = {
            'dept': 'دپارتمان', 'cat': 'دسته‌بندی',
            'product': 'محصول', 'code': 'کد',
        }
        await _edit(
            query,
            f'⚠️ حذف {labels.get(kind, "رکورد")} #{rid} برگشت‌پذیر نیست. مطمئنی؟',
            [[
                InlineKeyboardButton(
                    'بله، حذف شود',
                    callback_data=f'admx_delconfirm_{kind}_{rid}',
                ),
                InlineKeyboardButton('انصراف', callback_data='admx_shop'),
            ]],
        )
    elif data.startswith('admx_delconfirm_'):
        _, _, kind, rid = data.split('_', 3)
        tables = {'dept': 'SupportDepartments', 'cat': 'ProductCategories',
                  'product': 'StoreProducts', 'code': 'PromoCodes'}
        backs = {'dept': 'admx_departments', 'cat': 'admx_categories',
                  'product': 'admx_products', 'code': 'admx_shop'}
        if kind not in tables or not rid.isdigit():
            await query.answer('درخواست حذف نامعتبر است.', show_alert=True)
            return
        delete_simple_record(tables[kind], rid)
        log_admin_action(
            update.effective_user.id, 'record_deleted', kind, rid, ''
        )
        await query.edit_message_text('✅ حذف شد.', reply_markup=_kb([_back(backs[kind])]))
    elif data.startswith('admx_adminremove_'):
        if not await _owner_guard(update):
            return
        tg = data.rsplit('_', 1)[1]
        if admin_id() and str(admin_id()) == tg:
            await query.answer('مدیر اصلی env قابل حذف نیست.', show_alert=True)
            return
        remove_bot_admin(tg)
        invalidate_role_cache(tg)
        await query.edit_message_text('✅ دسترسی مدیر حذف شد.', reply_markup=_kb([_back('admx_admins')]))
    elif data.startswith('admx_massconfirm_'):
        raw_amount = data.rsplit('_', 1)[1]
        pending = ctx.user_data.pop('admin_masscharge_confirm', None) or {}
        if (
            not raw_amount.isdigit()
            or int(pending.get('amount') or 0) != int(raw_amount)
            or time.time() - float(pending.get('armed_at') or 0) > 120
        ):
            await query.answer(
                'تأیید منقضی یا نامعتبر است؛ شارژ همگانی را دوباره شروع کن.',
                show_alert=True,
            )
            return
        amount = int(raw_amount)
        await query.edit_message_text('⏳ شارژ همگانی در حال ثبت است…')
        count = await asyncio.to_thread(mass_charge_wallets, amount)
        log_admin_action(
            update.effective_user.id, 'mass_wallet_charge', 'wallets', '',
            f'amount={amount} users={count}',
        )
        await query.edit_message_text(
            f'✅ کیف پول {count} کاربر، هرکدام {amount:,} تومان شارژ شد.',
            reply_markup=admin_home_keyboard(),
        )
    elif data in (
        'admx_toggle_zp', 'admx_toggle_card',
        'admx_toggle_sales', 'admx_toggle_payments', 'admx_toggle_alerts',
    ):
        key = {
            'admx_toggle_zp': 'zarinpal_enabled',
            'admx_toggle_card': 'card_transfer_enabled',
            'admx_toggle_sales': 'sales_enabled',
            'admx_toggle_payments': 'payments_enabled',
            'admx_toggle_alerts': 'admin_alerts_enabled',
        }[data]
        current = get_setting(key, '1') != '0'
        new_value = '0' if current else '1'
        set_setting(key, new_value)
        log_admin_action(
            update.effective_user.id, 'setting_toggle', 'setting', key,
            f'value={new_value}',
        )
        back = 'admx_ops' if data == 'admx_toggle_alerts' else 'admx_finance'
        await query.edit_message_text(
            '✅ وضعیت تغییر کرد.', reply_markup=_kb([_back(back)])
        )
    else:
        await query.answer('این گزینه پنل شناخته نشد یا منقضی شده.', show_alert=True)


async def _show_simple_list(query, data):
    if data == 'admx_categories':
        rows = simple_list('ProductCategories', ['Id', 'Title', 'IsActive'])
        text, add_cb, kind, back = '🗂 دسته‌بندی‌ها', 'admi_category', 'cat', 'admx_shop'
    elif data == 'admx_products':
        rows = simple_list('StoreProducts', ['Id', 'Title', 'Price', 'Stock', 'IsActive'])
        text, add_cb, kind, back = '📦 محصولات', 'admi_product', 'product', 'admx_shop'
    elif data == 'admx_departments':
        rows = simple_list('SupportDepartments', ['Id', 'Title', 'IsActive'])
        text, add_cb, kind, back = '🎧 دپارتمان‌ها', 'admi_department', 'dept', 'admx_support'
    elif data in ('admx_gift', 'admx_discount'):
        code_type = 'gift' if data == 'admx_gift' else 'discount'
        all_rows = simple_list('PromoCodes', ['Id', 'Code', 'CodeType', 'Value', 'MaxUses',
                                             'UsedCount', 'IsActive'])
        rows = [r for r in all_rows if r[2] == code_type]
        text = '🎁 کدهای هدیه' if code_type == 'gift' else '🏷 کدهای تخفیف'
        add_cb = 'admi_gift' if code_type == 'gift' else 'admi_discount'
        kind, back = 'code', 'admx_shop'
    else:
        rows = list_bot_admins()
        buttons = []
        if admin_id():
            buttons.append([InlineKeyboardButton(
                f'👑 مدیر اصلی · {admin_id()}', callback_data='admx_noop'
            )])
        for tg, title, _active, _, role in rows:
            role_label = '⭐ پریمیوم' if role == 'premium' else '🛡 مدیر'
            buttons.append([InlineKeyboardButton(
                f'❌ {role_label} · {title or "بدون نام"} · {tg}',
                callback_data=f'admx_adminremove_{tg}'
            )])
        buttons.extend([
                        [InlineKeyboardButton('➕ مدیر کامل', callback_data='admi_admin'),
                         InlineKeyboardButton('⭐ مدیر پریمیوم', callback_data='admi_premiumadmin')],
                        _back('admx_settings')])
        await _edit(query, '👮 مدیران ربات\nبرای حذف روی مدیر بزن.', buttons)
        return
    lines = [text, '━━━━━━━━━━━━━━━']
    buttons = []
    for row in rows:
        if data == 'admx_products':
            lines.append(f'#{row[0]} · {row[1]} · {row[2]:,} ت · موجودی {row[3]}')
            buttons.append([InlineKeyboardButton(
                f'✏️ ویرایش #{row[0]}', callback_data=f'admx_product_{row[0]}'
            )])
        elif data in ('admx_gift', 'admx_discount'):
            status = '✅' if row[6] else '❌'
            lines.append(f'{status} #{row[0]} · {row[1]} · مقدار {row[3]} · {row[5]}/{row[4]}')
            buttons.append([InlineKeyboardButton(
                f'✏️ مدیریت #{row[0]}', callback_data=f'admx_promo_{row[0]}'
            )])
        else:
            lines.append(f'#{row[0]} · {row[1]}')
            buttons.append([InlineKeyboardButton(
                f'🗑 حذف #{row[0]}', callback_data=f'admx_del_{kind}_{row[0]}'
            )])
    if not rows:
        lines.append('موردی ثبت نشده است.')
    buttons.extend([[InlineKeyboardButton('➕ افزودن', callback_data=add_cb)], _back(back)])
    await _edit(query, '\n'.join(lines), buttons)


INPUT_ACTIONS = {
    'admi_broadcast': ('broadcast', 'متن پیام همگانی را بفرست.'),
    'admi_masscharge': ('masscharge', 'مبلغ شارژ همگانی را به تومان بفرست.'),
    'admi_ordersearch': ('ordersearch', 'شماره سفارش را بفرست (مثلاً 123).'),
    'admi_zpmerchant': ('setting:zarinpal_merchant_id', 'مرچنت آیدی زرین‌پال را بفرست.'),
    'admi_callback': ('setting:payment_callback_base', 'آدرس HTTPS پایه callback را بفرست.'),
    'admi_usdrate': (
        'setting:usd_toman_rate',
        'نرخ پشتیبان هر دلار به تومان را بفرست؛ فقط هنگام قطع نرخ زنده استفاده می‌شود.',
    ),
    'admi_cardnumber': ('setting:card_number', 'شماره کارت ۱۶ رقمی را بفرست.'),
    'admi_cardholder': ('setting:card_holder', 'نام صاحب کارت را بفرست.'),
    'admi_cardbank': ('setting:card_bank', 'نام بانک را بفرست.'),
    'admi_supportid': ('setting:support_id', 'آیدی پشتیبانی را با @ بفرست.'),
    'admi_credentialsupportid': (
        'setting:credential_support_id',
        'آیدی پشتیبان سفارش‌های جم با اطلاعات را با @ بفرست.',
    ),
    'admi_shopname': ('setting:shop_name', 'نام فروشگاه را بفرست.'),
    'admi_welcome': ('setting:welcome_text', 'متن کامل خوش‌آمد را بفرست. Markdown مجاز است.'),
    'admi_supporttext': ('setting:support_text', 'متن کامل بخش پشتیبانی را بفرست.'),
    'admi_lowstock': (
        'setting:low_stock_threshold',
        'حد هشدار موجودی کم را بفرست (مثلاً 5).',
    ),
    'admi_gemprofit': (
        'setting:gem_profit_percent',
        'درصد سود بسته‌های جم را بفرست (بین ۱ تا ۲۰۰). پیش‌فرض: 7',
    ),
    'admi_department': ('department', 'نام دپارتمان جدید را بفرست.'),
    'admi_category': ('category', 'نام دسته‌بندی جدید را بفرست.'),
    'admi_product': ('product', 'با این قالب بفرست:\nعنوان | قیمت | موجودی | شناسه دسته\nمثال:\nاکانت لول 70 | 500000 | 2 | 1'),
    'admi_gift': ('promo:gift', 'قالب: کد | مبلغ هدیه | تعداد استفاده\nمثال: GIFT100 | 100000 | 5'),
    'admi_discount': ('promo:discount', 'قالب: کد | درصد تخفیف | تعداد استفاده\nمثال: OFF20 | 20 | 100'),
    'admi_gemadd': ('gemadd', 'قالب: عنوان | مقدار جم | قیمت | موجودی\nمثال: بسته 110 جمی | 110 | 200000 | 9999'),
    'admi_senseadd': ('senseadd', 'قالب: عنوان | پلتفرم pc/mobile | قیمت | توضیح'),
    'admi_admin': ('adminadd', 'قالب: شناسه عددی تلگرام | نام مدیر\nمثال: 123456789 | علی'),
    'admi_premiumadmin': (
        'premiumadminadd',
        'قالب: شناسه عددی تلگرام | نام مدیر پریمیوم\n'
        'کاربر باید ابتدا ربات را /start کرده باشد.',
    ),
    'admi_forcedjoin': (
        'forcedjoinadd',
        'مشخصات کانال جوین اجباری را وارد کن.',
    ),
}


async def admin_input_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END
    data = query.data
    if data in ('admi_admin', 'admi_premiumadmin') and not await _owner_guard(update):
        return ConversationHandler.END
    action = None
    prompt = None
    for prefix, field in (
        ('admi_gemprice_', 'gemprice'), ('admi_gemtitle_', 'gemtitle'),
        ('admi_gemstock_', 'gemstock'), ('admi_senseprice_', 'senseprice'),
        ('admi_sensetitle_', 'sensetitle'), ('admi_sensedesc_', 'sensedesc'),
        ('admi_productprice_', 'productprice'), ('admi_producttitle_', 'producttitle'),
        ('admi_productstock_', 'productstock'),
    ):
        if data.startswith(prefix):
            action = f'{field}:{data[len(prefix):]}'
            prompt = 'مقدار جدید را بفرست.'
            break
    if not action:
        action, prompt = INPUT_ACTIONS[data]
    ctx.user_data['admin_ext_action'] = action
    if action in COMPOUND_FIELDS:
        ctx.user_data['admin_ext_draft'] = []
        await query.edit_message_text(
            _compound_prompt(action, 0), parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(prompt + '\n\n/cancel برای انصراف')
    return WAIT_VALUE


async def admin_input_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return ConversationHandler.END
    action = ctx.user_data.pop('admin_ext_action', '')
    raw = (update.message.text or '').strip()
    try:
        if action in COMPOUND_FIELDS:
            supplied = _split_compound(raw)
            if supplied is not None:
                p = supplied
                ctx.user_data.pop('admin_ext_draft', None)
            else:
                p = ctx.user_data.get('admin_ext_draft', [])
                p.append(raw)
                if len(p) < len(COMPOUND_FIELDS[action]):
                    ctx.user_data['admin_ext_draft'] = p
                    ctx.user_data['admin_ext_action'] = action
                    await update.message.reply_text(
                        _compound_prompt(action, len(p)), parse_mode='Markdown'
                    )
                    return WAIT_VALUE
                ctx.user_data.pop('admin_ext_draft', None)
            if len(p) != len(COMPOUND_FIELDS[action]):
                raise ValueError(
                    f'این بخش دقیقاً {len(COMPOUND_FIELDS[action])} مقدار نیاز دارد.'
                )
        if action == 'broadcast':
            sent, failed = 0, 0
            status = await update.message.reply_text('⏳ ارسال شروع شد…')
            for tg in list_all_telegram_ids():
                try:
                    await ctx.bot.send_message(chat_id=int(tg), text=raw)
                    sent += 1
                except Exception:
                    failed += 1
                if (sent + failed) % 25 == 0:
                    await asyncio.sleep(1)
            await status.edit_text(f'✅ ارسال تمام شد.\nموفق: {sent}\nناموفق: {failed}')
        elif action == 'masscharge':
            amount = int(raw.replace(',', ''))
            if not 1 <= amount <= 10_000_000:
                raise ValueError(
                    'مبلغ شارژ همگانی باید بین ۱ تا ۱۰٬۰۰۰٬۰۰۰ تومان باشد.'
                )
            ctx.user_data['admin_masscharge_confirm'] = {
                'amount': amount, 'armed_at': time.time(),
            }
            await update.message.reply_text(
                f'⚠️ قرار است کیف پول تمام کاربران، هرکدام {amount:,} تومان شارژ شود.\n'
                'این عملیات مالی قابل برگشت خودکار نیست. تأیید می‌کنی؟',
                reply_markup=_kb([[
                    InlineKeyboardButton(
                        'بله، شارژ همگانی انجام شود',
                        callback_data=f'admx_massconfirm_{amount}',
                    ),
                    InlineKeyboardButton('انصراف', callback_data='admx_actions'),
                ]]),
            )
        elif action == 'ordersearch':
            order = get_order_admin(int(raw.lstrip('#')))
            if not order:
                raise ValueError('سفارش پیدا نشد.')
            receipt = None
            if order[5] == 'pending' and order[4] == 'card_transfer':
                receipt = get_payment_receipt(order_id=order[0], pending_only=True)
            kb = (
                admin_card_keyboard(order[0])
                if receipt and receipt[2]
                else admin_home_keyboard()
            )
            await update.message.reply_text(
                f'🔎 سفارش #{order[0]}\n'
                f'کاربر: {_md_safe(order[7])} @{_md_safe(order[8])}\n'
                f'شناسه تلگرام: `{order[1]}`\nمبلغ: {order[2]:,} تومان\n'
                f'تخفیف: {order[3]:,}\nروش: {order[4]}\nوضعیت: {order[5]}\n'
                f'تاریخ: {order[6]}'
                + ('\n📸 رسید تصویری در انتظار بررسی دارد.' if receipt else ''),
                parse_mode='Markdown',
                reply_markup=kb,
            )
        elif action.startswith('setting:'):
            key = action.split(':', 1)[1]
            if key == 'zarinpal_merchant_id':
                try:
                    raw = str(uuid.UUID(raw.strip()))
                except (ValueError, AttributeError):
                    raise ValueError('مرچنت زرین‌پال باید UUID معتبر باشد.') from None
            if key == 'payment_callback_base':
                parsed = urlparse(raw)
                if (
                    parsed.scheme != 'https' or not parsed.hostname
                    or parsed.username or parsed.password
                ):
                    raise ValueError('آدرس callback باید HTTPS معتبر و بدون رمز داخل URL باشد.')
            if key == 'usd_toman_rate':
                rate = int(raw.replace(',', ''))
                if not 10_000 <= rate <= 10_000_000:
                    raise ValueError('نرخ دلار خارج از محدوده مجاز است.')
                raw = str(rate)
            if key == 'low_stock_threshold':
                threshold = int(raw.replace(',', ''))
                if not 0 <= threshold <= 10_000:
                    raise ValueError('حد موجودی باید بین ۰ تا ۱۰٬۰۰۰ باشد.')
                raw = str(threshold)
            if key == 'gem_profit_percent':
                profit = int(raw.replace('%', '').replace('٪', '').replace(',', ''))
                if not 1 <= profit <= 200:
                    raise ValueError('درصد سود باید بین ۱ تا ۲۰۰ باشد.')
                raw = str(profit)
            if key == 'card_number' and len(''.join(c for c in raw if c.isdigit())) != 16:
                raise ValueError('شماره کارت باید ۱۶ رقم باشد.')
            if key in ('support_id', 'credential_support_id'):
                raw = raw.strip()
                if not raw.startswith('@') or not raw[1:].replace('_', '').isalnum():
                    raise ValueError('آیدی پشتیبانی باید با @ و به‌شکل معتبر وارد شود.')
            set_setting(key, raw)
            log_admin_action(
                update.effective_user.id, 'setting_updated', 'setting', key,
                'value changed' if key != 'low_stock_threshold' else f'value={raw}',
            )
            await update.message.reply_text('✅ ذخیره شد.', reply_markup=admin_home_keyboard())
        elif action == 'department':
            add_department(raw)
            await update.message.reply_text('✅ دپارتمان اضافه شد.', reply_markup=admin_home_keyboard())
        elif action == 'category':
            add_category(raw)
            await update.message.reply_text('✅ دسته‌بندی اضافه شد.', reply_markup=admin_home_keyboard())
        elif action == 'product':
            category_id = int(p[3]) if p[3] not in ('', '0', '-') else None
            add_store_product(p[0], int(p[1].replace(',', '')), int(p[2]), category_id)
            await update.message.reply_text('✅ محصول اضافه شد.', reply_markup=admin_home_keyboard())
        elif action.startswith('promo:'):
            if action == 'promo:discount' and not 1 <= int(p[1]) <= 99:
                raise ValueError('درصد تخفیف باید بین ۱ تا ۹۹ باشد.')
            add_promo_code(p[0], action.split(':')[1], p[1], p[2])
            await update.message.reply_text('✅ کد ساخته شد.', reply_markup=admin_home_keyboard())
        elif action == 'gemadd':
            add_gem_package(p[0], p[1], p[2].replace(',', ''), p[3])
            await update.message.reply_text('✅ بسته جم اضافه شد.', reply_markup=admin_home_keyboard())
        elif action == 'senseadd':
            platform = p[1].lower()
            if platform not in ('pc', 'mobile'):
                raise ValueError('پلتفرم فقط pc یا mobile است.')
            add_sense_package(
                p[0], platform, p[2].replace(',', ''),
                '' if p[3] == '-' else p[3],
            )
            await update.message.reply_text('✅ پک سنس اضافه شد.', reply_markup=admin_home_keyboard())
        elif action in ('adminadd', 'premiumadminadd'):
            if not await _owner_guard(update):
                return ConversationHandler.END
            if not p[0].isdigit():
                raise ValueError('شناسه تلگرام باید عددی باشد.')
            role = 'premium' if action == 'premiumadminadd' else 'admin'
            add_bot_admin(
                p[0], p[1] if len(p) > 1 else '', role=role,
                added_by=update.effective_user.id,
            )
            invalidate_role_cache(p[0])
            await update.message.reply_text(
                '✅ دسترسی مدیر ثبت شد.', reply_markup=admin_home_keyboard()
            )
        elif action == 'forcedjoinadd':
            chat_id = p[0].strip()
            if not valid_forced_join_chat_id(chat_id):
                raise ValueError(
                    'شناسه باید @username عمومی یا شناسه عددی -100... باشد.'
                )
            if not valid_telegram_invite_url(p[1]):
                raise ValueError('لینک باید HTTPS معتبر تلگرام باشد.')
            try:
                bot_member = await ctx.bot.get_chat_member(
                    chat_id=chat_id,
                    user_id=ctx.bot.id,
                )
            except Exception:
                raise ValueError(
                    'ربات به کانال دسترسی ندارد؛ اول ربات را داخل کانال ادمین کن.'
                ) from None
            if bot_member.status not in ('administrator', 'creator'):
                raise ValueError(
                    'برای بررسی مطمئن عضویت، ربات باید ادمین کانال باشد.'
                )
            title = '' if p[2].strip() == '-' else p[2].strip()
            add_forced_join_channel(chat_id, p[1].strip(), title)
            invalidate_forced_join_cache()
            await update.message.reply_text(
                '✅ کانال به جوین اجباری اضافه شد.\n'
                'حتماً ربات را داخل کانال ادمین کن.',
                reply_markup=admin_home_keyboard(),
            )
        elif action.startswith(('gemprice:', 'gemtitle:', 'gemstock:')):
            kind, gid = action.split(':')
            field = {'gemprice': 'Price', 'gemtitle': 'Title', 'gemstock': 'Stock'}[kind]
            update_gem_package(gid, field, raw.replace(',', '') if field != 'Title' else raw)
            await update.message.reply_text('✅ بسته جم ویرایش شد.', reply_markup=admin_home_keyboard())
        elif action.startswith(('senseprice:', 'sensetitle:', 'sensedesc:')):
            kind, sid = action.split(':')
            field = {
                'senseprice': 'Price', 'sensetitle': 'Title', 'sensedesc': 'Description',
            }[kind]
            update_sense_package(sid, field, raw.replace(',', '') if field == 'Price' else raw)
            await update.message.reply_text('✅ پک سنس ویرایش شد.', reply_markup=admin_home_keyboard())
        elif action.startswith(('productprice:', 'producttitle:', 'productstock:')):
            kind, pid = action.split(':')
            field = {
                'productprice': 'Price', 'producttitle': 'Title', 'productstock': 'Stock',
            }[kind]
            update_store_product(pid, field, raw.replace(',', '') if field != 'Title' else raw)
            await update.message.reply_text('✅ محصول ویرایش شد.', reply_markup=admin_home_keyboard())
    except (ValueError, IndexError) as e:
        ctx.user_data['admin_ext_action'] = action
        if action in COMPOUND_FIELDS:
            ctx.user_data['admin_ext_draft'] = []
            retry = '\n\nفرم از مرحله اول شروع شد.'
        else:
            retry = ''
        await update.message.reply_text(
            f'❌ ورودی نامعتبر: {e}{retry}\nدوباره بفرست یا /cancel بزن.'
        )
        if action in COMPOUND_FIELDS:
            await update.message.reply_text(
                _compound_prompt(action, 0), parse_mode='Markdown'
            )
        return WAIT_VALUE
    except Exception as e:
        await update.message.reply_text(f'❌ عملیات انجام نشد: {e}', reply_markup=admin_home_keyboard())
    return ConversationHandler.END


async def admin_input_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('admin_ext_action', None)
    ctx.user_data.pop('admin_ext_draft', None)
    await update.message.reply_text('انصراف.', reply_markup=admin_home_keyboard())
    return ConversationHandler.END


def admin_extended_conversation_handler():
    patterns = list(INPUT_ACTIONS)
    entry_pattern = (
        '^(' + '|'.join(patterns)
        + r'|admi_(?:gemprice|gemtitle|gemstock|senseprice|sensetitle|sensedesc'
        r'|productprice|producttitle|productstock)_\d+)$'
    )
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_input_start, pattern=entry_pattern)],
        states={WAIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_input_receive)]},
        fallbacks=[CommandHandler('cancel', admin_input_cancel)],
        allow_reentry=True,
    )
