"""Free Fire weekly/monthly memberships fulfilled with temporary account access."""
import asyncio

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

from credential_vault import (
    CredentialVaultError, encrypt_credentials, is_configured, mask_identifier,
)
from db import (
    create_credential_gem_order_atomic, get_gem, get_gems_by_credentials,
    get_or_create_user, get_wallet_balance,
)
from keyboards import (
    credential_backup_keyboard, credential_cancel_keyboard,
    credential_confirm_keyboard, credential_method_keyboard,
    credential_products_keyboard, freefire_products_keyboard, main_menu,
    pay_method_keyboard,
)
from payment_safety import checked_amount
from text_safety import markdown_safe


CRED_QUANTITY, CRED_METHOD, CRED_IDENTIFIER, CRED_PASSWORD, CRED_BACKUP, CRED_CONFIRM = range(19, 25)

CREDENTIAL_QTY_MAX = 50
_DIGIT_MAP = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')


def parse_credential_quantity(raw) -> int:
    text = str(raw or '').strip().translate(_DIGIT_MAP).replace(',', '').replace(' ', '')
    if not text.isdigit():
        raise ValueError('تعداد باید یک عدد صحیح باشد (مثلاً ۱ یا ۳).')
    qty = int(text)
    if qty < 1 or qty > CREDENTIAL_QTY_MAX:
        raise ValueError(f'تعداد باید بین ۱ تا {CREDENTIAL_QTY_MAX} باشد.')
    return qty

METHOD_META = {
    'google': {
        'label': 'Gmail / Google',
        'id_prompt': (
            '📧 *مرحله ۱ از ۳ — ایمیل Gmail*\n'
            'ایمیل اکانت گوگل متصل به فری‌فایر را بفرست.\n'
            'مثال: `name@gmail.com`'
        ),
        'pass_prompt': (
            '🔑 *مرحله ۲ از ۳ — رمز عبور*\n'
            'رمز فعلی اکانت Gmail را بفرست.\n'
            'بعد از تحویل حتماً رمز را عوض کن. این پیام از گفت‌وگو حذف می‌شود.'
        ),
        'backup_prompt': (
            '🛡 *مرحله ۳ از ۳ — کد بک‌آپ Gmail*\n'
            '━━━━━━━━━━━━━━━\n'
            'این کد برای ورود ادمین به اکانت لازم است.\n'
            'اول طبق راهنما کد را پیدا کن و اینجا بفرست.\n\n'
            '*راهنمای گرفتن کد بک‌آپ گوگل:*\n'
            '۱) گوشی یا کامپیوتر → برو به\n'
            '`https://myaccount.google.com`\n'
            '۲) با همان ایمیلی که به فری‌فایر وصل است وارد شو\n'
            '۳) از سمت چپ بزن: *Security* یا *امنیت*\n'
            '۴) پیدا کن: *2-Step Verification* / *تأیید دو مرحله‌ای*\n'
            '   (اگر خاموش بود اول روشن کن)\n'
            '۵) پایین صفحه بزن: *Backup codes* / *کدهای پشتیبان*\n'
            '۶) بزن: *Get backup codes* / *دریافت کدها*\n'
            '۷) چند کد چندرقمی می‌بینی — همه‌شان یا چند تا را اینجا بفرست\n'
            '   (هر خط یک کد)\n\n'
            '⚠️ کدهای استفاده‌شده را نفرست؛ کد تازه بفرست.'
        ),
    },
    'facebook': {
        'label': 'Facebook',
        'id_prompt': (
            '📘 *مرحله ۱ از ۳ — شناسه Facebook*\n'
            'ایمیل، شماره موبایل یا نام‌کاربری فیسبوک متصل به فری‌فایر را بفرست.'
        ),
        'pass_prompt': (
            '🔑 *مرحله ۲ از ۳ — رمز عبور Facebook*\n'
            'رمز ورود فیسبوک را بفرست.\n'
            'بعد از تحویل رمز را عوض کن. این پیام حذف می‌شود.'
        ),
        'backup_prompt': (
            '🛡 *مرحله ۳ از ۳ — کد بک‌آپ Facebook*\n'
            '━━━━━━━━━━━━━━━\n'
            'این کد برای ورود ادمین به اکانت لازم است.\n'
            'اول طبق راهنما کد را پیدا کن و اینجا بفرست.\n\n'
            '*راهنمای گرفتن کد بک‌آپ فیسبوک:*\n'
            '۱) اپ یا سایت فیسبوک را باز کن و وارد همان حساب شو\n'
            '۲) برو به *Settings / تنظیمات*\n'
            '   یا *Accounts Center / مرکز حساب‌ها*\n'
            '۳) بزن: *Password and security* / *رمز عبور و امنیت*\n'
            '۴) بزن: *Two-factor authentication* / *تأیید دو مرحله‌ای*\n'
            '   (اگر خاموش بود اول روشن کن)\n'
            '۵) باز کن: *Recovery codes* یا *Backup codes*\n'
            '   / *کدهای بازیابی*\n'
            '۶) کدها را کپی کن و همین‌جا بفرست (هر خط یک کد)'
        ),
    },
    'vk': {
        'label': 'VK',
        'id_prompt': (
            '🟣 *مرحله ۱ از ۳ — شناسه VK*\n'
            'ایمیل، شماره موبایل یا نام‌کاربری VK متصل به فری‌فایر را بفرست.'
        ),
        'pass_prompt': (
            '🔑 *مرحله ۲ از ۳ — رمز عبور VK*\n'
            'رمز ورود VK را بفرست.\n'
            'بعد از تحویل رمز را عوض کن. این پیام حذف می‌شود.'
        ),
        'backup_prompt': (
            '🛡 *مرحله ۳ از ۳ — کد بک‌آپ VK*\n'
            '━━━━━━━━━━━━━━━\n'
            'این کد برای ورود ادمین به اکانت لازم است.\n'
            'اول طبق راهنما کد را پیدا کن و اینجا بفرست.\n\n'
            '*راهنمای گرفتن کد بک‌آپ VK:*\n'
            '۱) اپ یا سایت VK را باز کن و وارد همان حساب شو\n'
            '۲) برو به *Settings* / *Настройки* / *تنظیمات*\n'
            '۳) باز کن: *Security* / *Безопасность* / *امنیت*\n'
            '۴) بزن: *Two-step verification* / *تأیید دو مرحله‌ای*\n'
            '   (اگر خاموش بود اول روشن کن)\n'
            '۵) باز کن: *Backup codes* / *کدهای پشتیبان*\n'
            '۶) کدها را کپی کن و همین‌جا بفرست (هر خط یک کد)'
        ),
    },
}


def _backup_footer_text():
    return (
        '\n\n━━━━━━━━━━━━━━━\n'
        '✅ اگر کد را پیدا کردی → همین‌جا بفرست\n'
        '🆘 اگر بلد نیستی / پیدا نکردی → دکمه زیر را بزن:\n'
        '*«نیاز به راهنمایی — بک‌آپ بلد نیستم»*\n'
        'بعد پرداخت کن؛ *پس از پرداخت موفق* پشتیبانی با شماره سفارش کمکت می‌کند.'
    )


def _clear_secrets(info):
    if not info:
        return
    info.pop('password', None)
    info.pop('backup_code', None)


async def freefire_products_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        '🎮 *محصولات فری‌فایر*\n'
        '━━━━━━━━━━━━━━━\n'
        'روش خرید را انتخاب کن:\n\n'
        '🆔 *جم با آیدی*\n'
        '⚡ تحویل لحظه‌ای · قیمت پایین\n'
        'فقط آیدی بازی را می‌فرستی و جم خودکار واریز می‌شود.\n\n'
        '🔐 *جم با اطلاعات*\n'
        '📅 عضویت هفتگی و ماهانه\n'
        'با اطلاعات ورود اکانت، توسط پشتیبانی انجام می‌شود.'
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=freefire_products_keyboard()
        )
    else:
        await update.message.reply_text(
            text, parse_mode='Markdown', reply_markup=freefire_products_keyboard()
        )


async def credential_products_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    products = await asyncio.to_thread(get_gems_by_credentials)
    text = (
        '🔐 *جم با اطلاعات اکانت*\n'
        '━━━━━━━━━━━━━━━\n'
        'عضویت *هفتگی* یا *ماهانه* را انتخاب کن.\n'
        'بعد از انتخاب بسته، تعداد را بفرست؛ سپس روش ورود ← شناسه ← رمز ← بک‌آپ.\n\n'
        'راهنمای گرفتن بک‌آپ برای Gmail / Facebook / VK داخل چت می‌آید.\n'
        '🔒 بعد از پرداخت، دسترسی به آیدی پشتیبان باز می‌شود.'
    )
    if not products:
        text += '\n\n❌ فعلاً محصول فعالی وجود ندارد.'
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        text, parse_mode='Markdown',
        reply_markup=credential_products_keyboard(products),
    )


async def show_credential_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.rsplit('_', 1)[1])
    product = await asyncio.to_thread(get_gem, product_id)
    if not product or product[7] != 'by_credentials':
        await query.edit_message_text('❌ محصول پیدا نشد یا غیرفعال شده است.')
        return
    plan = 'هفتگی' if product[6] == 'weekly' else 'ماهانه'
    text = (
        f'🔐 *{markdown_safe(product[1], 120)}*\n'
        '━━━━━━━━━━━━━━━\n'
        f'📅 دوره: *{plan}*\n'
        f'💰 قیمت هر عدد: *{int(product[4]):,} تومان*\n'
        '⏳ تحویل: دستی پس از بررسی اطلاعات توسط ادمین\n\n'
        'مراحل بعدی:\n'
        '۱) وارد کردن تعداد\n'
        '۲) انتخاب روش ورود (Gmail / Facebook / VK)\n'
        '۳) شناسه ورود\n'
        '۴) رمز عبور\n'
        '۵) راهنمای بک‌آپ (اگر بلد نیستی: نیاز به راهنمایی)\n'
        '۶) پرداخت جمع کل\n\n'
        '🔒 بعد از پرداخت، دسترسی به آیدی پشتیبان باز می‌شود.\n'
        'بعد از تحویل حتماً رمز اکانت را عوض کن.'
    )
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    await query.edit_message_text(
        text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ ادامه و ثبت اطلاعات', callback_data=f'cbuy_{product_id}')],
            [InlineKeyboardButton('🔙 بازگشت', callback_data='gems_credentials')],
        ])
    )


async def credential_buy_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.rsplit('_', 1)[1])
    product = await asyncio.to_thread(get_gem, product_id)
    if not product or product[7] != 'by_credentials' or product[11] is False:
        await query.edit_message_text('❌ این محصول در دسترس نیست.')
        return ConversationHandler.END
    if not is_configured():
        await query.edit_message_text(
            '❌ بخش ثبت اطلاعات اکانت موقتاً در دسترس نیست. با پشتیبانی تماس بگیر.'
        )
        return ConversationHandler.END
    ctx.user_data['credential_buy'] = {
        'pk': product_id, 'title': str(product[1]), 'unit_price': int(product[4]),
        'price': int(product[4]),
    }
    await query.edit_message_text(
        f'🔢 *تعداد را وارد کن*\n'
        f'محصول: {markdown_safe(product[1], 120)}\n'
        f'قیمت هر عدد: *{int(product[4]):,} تومان*\n\n'
        f'یک عدد بین ۱ تا {CREDENTIAL_QTY_MAX} بفرست.\n'
        'جمع کل بعد از وارد کردن تعداد مشخص می‌شود.',
        parse_mode='Markdown',
        reply_markup=credential_cancel_keyboard(),
    )
    return CRED_QUANTITY


async def credential_quantity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    info = ctx.user_data.get('credential_buy')
    if not info:
        return ConversationHandler.END
    try:
        qty = parse_credential_quantity(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(
            f'❌ {exc}',
            reply_markup=credential_cancel_keyboard(),
        )
        return CRED_QUANTITY
    unit = int(info.get('unit_price') or info.get('price') or 0)
    try:
        total = checked_amount(unit * qty, label='مبلغ کل')
    except ValueError as exc:
        await update.message.reply_text(
            f'❌ {exc}',
            reply_markup=credential_cancel_keyboard(),
        )
        return CRED_QUANTITY
    info['quantity'] = qty
    info['price'] = total
    await update.message.reply_text(
        f'✅ تعداد: *{qty}* عدد\n'
        f'جمع کل: *{total:,} تومان*\n\n'
        '🔐 *روش ورود به اکانت را انتخاب کن*\n'
        'فری‌فایر با کدام حساب ذخیره/متصل شده؟',
        parse_mode='Markdown',
        reply_markup=credential_method_keyboard(),
    )
    return CRED_METHOD


async def credential_method_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    info = ctx.user_data.get('credential_buy')
    if not info or not int(info.get('quantity') or 0):
        if query:
            await query.edit_message_text('❌ اول تعداد را وارد کن؛ از انتخاب بسته دوباره شروع کن.')
        return ConversationHandler.END
    method = query.data.split('_')[-1]
    if method not in METHOD_META:
        await query.edit_message_text('❌ روش ورود نامعتبر است.')
        return ConversationHandler.END
    info['method'] = method
    await query.edit_message_text(
        METHOD_META[method]['id_prompt'],
        parse_mode='Markdown',
        reply_markup=credential_cancel_keyboard(),
    )
    return CRED_IDENTIFIER


async def credential_identifier(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    value = (update.message.text or '').strip()
    info = ctx.user_data.get('credential_buy')
    if not info or not info.get('method'):
        return ConversationHandler.END
    if len(value) < 4 or len(value) > 180 or '\n' in value:
        await update.message.reply_text('شناسه ورود معتبر نیست؛ دوباره بفرست.')
        return CRED_IDENTIFIER
    method = info['method']
    if method == 'google' and '@' not in value:
        await update.message.reply_text(
            'برای Gmail باید ایمیل کامل بفرستی (مثلاً name@gmail.com).'
        )
        return CRED_IDENTIFIER
    info['identifier'] = value
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.effective_chat.send_message(
        METHOD_META[method]['pass_prompt'],
        parse_mode='Markdown',
        reply_markup=credential_cancel_keyboard(),
        protect_content=True,
    )
    return CRED_PASSWORD


async def credential_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    password = update.message.text or ''
    info = ctx.user_data.get('credential_buy')
    if not info or not info.get('method'):
        return ConversationHandler.END
    if not 6 <= len(password) <= 200 or '\n' in password:
        await update.message.reply_text('رمز باید بین ۶ تا ۲۰۰ کاراکتر و تک‌خطی باشد.')
        return CRED_PASSWORD
    info['password'] = password
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.effective_chat.send_message(
        METHOD_META[info['method']]['backup_prompt'] + _backup_footer_text(),
        parse_mode='Markdown',
        reply_markup=credential_backup_keyboard(),
        protect_content=True,
    )
    return CRED_BACKUP


async def _show_confirm(update, ctx, *, via_callback=False):
    info = ctx.user_data.get('credential_buy')
    if not info:
        return ConversationHandler.END
    method_label = METHOD_META[info['method']]['label']
    has_backup = bool(str(info.get('backup_code') or '').strip())
    backup_line = (
        'ثبت شد ✅' if has_backup else 'نیاز به راهنمایی / ارسال نشده 🆘'
    )
    qty = int(info.get('quantity') or 1)
    unit = int(info.get('unit_price') or 0)
    unit_line = f'قیمت هر عدد: {unit:,} تومان\n' if unit else ''
    text = (
        f'✅ *بازبینی اطلاعات*\n'
        f'━━━━━━━━━━━━━━━\n'
        f'محصول: {markdown_safe(info["title"], 120)}\n'
        f'تعداد: *{qty}* عدد\n'
        f'{unit_line}'
        f'روش ورود: {method_label}\n'
        f'شناسه: `{markdown_safe(mask_identifier(info["identifier"]), 100)}`\n'
        f'رمز: ثبت شد ✅\n'
        f'کد بک‌آپ: {backup_line}\n'
        f'جمع کل: *{info["price"]:,} تومان*\n\n'
        f'با تأیید، سفارش ساخته می‌شود و صفحه پرداخت باز می‌شود.\n'
    )
    if not has_backup:
        text += (
            '🆘 بک‌آپ نفرستادی / نیاز به راهنمایی زدی.\n'
            'الان پرداخت کن؛ *بعد از پرداخت موفق* دکمه پشتیبانی باز می‌شود.\n'
            'همان‌جا شماره سفارش را بفرست تا کمکت کنند بک‌آپ را درست کنند.\n'
        )
    else:
        text += (
            'اگر بعد از پرداخت بک‌آپ کار نکرد، دکمه پشتیبانی با شماره سفارش برایت می‌آید.\n'
        )
    if via_callback:
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=credential_confirm_keyboard(),
        )
    else:
        await update.effective_chat.send_message(
            text, parse_mode='Markdown', reply_markup=credential_confirm_keyboard(),
        )
    return CRED_CONFIRM


async def credential_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    info = ctx.user_data.get('credential_buy')
    if not info:
        return ConversationHandler.END
    backup = (update.message.text or '').strip()
    if len(backup) < 4 or len(backup) > 800:
        await update.message.reply_text(
            'اگر کد بک‌آپ داری بین ۴ تا ۸۰۰ کاراکتر بفرست.\n'
            'اگر بلد نیستی پیدا کنی، دکمه *«نیاز به راهنمایی»* را بزن '
            'و بعد از پرداخت پشتیبانی کمکت می‌کند.',
            parse_mode='Markdown',
            reply_markup=credential_backup_keyboard(),
        )
        return CRED_BACKUP
    info['backup_code'] = backup
    info['two_factor'] = True
    try:
        await update.message.delete()
    except Exception:
        pass
    return await _show_confirm(update, ctx, via_callback=False)


async def credential_backup_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """بلد نیست / بک‌آپ ندارد → برو پرداخت؛ راهنمایی واقعی بعد از پرداخت."""
    query = update.callback_query
    await query.answer(
        'باشه — بعد از پرداخت، پشتیبانی کمکت می‌کند.',
        show_alert=False,
    )
    info = ctx.user_data.get('credential_buy')
    if not info:
        return ConversationHandler.END
    info['backup_code'] = ''
    info['two_factor'] = False
    info['backup_skipped'] = True
    return await _show_confirm(update, ctx, via_callback=True)


async def credential_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    info = ctx.user_data.get('credential_buy')
    if not info or not info.get('password'):
        await query.edit_message_text(
            '❌ اطلاعات ناقص است. دوباره از اول ثبت کن.'
        )
        return ConversationHandler.END
    current = await asyncio.to_thread(get_gem, info['pk'])
    if not current or current[7] != 'by_credentials':
        _clear_secrets(info)
        ctx.user_data.pop('credential_buy', None)
        await query.edit_message_text('❌ محصول دیگر فعال نیست.')
        return ConversationHandler.END
    try:
        qty = int(info.get('quantity') or 1)
        unit = checked_amount(current[4], label='قیمت محصول')
        price = checked_amount(unit * qty, label='مبلغ کل')
        ciphertext = encrypt_credentials(
            info['identifier'],
            info['password'],
            backup_code=info.get('backup_code') or '',
        )
        user = update.effective_user
        full_name = f'{user.first_name or ""} {user.last_name or ""}'.strip() or 'کاربر تلگرام'
        two_factor = bool(info.get('two_factor') or info.get('backup_code'))

        def persist():
            db_id, _ = get_or_create_user(
                user.id, user.first_name or '', user.last_name or '', user.username or ''
            )
            order_id, title, saved_price = create_credential_gem_order_atomic(
                db_id, info['pk'], price, telegram_id=user.id, full_name=full_name,
                login_method=info['method'], credential_ciphertext=ciphertext,
                two_factor_enabled=two_factor,
                quantity=qty,
            )
            return db_id, order_id, title, saved_price, int(get_wallet_balance(db_id) or 0)

        db_id, order_id, title, price, balance = await asyncio.to_thread(persist)
    except (ValueError, CredentialVaultError) as exc:
        _clear_secrets(info)
        ctx.user_data.pop('credential_buy', None)
        await query.edit_message_text(f'❌ {exc}')
        return ConversationHandler.END
    finally:
        _clear_secrets(info)
    ctx.user_data['db_id'] = db_id
    ctx.user_data['pending_order'] = {
        'order_id': order_id, 'total': price, 'title': title, 'tg_id': user.id,
    }
    ctx.user_data.pop('credential_buy', None)
    try:
        from handlers.payment import _notify_credential_sale
        await _notify_credential_sale(ctx.bot, order_id, event='created')
    except Exception:
        pass
    await query.edit_message_text(
        f'✦ *انتخاب روش پرداخت*\n'
        f'سفارش `#{order_id}`\n'
        f'محصول: {markdown_safe(title, 120)}\n'
        f'تعداد: *{int(info.get("quantity") or 1)}* عدد\n'
        f'مبلغ: *{price:,} تومان*\n'
        f'موجودی کیف پول: *{balance:,} تومان*\n\n'
        'بعد از پرداخت موفق، سفارش برای ادمین ارسال می‌شود.\n'
        'اگر بک‌آپ بلد نبودی، *بعد از پرداخت* دکمه پیام به پشتیبانی با شماره سفارش برایت باز می‌شود.',
        parse_mode='Markdown',
        reply_markup=pay_method_keyboard(
            order_id, can_wallet=True, wallet_balance=balance, remaining=price
        ),
    )
    return ConversationHandler.END


async def credential_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    info = ctx.user_data.pop('credential_buy', None)
    _clear_secrets(info)
    if update.callback_query:
        await update.callback_query.answer('لغو شد')
        await update.callback_query.edit_message_text('✖️ ثبت اطلاعات لغو شد.')
        await update.callback_query.message.reply_text('منوی اصلی', reply_markup=main_menu())
    return ConversationHandler.END


def credential_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(credential_buy_start, pattern=r'^cbuy_\d+$')],
        states={
            CRED_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, credential_quantity),
                CallbackQueryHandler(credential_cancel, pattern='^cred_cancel$'),
            ],
            CRED_METHOD: [
                CallbackQueryHandler(
                    credential_method_selected,
                    pattern=r'^cred_method_(google|facebook|vk)$',
                ),
                CallbackQueryHandler(credential_cancel, pattern='^cred_cancel$'),
            ],
            CRED_IDENTIFIER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, credential_identifier),
                CallbackQueryHandler(credential_cancel, pattern='^cred_cancel$'),
            ],
            CRED_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, credential_password),
                CallbackQueryHandler(credential_cancel, pattern='^cred_cancel$'),
            ],
            CRED_BACKUP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, credential_backup),
                CallbackQueryHandler(credential_backup_skip, pattern='^cred_backup_skip$'),
                CallbackQueryHandler(credential_cancel, pattern='^cred_cancel$'),
            ],
            CRED_CONFIRM: [
                CallbackQueryHandler(credential_confirm, pattern='^cred_confirm$'),
                CallbackQueryHandler(credential_cancel, pattern='^cred_cancel$'),
            ],
        },
        fallbacks=[CallbackQueryHandler(credential_cancel, pattern='^cred_cancel$')],
        allow_reentry=True,
    )
