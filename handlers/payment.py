from telegram import Update
from telegram.ext import ContextTypes
from keyboards import checkout_keyboard, main_menu, cart_keyboard
from db import get_or_create_user, create_order, add_order_item, update_order_status
from payments import request_payment, verify_payment
from handlers.cart import _get_cart, _cart_total, _cart_text


async def checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cart = _get_cart(ctx)

    if not cart:
        await query.edit_message_text(
            "❌ سبد خریدت خالیه!\nاول یه چیزی انتخاب کن.",
            reply_markup=cart_keyboard(has_items=False)
        )
        return

    total = _cart_total(cart)

    # build order in DB
    tg_id = ctx.user_data.get('tg_id') or update.effective_user.id
    db_user_id = ctx.user_data.get('db_id')
    if not db_user_id:
        user = update.effective_user
        db_user_id, _ = get_or_create_user(tg_id, user.first_name or '', user.last_name or '',
                                            user.username or '')
        ctx.user_data['db_id'] = db_user_id

    order_id = create_order(db_user_id, total)
    for key, item in cart.items():
        add_order_item(order_id, item['name'], item['qty'], item['price'])

    # request ZarinPal link
    desc = f"خرید از Atomic Shop — سفارش #{order_id}"
    callback_url = f"https://t.me/atomic_shop_bot?start=verify_{order_id}"
    ok, authority_or_err, pay_url = request_payment(total, desc, callback_url)

    if not ok:
        await query.edit_message_text(
            f"❌ خطا در اتصال به درگاه پرداخت:\n`{authority_or_err}`\n\nدوباره امتحان کن.",
            parse_mode='Markdown',
            reply_markup=cart_keyboard(has_items=True)
        )
        return

    ctx.user_data['pending_order'] = {'order_id': order_id, 'authority': authority_or_err}

    text = (
        f"💳 *پرداخت سفارش #{order_id}*\n\n"
        f"{_cart_text(cart)}\n\n"
        f"روی دکمه زیر بزن و در درگاه پرداخت کن.\n"
        f"بعد از پرداخت دکمه «تأیید پرداخت» رو بزن."
    )
    await query.edit_message_text(
        text, parse_mode='Markdown',
        reply_markup=checkout_keyboard(order_id, pay_url)
    )


async def verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Called when user taps 'تأیید پرداخت' button."""
    query = update.callback_query
    await query.answer("در حال بررسی...")

    pending = ctx.user_data.get('pending_order')
    if not pending:
        await query.edit_message_text("❌ سفارشی برای تأیید پیدا نشد.")
        return

    order_id = pending['order_id']
    authority = pending['authority']
    cart = _get_cart(ctx)
    total = _cart_total(cart)

    ok, ref_id, card_pan = verify_payment(total, authority)

    if ok:
        update_order_status(order_id, 'paid')
        ctx.user_data['cart'] = {}
        ctx.user_data.pop('pending_order', None)
        text = (
            f"✅ *پرداخت موفق!*\n\n"
            f"📦 سفارش #{order_id} ثبت شد\n"
            f"🧾 کد پیگیری: `{ref_id}`\n"
            f"💳 کارت: `{card_pan}`\n\n"
            f"در اسرع وقت محصولت تحویل داده میشه 🚀"
        )
    else:
        update_order_status(order_id, 'failed')
        ctx.user_data.pop('pending_order', None)
        text = (
            f"❌ *پرداخت ناموفق*\n\n"
            f"سفارش #{order_id} لغو شد.\n"
            f"اگر مبلغی کسر شده تا ۷۲ ساعت برمیگرده.\n"
            f"کد: `{ref_id}`"
        )

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=None)
    await update.effective_message.reply_text("چه کاری میتونم برات بکنم؟", reply_markup=main_menu())
