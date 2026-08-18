"""خرید گیفت‌کارت گوگل‌پلی و آیتونز از G2Bulk — تحویل کد خودکار."""
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

import appearance
import g2bulk
from db import (
    create_gift_card_order_atomic,
    get_bool_setting,
    get_or_create_user,
    get_priced_gift_card,
    get_wallet_balance,
    priced_gift_cards,
)
from keyboards import (
    giftcard_buy_keyboard,
    giftcard_list_keyboard,
    giftcard_menu_keyboard,
    main_menu,
    pay_method_keyboard,
)
from payment_safety import checked_amount
from text_safety import markdown_safe

_BRANDS = g2bulk.GIFT_CARD_BRAND_ORDER
_BRAND_PREFIX = 'gc_b_'


def parse_gift_brand(data):
    """کلید کامل دسته را برمی‌گرداند؛ gc_b_gplay_us → gplay_us نه us."""
    raw = str(data or '')
    if raw.startswith(_BRAND_PREFIX):
        brand = raw[len(_BRAND_PREFIX):]
        if brand in _BRANDS:
            return brand
    return None


async def giftcard_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    payload = appearance.message_kwargs(
        't.gc.hdr', appearance.DEFAULTS['t.gc.hdr'],
    )
    catalog = await asyncio.to_thread(priced_gift_cards)
    kb = giftcard_menu_keyboard(catalog if catalog.get('ok') else None)
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                **payload, reply_markup=kb,
            )
        except Exception:
            await update.callback_query.edit_message_text(
                payload.get('text') or appearance.DEFAULTS['t.gc.hdr'],
                reply_markup=kb,
            )
        return
    try:
        await update.message.reply_text(**payload, reply_markup=kb)
    except Exception:
        await update.message.reply_text(
            payload.get('text') or appearance.DEFAULTS['t.gc.hdr'],
            reply_markup=kb,
        )


async def giftcard_brand(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    brand = parse_gift_brand(query.data)
    if not brand:
        await query.edit_message_text('❌ این دسته گیفت‌کارت معتبر نیست.')
        return
    catalog = await asyncio.to_thread(priced_gift_cards, brand=brand)
    title = g2bulk.gift_card_brand_title(brand)
    if not catalog.get('ok'):
        await query.edit_message_text(
            f'❌ {catalog.get("error") or "کاتالوگ در دسترس نیست."}',
            reply_markup=giftcard_menu_keyboard(None),
        )
        return
    items = catalog.get('brands', {}).get(brand) or catalog.get('items') or []
    text = (
        f'🎁 *{markdown_safe(title, 80)}*\n'
        '━━━━━━━━━━━━━━━\n'
        'مبلغ روی کارت را انتخاب کن.\n'
        'قیمت فروش فقط به *تومان* است.'
    )
    if not items:
        text += '\n\n❌ فعلاً مبلغی در این دسته موجود نیست.'
    await query.edit_message_text(
        text, parse_mode='Markdown',
        reply_markup=giftcard_list_keyboard(brand, items),
    )


async def giftcard_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.rsplit('_', 1)[-1])
    item = await asyncio.to_thread(get_priced_gift_card, product_id)
    if not item.get('ok'):
        await query.edit_message_text(
            f'❌ {item.get("error") or "گیفت‌کارت پیدا نشد."}',
            reply_markup=giftcard_menu_keyboard(None),
        )
        return
    title = markdown_safe(item['brand_title'], 80)
    face = markdown_safe(item['face_label'], 40)
    price = int(item['sale_toman'])
    text = (
        f'🎁 *{title}*\n'
        f'━━━━━━━━━━━━━━━\n'
        f'📦 مبلغ کارت: *{face}*\n'
        f'💰 قیمت: *{price:,} تومان*\n'
        f'⚡️ تحویل آنی کد بعد از پرداخت\n'
    )
    if not item.get('can_buy'):
        text += '\n❌ این مبلغ الان موجود نیست.'
        await query.edit_message_text(
            text, parse_mode='Markdown',
            reply_markup=giftcard_list_keyboard(
                item['brand'],
                (await asyncio.to_thread(priced_gift_cards, brand=item['brand']))
                .get('items') or [],
            ),
        )
        return
    await query.edit_message_text(
        text, parse_mode='Markdown',
        reply_markup=giftcard_buy_keyboard(product_id, item['brand']),
    )


async def giftcard_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_text(
            '⏳ *در حال بررسی موجودی و قیمت…*',
            parse_mode='Markdown',
        )
    except Exception:
        pass
    if not await asyncio.to_thread(get_bool_setting, 'sales_enabled', True):
        await query.edit_message_text(
            '⛔ فروش موقتاً توسط مدیریت متوقف شده است.',
            reply_markup=main_menu(),
        )
        return
    product_id = int(query.data.rsplit('_', 1)[-1])
    item = await asyncio.to_thread(get_priced_gift_card, product_id, True)
    if not item.get('ok') or not item.get('can_buy'):
        await query.edit_message_text(
            f'❌ {item.get("error") or "این گیفت‌کارت دیگر موجود نیست."}'
        )
        return
    try:
        price = checked_amount(item['sale_toman'], label='قیمت گیفت‌کارت')
    except ValueError:
        await query.edit_message_text('❌ قیمت این گیفت‌کارت معتبر نیست.')
        return

    user = update.effective_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or 'کاربر تلگرام'

    def persist_order():
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        order_id, title, sale = create_gift_card_order_atomic(
            db_id, product_id, price,
            telegram_id=user.id, full_name=full_name,
        )
        return db_id, order_id, title, sale, int(get_wallet_balance(db_id) or 0)

    try:
        db_id, order_id, title, sale, balance = await asyncio.to_thread(persist_order)
    except ValueError as exc:
        await query.edit_message_text(f'❌ {exc}')
        return
    ctx.user_data['db_id'] = db_id
    ctx.user_data['pending_order'] = {
        'order_id': order_id,
        'total': sale,
        'title': title,
        'tg_id': user.id,
    }
    text = (
        f'✦ *انتخاب روش پرداخت*\n'
        f'سفارش `#{order_id}`\n'
        f'┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n'
        f'🎁 {markdown_safe(title, 120)}\n'
        f'مبلغ: *{sale:,}* تومان\n'
        f'موجودی کیف پول: *{balance:,}* ت\n'
        '⚡ بعد از تأیید پرداخت، کد گیفت‌کارت همان لحظه برایت ارسال می‌شود.\n'
        '┄┄┄┄┄┄┄┄┄┄┄┄┄┄\nروش را انتخاب کن:'
    )
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=pay_method_keyboard(
            order_id,
            can_wallet=True,
            wallet_balance=balance,
            remaining=sale,
        ),
    )
