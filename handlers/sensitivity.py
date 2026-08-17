"""پک سنس — بخش PC با پرداخت زرین‌پال / کارت‌به‌کارت (مثل جم)."""
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from keyboards import (
    main_menu, sens_platform_keyboard, sens_pc_packs_keyboard,
    pay_method_keyboard, updating_keyboard,
)
import appearance
from db import (
    get_or_create_user, create_sense_order_atomic, get_wallet_balance,
    list_sense_packages, get_sense_package, get_bool_setting,
)
from payment_safety import checked_amount
from text_safety import markdown_safe

async def sens_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    payload = appearance.message_kwargs('t.sense.hdr', appearance.DEFAULTS['t.sense.hdr'])
    kb = sens_platform_keyboard()
    try:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(**payload, reply_markup=kb)
        else:
            await update.message.reply_text(**payload, reply_markup=kb)
    except Exception:
        if update.callback_query:
            await update.callback_query.edit_message_text(payload['text'], reply_markup=kb)
        else:
            await update.message.reply_text(payload['text'], reply_markup=kb)


async def sens_pc_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lines = [
        appearance.user_label('t.sense.pc', appearance.DEFAULTS['t.sense.pc']),
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        "بسته را انتخاب کن:",
        "",
    ]
    packs = await asyncio.to_thread(list_sense_packages, 'pc', active_only=True)
    for p in packs:
        lines.append(f"• *{markdown_safe(p[1], 120)}* — {p[3]:,} تومان")
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
    packs = await asyncio.to_thread(list_sense_packages, 'mobile', active_only=True)
    if packs:
        lines = [appearance.user_label('t.sense.mob', appearance.DEFAULTS['t.sense.mob']), "┄┄┄┄┄┄┄┄┄┄┄┄┄┄", "بسته را انتخاب کن:", ""]
        for p in packs:
            lines.append(f"• *{markdown_safe(p[1], 120)}* — {p[3]:,} تومان")
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
    if not await asyncio.to_thread(get_bool_setting, 'sales_enabled', True):
        await query.edit_message_text(
            "⛔ فروش موقتاً توسط مدیریت متوقف شده است.",
            reply_markup=main_menu(),
        )
        return
    key = query.data.replace('sens_buy_', '')
    # Only current database rows are purchasable. Old static callback buttons
    # must not bypass an admin deletion, deactivation, or price change.
    row = await asyncio.to_thread(get_sense_package, key) if key.isdigit() else None
    if not row or not row[5]:
        await query.edit_message_text("بسته پیدا نشد.", reply_markup=main_menu())
        return
    pack = {'key': row[0], 'title': row[1], 'price': row[3], 'desc': row[4]}

    try:
        pack['price'] = checked_amount(pack.get('price'), label='قیمت بسته')
    except ValueError:
        await query.edit_message_text(
            "❌ قیمت این بسته معتبر نیست؛ سفارش ساخته نشد.",
            reply_markup=main_menu(),
        )
        return

    user = update.effective_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or 'کاربر تلگرام'

    def persist_order():
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        order_id, title, price = create_sense_order_atomic(
            db_id, pack['key'], pack['price'], telegram_id=user.id,
            full_name=full_name,
        )
        return db_id, order_id, title, price, int(get_wallet_balance(db_id) or 0)

    try:
        db_id, order_id, title, price, balance = await asyncio.to_thread(persist_order)
    except ValueError as exc:
        await query.edit_message_text(f"❌ {exc}", reply_markup=main_menu())
        return
    pack['title'], pack['price'] = title, price
    ctx.user_data['db_id'] = db_id

    ctx.user_data['pending_order'] = {
        'order_id': order_id,
        'total': pack['price'],
        'title': pack['title'],
        'tg_id': user.id,
        'kind': 'sense',
    }

    text = (
        f"✦ *انتخاب روش پرداخت*\n"
        f"سفارش `#{order_id}`\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"🎯 {markdown_safe(pack['title'], 120)}\n"
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
