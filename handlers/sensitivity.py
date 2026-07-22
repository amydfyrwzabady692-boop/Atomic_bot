"""پک سنس — بخش PC با پرداخت زرین‌پال / کارت‌به‌کارت (مثل جم)."""
from telegram import Update
from telegram.ext import ContextTypes

from keyboards import (
    main_menu, sens_platform_keyboard, sens_pc_packs_keyboard,
    pay_method_keyboard, updating_keyboard,
)
from db import (
    get_or_create_user, create_order, add_order_item, get_wallet_balance,
)

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
    for p in SENSE_PC_PACKS.values():
        lines.append(f"• *{p['title']}* — {p['price']:,} تومان")
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode='Markdown',
        reply_markup=sens_pc_packs_keyboard(SENSE_PC_PACKS),
    )


async def sens_mobile_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✦ *پک سنس — موبایل*\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        "این بخش به‌زودی فعال می‌شود.",
        parse_mode='Markdown',
        reply_markup=updating_keyboard('sens'),
    )


async def sens_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """sens_buy_basic / sens_buy_plus"""
    query = update.callback_query
    await query.answer()
    key = query.data.replace('sens_buy_', '')
    pack = SENSE_PC_PACKS.get(key)
    if not pack:
        await query.edit_message_text("بسته پیدا نشد.", reply_markup=main_menu())
        return

    user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
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

    balance = get_wallet_balance(db_id)
    text = (
        f"✦ *انتخاب روش پرداخت*\n"
        f"سفارش `#{order_id}`\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"🎯 {pack['title']}\n"
        f"مبلغ: *{pack['price']:,}* تومان\n"
        f"موجودی کیف پول: {balance:,} ت\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"بعد از پرداخت موفق، پک در *پیوی تلگرام* برات ارسال می‌شود.\n"
        f"روش را انتخاب کن:"
    )
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=pay_method_keyboard(
            order_id,
            can_wallet=balance > 0,
            wallet_balance=balance,
            remaining=pack['price'],
        ),
    )
