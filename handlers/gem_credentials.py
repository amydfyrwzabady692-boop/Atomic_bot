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
    credential_2fa_keyboard, credential_cancel_keyboard,
    credential_confirm_keyboard, credential_method_keyboard,
    credential_products_keyboard, freefire_products_keyboard, main_menu,
    pay_method_keyboard,
)
from payment_safety import checked_amount
from text_safety import markdown_safe


CRED_IDENTIFIER, CRED_PASSWORD, CRED_2FA, CRED_CONFIRM = range(20, 24)


async def freefire_products_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        '🎮 *محصولات فری‌فایر*\n'
        'روش خرید را انتخاب کن:\n\n'
        '🆔 *جم با آیدی* — همان بسته‌ها و تحویل خودکار فعلی\n'
        '🔐 *جم با اطلاعات* — عضویت هفتگی و ماهانه با انجام دستی امن'
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
        'محصول موردنظر را انتخاب کن. اطلاعات ورود رمزگذاری می‌شود و بعد از '
        'تکمیل یا لغو سفارش حذف خواهد شد.'
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
    base_try = 60 if product[6] == 'weekly' else 300
    text = (
        f'🔐 *{markdown_safe(product[1], 120)}*\n'
        '━━━━━━━━━━━━━━━\n'
        f'📅 دوره: *{plan}*\n'
        f'🇹🇷 مبنای هزینه: iTunes Turkey {base_try} TRY\n'
        f'💰 قیمت فروش: *{int(product[4]):,} تومان*\n'
        '⏳ تحویل: دستی پس از بررسی اطلاعات\n\n'
        'برای ورود، یک رمز موقت بده و بعد از تحویل حتماً آن را عوض کن. '
        'کد یک‌بارمصرف دو مرحله‌ای داخل ربات ذخیره نمی‌شود و هنگام ورود '
        'با پشتیبانی هماهنگ خواهد شد.'
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
            '❌ بخش امن اطلاعات روی سرور هنوز پیکربندی نشده است. با پشتیبانی تماس بگیر.'
        )
        return ConversationHandler.END
    ctx.user_data['credential_buy'] = {
        'pk': product_id, 'title': str(product[1]), 'price': int(product[4]),
    }
    await query.edit_message_text(
        '🔐 اکانت فری‌فایر با کدام روش ذخیره/متصل شده است؟',
        reply_markup=credential_method_keyboard(),
    )
    return CRED_IDENTIFIER


async def credential_method_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    info = ctx.user_data.get('credential_buy')
    if not info:
        return ConversationHandler.END
    method = query.data.split('_')[-1]
    info['method'] = method
    labels = {'google': 'ایمیل Gmail', 'facebook': 'ایمیل، شماره یا نام کاربری Facebook',
              'vk': 'ایمیل، شماره یا نام کاربری VK'}
    warning = ''
    if method == 'google':
        warning = (
            '\n\n⚠️ App Password گوگل برای ورود وب/OAuth فری‌فایر قابل اتکا نیست؛ '
            'ایمیل اصلی و یک رمز موقت بفرست. کد دو مرحله‌ای را فقط هنگام ورود '
            'با پشتیبانی هماهنگ کن.'
        )
    await query.edit_message_text(
        f'شناسه ورود را بفرست:\n{labels[method]}{warning}',
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
    info['identifier'] = value
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.effective_chat.send_message(
        '🔑 حالا *رمز موقت* اکانت را بفرست.\n'
        'بعد از تکمیل سفارش آن را تغییر بده. پیام رمز از گفت‌وگو حذف می‌شود.',
        parse_mode='Markdown', reply_markup=credential_cancel_keyboard(),
        protect_content=True,
    )
    return CRED_PASSWORD


async def credential_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    password = update.message.text or ''
    info = ctx.user_data.get('credential_buy')
    if not info:
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
        '🛡 تأیید دومرحله‌ای این حساب فعال است؟\n\n'
        'کد لحظه‌ای یا Recovery Code را اینجا نفرست. اگر فعال باشد، هنگام ورود '
        'پشتیبانی برای دریافت همان کد یک‌بارمصرف با تو هماهنگ می‌کند.',
        reply_markup=credential_2fa_keyboard(),
    )
    return CRED_2FA


async def credential_2fa_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    info = ctx.user_data.get('credential_buy')
    if not info:
        return ConversationHandler.END
    info['two_factor'] = query.data.endswith('_yes')
    method_label = {'google': 'Gmail / Google', 'facebook': 'Facebook', 'vk': 'VK'}[info['method']]
    guide = ''
    if not info['two_factor']:
        guide = {
            'google': (
                '\n\n🛡 راهنمای فعال‌سازی: Google Account ← Security ← '
                '2-Step Verification. کد بازیابی را برای خودت نگه دار و داخل ربات نفرست.'
            ),
            'facebook': (
                '\n\n🛡 راهنمای فعال‌سازی: Facebook Settings ← Password and security ← '
                'Two-factor authentication. کد بازیابی را داخل ربات نفرست.'
            ),
            'vk': (
                '\n\n🛡 راهنمای فعال‌سازی: VK Settings ← Security ← Two-step '
                'verification. کد بازیابی را داخل ربات نفرست.'
            ),
        }[info['method']]
    await query.edit_message_text(
        f'✅ *بازبینی اطلاعات*\n'
        f'محصول: {markdown_safe(info["title"], 120)}\n'
        f'روش ورود: {method_label}\n'
        f'شناسه: `{markdown_safe(mask_identifier(info["identifier"]), 100)}`\n'
        f'تأیید دومرحله‌ای: {"فعال" if info["two_factor"] else "غیرفعال"}\n'
        f'مبلغ: *{info["price"]:,} تومان*\n\n'
        f'با تأیید، اطلاعات رمزگذاری و سفارش ساخته می‌شود.{guide}',
        parse_mode='Markdown', reply_markup=credential_confirm_keyboard(),
    )
    return CRED_CONFIRM


async def credential_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    info = ctx.user_data.get('credential_buy')
    if not info or not info.get('password'):
        await query.edit_message_text('❌ اطلاعات ناقص یا جلسه منقضی شده است.')
        return ConversationHandler.END
    current = await asyncio.to_thread(get_gem, info['pk'])
    if not current or current[7] != 'by_credentials':
        ctx.user_data.pop('credential_buy', None)
        await query.edit_message_text('❌ محصول دیگر فعال نیست.')
        return ConversationHandler.END
    try:
        price = checked_amount(current[4], label='قیمت محصول')
        ciphertext = encrypt_credentials(info['identifier'], info['password'])
        user = update.effective_user
        full_name = f'{user.first_name or ""} {user.last_name or ""}'.strip() or 'کاربر تلگرام'

        def persist():
            db_id, _ = get_or_create_user(
                user.id, user.first_name or '', user.last_name or '', user.username or ''
            )
            order_id, title, saved_price = create_credential_gem_order_atomic(
                db_id, info['pk'], price, telegram_id=user.id, full_name=full_name,
                login_method=info['method'], credential_ciphertext=ciphertext,
                two_factor_enabled=info['two_factor'],
            )
            return db_id, order_id, title, saved_price, int(get_wallet_balance(db_id) or 0)

        db_id, order_id, title, price, balance = await asyncio.to_thread(persist)
    except (ValueError, CredentialVaultError) as exc:
        ctx.user_data.pop('credential_buy', None)
        await query.edit_message_text(f'❌ {exc}')
        return ConversationHandler.END
    finally:
        if info:
            info.pop('password', None)
    ctx.user_data['db_id'] = db_id
    ctx.user_data['pending_order'] = {
        'order_id': order_id, 'total': price, 'title': title, 'tg_id': user.id,
    }
    ctx.user_data.pop('credential_buy', None)
    await query.edit_message_text(
        f'✦ *انتخاب روش پرداخت*\n'
        f'سفارش `#{order_id}`\n'
        f'محصول: {markdown_safe(title, 120)}\n'
        f'مبلغ: *{price:,} تومان*\n'
        f'موجودی کیف پول: *{balance:,} تومان*\n\n'
        'شماره سفارش را برای پیگیری نگه دار.',
        parse_mode='Markdown',
        reply_markup=pay_method_keyboard(
            order_id, can_wallet=True, wallet_balance=balance, remaining=price
        ),
    )
    return ConversationHandler.END


async def credential_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    info = ctx.user_data.pop('credential_buy', None)
    if info:
        info.pop('password', None)
    if update.callback_query:
        await update.callback_query.answer('لغو شد')
        await update.callback_query.edit_message_text('✖️ ثبت اطلاعات لغو شد.')
        await update.callback_query.message.reply_text('منوی اصلی', reply_markup=main_menu())
    return ConversationHandler.END


def credential_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(credential_buy_start, pattern=r'^cbuy_\d+$')],
        states={
            CRED_IDENTIFIER: [
                CallbackQueryHandler(credential_method_selected, pattern=r'^cred_method_(google|facebook|vk)$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, credential_identifier),
                CallbackQueryHandler(credential_cancel, pattern='^cred_cancel$'),
            ],
            CRED_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, credential_password),
                CallbackQueryHandler(credential_cancel, pattern='^cred_cancel$'),
            ],
            CRED_2FA: [
                CallbackQueryHandler(credential_2fa_selected, pattern=r'^cred_2fa_(yes|no)$'),
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
