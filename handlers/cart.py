from telegram import Update
from telegram.ext import ContextTypes
from keyboards import cart_keyboard
from db import get_product, get_sensitivity_pack

KIND_ICONS = {'p': '🎮', 'g': '💎', 's': '🎯'}


def _get_cart(ctx) -> dict:
    return ctx.user_data.setdefault('cart', {})


def _cart_total(cart):
    return sum(v['price'] * v['qty'] for v in cart.values())


def cart_add(ctx, kind, pk, name, price, meta=None, unique=False):
    """افزودن آیتم به سبد. unique=True یعنی هر بار یک خط جدا (مثل جم با UID مجزا)."""
    cart = _get_cart(ctx)
    if unique:
        n = 1
        while f"{kind}_{pk}_{n}" in cart:
            n += 1
        key = f"{kind}_{pk}_{n}"
        cart[key] = {'kind': kind, 'pk': pk, 'name': name, 'price': price, 'qty': 1,
                     'meta': meta or {}}
    else:
        key = f"{kind}_{pk}"
        if key in cart:
            cart[key]['qty'] += 1
        else:
            cart[key] = {'kind': kind, 'pk': pk, 'name': name, 'price': price, 'qty': 1,
                         'meta': meta or {}}
    return cart


def _cart_text(cart):
    if not cart:
        return "🛒 *سبد خرید خالیه*\n\nیه بسته جم یا محصول انتخاب کن تا اینجا اضافه بشه 😊"
    lines = ["🛒 *سبد خرید شما*", "━━━━━━━━━━━━━━━"]
    for item in cart.values():
        icon = KIND_ICONS.get(item['kind'], '•')
        subtotal = item['price'] * item['qty']
        qty = f" × {item['qty']}" if item['qty'] > 1 else ""
        lines.append(f"{icon} {item['name']}{qty}")
        # خط اطلاعات اختصاصی جم
        meta = item.get('meta') or {}
        if meta.get('game_uid'):
            lines.append(f"    └ 🆔 آیدی: `{meta['game_uid']}`")
        elif meta.get('login_email'):
            lines.append(f"    └ 🔐 اکانت: `{meta['login_email']}`")
        lines.append(f"    └ 💰 {subtotal:,} تومان")
    total = _cart_total(cart)
    lines.append("━━━━━━━━━━━━━━━")
    lines.append(f"💵 *جمع کل: {total:,} تومان*")
    return "\n".join(lines)


async def add_to_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """افزودن محصول فروشگاه یا پک سنس به سبد (add_p_5 | add_s_2)."""
    query = update.callback_query
    data = query.data
    parts = data.split('_')
    kind = parts[1]   # p | s
    pk = int(parts[2])

    if kind == 'p':
        item = get_product(pk)
        name, price = item[1], item[2]
    else:  # s
        row = get_sensitivity_pack(pk)
        name, price = row[1], row[2]

    cart = cart_add(ctx, kind, pk, name, price)
    await query.answer("✅ به سبد اضافه شد!")
    await query.edit_message_text(
        f"✅ *{name}* به سبد اضافه شد!\n\n"
        f"🛒 سبد شما: *{len(cart)} آیتم* | جمع: *{_cart_total(cart):,} تومان*",
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
