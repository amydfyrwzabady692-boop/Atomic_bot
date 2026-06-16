from telegram import Update
from telegram.ext import ContextTypes
from keyboards import cart_keyboard, main_menu
from db import get_product, get_gem, get_sensitivity_packs


def _get_cart(ctx) -> dict:
    return ctx.user_data.setdefault('cart', {})


def _cart_total(cart):
    return sum(v['price'] * v['qty'] for v in cart.values())


def _cart_text(cart):
    if not cart:
        return "🛒 *سبد خرید خالیه*\n\nبرو یه چیزی بخر 😊"
    lines = ["🛒 *سبد خرید شما:*\n"]
    for key, item in cart.items():
        subtotal = item['price'] * item['qty']
        lines.append(f"• {item['name']} × {item['qty']} = *{subtotal:,} ت*")
    total = _cart_total(cart)
    lines.append(f"\n💰 *جمع کل: {total:,} تومان*")
    return "\n".join(lines)


async def add_to_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✅ به سبد اضافه شد!")
    data = query.data  # add_p_5 | add_g_3 | add_s_2

    parts = data.split('_')
    kind = parts[1]   # p | g | s
    pk = int(parts[2])

    cart = _get_cart(ctx)
    key = f"{kind}_{pk}"

    if key in cart:
        cart[key]['qty'] += 1
        msg = f"✅ *{cart[key]['name']}* — تعداد: {cart[key]['qty']}"
    else:
        if kind == 'p':
            item = get_product(pk)
            name, price = item[1], item[2]
        elif kind == 'g':
            item = get_gem(pk)
            name, price = item[1], item[4]
        else:  # s
            # sensitivity packs — fetch by id
            from db import get_conn
            with get_conn() as conn:
                row = conn.cursor().execute(
                    "SELECT Id, Title, Price FROM SensitivityPacks WHERE Id=?", pk
                ).fetchone()
            name, price = row[1], row[2]

        cart[key] = {'name': name, 'price': price, 'qty': 1, 'kind': kind}
        msg = f"✅ *{name}* به سبد اضافه شد!"

    ctx.user_data['cart'] = cart
    await query.answer(msg.replace('*', ''), show_alert=False)
    await query.edit_message_text(
        msg + f"\n\n🛒 سبد شما: *{len(cart)} آیتم* | جمع: *{_cart_total(cart):,} ت*",
        parse_mode='Markdown',
        reply_markup=cart_keyboard(has_items=True)
    )


async def show_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cart = _get_cart(ctx)
    text = _cart_text(cart)
    kb = cart_keyboard(has_items=bool(cart))
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)


async def clear_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🗑 سبد پاک شد")
    ctx.user_data['cart'] = {}
    await query.edit_message_text(
        "🗑 سبد خرید خالی شد.",
        reply_markup=cart_keyboard(has_items=False)
    )
