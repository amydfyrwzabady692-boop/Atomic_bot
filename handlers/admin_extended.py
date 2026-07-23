"""بخش‌های توسعه‌یافته پنل تلگرامی ادمین."""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters,
)

from admin_notify import admin_id, is_admin
from db import (
    add_bot_admin, add_category, add_department, add_gem_package, add_promo_code,
    add_sense_package, add_store_product, admin_list_gems, admin_stats_full,
    delete_simple_record, get_gem, get_order_admin, get_sense_package, get_setting,
    list_all_telegram_ids, list_bot_admins, list_pending_receipts,
    list_pending_wallet_card_charges, list_sense_packages, list_users_filtered, mass_charge_wallets,
    remove_bot_admin, set_setting, simple_list, update_gem_package,
    update_sense_package,
)
from keyboards import admin_card_keyboard, admin_home_keyboard

WAIT_VALUE = 50


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


async def _edit(query, text, rows, markdown=False):
    await query.edit_message_text(
        text, parse_mode='Markdown' if markdown else None, reply_markup=_kb(rows)
    )


async def admin_ext_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return
    data = query.data

    if data == 'admx_shop':
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
        number = get_setting('card_number', '') or 'تنظیم نشده'
        merchant = get_setting('zarinpal_merchant_id', '') or 'از env سرور'
        await _edit(query, (
            '💳 امور مالی\n\n'
            f'زرین‌پال: {"✅" if zp else "❌"}\n'
            f'مرچنت: {merchant}\n'
            f'کارت‌به‌کارت: {"✅" if card else "❌"}\n'
            f'شماره کارت: {number}'
        ), [
            [InlineKeyboardButton('روشن/خاموش زرین‌پال', callback_data='admx_toggle_zp')],
            [InlineKeyboardButton('✏️ مرچنت زرین‌پال', callback_data='admi_zpmerchant')],
            [InlineKeyboardButton('✏️ آدرس callback', callback_data='admi_callback')],
            [InlineKeyboardButton('روشن/خاموش کارت', callback_data='admx_toggle_card')],
            [InlineKeyboardButton('✏️ شماره کارت', callback_data='admi_cardnumber')],
            [InlineKeyboardButton('✏️ صاحب کارت', callback_data='admi_cardholder')],
            [InlineKeyboardButton('✏️ نام بانک', callback_data='admi_cardbank')],
            [InlineKeyboardButton('🧾 رسیدهای تاییدنشده', callback_data='admx_receipts')],
            _back(),
        ])
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
        await _edit(query, f'🎧 تنظیمات پشتیبانی\n\nآیدی پشتیبانی: {support_id}', [
            [InlineKeyboardButton('✏️ تنظیم آیدی پشتیبانی', callback_data='admi_supportid')],
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
            [InlineKeyboardButton('👮 مدیران ربات', callback_data='admx_admins')],
            _back(),
        ])
    elif data == 'admx_stats':
        s = admin_stats_full()
        text = (
            '📊 *آمار کلی ربات*\n'
            '━━━━━━━━━━━━━━━\n'
            f'تعداد کل کاربران: *{s["users"]:,}*\n'
            f'کاربران دارای خرید: *{s["buyers"]:,}*\n'
            f'موجودی کل کاربران: *{s["wallet_sum"]:,}* تومان\n'
            f'تعداد کل فروش: *{s["sales_count"]:,}*\n'
            f'جمع کل فروش: *{s["sales_sum"]:,}* تومان\n'
            f'سفارش‌های باز: *{s["open_orders"]:,}*\n'
            f'رسیدهای در انتظار: *{len(list_pending_receipts(100)):,}*\n'
            f'تیکت باز: *{s["open_tickets"]:,}*'
        )
        await _edit(query, text, [_back()], markdown=True)
    elif data.startswith('admx_users_'):
        kind = data.replace('admx_users_', '')
        titles = {'balance': 'دارای موجودی', 'referral': 'دارای زیرمجموعه',
                  'card': 'شماره کارت فعال'}
        rows = list_users_filtered(kind)
        lines = [f'👥 کاربران {titles.get(kind, "")}', '━━━━━━━━━━━━━━━']
        for tg, name, username, balance, refs, card in rows:
            handle = f'@{username}' if username else (name or '—')
            extra = (f'{balance:,} ت' if kind == 'balance' else
                     f'{refs} زیرمجموعه' if kind == 'referral' else card)
            lines.append(f'{handle} · `{tg}` · {extra}')
        if not rows:
            lines.append('موردی ثبت نشده است.')
        await _edit(query, '\n'.join(lines), [_back('admx_actions')], markdown=True)
    elif data == 'admx_receipts':
        rows = list_pending_receipts()
        wallet_rows = list_pending_wallet_card_charges(30)
        lines = ['🧾 *رسیدهای تاییدنشده*', '━━━━━━━━━━━━━━━']
        buttons = []
        for oid, tg, total, created in rows:
            lines.append(f'سفارش `#{oid}` · {total:,} ت · `{tg}`')
            buttons.append([InlineKeyboardButton(
                f'بررسی سفارش #{oid}', callback_data=f'admx_receipt_{oid}'
            )])
        for txid, amount, _authority, _uid, tg, name in wallet_rows:
            lines.append(f'شارژ کیف پول `#{txid}` · {amount:,} ت · `{tg}` · {name or "—"}')
            buttons.append([InlineKeyboardButton(
                f'✅ تایید شارژ #{txid}', callback_data=f'wadmin_ok_{txid}'
            ), InlineKeyboardButton('❌ رد', callback_data=f'wadmin_no_{txid}')])
        if not rows and not wallet_rows:
            lines.append('✅ رسید تاییدنشده‌ای وجود ندارد.')
        buttons.append(_back('admx_finance'))
        await _edit(query, '\n'.join(lines), buttons, markdown=True)
    elif data.startswith('admx_receipt_'):
        oid = int(data.rsplit('_', 1)[1])
        order = get_order_admin(oid)
        if not order:
            await _edit(query, 'سفارش پیدا نشد.', [_back('admx_receipts')])
        else:
            await _edit(query, (
                f'🧾 سفارش #{oid}\n'
                f'کاربر: {order[7] or "—"} @{order[8] or "—"}\n'
                f'شناسه: {order[1]}\nمبلغ: {order[2]:,} تومان\n'
                f'روش: {order[4]}\nوضعیت: {order[5]}'
            ), [
                [InlineKeyboardButton('✅ تایید', callback_data=f'admin_ok_{oid}'),
                 InlineKeyboardButton('❌ رد', callback_data=f'admin_no_{oid}')],
                _back('admx_receipts'),
            ])
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
        g = get_gem(gid)
        if not g:
            await _edit(query, 'بسته پیدا نشد.', [_back('admx_gems')])
        else:
            await _edit(query, (
                f'💎 {g[1]}\nشناسه: {g[0]}\nمقدار: {g[2]}\n'
                f'قیمت: {g[4]:,} تومان\nموجودی: {g[10]}\n'
                f'فعال: {"بله" if g[11] else "خیر"}'
            ), [
                [InlineKeyboardButton('✏️ قیمت', callback_data=f'admi_gemprice_{gid}'),
                 InlineKeyboardButton('✏️ عنوان', callback_data=f'admi_gemtitle_{gid}')],
                [InlineKeyboardButton('✏️ موجودی', callback_data=f'admi_gemstock_{gid}'),
                 InlineKeyboardButton('فعال/غیرفعال', callback_data=f'admx_gemtoggle_{gid}')],
                _back('admx_gems'),
            ])
    elif data.startswith('admx_gemtoggle_'):
        gid = int(data.rsplit('_', 1)[1])
        g = get_gem(gid)
        update_gem_package(gid, 'IsAvailable', not bool(g[11]))
        await query.edit_message_text('✅ وضعیت بسته تغییر کرد.', reply_markup=_kb([_back('admx_gems')]))
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
        await _edit(query, (
            f'🎯 {p[1]}\nپلتفرم: {p[2]}\nقیمت: {p[3]:,} تومان\n'
            f'توضیح: {p[4] or "—"}\nفعال: {"بله" if p[5] else "خیر"}'
        ), [
            [InlineKeyboardButton('✏️ قیمت', callback_data=f'admi_senseprice_{sid}'),
             InlineKeyboardButton('✏️ عنوان', callback_data=f'admi_sensetitle_{sid}')],
            [InlineKeyboardButton('فعال/غیرفعال', callback_data=f'admx_sensetoggle_{sid}')],
            _back('admx_sense'),
        ])
    elif data.startswith('admx_sensetoggle_'):
        sid = int(data.rsplit('_', 1)[1])
        p = get_sense_package(sid)
        update_sense_package(sid, 'IsActive', not bool(p[5]))
        await query.edit_message_text('✅ وضعیت پک تغییر کرد.', reply_markup=_kb([_back('admx_sense')]))
    elif data in ('admx_categories', 'admx_products', 'admx_departments',
                  'admx_gift', 'admx_discount', 'admx_admins'):
        await _show_simple_list(query, data)
    elif data.startswith('admx_del_'):
        _, _, kind, rid = data.split('_', 3)
        tables = {'dept': 'SupportDepartments', 'cat': 'ProductCategories',
                  'product': 'StoreProducts', 'code': 'PromoCodes'}
        backs = {'dept': 'admx_departments', 'cat': 'admx_categories',
                 'product': 'admx_products', 'code': 'admx_shop'}
        delete_simple_record(tables[kind], rid)
        await query.edit_message_text('✅ حذف شد.', reply_markup=_kb([_back(backs[kind])]))
    elif data.startswith('admx_adminremove_'):
        tg = data.rsplit('_', 1)[1]
        if admin_id() and str(admin_id()) == tg:
            await query.answer('مدیر اصلی env قابل حذف نیست.', show_alert=True)
            return
        remove_bot_admin(tg)
        await query.edit_message_text('✅ دسترسی مدیر حذف شد.', reply_markup=_kb([_back('admx_admins')]))
    elif data in ('admx_toggle_zp', 'admx_toggle_card'):
        key = 'zarinpal_enabled' if data.endswith('_zp') else 'card_transfer_enabled'
        current = get_setting(key, '1') != '0'
        set_setting(key, '0' if current else '1')
        await query.edit_message_text('✅ وضعیت روش پرداخت تغییر کرد.',
                                      reply_markup=_kb([_back('admx_finance')]))


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
        for tg, title, active, _ in rows:
            buttons.append([InlineKeyboardButton(
                f'❌ {title or "مدیر"} · {tg}', callback_data=f'admx_adminremove_{tg}'
            )])
        buttons.extend([[InlineKeyboardButton('➕ افزودن مدیر', callback_data='admi_admin')],
                        _back('admx_settings')])
        await _edit(query, '👮 مدیران ربات\nبرای حذف روی مدیر بزن.', buttons)
        return
    lines = [text, '━━━━━━━━━━━━━━━']
    buttons = []
    for row in rows:
        if data == 'admx_products':
            lines.append(f'#{row[0]} · {row[1]} · {row[2]:,} ت · موجودی {row[3]}')
        elif data in ('admx_gift', 'admx_discount'):
            lines.append(f'#{row[0]} · {row[1]} · مقدار {row[3]} · {row[5]}/{row[4]}')
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
    'admi_cardnumber': ('setting:card_number', 'شماره کارت ۱۶ رقمی را بفرست.'),
    'admi_cardholder': ('setting:card_holder', 'نام صاحب کارت را بفرست.'),
    'admi_cardbank': ('setting:card_bank', 'نام بانک را بفرست.'),
    'admi_supportid': ('setting:support_id', 'آیدی پشتیبانی را با @ بفرست.'),
    'admi_shopname': ('setting:shop_name', 'نام فروشگاه را بفرست.'),
    'admi_welcome': ('setting:welcome_text', 'متن کامل خوش‌آمد را بفرست. Markdown مجاز است.'),
    'admi_supporttext': ('setting:support_text', 'متن کامل بخش پشتیبانی را بفرست.'),
    'admi_department': ('department', 'نام دپارتمان جدید را بفرست.'),
    'admi_category': ('category', 'نام دسته‌بندی جدید را بفرست.'),
    'admi_product': ('product', 'با این قالب بفرست:\nعنوان | قیمت | موجودی | شناسه دسته\nمثال:\nاکانت لول 70 | 500000 | 2 | 1'),
    'admi_gift': ('promo:gift', 'قالب: کد | مبلغ هدیه | تعداد استفاده\nمثال: GIFT100 | 100000 | 5'),
    'admi_discount': ('promo:discount', 'قالب: کد | درصد تخفیف | تعداد استفاده\nمثال: OFF20 | 20 | 100'),
    'admi_gemadd': ('gemadd', 'قالب: عنوان | مقدار جم | قیمت | موجودی\nمثال: بسته 110 جمی | 110 | 200000 | 9999'),
    'admi_senseadd': ('senseadd', 'قالب: عنوان | پلتفرم pc/mobile | قیمت | توضیح'),
    'admi_admin': ('adminadd', 'قالب: شناسه عددی تلگرام | نام مدیر\nمثال: 123456789 | علی'),
}


async def admin_input_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END
    data = query.data
    action = None
    prompt = None
    for prefix, field in (
        ('admi_gemprice_', 'gemprice'), ('admi_gemtitle_', 'gemtitle'),
        ('admi_gemstock_', 'gemstock'), ('admi_senseprice_', 'senseprice'),
        ('admi_sensetitle_', 'sensetitle'),
    ):
        if data.startswith(prefix):
            action = f'{field}:{data[len(prefix):]}'
            prompt = 'مقدار جدید را بفرست.'
            break
    if not action:
        action, prompt = INPUT_ACTIONS[data]
    ctx.user_data['admin_ext_action'] = action
    await query.edit_message_text(prompt + '\n\n/cancel برای انصراف')
    return WAIT_VALUE


async def admin_input_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return ConversationHandler.END
    action = ctx.user_data.pop('admin_ext_action', '')
    raw = (update.message.text or '').strip()
    try:
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
            count = mass_charge_wallets(amount)
            await update.message.reply_text(f'✅ کیف پول {count} کاربر، هرکدام {amount:,} تومان شارژ شد.')
        elif action == 'ordersearch':
            order = get_order_admin(int(raw.lstrip('#')))
            if not order:
                raise ValueError('سفارش پیدا نشد.')
            await update.message.reply_text(
                f'🔎 سفارش #{order[0]}\nکاربر: {order[7] or "—"} @{order[8] or "—"}\n'
                f'شناسه تلگرام: `{order[1]}`\nمبلغ: {order[2]:,} تومان\n'
                f'تخفیف: {order[3]:,}\nروش: {order[4]}\nوضعیت: {order[5]}\nتاریخ: {order[6]}',
                parse_mode='Markdown', reply_markup=admin_card_keyboard(order[0])
                if order[5] == 'pending' else admin_home_keyboard(),
            )
        elif action.startswith('setting:'):
            key = action.split(':', 1)[1]
            if key == 'payment_callback_base' and not raw.startswith('https://'):
                raise ValueError('آدرس callback باید با https:// شروع شود.')
            if key == 'card_number' and len(''.join(c for c in raw if c.isdigit())) != 16:
                raise ValueError('شماره کارت باید ۱۶ رقم باشد.')
            set_setting(key, raw)
            await update.message.reply_text('✅ ذخیره شد.', reply_markup=admin_home_keyboard())
        elif action == 'department':
            add_department(raw)
            await update.message.reply_text('✅ دپارتمان اضافه شد.', reply_markup=admin_home_keyboard())
        elif action == 'category':
            add_category(raw)
            await update.message.reply_text('✅ دسته‌بندی اضافه شد.', reply_markup=admin_home_keyboard())
        elif action == 'product':
            p = [x.strip() for x in raw.split('|')]
            add_store_product(p[0], int(p[1].replace(',', '')), int(p[2]), int(p[3]) if p[3] else None)
            await update.message.reply_text('✅ محصول اضافه شد.', reply_markup=admin_home_keyboard())
        elif action.startswith('promo:'):
            p = [x.strip() for x in raw.split('|')]
            add_promo_code(p[0], action.split(':')[1], p[1], p[2])
            await update.message.reply_text('✅ کد ساخته شد.', reply_markup=admin_home_keyboard())
        elif action == 'gemadd':
            p = [x.strip() for x in raw.split('|')]
            add_gem_package(p[0], p[1], p[2].replace(',', ''), p[3])
            await update.message.reply_text('✅ بسته جم اضافه شد.', reply_markup=admin_home_keyboard())
        elif action == 'senseadd':
            p = [x.strip() for x in raw.split('|')]
            platform = p[1].lower()
            if platform not in ('pc', 'mobile'):
                raise ValueError('پلتفرم فقط pc یا mobile است.')
            add_sense_package(p[0], platform, p[2].replace(',', ''), p[3] if len(p) > 3 else '')
            await update.message.reply_text('✅ پک سنس اضافه شد.', reply_markup=admin_home_keyboard())
        elif action == 'adminadd':
            p = [x.strip() for x in raw.split('|', 1)]
            if not p[0].isdigit():
                raise ValueError('شناسه تلگرام باید عددی باشد.')
            add_bot_admin(p[0], p[1] if len(p) > 1 else '')
            await update.message.reply_text('✅ مدیر اضافه شد.', reply_markup=admin_home_keyboard())
        elif action.startswith(('gemprice:', 'gemtitle:', 'gemstock:')):
            kind, gid = action.split(':')
            field = {'gemprice': 'Price', 'gemtitle': 'Title', 'gemstock': 'Stock'}[kind]
            update_gem_package(gid, field, raw.replace(',', '') if field != 'Title' else raw)
            await update.message.reply_text('✅ بسته جم ویرایش شد.', reply_markup=admin_home_keyboard())
        elif action.startswith(('senseprice:', 'sensetitle:')):
            kind, sid = action.split(':')
            field = 'Price' if kind == 'senseprice' else 'Title'
            update_sense_package(sid, field, raw.replace(',', '') if field == 'Price' else raw)
            await update.message.reply_text('✅ پک سنس ویرایش شد.', reply_markup=admin_home_keyboard())
    except (ValueError, IndexError) as e:
        ctx.user_data['admin_ext_action'] = action
        await update.message.reply_text(f'❌ ورودی نامعتبر: {e}\nدوباره بفرست یا /cancel بزن.')
        return WAIT_VALUE
    except Exception as e:
        await update.message.reply_text(f'❌ عملیات انجام نشد: {e}', reply_markup=admin_home_keyboard())
    return ConversationHandler.END


async def admin_input_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('admin_ext_action', None)
    await update.message.reply_text('انصراف.', reply_markup=admin_home_keyboard())
    return ConversationHandler.END


def admin_extended_conversation_handler():
    patterns = list(INPUT_ACTIONS)
    entry_pattern = '^(' + '|'.join(patterns) + r'|admi_(?:gemprice|gemtitle|gemstock|senseprice|sensetitle)_\d+)$'
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_input_start, pattern=entry_pattern)],
        states={WAIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_input_receive)]},
        fallbacks=[CommandHandler('cancel', admin_input_cancel)],
        allow_reentry=True,
    )
