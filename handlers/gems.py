"""خرید جم با آیدی — بسته‌ها مثل سایت + تایید G2Bulk."""
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

import g2bulk
from keyboards import (
    gems_list_keyboard, gem_detail_keyboard, gem_cancel_keyboard,
    gem_confirm_keyboard, pay_method_keyboard, main_menu,
)
from db import (
    get_gems_by_id, get_gem, get_or_create_user, create_order,
    add_order_item, add_gem_order_info, get_wallet_balance, update_order_status,
)

GEM_UID, GEM_CONFIRM = range(2)


def _gem_sold_out(g):
    auto = bool(g[8])
    stock = g[10] or 0
    available = g[11] is not False
    if auto:
        return not available
    return (not available) or stock <= 0


async def gems_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gems = get_gems_by_id()
    text = (
        "💎 *جم فری‌فایر — خرید با آیدی*\n"
        "━━━━━━━━━━━━━━━\n"
        "شارژ مستقیم الماس فری‌فایر (Middle East)\n"
        "_بعد از پرداخت موفق، جم به‌صورت خودکار واریز می‌شود_\n\n"
    )
    if not gems:
        text += "❌ فعلاً بسته‌ای فعال نیست. کمی بعد دوباره سر بزن."
        kb = gem_cancel_keyboard()
    else:
        text += "بسته مورد نظرت رو انتخاب کن 👇"
        kb = gems_list_keyboard(gems)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)


async def show_gem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'noop':
        return
    pk = int(query.data.split('_')[1])
    g = get_gem(pk)
    if not g:
        await query.edit_message_text("❌ بسته پیدا نشد.")
        return
    total = g[2] + (g[3] or 0)
    bonus = f"  (+{g[3]:,} هدیه 🎁)" if g[3] else ""
    price_line = f"💰 *{g[4]:,} تومان*"
    if g[5] and g[5] > g[4]:
        off = round((g[5] - g[4]) / g[5] * 100)
        price_line = f"~~{g[5]:,}~~ ← 💰 *{g[4]:,} تومان*  🔥 {off}%-"

    deliver = "⚡️ تحویل آنی (خودکار)" if g[8] else "⏳ تحویل دستی پس از تایید"
    text = (
        f"💎 *{g[1]}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔢 مقدار: *{total:,} الماس*{bonus}\n"
        f"🚚 {deliver}\n"
        f"{price_line}\n\n"
        f"برای ادامه فقط *آیدی بازی (UID)* لازم است."
    )
    if _gem_sold_out(g):
        text += "\n\n❌ این بسته فعلاً ناموجود است."
        await query.edit_message_text(text, parse_mode='Markdown',
                                      reply_markup=gems_list_keyboard(get_gems_by_id()))
        return
    await query.edit_message_text(
        text, parse_mode='Markdown', reply_markup=gem_detail_keyboard(pk)
    )


async def gem_buy_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pk = int(query.data.split('_')[1])
    g = get_gem(pk)
    if not g or _gem_sold_out(g):
        await query.edit_message_text("❌ این بسته در دسترس نیست.")
        return ConversationHandler.END

    ctx.user_data['gem_buy'] = {
        'pk': pk,
        'title': g[1],
        'amount': g[2],
        'price': g[4],
        'auto_deliver': bool(g[8]),
        'catalogue': g[9] or str(g[2]),
    }
    await query.edit_message_text(
        f"🆔 *ثبت سفارش — {g[1]}*\n"
        "━━━━━━━━━━━━━━━\n"
        "آیدی فری‌فایر (UID) را بفرست.\n"
        "_(عددی در پروفایل بازی، معمولاً حدود ۱۰ رقم)_",
        parse_mode='Markdown',
        reply_markup=gem_cancel_keyboard(),
    )
    return GEM_UID


async def gem_get_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = (update.message.text or '').strip()
    uid = uid.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    if not uid.isdigit() or len(uid) < 5:
        await update.message.reply_text(
            "⚠️ آیدی باید فقط عدد معتبر باشد. دوباره بفرست:",
            reply_markup=gem_cancel_keyboard(),
        )
        return GEM_UID

    info = ctx.user_data.get('gem_buy')
    if not info:
        await update.message.reply_text("❌ جلسه سفارش منقضی شد. دوباره از منو شروع کن.",
                                        reply_markup=main_menu())
        return ConversationHandler.END

    await update.message.reply_text("⏳ در حال بررسی آیدی بازی…")
    result = g2bulk.check_player_id(uid)
    if not result['ok']:
        await update.message.reply_text(
            f"❌ {result.get('error') or 'آیدی معتبر نیست.'}\n"
            "آیدی را اصلاح کن و دوباره بفرست:",
            reply_markup=gem_cancel_keyboard(),
        )
        return GEM_UID

    info['game_uid'] = uid
    info['player_name'] = result['name']
    await update.message.reply_text(
        f"✅ *اکانت تایید شد*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 نام اکانت: *{result['name']}*\n"
        f"🆔 UID: `{uid}`\n"
        f"💎 بسته: {info['title']}\n"
        f"💰 مبلغ: *{info['price']:,} تومان*\n\n"
        f"اگر درست است تایید کن تا بری سراغ پرداخت.",
        parse_mode='Markdown',
        reply_markup=gem_confirm_keyboard(),
    )
    return GEM_CONFIRM


async def gem_reedit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ آیدی جدید را بفرست:",
        reply_markup=gem_cancel_keyboard(),
    )
    return GEM_UID


async def gem_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    info = ctx.user_data.get('gem_buy')
    if not info or not info.get('game_uid'):
        await query.edit_message_text("❌ اطلاعات سفارش ناقص است.")
        return ConversationHandler.END

    user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or 'کاربر تلگرام'
    order_id = create_order(
        db_id, info['price'], telegram_id=user.id, full_name=full_name, payment_method='pending'
    )
    item_id = add_order_item(order_id, info['title'], info['price'], 1)
    add_gem_order_info(
        order_id, item_id, info['pk'], 'by_id',
        telegram_id=user.id,
        game_uid=info['game_uid'],
        player_name=info.get('player_name'),
    )

    ctx.user_data['pending_order'] = {
        'order_id': order_id,
        'total': info['price'],
        'title': info['title'],
        'game_uid': info['game_uid'],
        'player_name': info.get('player_name'),
        'tg_id': user.id,
    }
    ctx.user_data.pop('gem_buy', None)

    balance = get_wallet_balance(db_id)
    can_wallet = balance >= info['price']
    text = (
        f"💳 *انتخاب روش پرداخت — سفارش #{order_id}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💎 {info['title']}\n"
        f"🆔 `{info['game_uid']}` — {info.get('player_name') or ''}\n"
        f"💰 مبلغ: *{info['price']:,} تومان*\n"
        f"👛 موجودی کیف پول: {balance:,} ت\n\n"
        f"روش پرداخت را انتخاب کن:"
    )
    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=pay_method_keyboard(order_id, can_wallet=can_wallet),
    )
    return ConversationHandler.END


async def gem_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('gem_buy', None)
    query = update.callback_query
    if query:
        await query.answer("لغو شد")
        await query.edit_message_text("✖️ ثبت سفارش لغو شد.")
        await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
    else:
        await update.message.reply_text("✖️ لغو شد.", reply_markup=main_menu())
    return ConversationHandler.END


def gem_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(gem_buy_start, pattern=r'^gbuy_\d+$')],
        states={
            GEM_UID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, gem_get_uid),
                CallbackQueryHandler(gem_cancel, pattern='^gem_cancel$'),
            ],
            GEM_CONFIRM: [
                CallbackQueryHandler(gem_confirm, pattern='^gem_confirm$'),
                CallbackQueryHandler(gem_reedit, pattern='^gem_reedit$'),
                CallbackQueryHandler(gem_cancel, pattern='^gem_cancel$'),
            ],
        },
        fallbacks=[CallbackQueryHandler(gem_cancel, pattern='^gem_cancel$')],
        per_message=False,
        allow_reentry=True,
    )
