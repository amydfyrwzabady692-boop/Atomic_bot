"""پشتیبانی کاربر ↔ ادمین."""
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, CommandHandler, filters,
)

from keyboards import main_menu, support_cancel_keyboard, admin_ticket_keyboard
from admin_notify import notify_admin, is_admin
from db import (
    get_or_create_user, create_ticket, add_ticket_message,
    get_active_ticket_for_user, is_user_blocked, get_setting,
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

    default_text = (
        "🎧 *پشتیبانی Atomic*\n"
        "━━━━━━━━━━━━━━━\n"
        "پیامت را همین‌جا بفرست (متن).\n"
        "ادمین در تلگرام می‌بیند و جواب می‌دهد.\n\n"
        "برای انصراف /cancel"
    )
    text = get_setting('support_text', '').strip() or default_text
    support_id = get_setting('support_id', '').strip()
    if support_id:
        text += f"\n\nآیدی پشتیبانی: {support_id}"
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode='Markdown', reply_markup=support_cancel_keyboard()
            )
        except Exception:
            await update.callback_query.edit_message_text(
                text, reply_markup=support_cancel_keyboard()
            )
    else:
        try:
            await update.message.reply_text(
                text, parse_mode='Markdown', reply_markup=support_cancel_keyboard()
            )
        except Exception:
            await update.message.reply_text(text, reply_markup=support_cancel_keyboard())
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

    ticket_id = get_active_ticket_for_user(db_id)
    if ticket_id:
        add_ticket_message(ticket_id, 'user', text)
        subject = f"ادامه تیکت #{ticket_id}"
    else:
        subject = f"پشتیبانی از {user.first_name or user.id}"
        ticket_id = create_ticket(
            db_id, subject, text, category='bot', telegram_id=user.id
        )

    uname = f"@{user.username}" if user.username else "—"
    await notify_admin(
        ctx.bot,
        (
            f"🎧 *تیکت پشتیبانی #{ticket_id}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"کاربر: {user.full_name} ({uname})\n"
            f"تلگرام: `{user.id}`\n\n"
            f"{text}"
        ),
        reply_markup=admin_ticket_keyboard(ticket_id, user.id),
    )

    await update.message.reply_text(
        f"✅ پیام ثبت شد (تیکت #{ticket_id}).\n"
        f"به‌محض پاسخ ادمین همین‌جا خبرت می‌کنیم.",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def support_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
            MessageHandler(filters.Regex('^🎧 پشتیبانی$'), support_menu),
            CallbackQueryHandler(support_menu, pattern='^support$'),
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
