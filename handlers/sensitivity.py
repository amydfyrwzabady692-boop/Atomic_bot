"""پک سنس — بخش PC با پرداخت زرین‌پال / کارت‌به‌کارت (مثل جم)."""
from telegram import Update
from telegram.ext import ContextTypes

from keyboards import (
    main_menu, sens_platform_keyboard, sens_pc_packs_keyboard,
    pay_method_keyboard, updating_keyboard,
)
from db import (
    get_or_create_user, create_order, add_order_item, get_wallet_balance,
    list_sense_packages, get_sense_package,
)
from payment_safety import checked_amount

# قیمت‌ها به تومان
SENSE_PC_PACKS = {
    'basic': {
        'key': 'basic',
        'title': 'پک سنس PC',
        'price': 1_000_000,
        'desc': 'پک سنس مخصوص سیستم PC',
    },
    'plus': {
        'key': 'plus',
        'title': 'پک سنس PC + خدمات',
        'price': 2_200_000,
        'desc': 'پک سنس PC همراه با خدمات',
    },
}


async def sens_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "✦ *پک سنس*\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        "پلتفرم را انتخاب کن:"
    )
    kb = sens_platform_keyboard()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=kb
        )
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)


async def sens_pc_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lines = [
        "✦ *پک سنس — PC*",
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        "بسته را انتخاب کن:",
        "",
    ]
    packs = list_sense_packages('pc', active_only=True)
    for p in packs:
        lines.append(f"• *{p[1]}* — {p[3]:,} تومان")
    if not packs:
        lines.append("فعلاً پکی برای PC فعال نیست.")
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode='Markdown',
        reply_markup=sens_pc_packs_keyboard(packs),
    )


async def sens_mobile_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    packs = list_sense_packages('mobile', active_only=True)
    if packs:
        lines = ["✦ *پک سنس — موبایل*", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄", "بسته را انتخاب کن:", ""]
        for p in packs:
            lines.append(f"• *{p[1]}* — {p[3]:,} تومان")
        await query.edit_message_text(
            "\n".join(lines), parse_mode='Markdown',
            reply_markup=sens_pc_packs_keyboard(packs),
        )
        return
    await query.edit_message_text(
        "✦ *پک سنس — موبایل*\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        "این بخش به‌زودی فعال می‌شود.",
        parse_mode='Markdown',
        reply_markup=updating_keyboard('sens'),
    )


async def sens_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """خرید پک سنس پویا با شناسه دیتابیس."""
    query = update.callback_query
    await query.answer()
    key = query.data.replace('sens_buy_', '')
    row = get_sense_package(key) if key.isdigit() else None
    if row:
        pack = {'key': row[0], 'title': row[1], 'price': row[3], 'desc': row[4]}
    else:
        # سازگاری با دکمه‌های قدیمی که ممکن است هنوز در چت کاربر باشند
        pack = SENSE_PC_PACKS.get(key)
    if not pack or (row and not row[5]):
        await query.edit_message_text("بسته پیدا نشد.", reply_markup=main_menu())
        return

    try:
        pack['price'] = checked_amount(pack.get('price'), label='قیمت بسته')
    except ValueError:
        await query.edit_message_text(
            "❌ قیمت این بسته معتبر نیست؛ سفارش ساخته نشد.",
            reply_markup=main_menu(),
        )
        return

    user = update.effective_user
    db_id, _ = get_or_create_user(
        user.id, user.first_name or '', user.last_name or '', user.username or ''
    )
    ctx.user_data['db_id'] = db_id

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or 'کاربر تلگرام'
    order_id = create_order(
        db_id, pack['price'], telegram_id=user.id,
        full_name=full_name, payment_method='pending',
    )
    add_order_item(order_id, pack['title'], pack['price'], 1)

    ctx.user_data['pending_order'] = {
        'order_id': order_id,
        'total': pack['price'],
        'title': pack['title'],
        'tg_id': user.id,
        'kind': 'sense',
    }

    balance = int(get_wallet_balance(db_id) or 0)
    text = (
        f"✦ *انتخاب روش پرداخت*\n"
        f"سفارش `#{order_id}`\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"🎯 {pack['title']}\n"
        f"مبلغ: *{pack['price']:,}* تومان\n"
        f"موجودی کیف پول: *{balance:,}* ت\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"بعد از پرداخت موفق، پک در *پیوی تلگرام* برات ارسال می‌شود.\n"
        f"روش را انتخاب کن:"
    )
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=pay_method_keyboard(
            order_id,
            can_wallet=True,
            wallet_balance=balance,
            remaining=pack['price'],
        ),
    )
