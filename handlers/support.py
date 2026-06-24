from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, filters
)
from keyboards import support_keyboard, support_category_keyboard, cancel_keyboard, main_menu
from db import create_ticket, get_or_create_user

CHOOSE_CATEGORY, WRITE_SUBJECT, WRITE_MESSAGE = range(3)

# callback → (برچسب نمایشی، کد دسته در دیتابیس)
CATEGORIES = {
    'ticket_diamond': ('مشکل جم', 'diamond'),
    'ticket_payment': ('مشکل پرداخت', 'payment'),
    'ticket_order': ('مشکل سفارش', 'other'),
    'ticket_account': ('مشکل اکانت', 'account'),
    'ticket_other': ('سایر', 'other'),
}


async def support_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎧 *پشتیبانی Atomic Shop*\n\n"
        "برای ارسال تیکت روی «تیکت جدید» بزن.\n"
        "پشتیبانی ما ۲۴/۷ آنلاینه!"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=support_keyboard()
        )
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=support_keyboard())


async def new_ticket_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📂 *دسته‌بندی مشکل رو انتخاب کن:*",
        parse_mode='Markdown',
        reply_markup=support_category_keyboard()
    )
    return CHOOSE_CATEGORY


async def choose_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    label, code = CATEGORIES.get(query.data, ('سایر', 'other'))
    ctx.user_data['ticket_category'] = label
    ctx.user_data['ticket_category_code'] = code
    await query.edit_message_text(
        f"✅ دسته: *{label}*\n\n"
        "✏️ حالا موضوع تیکتت رو بنویس (یه جمله کوتاه):",
        parse_mode='Markdown',
        reply_markup=cancel_keyboard()
    )
    return WRITE_SUBJECT


async def write_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['ticket_subject'] = update.message.text.strip()
    await update.message.reply_text(
        f"📝 موضوع: *{ctx.user_data['ticket_subject']}*\n\n"
        "حالا مشکلت رو کامل توضیح بده:",
        parse_mode='Markdown',
        reply_markup=cancel_keyboard()
    )
    return WRITE_MESSAGE


async def write_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text.strip()
    tg_user = update.effective_user

    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            tg_user.id, tg_user.first_name or '', tg_user.last_name or '', tg_user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    subject = ctx.user_data.get('ticket_subject', 'بدون موضوع')
    category = ctx.user_data.get('ticket_category', 'سایر')
    category_code = ctx.user_data.get('ticket_category_code', 'other')

    ticket_id = create_ticket(db_id, subject, msg_text, category=category_code)

    ctx.user_data.pop('ticket_subject', None)
    ctx.user_data.pop('ticket_category', None)
    ctx.user_data.pop('ticket_category_code', None)

    await update.message.reply_text(
        f"✅ *تیکت #{ticket_id} ثبت شد!*\n\n"
        f"📂 دسته: {category}\n"
        f"📋 موضوع: {subject}\n\n"
        "پشتیبانی ما در اسرع وقت پاسخ میده. ممنون از صبرت 🙏",
        parse_mode='Markdown',
        reply_markup=main_menu()
    )
    return ConversationHandler.END


async def cancel_ticket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer("لغو شد")
        await query.edit_message_text("❌ ارسال تیکت لغو شد.", reply_markup=None)
    else:
        await update.message.reply_text("❌ لغو شد.", reply_markup=main_menu())
    ctx.user_data.pop('ticket_subject', None)
    ctx.user_data.pop('ticket_category', None)
    return ConversationHandler.END


def support_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(new_ticket_start, pattern='^new_ticket$')],
        states={
            CHOOSE_CATEGORY: [
                CallbackQueryHandler(choose_category, pattern='^ticket_'),
                CallbackQueryHandler(cancel_ticket, pattern='^cancel$'),
            ],
            WRITE_SUBJECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, write_subject),
                CallbackQueryHandler(cancel_ticket, pattern='^cancel$'),
            ],
            WRITE_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, write_message),
                CallbackQueryHandler(cancel_ticket, pattern='^cancel$'),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_ticket, pattern='^cancel$')],
        per_message=False,
    )
