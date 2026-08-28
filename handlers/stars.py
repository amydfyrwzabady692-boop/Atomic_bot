"""خرید استارز تلگرام با آیدی — شارژ مستقیم G2Bulk."""
import asyncio

from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

import appearance
import g2bulk
from db import (
    create_star_order_atomic,
    get_bool_setting,
    get_or_create_user,
    get_star_package,
    get_wallet_balance,
    list_star_packages,
    normalize_telegram_username,
)
from keyboards import (
    main_menu,
    pay_method_keyboard,
    stars_cancel_keyboard,
    stars_confirm_keyboard,
    stars_detail_keyboard,
    stars_list_keyboard,
    stars_uid_keyboard,
)
from payment_safety import checked_amount
from text_safety import markdown_safe

STARS_UID, STARS_CONFIRM = range(2)
STARS_PER_PAGE = 8

INSTANT_NOTE = (
    '⚡ اگر با درگاه زرین‌پال پرداخت کنی، بعد از تأیید پرداخت '
    'استارز به‌صورت آنی روی اکانت می‌نشیند.'
)


def _is_menu_tap(text):
    return (text or '').strip() in appearance.all_menu_labels()


def _md_escape(text):
    return markdown_safe(text, 160)


def _sold_out(pkg):
    return not pkg or not pkg.get('available') or int(pkg.get('price') or 0) <= 0


async def stars_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    packages = await asyncio.to_thread(list_star_packages)
    page = 1
    if update.callback_query and str(update.callback_query.data or '').startswith('st_page_'):
        page = int(update.callback_query.data.rsplit('_', 1)[-1])
    ctx.user_data['stars_page'] = page
    total_pages = max(1, (len(packages) + STARS_PER_PAGE - 1) // STARS_PER_PAGE)
    page = max(1, min(page, total_pages))
    payload = appearance.message_kwargs(
        't.stars.hdr', appearance.DEFAULTS['t.stars.hdr'],
        page=page, total=total_pages,
    )
    text = payload['text']
    if not packages:
        text += '\n❌ فعلاً بسته‌ای آماده نیست. کمی بعد دوباره سر بزن.'
        payload['text'] = text
        kb = stars_cancel_keyboard()
    else:
        kb = stars_list_keyboard(packages, page=page, per_page=STARS_PER_PAGE)

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(**payload, reply_markup=kb)
        except Exception:
            await update.callback_query.edit_message_text(text, reply_markup=kb)
        return
    try:
        await update.message.reply_text(**payload, reply_markup=kb)
    except Exception:
        await update.message.reply_text(text, reply_markup=kb)


async def show_star(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pk = int(query.data.split('_')[1])
    pkg = await asyncio.to_thread(get_star_package, pk)
    if not pkg:
        await query.edit_message_text('❌ بسته پیدا نشد.')
        return
    title = markdown_safe(pkg['title'], 120)
    text = (
        f"⭐ *{title}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔢 مقدار: *{int(pkg['stars']):,} استارز*\n"
        f"🚚 تحویل آنی روی آیدی تلگرام\n"
        f"💰 *{int(pkg['price']):,} تومان*\n\n"
        "بعد از انتخاب، آیدی اکانتی که می‌خوای استارز بزنی روش را می‌فرستی."
        f"\n\n{INSTANT_NOTE}"
    )
    if _sold_out(pkg):
        text += '\n\n❌ این مقدار فعلاً ناموجود است.'
        packages = await asyncio.to_thread(list_star_packages)
        await query.edit_message_text(
            text, parse_mode='Markdown',
            reply_markup=stars_list_keyboard(
                packages, page=ctx.user_data.get('stars_page', 1),
                per_page=STARS_PER_PAGE,
            ),
        )
        return
    await query.edit_message_text(
        text, parse_mode='Markdown',
        reply_markup=stars_detail_keyboard(
            pk, page=ctx.user_data.get('stars_page', 1)
        ),
    )


async def stars_buy_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pk = int(query.data.rsplit('_', 1)[-1])
    pkg = await asyncio.to_thread(get_star_package, pk)
    if _sold_out(pkg):
        await query.edit_message_text('❌ این بسته در دسترس نیست.')
        return ConversationHandler.END
    ctx.user_data['star_buy'] = {
        'pk': pk,
        'title': pkg['title'],
        'stars': pkg['stars'],
        'price': pkg['price'],
        'catalogue': pkg['catalogue'],
    }
    user = update.effective_user
    own = normalize_telegram_username(getattr(user, 'username', None) or '')
    await query.edit_message_text(
        f"🆔 *ثبت سفارش — {markdown_safe(pkg['title'], 120)}*\n"
        "━━━━━━━━━━━━━━━\n"
        "آیدی اکانتی که می‌خوای استارز بزنی روش بده.\n"
        "مثال: `@username`\n\n"
        "شناسه عددی تلگرام قبول نیست؛ باید آیدی عمومی (@) باشد.",
        parse_mode='Markdown',
        reply_markup=stars_uid_keyboard(own),
    )
    return STARS_UID


async def _accept_username(update, ctx, username, *, via_query=False):
    info = ctx.user_data.get('star_buy')
    if not info:
        text = '❌ جلسه سفارش منقضی شد. دوباره از منو شروع کن.'
        if via_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text, reply_markup=main_menu())
        return ConversationHandler.END
    wait_target = None
    if via_query:
        try:
            await update.callback_query.edit_message_text('⏳ در حال بررسی آیدی…')
        except Exception:
            pass
    else:
        wait_target = await update.message.reply_text('⏳ در حال بررسی آیدی…')

    player_name = ''
    if g2bulk.is_configured():
        result = await asyncio.to_thread(
            g2bulk.check_player_id, username, g2bulk.TELEGRAM_GAME_CODE,
        )
        if result.get('ok') and result.get('name'):
            player_name = str(result['name'])

    info['target_username'] = username
    info['player_name'] = player_name
    handle = f'@{username}'
    extra = f"\n👤 نام نمایشی: *{_md_escape(player_name)}*" if player_name else ''
    text = (
        f"✅ *آیدی ثبت شد*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🆔 اکانت: `{handle}`{extra}\n"
        f"⭐ بسته: {markdown_safe(info['title'], 120)}\n"
        f"💰 مبلغ: *{int(info['price']):,} تومان*\n\n"
        "اگر درست است تایید کن تا بری سراغ پرداخت."
    )
    if via_query:
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=stars_confirm_keyboard(),
        )
    else:
        await wait_target.edit_text(
            text, parse_mode='Markdown', reply_markup=stars_confirm_keyboard(),
        )
    return STARS_CONFIRM


async def stars_get_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or '').strip()
    if raw in appearance.all_menu_labels() or _is_menu_tap(raw):
        ctx.user_data.pop('star_buy', None)
        await update.message.reply_text('✖️ ثبت سفارش لغو شد.', reply_markup=main_menu())
        return ConversationHandler.END
    username = normalize_telegram_username(raw)
    if not username:
        await update.message.reply_text(
            '⚠️ آیدی باید @یوزرنیم عمومی تلگرام باشد (نه شناسه عددی).\n'
            'مثال: `@omid_1797` — دوباره بفرست:',
            parse_mode='Markdown',
            reply_markup=stars_uid_keyboard(
                normalize_telegram_username(
                    getattr(update.effective_user, 'username', None) or ''
                )
            ),
        )
        return STARS_UID
    return await _accept_username(update, ctx, username)


async def stars_use_self(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    own = normalize_telegram_username(
        getattr(update.effective_user, 'username', None) or ''
    )
    if not own:
        await query.edit_message_text(
            'این اکانت آیدی عمومی (@) ندارد.\n'
            'آیدی اکانتی که می‌خوای استارز بزنی روش را بفرست.',
            reply_markup=stars_uid_keyboard(''),
        )
        return STARS_UID
    return await _accept_username(update, ctx, own, via_query=True)


async def stars_reedit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    own = normalize_telegram_username(
        getattr(update.effective_user, 'username', None) or ''
    )
    await query.edit_message_text(
        '✏️ آیدی جدید را بفرست:\nمثال: `@username`',
        parse_mode='Markdown',
        reply_markup=stars_uid_keyboard(own),
    )
    return STARS_UID


async def stars_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_text(
            '⏳ *در حال بررسی نهایی موجودی و قیمت…*',
            parse_mode='Markdown',
        )
    except Exception:
        pass
    if not await asyncio.to_thread(get_bool_setting, 'sales_enabled', True):
        await query.edit_message_text(
            '⛔ فروش موقتاً توسط مدیریت متوقف شده است.',
            reply_markup=main_menu(),
        )
        return ConversationHandler.END
    info = ctx.user_data.get('star_buy')
    if not info or not info.get('target_username'):
        await query.edit_message_text('❌ اطلاعات سفارش ناقص است.')
        return ConversationHandler.END
    current = await asyncio.to_thread(get_star_package, info.get('pk'))
    if _sold_out(current):
        ctx.user_data.pop('star_buy', None)
        await query.edit_message_text(
            '❌ این بسته دیگر موجود نیست. لطفاً دوباره از فهرست انتخاب کن.'
        )
        return ConversationHandler.END
    try:
        current_price = checked_amount(current['price'], label='قیمت استارز')
    except ValueError:
        ctx.user_data.pop('star_buy', None)
        await query.edit_message_text('❌ قیمت این بسته معتبر نیست؛ سفارش ساخته نشد.')
        return ConversationHandler.END
    info.update({
        'title': current['title'],
        'price': current_price,
        'catalogue': current['catalogue'],
        'stars': current['stars'],
    })
    user = update.effective_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or 'کاربر تلگرام'

    def persist_order():
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        order_id, title, price = create_star_order_atomic(
            db_id, info['pk'], info['price'],
            telegram_id=user.id,
            full_name=full_name,
            target_username=info['target_username'],
            player_name=info.get('player_name') or '',
        )
        return db_id, order_id, title, price, int(get_wallet_balance(db_id) or 0)

    try:
        db_id, order_id, title, price, balance = await asyncio.to_thread(persist_order)
    except ValueError as exc:
        ctx.user_data.pop('star_buy', None)
        await query.edit_message_text(f'❌ {exc}')
        return ConversationHandler.END
    ctx.user_data['db_id'] = db_id
    ctx.user_data['pending_order'] = {
        'order_id': order_id,
        'total': price,
        'title': title,
        'tg_id': user.id,
    }
    ctx.user_data.pop('star_buy', None)
    handle = f"@{info['target_username']}"
    text = (
        f"✦ *انتخاب روش پرداخت*\n"
        f"سفارش `#{order_id}`\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"⭐ {markdown_safe(title, 120)}\n"
        f"اکانت `{handle}`\n"
        f"مبلغ: *{price:,}* تومان\n"
        f"موجودی کیف پول: *{balance:,}* ت\n"
        f"{INSTANT_NOTE}\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄\nروش را انتخاب کن:"
    )
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=pay_method_keyboard(
            order_id,
            can_wallet=True,
            wallet_balance=balance,
            remaining=price,
        ),
    )
    return ConversationHandler.END


async def stars_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('star_buy', None)
    query = update.callback_query
    if query:
        await query.answer('لغو شد')
        await query.edit_message_text('✖️ ثبت سفارش لغو شد.')
        await query.message.reply_text('چه کاری برات بکنم؟', reply_markup=main_menu())
    else:
        await update.message.reply_text('✖️ لغو شد.', reply_markup=main_menu())
    return ConversationHandler.END


def stars_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(stars_buy_start, pattern=r'^st_buy_\d+$')],
        states={
            STARS_UID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, stars_get_uid),
                CallbackQueryHandler(stars_use_self, pattern='^st_self$'),
                CallbackQueryHandler(stars_cancel, pattern='^st_cancel$'),
            ],
            STARS_CONFIRM: [
                CallbackQueryHandler(stars_confirm, pattern='^st_confirm$'),
                CallbackQueryHandler(stars_reedit, pattern='^st_reedit$'),
                CallbackQueryHandler(stars_cancel, pattern='^st_cancel$'),
            ],
        },
        fallbacks=[CallbackQueryHandler(stars_cancel, pattern='^st_cancel$')],
        per_message=False,
        allow_reentry=True,
    )
