import os
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, filters
)
from keyboards import (
    card_payment_keyboard, receipt_skip_keyboard, main_menu, cart_keyboard,
)
from db import (
    get_or_create_user, create_order, add_order_item, add_gem_order_info,
    update_order_status, decrement_gem_stock,
)
from handlers.cart import _get_cart, _cart_total, _cart_text

CARD_NUMBER = os.getenv('CARD_NUMBER', '')
CARD_HOLDER = os.getenv('CARD_HOLDER', '')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID', '').strip()

WAIT_RECEIPT = 0


def _card_pretty(num):
    return ' '.join(num[i:i + 4] for i in range(0, len(num), 4)) if num else num


async def checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cart = _get_cart(ctx)

    if not cart:
        await query.edit_message_text(
            "❌ سبد خریدت خالیه!\nاول یه بسته انتخاب کن.",
            reply_markup=cart_keyboard(has_items=False)
        )
        return

    total = _cart_total(cart)
    user = update.effective_user
    tg_id = user.id

    db_user_id = ctx.user_data.get('db_id')
    if not db_user_id:
        db_user_id, _ = get_or_create_user(
            tg_id, user.first_name or '', user.last_name or '', user.username or ''
        )
        ctx.user_data['db_id'] = db_user_id

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or 'کاربر تلگرام'
    order_id = create_order(db_user_id, total, telegram_id=tg_id, full_name=full_name)

    gem_pks = []
    for item in cart.values():
        product_id = item['pk'] if item['kind'] == 'p' else None
        item_id = add_order_item(order_id, item['name'], item['price'], item['qty'], product_id)
        if item['kind'] == 'g':
            m = item.get('meta') or {}
            add_gem_order_info(
                order_id, item_id, item['pk'], m.get('purchase_type', 'by_id'),
                telegram_id=tg_id, game_uid=m.get('game_uid'),
                login_method=m.get('login_method'), login_email=m.get('login_email'),
                login_password=m.get('login_password'), backup_code=m.get('backup_code'),
            )
            gem_pks.append(item['pk'])

    ctx.user_data['pending_order'] = {
        'order_id': order_id, 'total': total, 'gem_pks': gem_pks,
    }

    text = (
        f"💳 *پرداخت کارت‌به‌کارت — سفارش #{order_id}*\n"
        "━━━━━━━━━━━━━━━\n"
        f"{_cart_text(cart)}\n"
        "━━━━━━━━━━━━━━━\n"
        f"مبلغ قابل پرداخت: *{total:,} تومان*\n\n"
        f"🔢 شماره کارت:\n`{_card_pretty(CARD_NUMBER)}`\n"
        f"👤 به نام: *{CARD_HOLDER}*\n\n"
        "1️⃣ مبلغ بالا رو دقیق کارت‌به‌کارت کن.\n"
        "2️⃣ بعد دکمه *«پرداخت کردم»* رو بزن و *عکس رسید* یا *کد پیگیری* رو بفرست.\n\n"
        "_(روی شماره کارت بزن تا کپی بشه)_"
    )
    await query.edit_message_text(
        text, parse_mode='Markdown', reply_markup=card_payment_keyboard()
    )


async def paid_claim_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """paid_done → از کاربر رسید می‌خواهیم."""
    query = update.callback_query
    await query.answer()
    if not ctx.user_data.get('pending_order'):
        await query.edit_message_text("❌ سفارشی برای پرداخت پیدا نشد.")
        return ConversationHandler.END
    await query.edit_message_text(
        "🧾 *ارسال رسید پرداخت*\n"
        "━━━━━━━━━━━━━━━\n"
        "لطفاً *عکس رسید* یا *کد پیگیری/شماره تراکنش* رو همینجا بفرست.",
        parse_mode='Markdown', reply_markup=receipt_skip_keyboard()
    )
    return WAIT_RECEIPT


async def _finalize(update, ctx, receipt_msg=None, via_query=None):
    pending = ctx.user_data.get('pending_order')
    if not pending:
        return ConversationHandler.END
    order_id = pending['order_id']
    user = update.effective_user

    # رزرو موجودی جم
    for pk in pending.get('gem_pks', []):
        decrement_gem_stock(pk, 1)
    update_order_status(order_id, 'pending')

    # ارسال اطلاعات و رسید برای ادمین
    if ADMIN_CHAT_ID:
        try:
            uname = f"@{user.username}" if user.username else "—"
            await ctx.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=(f"🆕 سفارش #{order_id} — پرداخت کارت‌به‌کارت\n"
                      f"مبلغ: {pending['total']:,} ت\n"
                      f"کاربر: {user.full_name} ({uname})\n"
                      f"آیدی تلگرام: {user.id}")
            )
            if receipt_msg:
                await ctx.bot.copy_message(
                    chat_id=int(ADMIN_CHAT_ID),
                    from_chat_id=receipt_msg.chat_id,
                    message_id=receipt_msg.message_id,
                )
        except Exception:
            pass

    ctx.user_data['cart'] = {}
    ctx.user_data.pop('pending_order', None)

    text = (
        "✅ *رسیدت دریافت شد!*\n"
        "━━━━━━━━━━━━━━━\n"
        f"📦 سفارش #{order_id} ثبت شد و در حال بررسیه.\n"
        "بعد از تأیید پرداخت، سفارشت تحویل داده می‌شه و از همین‌جا بهت خبر می‌دیم 🚀\n\n"
        "ممنون از خریدت 💎"
    )
    if via_query:
        await via_query.edit_message_text(text, parse_mode='Markdown')
        await via_query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())
    return ConversationHandler.END


async def receive_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _finalize(update, ctx, receipt_msg=update.message)


async def skip_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await _finalize(update, ctx, receipt_msg=None, via_query=query)


async def cancel_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("انصراف داده شد")
    pending = ctx.user_data.pop('pending_order', None)
    if pending:
        update_order_status(pending['order_id'], 'canceled')
    await query.edit_message_text(
        "❌ پرداخت لغو شد. سبد خریدت دست‌نخورده باقی موند.",
        reply_markup=cart_keyboard(has_items=bool(_get_cart(ctx)))
    )
    return ConversationHandler.END


def payment_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(paid_claim_start, pattern='^paid_done$')],
        states={
            WAIT_RECEIPT: [
                MessageHandler((filters.PHOTO | filters.Document.ALL |
                                (filters.TEXT & ~filters.COMMAND)), receive_receipt),
                CallbackQueryHandler(skip_receipt, pattern='^paid_skip$'),
                CallbackQueryHandler(cancel_order, pattern='^cancel_order$'),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_order, pattern='^cancel_order$')],
        per_message=False,
    )
