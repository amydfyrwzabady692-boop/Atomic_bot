"""پشتیبانی کاربر ↔ ادمین."""
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, CommandHandler, filters,
)

from keyboards import main_menu, support_cancel_keyboard, admin_ticket_keyboard
import appearance
from admin_notify import (
    notify_admin, notify_credential_admins, is_admin, is_credential_admin,
)
from db import (
    get_or_create_user, create_ticket, add_ticket_message,
    get_active_ticket_for_user, is_user_blocked, get_setting, get_credential_order,
)

WAIT_MSG = 0


async def support_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_blocked(user.id):
        text = "🚫 حساب شما بلاک است و نمی‌توانید تیکت بسازید."
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return ConversationHandler.END

    if is_admin(user.id):
        text = (
            "🎧 شما ادمین هستی.\n"
            "برای مدیریت تیکت‌ها دستور /admin را بزن و بخش «تیکت‌های باز» را باز کن."
        )
        if update.message:
            await update.message.reply_text(text, reply_markup=main_menu())
        return ConversationHandler.END

    if is_credential_admin(user.id) and not is_admin(user.id):
        text = (
            "🔐 شما پشتیبان جم با اطلاعات هستی.\n"
            "برای سفارش‌ها و تیکت‌های این بخش دستور /credadmin را بزن."
        )
        if update.message:
            await update.message.reply_text(text, reply_markup=main_menu())
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text)
        return ConversationHandler.END

    ctx.user_data['support_category'] = 'bot'
    ctx.user_data.pop('support_order_id', None)

    stored = get_setting('support_text', '').strip()
    payload = appearance.message_kwargs(
        't.support', stored or appearance.DEFAULTS['t.support']
    )
    text = payload['text']
    from db import get_support_contact
    support = get_support_contact()
    if support.get('handle'):
        text += f"\n\n🎧 آیدی پشتیبانی:\n`{support['handle']}`"
    payload['text'] = text
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                **payload, reply_markup=support_cancel_keyboard()
            )
        except Exception:
            await update.callback_query.edit_message_text(
                text, reply_markup=support_cancel_keyboard()
            )
    else:
        try:
            await update.message.reply_text(
                **payload, reply_markup=support_cancel_keyboard()
            )
        except Exception:
            await update.message.reply_text(text, reply_markup=support_cancel_keyboard())
    return WAIT_MSG


async def credential_support_ticket_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """بعد از پرداخت جم با اطلاعات — تیکت راهنمایی بک‌آپ برای پشتیبان همان بخش."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if is_user_blocked(user.id):
        await query.edit_message_text("🚫 حساب شما بلاک است.")
        return ConversationHandler.END
    if is_admin(user.id) or is_credential_admin(user.id):
        await query.edit_message_text(
            "این مسیر برای مشتری است. از /credadmin تیکت‌ها را مدیریت کن."
        )
        return ConversationHandler.END

    order_id = int(query.data.rsplit('_', 1)[1])
    row = get_credential_order(order_id)
    if not row or str(row[1]) != str(user.id):
        await query.edit_message_text("❌ این سفارش مال شما نیست یا پیدا نشد.")
        return ConversationHandler.END
    if row[3] not in ('paid', 'processing', 'delivered', 'completed'):
        await query.edit_message_text(
            "اول باید پرداخت سفارش موفق باشد؛ بعد می‌توانی تیکت راهنمایی باز کنی."
        )
        return ConversationHandler.END

    ctx.user_data['support_category'] = 'credential'
    ctx.user_data['support_order_id'] = order_id
    text = (
        f"🆘 *راهنمایی بک‌آپ — سفارش #{order_id}*\n"
        "━━━━━━━━━━━━━━━\n"
        "پیامت را همین‌جا بفرست.\n"
        "پشتیبان بخش جم با اطلاعات می‌بیند و کمکت می‌کند.\n\n"
        "حتماً بگو بک‌آپ بلد نیستی یا کد کار نمی‌کند.\n"
        "برای انصراف /cancel"
    )
    await query.edit_message_text(
        text, parse_mode='Markdown', reply_markup=support_cancel_keyboard()
    )
    return WAIT_MSG


async def support_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_blocked(user.id):
        await update.message.reply_text("🚫 حساب شما بلاک است.")
        return ConversationHandler.END

    text = (update.message.text or '').strip()
    if not text:
        await update.message.reply_text("فقط متن بفرست.")
        return WAIT_MSG

    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    category = str(ctx.user_data.get('support_category') or 'bot')
    order_id = ctx.user_data.get('support_order_id')

    ticket_id = get_active_ticket_for_user(db_id, category=category)
    if ticket_id:
        add_ticket_message(ticket_id, 'user', text)
        subject = f"ادامه تیکت #{ticket_id}"
    else:
        if category == 'credential' and order_id:
            subject = f"راهنمایی بک‌آپ سفارش #{order_id} — {user.first_name or user.id}"
            body = f"سفارش #{order_id}\n\n{text}"
        else:
            subject = f"پشتیبانی از {user.first_name or user.id}"
            body = text
        ticket_id = create_ticket(
            db_id, subject, body, category=category, telegram_id=user.id
        )

    uname = f"@{user.username}" if user.username else "—"
    back_to = 'adm_cred_tickets' if category == 'credential' else 'admx_hub_support'
    notify_text = (
        f"{'🔐' if category == 'credential' else '🎧'} *تیکت پشتیبانی #{ticket_id}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"دسته: {'جم با اطلاعات' if category == 'credential' else 'عمومی'}\n"
        f"کاربر: {user.full_name} ({uname})\n"
        f"تلگرام: `{user.id}`\n"
    )
    if order_id:
        notify_text += f"سفارش: `#{order_id}`\n"
    notify_text += f"\n{text}"

    markup = admin_ticket_keyboard(ticket_id, user.id if category != 'credential' else None,
                                   back_to=back_to)
    # برای credential به پشتیبان همان بخش؛ برای بقیه به ادمین کامل
    if category == 'credential':
        # دکمه باز کردن سفارش هم مفید است
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        rows = list(markup.inline_keyboard)
        if order_id:
            rows.insert(0, [InlineKeyboardButton(
                f'🔐 باز کردن سفارش #{order_id}',
                callback_data=f'admx_credential_{order_id}',
            )])
        markup = InlineKeyboardMarkup(rows)
        await notify_credential_admins(
            ctx.bot, notify_text, reply_markup=markup,
        )
    else:
        await notify_admin(
            ctx.bot, notify_text, reply_markup=markup,
        )

    ctx.user_data.pop('support_category', None)
    ctx.user_data.pop('support_order_id', None)

    await update.message.reply_text(
        f"✅ پیام ثبت شد (تیکت #{ticket_id}).\n"
        f"به‌محض پاسخ پشتیبانی همین‌جا خبرت می‌کنیم.",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def support_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('support_category', None)
    ctx.user_data.pop('support_order_id', None)
    if update.callback_query:
        await update.callback_query.answer("انصراف")
        await update.callback_query.edit_message_text("پشتیبانی لغو شد.")
        await update.callback_query.message.reply_text(
            "منوی اصلی:", reply_markup=main_menu()
        )
    else:
        await update.message.reply_text("پشتیبانی لغو شد.", reply_markup=main_menu())
    return ConversationHandler.END


def support_conversation_handler():
    return ConversationHandler(
        entry_points=[
            MessageHandler(appearance.MenuActionFilter('support'), support_menu),
            CallbackQueryHandler(support_menu, pattern='^support$'),
            CallbackQueryHandler(
                credential_support_ticket_start, pattern=r'^cred_ticket_\d+$'
            ),
        ],
        states={
            WAIT_MSG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive),
            ],
        },
        fallbacks=[
            CommandHandler('cancel', support_cancel),
            CallbackQueryHandler(support_cancel, pattern='^support_cancel$'),
        ],
        allow_reentry=True,
    )
